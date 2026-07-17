# GMSA UTAS Backend

FastAPI + PostgreSQL application for the GMSA UTAS website — a server-rendered
public site, member portal, and admin/superadmin dashboard (Jinja2 + Tailwind
+ Alpine.js), plus a JSON API under `/api` for programmatic access.

## Stack

- Python 3.11, FastAPI, Jinja2 (server-rendered pages)
- SQLAlchemy 2.0 (async) + asyncpg, PostgreSQL 16
- Alembic migrations
- Pydantic v2 schemas (JSON API)
- JWT auth (access + refresh tokens, cookie-based for the web app), role-based
  access (`member`, `admin`, `superadmin`)
- Paystack (payments), Arkesel (SMS), Resend (email)

## Running locally

### Option A — Docker Compose (recommended)

```bash
cp .env.example .env   # fill in real secrets as needed; safe to leave optional ones blank
docker compose up --build
```

This starts a PostgreSQL 16 container, runs `alembic upgrade head`, and
serves the app at `http://localhost:8000`. Interactive API docs (JSON API
only) are at `http://localhost:8000/docs`.

### Option B — Local virtualenv

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
cp .env.example .env

# Make sure PostgreSQL is running locally and the database in DATABASE_URL
# exists, then:
alembic upgrade head
uvicorn app.main:app --reload
```

### First-time setup: create a superadmin

A fresh database has zero users. To get into the admin dashboard:

1. Start the app and register a normal account at `/register`.
2. Promote it to superadmin:
   ```bash
   PYTHONPATH=. python scripts/promote_superadmin.py you@example.com
   ```
3. Log in at `/admin/login`.

### Demo data (local development only)

```bash
PYTHONPATH=. python scripts/seed.py
```

Wipes and repopulates every application table with a consistent set of demo
data (org settings, ~14 members/admins, projects, blog posts, events,
leadership boards, resources, dues, transactions, etc). All seeded users
share the password `password123`.

**Never run this against a production database** — it deletes existing rows
before reinserting demo data.

## Environment variables

See `.env.example` for the full annotated list. The app boots fine with only
`DATABASE_URL` and `SECRET_KEY` set — every third-party integration degrades
gracefully when its key is missing (e.g. payment init returns a clear error,
email/SMS sends are recorded as failed rather than crashing the request).

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes | Async SQLAlchemy URL. A plain `postgres://`/`postgresql://` from a managed host (Render, Railway, Neon) is automatically rewritten to use the `asyncpg` driver and the `ssl=` param it expects. |
| `SECRET_KEY` | Yes | JWT signing key. Set a long random value in production. |
| `ENVIRONMENT` | No | `development` (default) or `production`. |
| `CORS_ORIGINS` | No | Comma-separated allowed origins. |
| `PAYSTACK_SECRET_KEY` / `PAYSTACK_PUBLIC_KEY` / `PAYSTACK_CALLBACK_URL` | No | Dues/donation/project payments. |
| `ARKESEL_API_KEY` / `ARKESEL_SENDER_ID` | No | SMS campaigns. |
| `EMAIL_PROVIDER` | No | `resend` (default), `brevo`, or `ses` — picks which one actually sends. All three are REST APIs (no SMTP), so none are affected by hosts that block outbound SMTP ports. Also settable at runtime, without a redeploy, via **Admin → Settings → Email & SMS**. |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | No | Transactional email (welcome/reset/voter-token emails, campaigns) when `EMAIL_PROVIDER=resend`. `RESEND_FROM_EMAIL` must be on a domain verified in the Resend dashboard. Both also settable via the superadmin dashboard instead of an env var (see below). |
| `BREVO_API_KEY` / `BREVO_FROM_EMAIL` | No | Same, when `EMAIL_PROVIDER=brevo`. Also settable via the superadmin dashboard. |
| `SES_ACCESS_KEY_ID` / `SES_SECRET_ACCESS_KEY` / `SES_FROM_EMAIL` / `SES_REGION` | No | Same, when `EMAIL_PROVIDER=ses`. Requires the AWS account to have SES production access (not sandboxed) and the sending domain verified in SES. Keys/from-address also settable via the superadmin dashboard. |
| `SECRETS_ENCRYPTION_KEY` | No | Enables **Admin → API Keys**, where a superadmin can set/rotate the active email provider's API key through the dashboard instead of redeploying — stored encrypted in the database. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Deliberately separate from `SECRET_KEY`. |
| `DUES_AMOUNT_GHS` | No | Fallback per-semester dues amount. |
| `DUES_AMOUNT_LEVEL_100` / `DUES_AMOUNT_CONTINUING` / `DUES_AMOUNT_FINAL_YEAR` | No | Tiered per-semester dues by academic level (see below). |

## Academic levels & tiered dues

Every member registers with a **student ID** and a **program category**
(`diploma`, `degree`, `postgraduate`, `masters` or `phd`). The first four
digits of the student ID are taken as the admission year (e.g. `2022` in
`20220404172`).

From these (`app/services/academic.py`) the backend derives current level
(`100`/`200`/`300`/...), expected graduation year, and dues tier
(`level_100` / `continuing` / `final_year`) — each with its own per-semester
amount. An admin can override a specific member's level/graduation year from
their member detail page for cases the formula can't handle on its own (e.g.
a diploma graduate continuing into a degree program at level 200, or a
rusticated student) — the override always wins over the computed value.

## Database migrations

The schema currently lives in a **single** Alembic migration
(`alembic/versions/`) — there's no accumulated history to reason about yet.

