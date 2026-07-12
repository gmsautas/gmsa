from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    category: str
    icon: str
    summary: str
    target: Decimal
    current: Decimal
    currency: str
    status: str
    deadline: date | None
    progress_percent: int


class ProjectCreate(BaseModel):
    slug: str
    title: str
    category: str
    icon: str = "layout-grid"
    summary: str
    target: Decimal
    currency: str = "GHS"
    status: str = "open"
    deadline: date | None = None


class ProjectUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    icon: str | None = None
    summary: str | None = None
    target: Decimal | None = None
    status: str | None = None
    deadline: date | None = None
