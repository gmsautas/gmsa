"""Self-service password reset: request a link, verify it, consume it."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.models import PasswordResetToken, User
from app.services import email_failures, resend_client


class PasswordResetError(Exception):
    pass


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def _button_email_html(*, greeting_name: str, intro_html: str, action_url: str, footer_html: str) -> str:
    """Shared layout for every account/reset email that centers on a single
    click-to-act link — renders as a real button (styled anchor tag, since
    email clients don't run external CSS) with the raw URL underneath as a
    fallback for clients that strip button styling."""
    return (
        f"<p>Assalamu alaikum {greeting_name},</p>"
        f"{intro_html}"
        f"<p style='margin:28px 0'>"
        f"<a href='{action_url}' "
        f"style='background:#0f5132;color:#ffffff;padding:12px 28px;border-radius:8px;"
        f"text-decoration:none;font-weight:bold;display:inline-block;font-family:sans-serif'>"
        f"Set My Password</a></p>"
        f"<p style='font-size:12px;color:#666'>Or paste this link into your browser: "
        f"<a href='{action_url}'>{action_url}</a></p>"
        f"{footer_html}"
    )


async def _issue_reset_link(db: AsyncSession, *, user: User, base_url: str) -> str:
    """Invalidates any earlier unused link, issues a new one, durably commits
    it, and returns the action_url -- but does NOT send anything. Split out
    of _issue_and_email_link so a caller that wants to embed the link in an
    email it's composing itself (e.g. combined with a voter token in one
    message, see app.services.elections.import_register) can get the link
    without triggering a separate send."""
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=datetime.utcnow())
    )

    plaintext = _generate_token()
    expires_at = datetime.utcnow() + timedelta(minutes=settings.password_reset_token_expire_minutes)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(plaintext),
            expires_at=expires_at,
        )
    )
    await db.commit()

    return f"{base_url.rstrip('/')}/reset-password?token={plaintext}"


async def _send_link_email(
    *, email: str, name: str, action_url: str, subject: str, intro_html: str
) -> None:
    html = _button_email_html(
        greeting_name=name,
        intro_html=intro_html,
        action_url=action_url,
        footer_html=(
            f"<p>This link is valid for {settings.password_reset_token_expire_minutes} minutes "
            f"and can only be used once.</p>"
        ),
    )
    await resend_client.send_email(to=[email], subject=subject, html=html)


async def _issue_and_email_link(
    db: AsyncSession, *, user: User, base_url: str, subject: str, intro_html: str
) -> None:
    action_url = await _issue_reset_link(db, user=user, base_url=base_url)
    await _send_link_email(
        email=user.email, name=user.name, action_url=action_url, subject=subject, intro_html=intro_html
    )


async def request_reset(db: AsyncSession, *, email: str, base_url: str) -> None:
    """Look up the account and, if it exists, email a reset link. Always
    completes without raising for an unknown email — the caller shows the same
    generic "check your inbox" message either way, so this can't be used to
    enumerate which emails have accounts.

    A failed send (e.g. a misconfigured sender domain) is still never
    surfaced to the caller -- that's the whole point of the anti-enumeration
    behavior above -- but it's no longer silently lost server-side either:
    see email_failures.record_failure."""
    normalized = (email or "").strip().lower()
    if not normalized:
        return

    user = (
        await db.execute(select(User).where(func.lower(User.email) == normalized))
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        return

    try:
        await _issue_and_email_link(
            db,
            user=user,
            base_url=base_url,
            subject="Reset your GMSA UTAS password",
            intro_html=(
                "<p>We received a request to reset your GMSA UTAS account password. "
                "Click below to choose a new one:</p>"
            ),
        )
    except resend_client.ResendError as err:
        await email_failures.record_failure(
            db, recipient=user.email, purpose="password_reset", error=err
        )


async def send_account_setup_link(db: AsyncSession, *, user: User, base_url: str) -> None:
    """For a brand-new account, or an existing one whose original setup link
    never arrived — issues a fresh link and emails it instead of a plaintext
    temp password, so the member never has to copy/type one in. Unlike
    request_reset, the caller already knows this account exists (it just
    created or looked it up), so there's no anti-enumeration silent-return
    here, and a failed send is raised rather than swallowed so callers (the
    register-import flow, admin actions, scripts/local_gmail_import.py) can
    detect and report it instead of silently losing the notification."""
    await _issue_and_email_link(
        db,
        user=user,
        base_url=base_url,
        subject="Welcome to GMSA UTAS — set up your account",
        intro_html=(
            "<p>An account has been created for you on the GMSA UTAS member portal "
            f"(<strong>{user.email}</strong>). Click below to set your password and get started:</p>"
        ),
    )


async def issue_account_setup_link(db: AsyncSession, *, user: User, base_url: str) -> str:
    """Like send_account_setup_link, but returns the link instead of emailing
    it -- for a caller that wants to embed it in an email it's composing
    itself (e.g. app.services.elections.import_register combining it with a
    voter token into one message) rather than send it as its own message."""
    return await _issue_reset_link(db, user=user, base_url=base_url)


async def _get_valid_token_row(db: AsyncSession, token: str) -> tuple[PasswordResetToken, User]:
    row = (
        await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(token))
        )
    ).scalar_one_or_none()
    if row is None:
        raise PasswordResetError("This reset link is invalid.")
    if row.used_at is not None:
        raise PasswordResetError("This reset link has already been used.")
    if row.expires_at < datetime.utcnow():
        raise PasswordResetError("This reset link has expired — request a new one.")

    user = await db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise PasswordResetError("This reset link is invalid.")
    return row, user


async def verify_reset_token(db: AsyncSession, token: str) -> User:
    """Read-only check used to render the 'set new password' page without
    consuming the token."""
    _row, user = await _get_valid_token_row(db, token)
    return user


async def consume_reset_token(db: AsyncSession, *, token: str, new_password: str) -> User:
    """Returns the user so the web layer can log them straight in (set the
    same session cookies normal login would) instead of sending them back to
    the login page to type the password they just chose."""
    row, user = await _get_valid_token_row(db, token)
    user.password_hash = await hash_password(new_password)
    # Setting a password through a verified emailed link already satisfies
    # "the member has set their own password" -- without this, someone using
    # their account-setup link (see send_account_setup_link) would be forced
    # through force_password_change.html a second time right after logging in.
    user.must_change_password = False
    row.used_at = datetime.utcnow()
    await db.commit()
    return user
