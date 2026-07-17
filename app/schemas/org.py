from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# Fields only a super admin may change via PATCH /org (payout details).
PAYMENT_FIELDS = {
    "momo_number",
    "momo_name",
    "bank_name",
    "bank_account_name",
    "bank_account_number",
}


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    full_name: str
    tagline: str
    email: str
    phone: str
    address: str
    social: dict
    momo_number: str | None = None
    momo_name: str | None = None
    bank_name: str | None = None
    bank_account_name: str | None = None
    bank_account_number: str | None = None


class OrgUpdate(BaseModel):
    name: str | None = None
    full_name: str | None = None
    tagline: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    social: dict | None = None
    momo_number: str | None = None
    momo_name: str | None = None
    bank_name: str | None = None
    bank_account_name: str | None = None
    bank_account_number: str | None = None


class StatsOut(BaseModel):
    active_members: int
    total_raised: Decimal
    events_per_year: int
    years_active: int


class ContactMessageRequest(BaseModel):
    name: str = Field(..., max_length=120)
    email: str = Field(..., max_length=255)
    subject: str = Field(..., max_length=200)
    message: str = Field(..., max_length=5000)
