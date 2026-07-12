"""DB-backed, encrypted storage for API keys managed via the superadmin
dashboard (see app.web.secrets_web), with an env-var fallback so the app still
boots and sends mail even if nothing has been configured in the DB yet.

Values are cached in-process after being read/written so hot paths (e.g.
sending an email) never need to await a DB round trip or thread an
AsyncSession through call sites that don't otherwise need one. This assumes a
single app instance/worker process — if this app is ever run with multiple
worker processes, a key rotated via the dashboard won't be picked up by other
workers until they restart.
"""

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import AppSecret, User

_cache: dict[str, str] = {}


class SecretsError(Exception):
    """Raised for a missing or malformed SECRETS_ENCRYPTION_KEY — always a
    server misconfiguration, never something a request's input caused."""


def _fernet() -> Fernet | None:
    key = settings.secrets_encryption_key.strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as err:
        raise SecretsError(
            "SECRETS_ENCRYPTION_KEY is set but isn't a valid Fernet key — generate one with "
            '`python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and make sure it\'s copied in full, '
            "with no extra whitespace or line breaks."
        ) from err


def is_configured() -> bool:
    """True only if SECRETS_ENCRYPTION_KEY is set AND well-formed — used to
    decide whether the API Keys page's save form should be enabled."""
    try:
        return _fernet() is not None
    except SecretsError:
        return False


def encrypt(plaintext: str) -> str:
    fernet = _fernet()
    if fernet is None:
        raise SecretsError("SECRETS_ENCRYPTION_KEY is not configured")
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    fernet = _fernet()
    if fernet is None:
        raise SecretsError("SECRETS_ENCRYPTION_KEY is not configured")
    return fernet.decrypt(ciphertext.encode()).decode()


def _tail(plaintext: str) -> str:
    return plaintext[-4:] if len(plaintext) >= 4 else plaintext


def mask(tail: str) -> str:
    return f"{'•' * 8}{tail}"


async def load_cache(db: AsyncSession) -> None:
    """Populate the in-memory cache from the DB. Call once at app startup.

    Never raises — a missing/malformed key or a row encrypted under a
    different key than what's configured now (e.g. after rotation) just means
    DB-backed secrets are skipped for this run; callers fall back to their env
    var. The app must still boot either way.
    """
    try:
        if _fernet() is None:
            return
        result = await db.execute(select(AppSecret))
        for row in result.scalars().all():
            try:
                _cache[row.key_name] = decrypt(row.encrypted_value)
            except InvalidToken:
                continue
    except SecretsError:
        return


async def set_secret(db: AsyncSession, key_name: str, plaintext: str, admin: User) -> None:
    encrypted = encrypt(plaintext)
    result = await db.execute(select(AppSecret).where(AppSecret.key_name == key_name))
    row = result.scalar_one_or_none()
    if row is None:
        row = AppSecret(key_name=key_name)
        db.add(row)
    row.encrypted_value = encrypted
    row.last4 = _tail(plaintext)
    row.updated_by_id = admin.id
    await db.commit()
    _cache[key_name] = plaintext


def get_secret(key_name: str, env_fallback: str = "") -> str:
    return _cache.get(key_name) or env_fallback


async def get_masked(db: AsyncSession, key_name: str) -> str | None:
    result = await db.execute(select(AppSecret).where(AppSecret.key_name == key_name))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return mask(row.last4)
