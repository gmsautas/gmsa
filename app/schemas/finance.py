from datetime import date as _date
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: _date
    name: str
    type: str
    description: str
    amount: Decimal
    currency: str
    method: str | None
    reference: str
    status: str
    project: str | None = None


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: _date
    description: str
    category: str
    amount: Decimal
    currency: str
    recorded_by: str | None
    receipt_url: str | None


class ExpenseCreate(BaseModel):
    date: _date | None = None
    description: str
    category: str
    amount: Decimal
    currency: str = "GHS"
    receipt_url: str | None = None


class DuesRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    semester: str
    amount: Decimal
    currency: str
    status: str
    due_date: _date | None
    paid_at: datetime | None


class DuesStatusOut(BaseModel):
    status: str
    amount: Decimal | None
    semester: str | None
    history: list[DuesRecordOut]


class FinanceSummaryOut(BaseModel):
    total_income: Decimal
    total_expenses: Decimal
    net_balance: Decimal
    active_projects: int


# ---- Payments (Paystack) ---------------------------------------------------

PAYMENT_TYPES = ("dues", "donation", "project")


class PaymentInitRequest(BaseModel):
    type: str
    amount: Decimal | None = None
    email: EmailStr | None = None
    donor_name: str | None = None
    project_slug: str | None = None


class PaymentInitResponse(BaseModel):
    authorization_url: str
    access_code: str
    reference: str


class PaymentVerifyResponse(BaseModel):
    reference: str
    status: str
    amount: Decimal
    currency: str
