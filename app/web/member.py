import io
import secrets
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.deps_web import require_member
from app.core.security import hash_password, verify_password
from app.core.templates import templates
from app.models.models import Announcement, DuesRecord, Event, OrgSettings, Resource, Rsvp, Transaction
from app.services import academic, storage

router = APIRouter(tags=["web-member"])


async def _get_org(db: AsyncSession) -> OrgSettings | None:
    return await db.get(OrgSettings, 1)


# ── Dashboard ─────────────────────────────────────────────────────────────────


@router.get("/dashboard", name="member_dashboard")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    # Dues status
    dues_result = await db.execute(
        select(DuesRecord)
        .where(DuesRecord.user_id == user.id)
        .order_by(DuesRecord.due_date.desc())
    )
    dues = dues_result.scalars().all()
    current_due = dues[0] if dues else None

    # Events the user has RSVPed to
    rsvps_result = await db.execute(
        select(Event)
        .join(Rsvp, Rsvp.event_id == Event.id)
        .where(Rsvp.user_id == user.id)
        .order_by(Event.date)
    )
    rsvped_events = rsvps_result.scalars().all()

    # Recent announcements
    ann_result = await db.execute(
        select(Announcement).order_by(Announcement.date.desc()).limit(5)
    )
    announcements = ann_result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="member/dashboard.html",
        context={
            "user": user,
            "active_nav": "dashboard",
            "current_due": current_due,
            "rsvped_events": rsvped_events,
            "announcements": announcements,
        },
    )


# ── Dues ─────────────────────────────────────────────────────────────────────


@router.get("/dues", name="member_dues")
async def dues_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    dues_result = await db.execute(
        select(DuesRecord)
        .where(DuesRecord.user_id == user.id)
        .order_by(DuesRecord.due_date.desc())
    )
    dues_list = dues_result.scalars().all()
    org = await _get_org(db)

    flash = None
    if request.query_params.get("paid"):
        flash = "Payment initiated — complete payment via Paystack."
    elif request.query_params.get("momo_submitted"):
        flash = "Payment proof submitted — awaiting verification by an admin."

    return templates.TemplateResponse(
        request=request,
        name="member/dues.html",
        context={
            "user": user,
            "active_nav": "dues",
            "dues_list": dues_list,
            "org": org,
            "flash": flash,
            "error": request.query_params.get("momo_error"),
        },
    )


@router.post("/dues/{due_id}/pay-init", name="member_dues_pay_init")
async def dues_pay_init(
    due_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db)

    due = await db.get(DuesRecord, due_id)
    if not due or due.user_id != user.id:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if due.status == "paid":
        return JSONResponse({"error": "Already paid"})

    reference = f"dues-{due.id}-{secrets.token_hex(6)}"
    amount_kobo = int(float(due.amount) * 100)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.paystack.co/transaction/initialize",
            headers={"Authorization": f"Bearer {settings.paystack_secret_key}"},
            json={
                "email": user.email,
                "amount": amount_kobo,
                "reference": reference,
                "callback_url": str(request.base_url) + "member/dues",
                "metadata": {
                    "type": "dues",
                    "user_id": user.id,
                    "due_id": due.id,
                },
            },
        )

    data = resp.json()
    if data.get("status"):
        return JSONResponse({"authorization_url": data["data"]["authorization_url"]})
    return JSONResponse({"error": "Payment initialization failed"})


