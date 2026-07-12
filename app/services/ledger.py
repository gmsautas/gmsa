"""Side-effects applied when a payment transitions between statuses.

Shared by the payment verify endpoint and the Paystack webhook handler so
both code paths stay in sync.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DuesRecord, Project, Transaction


async def apply_successful_transaction(db: AsyncSession, transaction: Transaction) -> None:
    """Apply the effects of a transaction that just became 'success'."""
    if transaction.type == "project" and transaction.project_id:
        project = await db.get(Project, transaction.project_id)
        if project is not None:
            project.current = Decimal(project.current) + Decimal(transaction.amount)

    if transaction.type == "dues":
        result = await db.execute(
            select(DuesRecord).where(DuesRecord.transaction_id == transaction.id)
        )
        dues_record = result.scalar_one_or_none()
        if dues_record is not None:
            dues_record.status = "paid"
            dues_record.paid_at = transaction.updated_at


async def revert_pending_dues(db: AsyncSession, transaction: Transaction) -> None:
    """Roll a dues record back to 'unpaid' if its payment failed."""
    if transaction.type != "dues":
        return
    result = await db.execute(
        select(DuesRecord).where(DuesRecord.transaction_id == transaction.id)
    )
    dues_record = result.scalar_one_or_none()
    if dues_record is not None and dues_record.status == "pending":
        dues_record.status = "unpaid"
