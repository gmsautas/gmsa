from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps_web import require_member
from app.core.security import verify_password, create_access_token, create_refresh_token, hash_password
from app.core.templates import templates
from app.api.deps import get_user_by_email
from app.models.models import User
from app.services import academic, password_reset
from app.services.academic import PROGRAM_CATEGORIES
from app.services.password_reset import PasswordResetError

router = APIRouter(tags=["web-auth"])


def _safe_next(next_: str | None, fallback: str) -> str:
    """Only redirect to a local page path — never an absolute/external URL."""
    if next_ and next_.startswith("/") and not next_.startswith("//"):
        return next_
    return fallback


# ── Member Login ───────────────────────────────────────────────────────────────

@router.get("/login", name="login")
async def login_page(request: Request):
    registered = request.query_params.get("registered")
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={
            "request": request,
            "registered": registered,
            "reset": request.query_params.get("reset"),
            "next": request.query_params.get("next", ""),
        },
    )


@router.post("/login", name="login_submit")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_email(db, email)
    if not user or not verify_password(password, user.password_hash) or user.status != "active":
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={"request": request, "error": "Invalid email or password", "next": next or ""},
            status_code=401,
        )

    resp = RedirectResponse(url=_safe_next(next, "/member/dashboard"), status_code=303)
    resp.set_cookie(
        "gmsa_access",
        create_access_token(str(user.id)),
        httponly=True,
        samesite="lax",
        max_age=3600,
    )
    resp.set_cookie(
        "gmsa_refresh",
        create_refresh_token(str(user.id)),
        httponly=True,
        samesite="lax",
        max_age=2592000,
    )
    return resp


@router.get("/logout", name="logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("gmsa_access")
    resp.delete_cookie("gmsa_refresh")
    return resp


@router.get("/admin/login", name="admin_login")
async def admin_login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/admin_login.html",
        context={"request": request, "next": request.query_params.get("next", "")},
    )


@router.post("/admin/login", name="admin_login_submit")
async def admin_login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_email(db, email)
    if not user or not verify_password(password, user.password_hash) or user.status != "active":
        return templates.TemplateResponse(
            request=request,
            name="auth/admin_login.html",
            context={"request": request, "error": "Invalid email or password", "next": next or ""},
            status_code=401,
        )

    if user.role not in ("admin", "superadmin"):
        return templates.TemplateResponse(
            request=request,
            name="auth/admin_login.html",
            context={"request": request, "error": "Access denied — admin accounts only", "next": next or ""},
            status_code=403,
        )

    resp = RedirectResponse(url=_safe_next(next, "/admin/dashboard"), status_code=303)
    resp.set_cookie(
        "gmsa_access",
        create_access_token(str(user.id)),
        httponly=True,
        samesite="lax",
        max_age=3600,
    )
    resp.set_cookie(
        "gmsa_refresh",
        create_refresh_token(str(user.id)),
        httponly=True,
        samesite="lax",
        max_age=2592000,
    )
    return resp


# ── Admin Logout ───────────────────────────────────────────────────────────────

@router.get("/admin/logout", name="admin_logout")
async def admin_logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie("gmsa_access")
    resp.delete_cookie("gmsa_refresh")
    return resp



@router.get("/register", name="register")
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="auth/register.html", context={"prefill": {}})


@router.post("/register", name="register_submit")
async def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    student_id: str = Form(...),
    program_category: str = Form(...),
    phone: str = Form(""),
    program: str = Form(""),
    terms: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    name = name.strip()
    email = email.strip().lower()
    student_id = student_id.strip()
    prefill = {
        "name": name,
        "email": email,
        "phone": phone,
        "program": program,
        "student_id": student_id,
        "program_category": program_category,
    }

    def reg_error(message: str, status_code: int = 422):
        return templates.TemplateResponse(
            request=request,
            name="auth/register.html",
            context={"error": message, "prefill": prefill},
            status_code=status_code,
        )

    # Validate name (parity with the API's RegisterRequest schema)
    if len(name) < 2:
        return reg_error("Please enter your full name")

    # Validate student ID — must be digits only (matches API + downstream
    # academic-year and election-register logic that assume a clean numeric ID)
    if not student_id.isdigit():
        return reg_error("Student ID must contain only digits")

    # Validate program category against the known set
    if program_category not in PROGRAM_CATEGORIES:
        return reg_error("Please select a valid programme category")

    # Validate password match
    if password != confirm_password:
        return reg_error("Passwords do not match")

    # Validate password length
    if len(password) < 8:
        return reg_error("Password must be at least 8 characters")

    # Validate terms acceptance
    if not terms:
        return reg_error("You must agree to the code of conduct and privacy policy")

    # Check for duplicate email
    existing = await get_user_by_email(db, email)
    if existing:
        return reg_error("Email already registered", 409)

    # Check for duplicate student ID
    res = await db.execute(select(User).where(User.student_id == student_id))
    if res.scalar_one_or_none():
        return reg_error("Student ID already registered", 409)

    # Create the user
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        phone=phone or None,
        program=program or None,
        program_category=program_category,
        student_id=student_id,
        grad_year=academic.graduation_year(student_id, program_category),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Backstop for the tiny window between the duplicate checks above and
        # this insert (two simultaneous submissions) — the DB unique constraints
        # are the real guarantee; surface them as a friendly message, not a 500.
        await db.rollback()
        return reg_error("That email or student ID is already registered", 409)

    return RedirectResponse("/login?registered=1", status_code=303)


