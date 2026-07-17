"""In-process cache of DB-backed operational settings (the `OrgSettings`
singleton row's email/SMS-provider and dues-amount columns), so hot paths
like `resend_client.send_email` / `arkesel.send_sms` and the dues-amount
lookups in `app.services.academic` never need to await a DB round trip or
thread an `AsyncSession` through their many call sites.

Mirrors the pattern in `app.services.secrets_store`'s `_cache`: populated
once at app startup (see `app.main`) and refreshed synchronously whenever
`/admin/settings` saves the Email & SMS or Dues Amounts section, so a change
takes effect on the very next send/lookup with no redeploy.

Every cached field is nullable at the DB layer -- `None` (including "never
loaded because the row doesn't exist yet") means "fall back to the
`app.core.config.Settings` env var default," so a site that never touches
the new settings UI keeps behaving exactly as before.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import OrgSettings

# Only the columns this cache is responsible for -- OrgSettings has other
# (non-cached) fields like `name`/`social` that are read straight from the DB
# on the settings page and don't need a hot-path cache.
_FIELDS = (
    "email_provider",
    "brevo_from_email",
    "arkesel_sender_id",
    "dues_amount_level_100",
    "dues_amount_continuing",
    "dues_amount_final_year",
)

_cache: dict[str, object] = {}


async def load_cache(db: AsyncSession) -> None:
    """(Re)populate the in-memory cache from the `org_settings` row. Call once
    at app startup, and again after any admin save that touches one of the
    fields above. Never raises -- if the singleton row doesn't exist yet
    (fresh DB, no admin has ever saved `/admin/settings`), the cache is just
    cleared and every `get()` falls back to the env var default.
    """
    result = await db.execute(select(OrgSettings).where(OrgSettings.id == 1))
    org = result.scalar_one_or_none()
    if org is None:
        _cache.clear()
        return
    for field in _FIELDS:
        _cache[field] = getattr(org, field)


def get(field: str):
    """The DB-backed value for `field`, or None if it's unset (or the cache
    hasn't been loaded yet) -- callers are expected to `or` this with their
    own env var default, same convention as `secrets_store.get_secret`.
    """
    return _cache.get(field)
