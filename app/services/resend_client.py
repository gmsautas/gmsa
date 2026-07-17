"""Transactional email, sent via Brevo or Gmail SMTP depending on
EMAIL_PROVIDER. Brevo is a REST API (no SMTP), so it's unaffected by hosts
that block outbound SMTP ports. Gmail uses real SMTP and only works from a
network that doesn't block ports 587/465 — see
infrastructure/gmail/local_gmail_import.py for the local-only bulk-send flow
that uses it. Kept as one entry point (`send_email`) so callers don't need to
know which provider is actually configured.

Docs: https://developers.brevo.com/reference/sendtransacemail
"""

import asyncio
import json
import logging
import smtplib
import time
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from pathlib import Path

import httpx

from app.core.config import settings
from app.services import org_settings_cache, secrets_store

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587
# Gmail throttles/flags bursts even under the daily cap — keep a floor between
# individual sends when running a bulk import through
# infrastructure/gmail/local_gmail_import.py.
GMAIL_MIN_SECONDS_BETWEEN_SENDS = 1.2
# Send counters persist here, relative to CWD -- the app (WORKDIR /app in the
# container) and infrastructure/gmail/local_gmail_import.py (meant to be run
# from the repo root) both have CWD == repo root, so this one relative path
# resolves to the same file for both. Re-running later the same day continues
# counting toward each account's daily cap instead of resetting to zero.
GMAIL_STATE_FILE = Path("infrastructure/gmail/.gmail_send_state.json")

_last_gmail_send_at_by_account: dict[str, float] = {}


class ResendError(Exception):
    """Raised when an email fails to send, regardless of which provider was used."""


async def send_email(*, to: list[str], subject: str, html: str) -> dict:
    if not to:
        raise ResendError("No recipients provided")

    provider = (org_settings_cache.get("email_provider") or settings.email_provider).strip().lower()
    if provider == "gmail":
        return await _send_via_gmail(to=to, subject=subject, html=html)
    return await _send_via_brevo(to=to, subject=subject, html=html)


async def _send_via_brevo(*, to: list[str], subject: str, html: str) -> dict:
    api_key = secrets_store.get_secret("brevo_api_key", settings.brevo_api_key)
    if not api_key:
        logger.error("BREVO_API_KEY is not configured")
        raise ResendError("BREVO_API_KEY is not configured")

    sender_name, sender_email = parseaddr(
        org_settings_cache.get("brevo_from_email") or settings.brevo_from_email
    )
    sender = {"email": sender_email}
    if sender_name:
        sender["name"] = sender_name

    payload = {
        "sender": sender,
        "to": [{"email": addr} for addr in to],
        "subject": subject,
        "htmlContent": html,
    }
    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(BREVO_URL, json=payload, headers=headers)
        except httpx.HTTPError as err:
            logger.error("Could not reach Brevo: %s", err)
            raise ResendError(f"Could not reach Brevo: {err}") from err
    try:
        data = response.json()
    except ValueError as err:
        logger.error("Unexpected response from Brevo (status %s)", response.status_code)
        raise ResendError(
            f"Unexpected response from Brevo (status {response.status_code})"
        ) from err
    if not response.is_success:
        logger.error("Brevo send failed (status %s): %s", response.status_code, data)
        raise ResendError(data.get("message", "Failed to send email"))
    return data


def _gmail_accounts() -> list[tuple[str, str]]:
    """Configured (user, app_password) pairs, in priority order. Account 1 is
    used for every send until it hits gmail_daily_cap_per_account, then
    account 2 takes over, then so on through account 5 -- never round-robin,
    so each account's outbound traffic looks like one steady sender for the
    day rather than several accounts each sending a handful, which reads as
    more suspicious to Gmail's abuse detection."""
    candidates = [
        (settings.gmail_smtp_user, settings.gmail_smtp_app_password),
        (settings.gmail_smtp_user_2, settings.gmail_smtp_app_password_2),
        (settings.gmail_smtp_user_3, settings.gmail_smtp_app_password_3),
        (settings.gmail_smtp_user_4, settings.gmail_smtp_app_password_4),
        (settings.gmail_smtp_user_5, settings.gmail_smtp_app_password_5),
    ]
    return [(user, pwd) for user, pwd in candidates if user and pwd]


