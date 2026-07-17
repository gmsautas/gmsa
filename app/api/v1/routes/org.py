import html
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.models import Event, OrgSettings, Transaction, User
from app.schemas.org import PAYMENT_FIELDS, ContactMessageRequest, OrgOut, OrgUpdate, StatsOut
from app.services.resend_client import ResendError, send_email

router = APIRouter(prefix="/org", tags=["org"])
contact_router = APIRouter(tags=["org"])


async def _get_or_create_org(db: AsyncSession) -> OrgSettings:
    org = await db.get(OrgSettings, 1)
    if org is None:
        org = OrgSettings(id=1)
        db.add(org)
        await db.commit()
        await db.refresh(org)
    return org


@router.get("", response_model=OrgOut)
async def get_org(db: AsyncSession = Depends(get_db)) -> OrgSettings:
    return await _get_or_create_org(db)


@router.patch("", response_model=OrgOut)
async def update_org(
    payload: OrgUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> OrgSettings:
    data = payload.model_dump(exclude_unset=True)
    if (data.keys() & PAYMENT_FIELDS) and admin.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Changing payout details requires a super admin",
        )
    org = await _get_or_create_org(db)
    for field, value in data.items():
        setattr(org, field, value)
    await db.commit()
    await db.refresh(org)
    return org


@router.get("/stats", response_model=StatsOut)
async def get_stats(db: AsyncSession = Depends(get_db)) -> StatsOut:
    org = await _get_or_create_org(db)

    active_members_result = await db.execute(
        select(func.count()).select_from(User).where(User.status == "active")
    )
    active_members = active_members_result.scalar_one()

    total_raised_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.status == "success"
        )
    )
    total_raised = total_raised_result.scalar_one()

    today = date.today()
    window_start = today - timedelta(days=182)
    window_end = today + timedelta(days=182)
    events_per_year_result = await db.execute(
        select(func.count())
        .select_from(Event)
        .where(Event.date >= window_start, Event.date <= window_end)
    )
    events_per_year = events_per_year_result.scalar_one()

    years_active = today.year - org.founding_year

    return StatsOut(
        active_members=active_members,
        total_raised=total_raised,
        events_per_year=events_per_year,
        years_active=years_active,
    )


@contact_router.post("/contact", status_code=status.HTTP_202_ACCEPTED)
async def contact(payload: ContactMessageRequest, db: AsyncSession = Depends(get_db)) -> dict:
    org = await _get_or_create_org(db)

    # Escape user-supplied text before it lands in HTML sent to a real staff
    # inbox -- otherwise a submitter can inject markup/scripts into the email.
    body_html = (
        f"<p><strong>Name:</strong> {html.escape(payload.name)}</p>"
        f"<p><strong>Email:</strong> {html.escape(payload.email)}</p>"
        f"<p><strong>Message:</strong></p>"
        f"<p>{html.escape(payload.message)}</p>"
    )

    try:
        await send_email(
            to=[org.email],
            subject=f"Contact form: {payload.subject.strip()}",
            html=body_html,
        )
    except ResendError:
        pass

    return {"message": "Thanks for reaching out — we'll get back to you soon."}
