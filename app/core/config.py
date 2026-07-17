from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
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

    # Connection pool sizing (app.core.database). This is a small org's app --
    # a few hundred members, occasional concurrent admin/member usage on a
    # single web service instance -- so these are chosen deliberately rather
    # than left at SQLAlchemy's QueuePool defaults (5 + 10 = 15), which exist
    # for a generic workload, not this one. pool_size=10 comfortably covers
    # normal request concurrency plus a couple of admin operations (e.g. bulk
    # member imports) running at once; max_overflow=5 gives a small burst
    # buffer for traffic spikes. The 15-connection ceiling this implies also
    # matters because Postgres itself caps max_connections -- often fairly low
    # on hobby/free tiers (roughly 20-100 depending on provider) -- and this
    # app is only one of possibly several things sharing that budget. Revisit
    # upward if the org's membership/usage grows enough to saturate this.
    db_pool_size: int = 10
    db_max_overflow: int = 5

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

        # Those same hosts also hand back libpq-style query params asyncpg's
        # connect() doesn't accept as keyword arguments -- as-is, these crash
        # with "connect() got an unexpected keyword argument '<name>'" the
        # moment anything tries to open a connection (this has now happened
        # in production for both params below, not just hypothetically).
        parts = urlsplit(value)
        query = dict(parse_qsl(parts.query))
        changed = False
        if "sslmode" in query:
            # asyncpg's equivalent is spelled ?ssl=<value> instead.
            query["ssl"] = query.pop("sslmode")
            changed = True
        if "channel_binding" in query:
            # Neon's dashboard connection string includes ?channel_binding=require
            # for SCRAM channel binding -- asyncpg negotiates that automatically
            # over an SSL connection and has no parameter for it at all, so this
            # one is just dropped rather than renamed.
            query.pop("channel_binding")
            changed = True
        if changed:
            value = urlunsplit(parts._replace(query=urlencode(query)))

        return value

    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_callback_url: str = "http://localhost:8123/member/dues.html"

    arkesel_api_key: str = ""
    arkesel_sender_id: str = "GMSA-UTAS"

    # EMAIL_PROVIDER selects which backend app.services.resend_client.send_email
    # dispatches to: "brevo" (default) or "gmail".
    # Brevo is a REST API (no SMTP), so it's unaffected by hosts that block
    # outbound SMTP ports -- it's the provider the live hosted app actually
    # uses. "gmail" uses real SMTP and only works from a network that doesn't
    # block ports 587/465 — i.e. NOT from a hosted deployment, only from a
    # local machine — see infrastructure/gmail/local_gmail_import.py.
    # NOTE: this env var is only the fallback default now — the active value
    # can be overridden at runtime via /admin/settings' Email & SMS section
    # (see app.services.org_settings_cache), no redeploy required.
    email_provider: str = "brevo"

    brevo_api_key: str = ""
    brevo_from_email: str = "GMSA UTAS <no-reply@gmsautas.org>"

    # Gmail SMTP — local-only bulk-send path (see infrastructure/gmail/local_gmail_import.py).
    # Up to 5 accounts can be configured (account 1 is required, 2-5 are
    # optional); resend_client drains account 1 completely (up to
    # gmail_daily_cap_per_account) before ever touching account 2, then so on
    # through account 5 -- never round-robin -- to raise the effective daily
    # ceiling beyond one account's limit. Each *_user must be the full
    # @gmail.com address; each *_app_password is a 16-char App Password
    # (Google Account > Security > App Passwords), not the account's real
    # login password.
    gmail_smtp_user: str = ""
    gmail_smtp_app_password: str = ""
    gmail_smtp_user_2: str = ""
    gmail_smtp_app_password_2: str = ""
    gmail_smtp_user_3: str = ""
    gmail_smtp_app_password_3: str = ""
    gmail_smtp_user_4: str = ""
    gmail_smtp_app_password_4: str = ""
    gmail_smtp_user_5: str = ""
    gmail_smtp_app_password_5: str = ""
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

    # Tiered annual dues amounts, by academic level. No separate flat/default
    # amount -- a member whose tier can't be resolved is billed at the
    # Continuing rate (see app.services.academic.effective_dues_amount).
    dues_amount_level_100: int = 100
    dues_amount_continuing: int = 150
    dues_amount_final_year: int = 200

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _reject_default_secret_outside_dev(self) -> "Settings":
        # secret_key signs every JWT access/refresh token (app.core.security) and
        # HMACs every password-reset and voter token (app.services.password_reset,
        # app.services.elections). The literal default below is public (it's in
        # this file, in source control) — booting a non-development environment
        # with it still in effect means anyone can forge those tokens. Render's
        # render.yaml auto-generates a real value and Railway's docs say to set
        # one manually, but neither guarantees the env var actually reaches the
        # container (dropped on redeploy, a new service, a bare `docker run`) --
        # so refuse to start rather than silently sign tokens with a known key.
        if self.environment != "development" and self.secret_key == "dev-secret-key-change-me":
            raise ValueError(
                "SECRET_KEY is still the default 'dev-secret-key-change-me' while "
                f"ENVIRONMENT={self.environment!r} (!= 'development'). Set a real "
                "SECRET_KEY env var before starting the app outside development -- "
                "generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