def _load_gmail_state() -> dict:
    today = date.today().isoformat()
    data: dict = {}
    if GMAIL_STATE_FILE.exists():
        try:
            data = json.loads(GMAIL_STATE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
    if data.get("date") != today:
        data = {"date": today, "counts": {}}
    data.setdefault("counts", {})
    # "disabled" holds accounts whose App Password was rejected mid-run (bad
    # credentials, not a legitimate daily-cap exhaustion) — kept separate from
    # counts so the end-of-run report doesn't lie about how many that account
    # actually sent. Persisted (not just in-memory) so a broken account stays
    # skipped for the rest of the day even across separate script invocations,
    # instead of every retry re-trying it and failing real voters' emails.
    data.setdefault("disabled", [])
    return data


def _save_gmail_state(state: dict) -> None:
    try:
        GMAIL_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError as err:
        logger.warning("Could not persist Gmail send counters: %s", err)


def _pick_gmail_account(
    accounts: list[tuple[str, str]], state: dict, *, skip: set[str]
) -> tuple[str, str] | None:
    """Returns the first configured account (in order) that isn't disabled
    (bad credentials), hasn't hit today's persisted cap, and isn't in `skip`
    (already tried and failed within this same send attempt) -- i.e. drains
    account 1 completely before ever touching account 2. Returns None once no
    eligible account remains."""
    counts = state["counts"]
    disabled = set(state["disabled"])
    for user, pwd in accounts:
        if user in disabled or user in skip:
            continue
        if counts.get(user, 0) < settings.gmail_daily_cap_per_account:
            return user, pwd
    return None


def _send_via_gmail_sync(*, to: list[str], subject: str, html: str) -> None:
    accounts = _gmail_accounts()
    if not accounts:
        raise ResendError("No Gmail accounts configured (GMAIL_SMTP_USER / _2 / _3 / _4 / _5)")

    state = _load_gmail_state()
    tried: set[str] = set()
    last_error: Exception | None = None

    while True:
        picked = _pick_gmail_account(accounts, state, skip=tried)
        if picked is None:
            if last_error is not None:
                raise ResendError(
                    f"Every configured Gmail account failed or is unavailable; last error: {last_error}"
                )
            raise ResendError(
                f"All {len(accounts)} configured Gmail account(s) have reached today's send cap "
                f"({settings.gmail_daily_cap_per_account}/account/day) or are disabled — wait "
                f"until tomorrow (US/Pacific midnight, when Google resets it) or configure another account"
            )
        user, app_password = picked

        last_at = _last_gmail_send_at_by_account.get(user, 0.0)
        elapsed = time.monotonic() - last_at
        if elapsed < GMAIL_MIN_SECONDS_BETWEEN_SENDS:
            time.sleep(GMAIL_MIN_SECONDS_BETWEEN_SENDS - elapsed)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((settings.gmail_from_name, user))
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30) as client:
                client.starttls()
                client.login(user, app_password)
                client.sendmail(user, to, msg.as_string())
        except smtplib.SMTPAuthenticationError as err:
            # Bad App Password / 2FA not enabled -- this account will never
            # work for the rest of the run, so disable it and try the next
            # one instead of failing this (and every future) email.
            logger.error("Gmail account %s rejected its App Password -- disabling it: %s", user, err)
            state["disabled"] = sorted(set(state["disabled"]) | {user})
            _save_gmail_state(state)
            tried.add(user)
            last_error = err
            continue

        _last_gmail_send_at_by_account[user] = time.monotonic()
        state["counts"][user] = state["counts"].get(user, 0) + 1
        _save_gmail_state(state)
        return


def gmail_account_usage() -> dict[str, int]:
    """Today's persisted per-account send counts (see GMAIL_STATE_FILE), for
    reporting from infrastructure/gmail/local_gmail_import.py."""
    return dict(_load_gmail_state()["counts"])


def gmail_disabled_accounts() -> list[str]:
    """Accounts disabled today after a bad-credentials failure. Delete
    infrastructure/gmail/.gmail_send_state.json (or edit out just that
    account) after fixing the App Password to let it be tried again today."""
    return list(_load_gmail_state()["disabled"])


async def _send_via_gmail(*, to: list[str], subject: str, html: str) -> dict:
    if not _gmail_accounts():
        logger.error("No Gmail accounts configured (GMAIL_SMTP_USER / _2 / _3 / _4 / _5)")
        raise ResendError("No Gmail accounts configured (GMAIL_SMTP_USER / _2 / _3 / _4 / _5)")

    try:
        await asyncio.to_thread(_send_via_gmail_sync, to=to, subject=subject, html=html)
    except smtplib.SMTPException as err:
        logger.error("Gmail SMTP send failed: %s", err)
        raise ResendError(f"Gmail SMTP send failed: {err}") from err
    except OSError as err:
        logger.error("Could not reach Gmail SMTP: %s", err)
        raise ResendError(f"Could not reach Gmail SMTP: {err}") from err
    return {"provider": "gmail", "to": to}
