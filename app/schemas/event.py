from datetime import date as _date

from pydantic import BaseModel, ConfigDict


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    category: str
    date: _date
    time: str
    end_time: str | None
    location: str
    description: str
    is_public: bool
    capacity: int | None
    rsvp_required: bool
    icon: str
    rsvp_count: int = 0
    is_rsvped: bool = False


class EventCreate(BaseModel):
    title: str
    category: str
    date: _date
    time: str
    end_time: str | None = None
    location: str
    description: str
    is_public: bool = True
    capacity: int | None = None
    rsvp_required: bool = False
    icon: str = "calendar"


class EventUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    date: _date | None = None
    time: str | None = None
    end_time: str | None = None
    location: str | None = None
    description: str | None = None
    is_public: bool | None = None
    capacity: int | None = None
    rsvp_required: bool | None = None
    icon: str | None = None
