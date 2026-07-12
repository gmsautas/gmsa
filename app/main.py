from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.deps_web import PageRedirect
from app.services import org_settings_cache, secrets_store

app = FastAPI(title="GMSA UTAS", version="0.1.0")


@app.on_event("startup")
async def _load_app_secrets() -> None:
    async with AsyncSessionLocal() as db:
        await secrets_store.load_cache(db)
        await org_settings_cache.load_cache(db)


BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.exception_handler(PageRedirect)
async def page_redirect_handler(request: Request, exc: PageRedirect):
    return RedirectResponse(url=exc.url, status_code=exc.status_code)


from app.web import public as web_public
from app.web import auth_web
from app.web import member as web_member
from app.web import admin_web
from app.web import elections_web
from app.web import secrets_web
from app.web import email_failures_web

app.include_router(web_public.router)
app.include_router(auth_web.router)
app.include_router(web_member.router, prefix="/member")
app.include_router(admin_web.router, prefix="/admin")
app.include_router(elections_web.admin_router, prefix="/admin/elections")
app.include_router(elections_web.member_router, prefix="/member/elections")
app.include_router(secrets_web.router, prefix="/admin")
app.include_router(email_failures_web.router, prefix="/admin")


app.include_router(api_router, prefix="/api")


@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
