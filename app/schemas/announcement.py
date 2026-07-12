from datetime import date as _date

from pydantic import BaseModel, ConfigDict


class AnnouncementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    date: _date
    audience: str
    link_url: str | None = None


class AnnouncementCreate(BaseModel):
    title: str
    body: str
    date: _date | None = None
    audience: str = "All Members"
    link_url: str | None = None


class AnnouncementUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    date: _date | None = None
    audience: str | None = None
    link_url: str | None = None