@router.post("/dues/{due_id}/pay-momo", name="member_dues_pay_momo")
async def dues_pay_momo(
    due_id: int,
    request: Request,
    proof: UploadFile,
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db)

    due = await db.get(DuesRecord, due_id)
    if not due or due.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if due.status == "paid":
        return RedirectResponse("/member/dues", status_code=303)

    try:
        proof_url, _size = await storage.save_upload(
            proof,
            "payments",
            allowed_exts=storage.PROOF_EXTS,
            # Admins open this full-size to read a transaction reference/
            # amount (admin/finance/momo.html) rather than viewing it as a
            # thumbnail, so keep more resolution than the avatar default.
            max_dimension=storage.MAX_PROOF_IMAGE_DIMENSION,
        )
    except storage.UploadError as err:
        return RedirectResponse(
            f"/member/dues?momo_error={quote(str(err))}", status_code=303
        )

    reference = f"momo-{due.id}-{secrets.token_hex(6)}"
    description = f"{due.semester} dues — Mobile Money" + (f" ({note.strip()})" if note.strip() else "")
    tx = Transaction(
        user_id=user.id,
        donor_name=user.name,
        type="dues",
        description=description,
        amount=due.amount,
        currency=due.currency,
        method="momo_manual",
        reference=reference,
        status="pending",
        proof_url=proof_url,
    )
    db.add(tx)
    await db.flush()

    due.status = "pending"
    due.transaction_id = tx.id
    await db.commit()

    return RedirectResponse("/member/dues?momo_submitted=1", status_code=303)


@router.get("/dues/{due_id}/receipt", name="member_dues_receipt")
async def dues_receipt(due_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    due = await db.get(DuesRecord, due_id)
    if not due or due.user_id != user.id or due.status != "paid":
        raise HTTPException(status_code=404, detail="Receipt not available")

    org = await _get_org(db)
    transaction = await db.get(Transaction, due.transaction_id) if due.transaction_id else None

    pdf_bytes = _render_receipt_pdf(org, user, due, transaction)
    filename = f"GMSA-Receipt-{due.semester.replace(' ', '-')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render_receipt_pdf(org, user, due, transaction) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, _height = A4

    org_name = org.full_name if org else "GMSA UTAS"
    top = 270 * mm

    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, top, org_name)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, top - 7 * mm, "Official Dues Payment Receipt")

    c.line(20 * mm, top - 12 * mm, width - 20 * mm, top - 12 * mm)

    rows = [
        ("Member Name", user.name),
        ("Email", user.email),
        ("Semester", due.semester),
        ("Amount Paid", f"{due.currency} {float(due.amount):,.2f}"),
        ("Date Paid", due.paid_at.strftime("%d %B %Y") if due.paid_at else "—"),
        ("Reference", transaction.reference if transaction else "—"),
        ("Payment Method", (transaction.method if transaction and transaction.method else "—")),
        ("Status", "Paid"),
    ]

    y = top - 25 * mm
    c.setFont("Helvetica", 11)
    for label, value in rows:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, f"{label}:")
        c.setFont("Helvetica", 11)
        c.drawString(70 * mm, y, str(value))
        y -= 8 * mm

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(20 * mm, 20 * mm, "Thank you for supporting GMSA UTAS. This receipt was generated automatically.")

    c.showPage()
    c.save()
    return buffer.getvalue()


# ── Profile ───────────────────────────────────────────────────────────────────


@router.get("/profile", name="member_profile")
async def profile_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    pwd_error = request.query_params.get("pwd_error")
    pwd_ok = request.query_params.get("pwd_ok")
    profile_ok = request.query_params.get("profile_ok")
    photo_ok = request.query_params.get("photo_ok")
    photo_error = request.query_params.get("photo_error")

    level = academic.effective_level(user)
    grad_year = academic.effective_grad_year(user)

    flash = None
    if pwd_ok:
        flash = "Password updated successfully."
    elif pwd_error == "1":
        flash = "Current password is incorrect."
    elif pwd_error == "2":
        flash = "New passwords do not match."
    elif profile_ok:
        flash = "Profile updated successfully."

    return templates.TemplateResponse(
        request=request,
        name="member/profile.html",
        context={
            "user": user,
            "active_nav": "profile",
            "level": level,
            "grad_year": grad_year,
            "flash": flash,
            "pwd_error": pwd_error,
            "pwd_ok": pwd_ok,
            "profile_ok": profile_ok,
            "photo_ok": photo_ok,
            "photo_error": photo_error,
        },
    )