# ── Forced Password Change ──────────────────────────────────────────────────

@router.get("/force-password-change", name="force_password_change")
async def force_password_change_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db, allow_password_change=True)
    if not user.must_change_password:
        dashboard = "/admin/dashboard" if user.role in ("admin", "superadmin") else "/member/dashboard"
        return RedirectResponse(dashboard, status_code=303)
    return templates.TemplateResponse(request=request, name="auth/force_password_change.html")


@router.post("/force-password-change", name="force_password_change_submit")
async def force_password_change_submit(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_member(request, db, allow_password_change=True)

    if new_password != confirm_password:
        return templates.TemplateResponse(
            request=request,
            name="auth/force_password_change.html",
            context={"error": "Passwords do not match"},
            status_code=422,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request=request,
            name="auth/force_password_change.html",
            context={"error": "Password must be at least 8 characters"},
            status_code=422,
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    await db.commit()

    dashboard = "/admin/dashboard" if user.role in ("admin", "superadmin") else "/member/dashboard"
    return RedirectResponse(dashboard, status_code=303)


# ── Self-service Password Reset ─────────────────────────────────────────────

@router.get("/forgot-password", name="forgot_password")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="auth/forgot_password.html")


@router.post("/forgot-password", name="forgot_password_submit")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await password_reset.request_reset(db, email=email, base_url=str(request.base_url))
    # Always show the same message, whether or not that email has an account —
    # otherwise this form could be used to check which emails are registered.
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context={"sent": True},
    )


@router.get("/reset-password", name="reset_password")
async def reset_password_page(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.query_params.get("token", "")
    try:
        await password_reset.verify_reset_token(db, token)
    except PasswordResetError as e:
        return templates.TemplateResponse(
            request=request,
            name="auth/reset_password.html",
            context={"error": str(e), "token": None},
        )
    return templates.TemplateResponse(
        request=request,
        name="auth/reset_password.html",
        context={"token": token},
    )


@router.post("/reset-password", name="reset_password_submit")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request=request,
            name="auth/reset_password.html",
            context={"error": "Passwords do not match", "token": token},
            status_code=422,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request=request,
            name="auth/reset_password.html",
            context={"error": "Password must be at least 8 characters", "token": token},
            status_code=422,
        )

    try:
        user = await password_reset.consume_reset_token(db, token=token, new_password=new_password)
    except PasswordResetError as e:
        return templates.TemplateResponse(
            request=request,
            name="auth/reset_password.html",
            context={"error": str(e), "token": None},
            status_code=400,
        )

    # Auto-login instead of sending them back to the login page to type the
    # password they just chose -- same cookies /login sets, since setting a
    # password through a verified link is just as much "proof of identity" as
    # a password field. This is what makes a voter's/member's setup link (see
    # member_provisioning.find_or_create_member_no_email and its "Account
    # Setup Link" spreadsheet column) a true one-click flow: click link, set
    # password, land signed in.
    dashboard = "/admin/dashboard" if user.role in ("admin", "superadmin") else "/member/dashboard"
    resp = RedirectResponse(dashboard, status_code=303)
    resp.set_cookie(
        "gmsa_access",
        create_access_token(str(user.id)),
        httponly=True,
        samesite="lax",
        max_age=3600,
    )
    resp.set_cookie(
        "gmsa_refresh",
        create_refresh_token(str(user.id)),
        httponly=True,
        samesite="lax",
        max_age=2592000,
    )
    return resp
