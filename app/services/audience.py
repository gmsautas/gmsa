"""Resolve a campaign "audience" key into a concrete list of recipients.

Used by the admin communications routes (SMS via Arkesel, email via Resend)
to turn a friendly audience selector into the matching set of users.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DuesRecord, User
from app.services import academic

AUDIENCE_LABELS: dict[str, str] = {
    "all": "All Members",
    "members": "Members",
    "admins": "Admins & Executives",
    "unpaid_dues": "Unpaid Dues",
    "paid_dues": "Paid Dues",
    "graduating": "Graduating Members",
}


def current_dues_period_label(today: date | None = None) -> str:
    """The current academic-year billing period, e.g. "2026/2027" for the
    Sept 2026 -- Aug 2027 academic year. Dues are billed once per academic
    year (not per semester), so every DuesRecord for a given year shares
    this one label regardless of when during the year it was generated."""
    start = academic.current_academic_year_start(today)
    return f"{start}/{start + 1}"


async def resolve_audience(db: AsyncSession, key: str) -> list[User]:
    base = select(User).where(User.status == "active")

    if key == "all":
        result = await db.execute(base)
    elif key == "members":
        result = await db.execute(base.where(User.role == "member"))
    elif key == "admins":
        result = await db.execute(base.where(User.role.in_(("admin", "superadmin"))))
    elif key == "graduating":
        result = await db.execute(base.where(User.grad_year == date.today().year))
    elif key in ("unpaid_dues", "paid_dues"):
        period = current_dues_period_label()
        target_status = "unpaid" if key == "unpaid_dues" else "paid"
        dues_result = await db.execute(
            select(DuesRecord.user_id).where(
                DuesRecord.semester == period, DuesRecord.status == target_status
            )
        )
        user_ids = [row[0] for row in dues_result.all()]
        if not user_ids:
            return []
        result = await db.execute(base.where(User.id.in_(user_ids)))
    else:
        return []

    return list(result.scalars().all())


def audience_label(key: str, count: int) -> str:
    label = AUDIENCE_LABELS.get(key, key)
    return f"{label} ({count})"
