from pydantic import BaseModel, EmailStr, Field, field_validator

from app.services.academic import PROGRAM_CATEGORIES


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = None
    program: str | None = None
    program_category: str
    student_id: str = Field(min_length=4, max_length=20)

    @field_validator("student_id")
    @classmethod
    def validate_student_id(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("Student ID must contain only digits")
        return value

    @field_validator("program_category")
    @classmethod
    def validate_program_category(cls, value: str) -> str:
        if value not in PROGRAM_CATEGORIES:
            raise ValueError(f"program_category must be one of: {', '.join(PROGRAM_CATEGORIES)}")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)
