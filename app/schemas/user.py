from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    phone: str | None
    program: str | None
    program_category: str | None
    student_id: str | None
    grad_year: int | None
    level: int | None
    role: str
    status: str
    member_since: date
    sms_opt_in: bool
    email_opt_in: bool
    initials: str


class UserUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    program: str | None = None
    grad_year: int | None = None
    sms_opt_in: bool | None = None
    email_opt_in: bool | None = None


class UserAdminOut(UserOut):
    dues_status: str


class UserAdminUpdate(BaseModel):
    role: str | None = None
    status: str | None = None
    title: str | None = None
    level_override: int | None = None
    grad_year_override: int | None = None
    academic_override_note: str | None = None


class BulkMemberAction(BaseModel):
    user_ids: list[int]
    action: str  # activate | deactivate | role_member | role_admin | delete
