"""Backfill DuesRecord rows for active members.

Nothing else in the app ever inserts a DuesRecord for the live (non-seed)
flow -- admins editing the per-tier amounts on /admin/settings only touch
OrgSettings, and the member dashboard/dues page just reads whatever
DuesRecord rows already exist. This is the one place that bridges the two:
call it whenever the admin should expect members to see a due (right after
saving dues amounts, or on demand via "Generate Dues Now").

Uses academic.effective_dues_amount, which already falls back to the
Continuing-tier amount for members with no resolvable level/tier (e.g.
bulk-upload accounts missing program_category), so a null level never means
a member gets skipped here.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import DuesRecord, User
from app.services import academic
from app.services.audience import current_dues_period_label


async def generate_dues_records(
    db: AsyncSession,
    *,
    semester: str | None = None,
    amount: Decimal | int | None = None,
) -> int:
    """Ensure every active member has a DuesRecord for `semester` (default:
    the current academic-year billing period). Members who already have one
    for that period are left untouched. Returns the number of rows created."""
    semester = semester or current_dues_period_label()

    users_result = await db.execute(select(User).where(User.status == "active"))
    users = list(users_result.scalars().all())

    existing_result = await db.execute(
        select(DuesRecord.user_id).where(DuesRecord.semester == semester)
    )
    existing_user_ids = {row[0] for row in existing_result.all()}

    created = 0
    for u in users:
        if u.id in existing_user_ids:
            continue
        record_amount = (
            Decimal(amount) if amount is not None else academic.effective_dues_amount(u)
        )
        db.add(
            DuesRecord(
                user_id=u.id,
                semester=semester,
                amount=record_amount,
                currency="GHS",
                status="unpaid",
            )
        )
        created += 1

    await db.commit()
    return created
