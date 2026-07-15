from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps_web import Forbidden, PageRedirect, require_superadmin
from app.core.templates import templates
from app.services import secrets_store

router = APIRouter()

# key_name -> (display label, help text)
MANAGED_KEYS = {
    "brevo_api_key": (
        "Brevo API Key",
        "Used to send transactional email (welcome messages, password resets, voter tokens, comms "
        "campaigns) when the active email provider is \"brevo\" (this page only manages the key, not "
        "which provider is active — see the Email & SMS section of /admin/settings).",
    ),
    "arkesel_api_key": (
        "Arkesel API Key",
        "Used to send SMS campaigns and voter/dues reminders via Arkesel. The sender ID is managed on the "
        "Email & SMS section of /admin/settings, not here.",
    ),
}


@router.get("/api-keys")
async def api_keys_page(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_superadmin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)
    except Forbidden:
        return RedirectResponse("/admin/dashboard", status_code=303)

    keys = []
    for key_name, (label, help_text) in MANAGED_KEYS.items():
        masked = await secrets_store.get_masked(db, key_name)
        keys.append({"key_name": key_name, "label": label, "help_text": help_text, "masked": masked})

    return templates.TemplateResponse(
        request=request,
        name="admin/api_keys.html",
        context={
            "admin": admin,
            "active_nav": "api-keys",
            "keys": keys,
            "encryption_configured": secrets_store.is_configured(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/api-keys")
async def api_keys_update(
    request: Request,
    key_name: str = Form(...),
    value: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_superadmin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)
    except Forbidden as e:
        return RedirectResponse(f"/admin/api-keys?error={quote(e.message)}", status_code=303)

    if key_name not in MANAGED_KEYS:
        return RedirectResponse(f"/admin/api-keys?error={quote('Unknown key')}", status_code=303)
    if not value.strip():
        return RedirectResponse(f"/admin/api-keys?error={quote('Value cannot be empty')}", status_code=303)

    try:
        await secrets_store.set_secret(db, key_name, value.strip(), admin)
    except secrets_store.SecretsError as err:
        return RedirectResponse(f"/admin/api-keys?error={quote(str(err))}", status_code=303)
    return RedirectResponse("/admin/api-keys?saved=1", status_code=303)
