from datetime import date as _date

from pydantic import BaseModel, ConfigDict


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    category: str
    type: str
    size: str | None
    date: _date
    description: str
    file_url: str


class ResourceCreate(BaseModel):
    title: str
    category: str
    type: str
    size: str | None = None
    date: _date | None = None
    description: str
    file_url: str


class ResourceUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    type: str | None = None
    size: str | None = None
    date: _date | None = None
    description: str | None = None
    file_url: str | None = None
