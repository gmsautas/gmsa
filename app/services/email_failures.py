"""Tiny observability sink for outbound email failures.

Every call site that sends email already has to catch
resend_client.ResendError -- either to preserve anti-enumeration behavior
(password_reset.request_reset must return normally even if the send fails,
so it can't reveal whether an account exists) or to keep a best-effort/
bulk-import flow from aborting on one bad address. Historically those catches
just swallowed the exception with no trace left anywhere, which is exactly
how the org's real "forgot password doesn't work" incident went undiagnosed
for a while (see app.core.config's resend_from_email/brevo_from_email fix in
the same change as this module).

record_failure logs the exception AND persists a row so it's never silently
invisible server-side again, while leaving each caller free to keep its own
user-facing behavior (swallow vs. re-raise) unchanged. See the superadmin-only
"Email Failures" panel (app.web.email_failures_web) for where these surface.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import EmailSendFailure

logger = logging.getLogger(__name__)


async def record_failure(db: AsyncSession, *, recipient: str, purpose: str, error: Exception) -> None:
    """Log and persist one failed email send. Always commits -- every call
    site catches ResendError only after its own preceding DB work is already
    durable (see each call site's comments), so this commit is never at risk
    of prematurely persisting an unrelated half-finished change."""
    provider = settings.email_provider.strip().lower()
    logger.error(
        "Email send failed (provider=%s, purpose=%s, recipient=%s): %s",
        provider,
        purpose,
        recipient,
        error,
    )
    db.add(
        EmailSendFailure(
            provider=provider,
            recipient=recipient,
            purpose=purpose,
            error=str(error)[:2000],
        )
    )
    await db.commit()
