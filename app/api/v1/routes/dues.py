from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.models import DuesRecord, User
from app.schemas.finance import DuesRecordOut, DuesStatusOut
from app.services import academic
from app.services.audience import current_dues_period_label
from app.services.dues import generate_dues_records

me_router = APIRouter(prefix="/me", tags=["dues"])
admin_router = APIRouter(prefix="/admin/dues", tags=["dues-admin"])


class AdminDuesRowOut(BaseModel):
    user_id: int
    name: str
    email: str
    student_id: str | None
    level: int | None
    dues_tier: str | None
    semester: str
    amount: Decimal
    currency: str
    status: str
    due_date: date | None
    paid_at: datetime | None


class DuesGenerateRequest(BaseModel):
    semester: str | None = None
    amount: int | None = None


@me_router.get("/dues", response_model=DuesStatusOut)
async def get_my_dues(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DuesStatusOut:
    semester = current_dues_period_label()

    result = await db.execute(
        select(DuesRecord)
        .where(DuesRecord.user_id == user.id)
        .order_by(DuesRecord.id.desc())
    )
    records = list(result.scalars().all())

    current = next((r for r in records if r.semester == semester), None)
    if current is not None:
        status_ = current.status
        amount = current.amount
    else:
        status_ = "unpaid"
        amount = academic.effective_dues_amount(user)

    return DuesStatusOut(
        status=status_,
        amount=amount,
        semester=semester,
        history=[DuesRecordOut.model_validate(r) for r in records],
    )


@admin_router.get("", response_model=list[AdminDuesRowOut])
async def list_admin_dues(
    semester: str | None = None,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[AdminDuesRowOut]:
    target_semester = semester or current_dues_period_label()

    users_result = await db.execute(
        select(User).where(User.status == "active").order_by(User.name)
    )
    users = list(users_result.scalars().all())

    records_result = await db.execute(
        select(DuesRecord).where(DuesRecord.semester == target_semester)
    )
    records_by_user = {r.user_id: r for r in records_result.scalars().all()}

    rows: list[AdminDuesRowOut] = []
    for u in users:
        level = academic.effective_level(u)
        tier = academic.effective_dues_tier(u)
        record = records_by_user.get(u.id)
        if record is not None:
            rows.append(
                AdminDuesRowOut(
                    user_id=u.id,
                    name=u.name,
                    email=u.email,
                    student_id=u.student_id,
                    level=level,
                    dues_tier=tier,
                    semester=record.semester,
                    amount=record.amount,
                    currency=record.currency,
                    status=record.status,
                    due_date=record.due_date,
                    paid_at=record.paid_at,
                )
            )
        else:
            rows.append(
                AdminDuesRowOut(
                    user_id=u.id,
                    name=u.name,
                    email=u.email,
                    student_id=u.student_id,
                    level=level,
                    dues_tier=tier,
                    semester=target_semester,
                    amount=academic.effective_dues_amount(u),
                    currency="GHS",
                    status="unpaid",
                    due_date=None,
                    paid_at=None,
                )
            )

    return rows


@admin_router.post("/generate")
async def generate_dues(
    payload: DuesGenerateRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    payload = payload or DuesGenerateRequest()
    semester = payload.semester or current_dues_period_label()

    created = await generate_dues_records(db, semester=semester, amount=payload.amount)
    return {"created": created, "semester": semester}
