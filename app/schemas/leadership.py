from pydantic import BaseModel, ConfigDict


class LeadershipMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    role: str
    phone: str | None
    initials: str | None
    photo_url: str | None
    order_index: int


class LeadershipMemberCreate(BaseModel):
    name: str
    role: str
    phone: str | None = None
    initials: str | None = None
    photo_url: str | None = None
    order_index: int = 0


class LeadershipMemberUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    phone: str | None = None
    initials: str | None = None
    photo_url: str | None = None
    order_index: int | None = None


class CommitteeMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    role: str
    phone: str | None
    order_index: int


class CommitteeMemberCreate(BaseModel):
    name: str
    role: str
    phone: str | None = None
    order_index: int = 0


class CommitteeMemberUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    phone: str | None = None
    order_index: int | None = None


class CommitteeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    order_index: int
    members: list[CommitteeMemberOut]


class CommitteeCreate(BaseModel):
    name: str
    order_index: int = 0


class CommitteeUpdate(BaseModel):
    name: str | None = None
    order_index: int | None = None


class LeadershipBoardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    term: str
    is_current: bool
    members: list[LeadershipMemberOut]
    committees: list[CommitteeOut]


class LeadershipBoardCreate(BaseModel):
    term: str
    is_current: bool = False


class LeadershipBoardUpdate(BaseModel):
    term: str | None = None
    is_current: bool | None = None
