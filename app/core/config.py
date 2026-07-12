from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    secret_key: str = "dev-secret-key-change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    password_reset_token_expire_minutes: int = 60
    algorithm: str = "HS256"

    cors_origins: str = "http://localhost:8123,http://127.0.0.1:8123"

    database_url: str = "postgresql+asyncpg://gmsa:gmsa@localhost:5432/gmsa_utas"

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, value: str) -> str:
        # Managed Postgres hosts (Render, Railway, Neon, Heroku-style, etc.) hand
        # back a plain postgres(ql):// URL — the async engine needs the +asyncpg
        # driver explicitly, so rewrite it rather than requiring every host's
        # dashboard value to be hand-edited.
        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://") :]

        # Those same hosts also hand back a libpq-style ?sslmode=require query
        # param, but asyncpg's connect() only understands ?ssl=<value> — as-is,
        # this crashes with "connect() got an unexpected keyword argument
        # 'sslmode'" the moment anything tries to open a connection.
        parts = urlsplit(value)
        query = dict(parse_qsl(parts.query))
        if "sslmode" in query:
            query["ssl"] = query.pop("sslmode")
            value = urlunsplit(parts._replace(query=urlencode(query)))

        return value

    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_callback_url: str = "http://localhost:8123/member/dues.html"

    arkesel_api_key: str = ""
    arkesel_sender_id: str = "GMSA-UTAS"

    # EMAIL_PROVIDER selects which backend app.services.resend_client.send_email
    # dispatches to: "resend" (default), "brevo", "ses", or "gmail".
    # Resend/Brevo/SES are all REST-API-based (no SMTP), so none of them are
    # affected by hosts that block outbound SMTP ports (Render, etc.). "gmail"
    # uses real SMTP and only works from a network that doesn't block ports
    # 587/465 — i.e. NOT from Render/Railway, only from a local machine — see
    # scripts/local_gmail_import.py.
    # NOTE: this env var is only the fallback default now — the active value
    # can be overridden at runtime via /admin/settings' Email & SMS section
    # (see app.services.org_settings_cache), no redeploy required.
    email_provider: str = "resend"

    resend_api_key: str = ""
    resend_from_email: str = "GMSA UTAS <no-reply@gmsautas.org>"

    brevo_api_key: str = ""
    brevo_from_email: str = "GMSA UTAS <no-reply@gmsautas.org>"

    # Amazon SES — sent via SES's HTTPS API (boto3), not its SMTP interface,
    # so it's unaffected by SMTP port blocks on any host. ses_from_email must
    # be on a domain/subdomain verified in the SES console, and the account
    # must have production access (not sandboxed) to send to unverified
    # recipients — see AWS Console > SES > Account dashboard.
    ses_region: str = "us-east-1"
    ses_access_key_id: str = ""
    ses_secret_access_key: str = ""
    ses_from_email: str = "GMSA UTAS <no-reply@gmsautas.org>"

    # Gmail SMTP — local-only bulk-send fallback (see scripts/local_gmail_import.py).
    # Up to 3 accounts can be configured (account 1 is required, 2 and 3 are
    # optional); resend_client drains account 1 completely (up to
    # gmail_daily_cap_per_account) before ever touching account 2, then
    # account 3 -- never round-robin -- to raise the effective daily ceiling
    # beyond one account's limit. Each *_user must be the full @gmail.com
    # address; each *_app_password is a 16-char App Password (Google Account
    # > Security > App Passwords), not the account's real login password.
    gmail_smtp_user: str = ""
    gmail_smtp_app_password: str = ""
    gmail_smtp_user_2: str = ""
    gmail_smtp_app_password_2: str = ""
    gmail_smtp_user_3: str = ""
    gmail_smtp_app_password_3: str = ""
    gmail_from_name: str = "GMSA UTAS"
    # Conservative per-account daily send ceiling — kept comfortably under
    # Google's own consumer-account limit to avoid tripping abuse detection.
    gmail_daily_cap_per_account: int = 455

    # Fernet key encrypting API keys stored in the app_secrets table (superadmin
    # dashboard). Deliberately separate from secret_key, which is already used
    # for JWTs and HMAC token hashing — rotating one must not invalidate the
    # other. Generate with: python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())". Leave unset to disable DB-backed
    # secrets (env vars still work as a fallback).
    secrets_encryption_key: str = ""

    prayer_default_city: str = "Accra"
    prayer_default_country: str = "Ghana"

    dues_amount_ghs: int = 150

    # Tiered per-semester dues amounts, by academic level.
    dues_amount_level_100: int = 100
    dues_amount_continuing: int = 150
    dues_amount_final_year: int = 200

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
