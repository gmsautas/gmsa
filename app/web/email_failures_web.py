from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps_web import Forbidden, PageRedirect, require_superadmin
from app.core.templates import templates
from app.models.models import EmailSendFailure

router = APIRouter()

# How many recent rows to show -- this is a lightweight triage panel, not a
# full audit log (see app.services.email_failures), so no pagination yet.
RECENT_LIMIT = 200


@router.get("/email-failures")
async def email_failures_page(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_superadmin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)
    except Forbidden:
        return RedirectResponse("/admin/dashboard", status_code=303)

    failures = (
        (
            await db.execute(
                select(EmailSendFailure)
                .order_by(EmailSendFailure.created_at.desc())
                .limit(RECENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/email_failures.html",
        context={
            "admin": admin,
            "active_nav": "email-failures",
            "failures": failures,
        },
    )
