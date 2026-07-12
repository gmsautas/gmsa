from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

AUDIENCE_CHOICES = (
    "all",
    "members",
    "admins",
    "unpaid_dues",
    "paid_dues",
    "graduating",
)


class SmsCampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: datetime = Field(validation_alias="created_at")
    audience: str
    message: str
    characters: int
    segments: int
    recipients_count: int
    status: str


class SmsCampaignCreate(BaseModel):
    audience: str
    message: str = Field(min_length=1, max_length=918)


class EmailCampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: datetime = Field(validation_alias="created_at")
    audience: str
    subject: str
    status: str
    open_rate: float | None
    recipients_count: int


class EmailCampaignCreate(BaseModel):
    audience: str
    subject: str
    body: str
