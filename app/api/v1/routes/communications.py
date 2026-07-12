from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.models import EmailCampaign, SmsCampaign, User
from app.schemas.campaign import (
    AUDIENCE_CHOICES,
    EmailCampaignCreate,
    EmailCampaignOut,
    SmsCampaignCreate,
    SmsCampaignOut,
)
from app.services import arkesel, campaign_sender
from app.services.audience import audience_label, resolve_audience

router = APIRouter(prefix="/admin", tags=["communications"])


@router.get("/sms-campaigns", response_model=list[SmsCampaignOut])
async def list_sms_campaigns(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[SmsCampaign]:
    result = await db.execute(select(SmsCampaign).order_by(SmsCampaign.created_at.desc()))
    return list(result.scalars().all())


@router.post(
    "/sms-campaigns", response_model=SmsCampaignOut, status_code=status.HTTP_201_CREATED
)
async def create_sms_campaign(
    payload: SmsCampaignCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> SmsCampaign:
    if payload.audience not in AUDIENCE_CHOICES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid audience. Must be one of: {', '.join(AUDIENCE_CHOICES)}",
        )

    recipients = [
        u for u in await resolve_audience(db, payload.audience) if u.phone and u.sms_opt_in
    ]

    campaign = SmsCampaign(
        audience=audience_label(payload.audience, len(recipients)),
        message=payload.message,
        characters=len(payload.message),
        segments=arkesel.count_segments(payload.message),
        recipients_count=len(recipients),
        sent_by_id=admin.id,
        status="pending",
    )
    db.add(campaign)
    # Commit (not just flush) to get a real campaign.id before the send loop
    # starts writing SmsCampaignRecipient rows against it -- see
    # app.services.campaign_sender for the paced/chunked send itself.
    await db.commit()
    await db.refresh(campaign)

    if recipients:
        await campaign_sender.send_sms_campaign(db, campaign, recipients, payload.message)
    else:
        campaign.status = "failed"
        await db.commit()
        await db.refresh(campaign)

    return campaign


@router.get("/email-campaigns", response_model=list[EmailCampaignOut])
async def list_email_campaigns(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[EmailCampaign]:
    result = await db.execute(select(EmailCampaign).order_by(EmailCampaign.created_at.desc()))
    return list(result.scalars().all())


@router.post(
    "/email-campaigns", response_model=EmailCampaignOut, status_code=status.HTTP_201_CREATED
)
async def create_email_campaign(
    payload: EmailCampaignCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> EmailCampaign:
    if payload.audience not in AUDIENCE_CHOICES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid audience. Must be one of: {', '.join(AUDIENCE_CHOICES)}",
        )

    recipients = [u for u in await resolve_audience(db, payload.audience) if u.email_opt_in]

    campaign = EmailCampaign(
        audience=audience_label(payload.audience, len(recipients)),
        subject=payload.subject,
        body=payload.body,
        recipients_count=len(recipients),
        sent_by_id=admin.id,
        status="pending",
        open_rate=None,
    )
    db.add(campaign)
    # Commit (not just flush) to get a real campaign.id before the send loop
    # starts writing EmailCampaignRecipient rows against it -- see
    # app.services.campaign_sender for the paced, one-call-per-recipient send.
    await db.commit()
    await db.refresh(campaign)

    if recipients:
        await campaign_sender.send_email_campaign(
            db, campaign, recipients, subject=payload.subject, html=payload.body
        )
    else:
        campaign.status = "failed"
        await db.commit()
        await db.refresh(campaign)

    return campaign