```bash
# After changing app/models/models.py:
alembic revision --autogenerate -m "describe the change"

# Apply migrations:
alembic upgrade head
```

## Deploying

### Render

`render.yaml` at the repo root is a ready-to-use Blueprint: Docker runtime,
builds from the repo-root `Dockerfile`. To deploy:

1. In the Render dashboard, **New → Blueprint**, point it at this repo.
2. Render provisions the web service from `render.yaml`. Set the
   `sync: false` env vars in the dashboard once (`DATABASE_URL` and any
   optional integrations you want live) — they won't be overwritten by
   future blueprint syncs.
3. **Database**: Render's free Postgres expires after 30 days, so
   `DATABASE_URL` is left for you to point at an external Postgres — a
   [Neon](https://neon.tech) free-tier database works well and has no forced
   expiry. Paste its connection string in as `DATABASE_URL`; the
   `postgres://`→`asyncpg` and `sslmode`→`ssl` rewriting happens
   automatically (see `app/core/config.py`).
4. Deploy runs `sh start.sh` (`alembic upgrade head` then `uvicorn`) —
   migrations apply automatically on every deploy.
5. Once it's live, bootstrap your first superadmin from the **Shell** tab on
   the Render service:
   ```bash
   python scripts/promote_superadmin.py you@example.com
   ```
   (register the account on the live site first, same as local setup above).

### Railway

`railway.json` at the repo root is a Railway config-as-code file (the
Railway equivalent of `render.yaml`): Dockerfile build pointed at the
repo-root `Dockerfile`, a `/` healthcheck, and an on-failure restart policy.
Railway builds directly from the Dockerfile, and `start.sh` already reads
Railway's injected `$PORT`.

1. **New Project → Deploy from GitHub repo**, select this repo. Railway will
   detect `railway.json`/the Dockerfile automatically and build from it — no
   Root Directory override needed, everything lives at the repo root.
2. **Add a database**: `+ New → Database → PostgreSQL` in the same project.
   Railway exposes it as `DATABASE_URL` — reference it in your web service's
   variables as `${{Postgres.DATABASE_URL}}` (Railway's variable-reference
   syntax), or paste the connection string directly. The same
   `postgres://`→`asyncpg` rewriting in `app/core/config.py` handles
   Railway's URL format automatically.
3. In the web service's **Variables**, set at minimum `DATABASE_URL` and
   `SECRET_KEY`, plus any optional integrations you want live (see the table
   above, and `infrastructure/railway/RAILWAY_DEPLOYMENT_NOTES.md` for the full list
   mirroring `render.yaml`'s).
4. **Before going live, attach a persistent Volume mounted at
   `/app/static/uploads`.** Without one, uploaded profile/candidate photos
   and payment-proof screenshots are lost on every redeploy — container
   storage is ephemeral on Railway (same issue exists on Render today, unfixed).
   See `infrastructure/railway/RAILWAY_DEPLOYMENT_NOTES.md` for details; this is a
   stopgap, not the eventual real fix (object storage), but it's cheap and
   should happen at cutover rather than after data is already lost.
5. Deploy. `start.sh` runs migrations on every deploy automatically.
6. Bootstrap your first superadmin via Railway's **Shell** (service →
   three-dot menu → **Shell**, or `railway run` from the CLI):
   ```bash
   python scripts/promote_superadmin.py you@example.com
   ```

## Project layout

- `app/web/` — server-rendered public site, member portal, and admin
  dashboard (the actual application most users interact with).
- `app/api/v1/routes/` — JSON API under `/api`, for programmatic access
  (mobile app, integrations, etc). Not required for the web app to function.
- `app/services/` — business logic shared between both layers (elections,
  academic level calculation, member provisioning, storage, email).
- `app/models/models.py` — the single source of truth for the DB schema.
- `templates/` / `static/` — Jinja2 templates and assets for the web app.

## JSON API overview

All routes below are prefixed with `/api`. Run the server and visit `/docs`
for full request/response schemas.

| Area | Routes |
| --- | --- |
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me` |
| Organisation | `GET/PATCH /org`, `GET /org/stats`, `POST /contact` |
| Projects | `GET /projects`, `GET /projects/{slug}`, admin CRUD |
| Blog | `GET /blog`, `GET /blog/{slug}`, `GET /blog/admin/all`, admin CRUD |
| Leadership | `GET /leadership`, admin board/member CRUD |
| Announcements | `GET /announcements` (auth required), admin CRUD |
| Resources | `GET /resources` (auth required), admin CRUD |
| Events | `GET /events`, `GET /events/{id}`, RSVP endpoints, `GET /me/rsvps`, admin CRUD |
| Dues | `GET /me/dues`, `GET /admin/dues`, `POST /admin/dues/generate` |
| Members | `GET/PATCH /admin/members`, `GET /admin/members/{id}`, `POST /admin/members/bulk` |
| Finance | `GET /admin/transactions`, `GET/POST /admin/expenses`, `GET /admin/finance/summary` |
| Payments | `POST /payments/initialize`, `GET /payments/verify/{reference}`, `POST /payments/webhook` |
| Communications | `GET/POST /admin/sms-campaigns`, `GET/POST /admin/email-campaigns` |

Members, elections, page content (home/About page copy), and prayer times
are managed through the admin web dashboard rather than the JSON API — see
`app/web/admin_web.py`, `app/web/elections_web.py`, and
`app/web/secrets_web.py`.
