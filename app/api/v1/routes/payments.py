from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional
from app.core.config import settings
from app.core.database import get_db
from app.models import DuesRecord, Project, Transaction, User
from app.schemas.finance import (
    PaymentInitRequest,
    PaymentInitResponse,
    PaymentVerifyResponse,
)
from app.services import ledger, org_settings_cache, paystack
from app.services.audience import current_semester_label

router = APIRouter(prefix="/payments", tags=["payments"])


def _method_label(channel: str | None) -> str:
    return {
        "card": "Card",
        "mobile_money": "Mobile Money",
        "bank": "Bank Transfer",
        "bank_transfer": "Bank Transfer",
        "ussd": "USSD",
        "qr": "QR Code",
    }.get(channel or "", "Other")


def _generate_reference() -> str:
    return f"GMSA-PSK-{uuid4().hex[:8].upper()}"


def _resolve_donor(
    user: User | None, payload: PaymentInitRequest
) -> tuple[str, str]:
    """Resolve (email, donor_name) for donation/project payments."""
    email = user.email if user else payload.email
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required for anonymous donations",
        )
    donor_name = user.name if user else (payload.donor_name or "Anonymous")
    return email, donor_name


@router.post("/initialize", response_model=PaymentInitResponse)
async def initialize_payment(
    payload: PaymentInitRequest,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> PaymentInitResponse:
    if payload.type == "dues":
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication is required to pay dues",
            )

        semester = current_semester_label()
        result = await db.execute(
            select(DuesRecord).where(
                DuesRecord.user_id == user.id, DuesRecord.semester == semester
            )
        )
        dues_record = result.scalar_one_or_none()

        if dues_record is not None and dues_record.status == "paid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dues already paid for this semester",
            )

        if dues_record is None:
            dues_record = DuesRecord(
                amount=org_settings_cache.get("dues_amount_ghs") or settings.dues_amount_ghs,
                currency="GHS",
                status="unpaid",
                semester=semester,
                user_id=user.id,
            )
            db.add(dues_record)

        transaction = Transaction(
            type="dues",
            status="pending",
            amount=dues_record.amount,
            currency=dues_record.currency,
            description=f"Membership Dues — {semester}",
            reference=_generate_reference(),
            user_id=user.id,
        )
        db.add(transaction)
        await db.flush()

        dues_record.transaction_id = transaction.id
        dues_record.status = "pending"

        email = user.email

    elif payload.type == "donation":
        if payload.amount is None or payload.amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A positive amount is required",
            )

        email, donor_name = _resolve_donor(user, payload)

        transaction = Transaction(
            type="donation",
            status="pending",
            amount=payload.amount,
            currency="GHS",
            description="General Donation",
            reference=_generate_reference(),
            user_id=user.id if user else None,
            donor_name=None if user else donor_name,
            donor_email=None if user else email,
        )
        db.add(transaction)
        await db.flush()

    elif payload.type == "project":
        if payload.amount is None or payload.amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A positive amount is required",
            )
        if not payload.project_slug:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A project is required",
            )

        result = await db.execute(
            select(Project).where(Project.slug == payload.project_slug)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        if project.status != "open":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This project is not currently accepting donations",
            )

        email, donor_name = _resolve_donor(user, payload)

        transaction = Transaction(
            type="project",
            status="pending",
            amount=payload.amount,
            currency="GHS",
            description=f"Donation: {project.title}",
            reference=_generate_reference(),
            project_id=project.id,
            user_id=user.id if user else None,
            donor_name=None if user else donor_name,
            donor_email=None if user else email,
        )
        db.add(transaction)
        await db.flush()

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment type"
        )

    await db.commit()

    try:
        data = await paystack.initialize_transaction(
            email=email,
            amount=transaction.amount,
            reference=transaction.reference,
            callback_url=settings.paystack_callback_url,
            metadata={"type": payload.type, "transaction_id": transaction.id},
        )
    except paystack.PaystackError as err:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(err)) from err

    await db.commit()

    return PaymentInitResponse(
        authorization_url=data["authorization_url"],
        access_code=data["access_code"],
        reference=transaction.reference,
    )


@router.get("/verify/{reference}", response_model=PaymentVerifyResponse)
async def verify_payment(
    reference: str,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> PaymentVerifyResponse:
    result = await db.execute(select(Transaction).where(Transaction.reference == reference))
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    if transaction.status != "pending":
        return PaymentVerifyResponse(
            reference=transaction.reference,
            status=transaction.status,
            amount=transaction.amount,
            currency=transaction.currency,
        )

    try:
        data = await paystack.verify_transaction(reference)
    except paystack.PaystackError as err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(err)) from err

    transaction.method = _method_label(data.get("channel"))
    transaction.paystack_data = data

    if data.get("status") == "success":
        transaction.status = "success"
        await ledger.apply_successful_transaction(db, transaction)
    else:
        transaction.status = "failed"
        await ledger.revert_pending_dues(db, transaction)

    await db.commit()

    return PaymentVerifyResponse(
        reference=transaction.reference,
        status=transaction.status,
        amount=transaction.amount,
        currency=transaction.currency,
    )


@router.post("/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    body = await request.body()
    signature = request.headers.get("x-paystack-signature")

    if not paystack.verify_webhook_signature(body, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    payload = await request.json()

    if payload.get("event") == "charge.success":
        data = payload["data"]
        reference = data["reference"]

        result = await db.execute(select(Transaction).where(Transaction.reference == reference))
        transaction = result.scalar_one_or_none()

        if transaction is not None and transaction.status == "pending":
            transaction.method = _method_label(data.get("channel"))
            transaction.paystack_data = data
            transaction.status = "success"
            await ledger.apply_successful_transaction(db, transaction)
            await db.commit()

    return {"received": True}