@router.post("/profile/photo", name="member_profile_photo")
async def profile_photo_update(
    request: Request,
    photo: UploadFile,
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db)

    try:
        photo_url, _size = await storage.save_upload(photo, "avatars")
    except storage.UploadError as err:
        return RedirectResponse(f"/member/profile?photo_error={quote(str(err))}", status_code=303)

    old_url = user.profile_picture_url
    user.profile_picture_url = photo_url
    await db.commit()

    if old_url:
        (storage.STATIC_DIR / old_url[len("/static/") :]).unlink(missing_ok=True)

    return RedirectResponse("/member/profile?photo_ok=1", status_code=303)


@router.post("/profile", name="member_profile_update")
async def profile_update(
    request: Request,
    name: str = Form(...),
    phone: str = Form(None),
    program: str = Form(None),
    sms_opt_in: bool = Form(False),
    email_opt_in: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db)

    user.name = name.strip()
    if phone:
        user.phone = phone.strip()
    if program:
        user.program = program.strip()
    user.sms_opt_in = sms_opt_in
    user.email_opt_in = email_opt_in
    await db.commit()

    return RedirectResponse("/member/profile?profile_ok=1", status_code=303)


@router.post("/profile/change-password", name="member_change_password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_new: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db)

    if not await verify_password(current_password, user.password_hash):
        return RedirectResponse("/member/profile?pwd_error=1", status_code=303)
    if new_password != confirm_new:
        return RedirectResponse("/member/profile?pwd_error=2", status_code=303)

    user.password_hash = await hash_password(new_password)
    await db.commit()

    return RedirectResponse("/member/profile?pwd_ok=1", status_code=303)


# ── Resources ─────────────────────────────────────────────────────────────────


@router.get("/resources", name="member_resources")
async def resources_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    resources_result = await db.execute(select(Resource).order_by(Resource.date.desc()))
    resources_orm = resources_result.scalars().all()

    # Serialize to plain dicts so tojson works in the template
    resources = [
        {
            "id": r.id,
            "title": r.title,
            "category": r.category,
            "type": r.type,
            "size": r.size or "",
            "date": r.date.strftime("%d %b %Y") if r.date else "",
            "description": r.description or "",
            "file_url": r.file_url or "#",
        }
        for r in resources_orm
    ]

    return templates.TemplateResponse(
        request=request,
        name="member/resources.html",
        context={
            "user": user,
            "active_nav": "resources",
            "resources": resources,
        },
    )


# ── Events ────────────────────────────────────────────────────────────────────


@router.get("/events", name="member_events")
async def events_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    events_result = await db.execute(
        select(Event)
        .where(Event.is_public == True)  # noqa: E712
        .order_by(Event.date)
        .options(selectinload(Event.rsvps))
    )
    events = events_result.scalars().all()

    rsvps_result = await db.execute(select(Rsvp).where(Rsvp.user_id == user.id))
    user_rsvp_event_ids = {rsvp.event_id for rsvp in rsvps_result.scalars().all()}

    return templates.TemplateResponse(
        request=request,
        name="member/events.html",
        context={
            "user": user,
            "active_nav": "events",
            "events": events,
            "user_rsvp_event_ids": user_rsvp_event_ids,
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/events/{event_id}/rsvp", name="member_rsvp_create")
async def rsvp_create(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db)

    existing = await db.execute(
        select(Rsvp).where(Rsvp.user_id == user.id, Rsvp.event_id == event_id)
    )
    if not existing.scalar_one_or_none():
        db.add(Rsvp(user_id=user.id, event_id=event_id))
        await db.commit()

    return RedirectResponse("/member/events?flash=You%27re+confirmed+for+this+event%21", status_code=303)


@router.post("/events/{event_id}/cancel-rsvp", name="member_rsvp_cancel")
async def rsvp_cancel(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db)

    result = await db.execute(
        select(Rsvp).where(Rsvp.user_id == user.id, Rsvp.event_id == event_id)
    )
    rsvp = result.scalar_one_or_none()
    if rsvp:
        await db.delete(rsvp)
        await db.commit()

    return RedirectResponse("/member/events?flash=RSVP+cancelled.", status_code=303)
