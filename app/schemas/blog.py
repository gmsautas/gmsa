from datetime import date as _date

from pydantic import BaseModel, ConfigDict


class BlogPostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    excerpt: str
    category: str
    author_name: str
    author_role: str | None
    date: _date
    read_time: int
    icon: str
    content: list[str]
    status: str


class BlogPostCreate(BaseModel):
    slug: str
    title: str
    excerpt: str
    category: str
    author_name: str
    author_role: str | None = None
    date: _date | None = None
    read_time: int = 1
    icon: str = "book-open"
    content: list[str]
    status: str = "published"


class BlogPostUpdate(BaseModel):
    slug: str | None = None
    title: str | None = None
    excerpt: str | None = None
    category: str | None = None
    author_name: str | None = None
    author_role: str | None = None
    date: _date | None = None
    read_time: int | None = None
    icon: str | None = None
    content: list[str] | None = None
    status: str | None = None
