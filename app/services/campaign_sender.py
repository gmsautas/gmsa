"""Sends SmsCampaign/EmailCampaign messages to an already-resolved audience.

Audience resolution stays in app.services.audience (resolve_audience) --
this module only owns the actual send loop, extracted out of
app/api/v1/routes/communications.py's route handlers (Phase 7 of the
remediation plan) for two reasons:

1. Privacy: the old code put the *entire* resolved audience into one
   `resend_client.send_email(to=[...all recipients...])` call. Resend/Brevo/
   SES all put the literal `to` list into the outgoing message headers, so
   every recipient's email address was visible to every other recipient --
   not just a throttling risk, a real address-disclosure leak. Fixed here by
   sending one call per recipient, each with a single-element `to` list.
2. Throttling: putting 100s-1000s of recipients into one API call (email) or
   an unbounded `recipients` array (SMS) risks the exact same "one giant
   blast" failure mode that caused this org's real production
   email-throttling incident. Fixed by pacing: a short delay between
   individual email sends, and chunking SMS into bounded batches with a
   pause between them.

Both send loops commit one row to *CampaignRecipient after each unit of work
(one recipient for email, one chunk for SMS) -- same commit-per-row,
partial-failure-tolerant pattern used by app.services.elections.import_register,
so a request that gets cut off partway (timeout, deploy, worker recycle)
only loses progress after the cutoff, never rolls back sends that already
went out.
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EmailCampaign,
    EmailCampaignRecipient,
    SmsCampaign,
    SmsCampaignRecipient,
    User,
)
from app.services import arkesel, resend_client

logger = logging.getLogger(__name__)

# ── Email ────────────────────────────────────────────────────────────────
# One send per recipient (see module docstring for why: a shared `to` array
# leaks every address to every other recipient). Paced with a short delay
# between sends rather than firing them all concurrently, so a large
# campaign doesn't slam whichever provider is configured (Resend/Brevo/SES/
# Gmail) with hundreds of near-simultaneous requests.
EMAIL_SEND_DELAY_SECONDS = 0.3

# ── SMS ──────────────────────────────────────────────────────────────────
# Arkesel's v2 /sms/send endpoint (app/services/arkesel.py) takes one shared
# `message` body plus a `recipients` array in a single API call. Per
# Arkesel's docs (https://developers.arkesel.com/sms-api) and how SMS
# aggregators generally behave, each number in `recipients` is sent its own
# individual text message by Arkesel server-side -- there is no shared "to"
# header an SMS recipient can see the way an email recipient can see a
# shared `to` array. So, unlike email, batching many recipients into one
# Arkesel call is NOT a privacy leak -- it is purely a rate-limiting /
# request-size concern. We chunk into bounded batches with a short pause
# between batches instead of sending one API call per recipient.
SMS_CHUNK_SIZE = 300
SMS_CHUNK_DELAY_SECONDS = 1.0


def _aggregate_status(sent: int, failed: int) -> str:
    """Roll per-recipient outcomes up into the campaign's single `status`
    column. "partial" (new, see models.CAMPAIGN_STATUSES) means at least one
    recipient succeeded and at least one failed -- previously invisible,
    since the old code only ever recorded "sent" or "failed" for the whole
    campaign."""
    if sent and failed:
        return "partial"
    if sent:
        return "sent"
    return "failed"


async def send_email_campaign(
    db: AsyncSession,
    campaign: EmailCampaign,
    recipients: list[User],
    *,
    subject: str,
    html: str,
) -> None:
    """Sends `subject`/`html` to each user in `recipients` one at a time,
    recording one EmailCampaignRecipient row per attempt and updating
    `campaign.status` to the aggregate outcome once done. Assumes `campaign`
    is already persisted (has an id) and `recipients` is non-empty --
    callers should handle the "no eligible recipients" case themselves."""
    sent = 0
    failed = 0
    last_index = len(recipients) - 1

    for index, user in enumerate(recipients):
        row = EmailCampaignRecipient(
            campaign_id=campaign.id, user_id=user.id, email=user.email, status="pending"
        )
        try:
            await resend_client.send_email(to=[user.email], subject=subject, html=html)
        except resend_client.ResendError as err:
            logger.error(
                "Email campaign %s: send to %s failed: %s", campaign.id, user.email, err
            )
            row.status = "failed"
            row.error = str(err)[:500]
            failed += 1
        else:
            row.status = "sent"
            row.sent_at = datetime.utcnow()
            sent += 1

        db.add(row)
        await db.commit()

        if index < last_index:
            await asyncio.sleep(EMAIL_SEND_DELAY_SECONDS)

    campaign.status = _aggregate_status(sent, failed)
    await db.commit()


async def send_sms_campaign(
    db: AsyncSession,
    campaign: SmsCampaign,
    recipients: list[User],
    message: str,
) -> None:
    """Sends `message` to `recipients` in chunks of SMS_CHUNK_SIZE, recording
    one SmsCampaignRecipient row per recipient per chunk and updating
    `campaign.status` to the aggregate outcome once done. Assumes `campaign`
    is already persisted (has an id) and `recipients` is non-empty --
    callers should handle the "no eligible recipients" case themselves."""
    chunks = [
        recipients[i : i + SMS_CHUNK_SIZE] for i in range(0, len(recipients), SMS_CHUNK_SIZE)
    ]
    sent = 0
    failed = 0
    last_index = len(chunks) - 1

    for index, chunk in enumerate(chunks):
        phones = [u.phone for u in chunk if u.phone]
        try:
            await arkesel.send_sms(phones, message)
        except arkesel.ArkeselError as err:
            logger.error(
                "SMS campaign %s: batch %d/%d (%d recipients) failed: %s",
                campaign.id,
                index + 1,
                len(chunks),
                len(chunk),
                err,
            )
            for user in chunk:
                db.add(
                    SmsCampaignRecipient(
                        campaign_id=campaign.id,
                        user_id=user.id,
                        phone=user.phone or "",
                        status="failed",
                        error=str(err)[:500],
                    )
                )
                failed += 1
        else:
            now = datetime.utcnow()
            for user in chunk:
                db.add(
                    SmsCampaignRecipient(
                        campaign_id=campaign.id,
                        user_id=user.id,
                        phone=user.phone or "",
                        status="sent",
                        sent_at=now,
                    )
                )
                sent += 1

        await db.commit()

        if index < last_index:
            await asyncio.sleep(SMS_CHUNK_DELAY_SECONDS)

    campaign.status = _aggregate_status(sent, failed)
    await db.commit()
