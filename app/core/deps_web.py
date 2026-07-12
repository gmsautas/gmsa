from urllib.parse import quote

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models.models import User


class PageRedirect(Exception):
    """Raise to redirect a browser page request."""

    def __init__(self, url: str, status_code: int = 303):
        self.url = url
        self.status_code = status_code


async def _user_from_cookie(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get("gmsa_access")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    result = await db.execute(
        select(User).where(User.id == int(user_id), User.status == "active")
    )
    return result.scalar_one_or_none()


async def get_optional_user(request: Request, db: AsyncSession) -> User | None:
    return await _user_from_cookie(request, db)


async def require_member(
    request: Request, db: AsyncSession, *, allow_password_change: bool = False
) -> User:
    user = await _user_from_cookie(request, db)
    if not user:
        raise PageRedirect(f"/login?next={quote(request.url.path)}")
    if user.must_change_password and not allow_password_change:
        raise PageRedirect("/force-password-change")
    return user


async def require_admin(
    request: Request, db: AsyncSession, *, allow_password_change: bool = False
) -> User:
    user = await _user_from_cookie(request, db)
    if not user or user.role not in ("admin", "superadmin"):
        raise PageRedirect(f"/admin/login?next={quote(request.url.path)}")
    if user.must_change_password and not allow_password_change:
        raise PageRedirect("/force-password-change")
    return user


class Forbidden(Exception):
    """Raised when a logged-in admin lacks the required (superadmin) role."""

    def __init__(self, message: str = "This action requires a super admin."):
        self.message = message


async def require_superadmin(request: Request, db: AsyncSession) -> User:
    """Gate for destructive/outcome-affecting actions. Assumes require_admin has
    already (or will) run; distinguishes a non-admin (redirect to login) from an
    admin who simply isn't a superadmin (Forbidden)."""
    user = await _user_from_cookie(request, db)
    if not user or user.role not in ("admin", "superadmin"):
        raise PageRedirect(f"/admin/login?next={quote(request.url.path)}")
    if user.role != "superadmin":
        raise Forbidden()
    if user.must_change_password:
        raise PageRedirect("/force-password-change")
    return user
