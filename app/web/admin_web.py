import base64
import io
import math
import re
from datetime import date as date_type
from datetime import datetime, time

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.deps_web import Forbidden, PageRedirect, require_admin, require_superadmin
from app.core.templates import templates
from app.models.models import (
    AboutPillar,
    Announcement,
    BlogPost,
    Committee,
    CommitteeMember,
    DuesRecord,
    EmailCampaign,
    Event,
    Expense,
    LeadershipBoard,
    LeadershipMember,
    Milestone,
    OrgSettings,
    PageContentBlock,
    PrayerTimes,
    Project,
    Resource,
    SmsCampaign,
    Transaction,
    User,
)
from app.services import academic, import_jobs, member_provisioning, org_settings_cache, storage

router = APIRouter()


# ─────────────────────────────────────────────
# PAGINATION
# ─────────────────────────────────────────────
#
# Shared by every server-rendered admin list page below -- these used to each
# cap out at a bare `.limit(N)` with no way to see or reach rows past it (the
# "fake pagination" audit finding). Mirrors the shape already proven in
# app.web.elections_web._voters_list_context/voters_list: page/per_page query
# params, a COUNT query run against the same filters as the main query
# (before offset/limit), and offset/limit on the main query.

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _parse_page_params(request: Request, *, default_per_page: int = DEFAULT_PAGE_SIZE) -> tuple[int, int]:
    try:
        page = int(request.query_params.get("page", 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.query_params.get("per_page", default_per_page))
    except ValueError:
        per_page = default_per_page
    return page, per_page


async def _paginate(
    db: AsyncSession,
    stmt,
    *,
    page: int,
    per_page: int,
    max_per_page: int = MAX_PAGE_SIZE,
):
    """Runs an already-filtered-but-not-yet-limited select() as a real
    page/offset query. Returns
    (rows, page, per_page, total, total_pages, has_prev, has_next).
    """
    page = max(page, 1)
    per_page = min(max(per_page, 1), max_per_page)

    total = (
        await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar() or 0
    total_pages = max(1, math.ceil(total / per_page)) if total else 1
    page = min(page, total_pages)

    rows = (
        (await db.execute(stmt.offset((page - 1) * per_page).limit(per_page)))
        .scalars()
        .all()
    )
    return rows, page, per_page, total, total_pages, page > 1, page < total_pages


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    members_count = (
        await db.execute(
            select(func.count(User.id)).where(User.role == "member", User.status == "active")
        )
    ).scalar() or 0

    txns_result = await db.execute(
        select(Transaction).order_by(Transaction.created_at.desc()).limit(10)
    )
    transactions = txns_result.scalars().all()

    total_income = (
        await db.execute(
            select(func.sum(Transaction.amount)).where(Transaction.status == "success")
        )
    ).scalar() or 0

    total_expenses = (
        await db.execute(select(func.sum(Expense.amount)))
    ).scalar() or 0

    projects_result = await db.execute(
        select(Project).where(Project.status == "open")
    )
    projects = projects_result.scalars().all()

    dues_paid = (
        await db.execute(
            select(func.count(DuesRecord.id)).where(DuesRecord.status == "paid")
        )
    ).scalar() or 0

    dues_unpaid = (
        await db.execute(
            select(func.count(DuesRecord.id)).where(DuesRecord.status == "unpaid")
        )
    ).scalar() or 0

    recent_expenses = (
        await db.execute(select(Expense).order_by(Expense.date.desc()).limit(6))
    ).scalars().all()

    net_balance = total_income - total_expenses

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "admin": admin,
            "active_nav": "dashboard",
            "members_count": members_count,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "net_balance": net_balance,
            "transactions": transactions,
            "projects": projects,
            "dues_paid": dues_paid,
            "dues_unpaid": dues_unpaid,
            "recent_expenses": recent_expenses,
        },
    )


# ─────────────────────────────────────────────
# MEMBERS
# ─────────────────────────────────────────────

@router.get("/members")
async def members_list(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    q = request.query_params.get("q", "")
    role_filter = request.query_params.get("role", "")
    status_filter = request.query_params.get("status", "active")

    stmt = select(User)
    if q:
        stmt = stmt.where(User.name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
    if role_filter:
        stmt = stmt.where(User.role == role_filter)
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    stmt = stmt.order_by(User.created_at.desc())

    page, per_page = _parse_page_params(request)
    members, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )

    total_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_count = (
        await db.execute(select(func.count(User.id)).where(User.status == "active"))
    ).scalar() or 0
    dues_unpaid = (
        await db.execute(
            select(func.count(DuesRecord.id)).where(DuesRecord.status == "unpaid")
        )
    ).scalar() or 0

    current_year = date_type.today().year
    graduating_count = (
        await db.execute(
            select(func.count(User.id)).where(User.grad_year == current_year)
        )
    ).scalar() or 0

    grad_years = {m.id: academic.effective_grad_year(m) for m in members}

    boards = (
        await db.execute(
            select(LeadershipBoard)
            .options(selectinload(LeadershipBoard.committees))
            .order_by(LeadershipBoard.term.desc())
        )
    ).scalars().all()
    committees = [
        {"id": c.id, "name": c.name, "board_term": b.term}
        for b in boards
        for c in sorted(b.committees, key=lambda c: c.order_index)
    ]

    return templates.TemplateResponse(
        request=request,
        name="admin/members.html",
        context={
            "admin": admin,
            "active_nav": "members",
            "members": members,
            "grad_years": grad_years,
            "boards": boards,
            "committees": committees,
            "q": q,
            "role_filter": role_filter,
            "status_filter": status_filter,
            "total_count": total_count,
            "active_count": active_count,
            "dues_unpaid": dues_unpaid,
            "graduating_count": graduating_count,
            "current_year": current_year,
            "error": request.query_params.get("error"),
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/members/new")
async def member_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="admin/member_create.html",
        context={
            "admin": admin,
            "active_nav": "members",
            "error": request.query_params.get("error"),
            "prefill": {},
        },
    )


@router.post("/members/new")
async def member_create_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    program: str = Form(""),
    program_category: str = Form(...),
    student_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

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

    def err(message: str):
        return templates.TemplateResponse(
            request=request,
            name="admin/member_create.html",
            context={"admin": admin, "active_nav": "members", "error": message, "prefill": prefill},
            status_code=422,
        )

    if len(name) < 2:
        return err("Please enter the member's full name")
    if not student_id.isdigit():
        return err("Student ID must contain only digits")
    if program_category not in academic.PROGRAM_CATEGORIES:
        return err("Please select a valid programme category")

    try:
        user, created, email_failed, _account_setup_url = await member_provisioning.find_or_create_member(
            db, student_id=student_id, email=email, name=name, base_url=str(request.base_url)
        )
    except member_provisioning.ProvisioningConflict as e:
        return err(str(e))

    if not created:
        return err(f"An account with this email or student ID already exists (#{user.id}).")

    user.phone = phone or None
    user.program = program or None
    user.program_category = program_category
    user.grad_year = academic.graduation_year(student_id, program_category)
    await db.commit()

    query = "?created=1" if not email_failed else "?created=1&email_failed=1"
    return RedirectResponse(f"/admin/members/{user.id}{query}", status_code=303)


@router.get("/members/import")
async def member_import_page(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="admin/member_import.html",
        context={"admin": admin, "active_nav": "members", "error": request.query_params.get("error")},
    )


async def _run_member_import_job(
    *, job_id: str, rows: list[dict], base_url: str, send_emails: bool
) -> None:
    """Runs the actual (slow -- one commit and, usually, one awaited email
    send per row) import off the request/response cycle; see
    member_import_submit, which schedules this via BackgroundTasks instead of
    awaiting it inline. Opens its OWN session -- FastAPI only runs background
    tasks after the response has been sent, so the request's
    `Depends(get_db)` session is already closed by the time this runs. Unlike
    the election register import, there's no election/admin row to re-fetch
    here -- import_members takes no such arguments.

    Wrapped in a broad try/except so any unexpected error still marks the job
    "failed" instead of leaving it stuck at "running" forever with no way for
    the admin to tell it died."""
    try:
        async with AsyncSessionLocal() as db:

            def on_row(index: int, total: int, _info: dict) -> None:
                import_jobs.update_progress(job_id, index)

            result = await member_provisioning.import_members(
                db, rows, base_url=base_url, send_emails=send_emails, on_row=on_row
            )
            import_jobs.complete_job(job_id, result)
    except Exception as err:  # noqa: BLE001 -- must never leave the job stuck at "running"
        import_jobs.fail_job(job_id, str(err))


@router.post("/members/import")
async def member_import_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    skip_emails: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    contents = await file.read()
    try:
        rows = member_provisioning.parse_register_file(file.filename or "", contents)
    except member_provisioning.RegisterFileError as err:
        return RedirectResponse(f"/admin/members/import?error={quote(str(err))}", status_code=303)

    # Parsing the file is fast and stays inline; only the row loop -- one
    # commit and, usually, one awaited email send per row -- is slow enough to
    # risk outrunning the hosting platform's reverse-proxy request timeout on
    # a large upload, so that part alone moves to a background task (see
    # _run_member_import_job). The admin is redirected to a status page
    # immediately instead of waiting on the response.
    send_emails = not bool(skip_emails)
    job_id = import_jobs.create_job("member_import")
    import_jobs.set_total(job_id, len(rows))
    background_tasks.add_task(
        _run_member_import_job,
        job_id=job_id,
        rows=rows,
        base_url=str(request.base_url),
        send_emails=send_emails,
    )
    return RedirectResponse(f"/admin/members/import-jobs/{job_id}", status_code=303)


@router.get("/members/import-jobs/{job_id}")
async def member_import_job_status(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    job = import_jobs.get_job(job_id)
    if job is None:
        return RedirectResponse(
            "/admin/members/import?error="
            + quote("This import job's progress is no longer available (it may have finished a "
                    "while ago, or the server restarted) — check the members list to see what came through."),
            status_code=303,
        )

    if job["status"] == "completed":
        result = job["result"]
        # Rebuilt here, not stored on the job -- same "generated only for
        # this page view, never persisted" handling member_import_submit used
        # to do inline. Only ever non-empty when the import ran with
        # send_emails=False (see MemberImportResult.credentials).
        credentials_b64 = None
        if result.credentials:
            workbook_bytes = member_provisioning.build_credentials_workbook(result.credentials)
            credentials_b64 = base64.b64encode(workbook_bytes).decode()

        return templates.TemplateResponse(
            request=request,
            name="admin/member_import_result.html",
            context={
                "admin": admin,
                "active_nav": "members",
                "result": result,
                "credentials_b64": credentials_b64,
            },
        )

    return templates.TemplateResponse(
        request=request,
        name="admin/import_job_status.html",
        context={
            "admin": admin,
            "active_nav": "members",
            "job": job,
            "back_url": "/admin/members/import",
        },
    )


# Bulk actions requiring a super admin — role changes and mass deactivation are
# a bigger blast radius than doing either one member at a time.
_SUPERADMIN_BULK_ACTIONS = {"role_member", "role_admin", "delete"}


@router.post("/members/bulk")
async def bulk_update_members(
    request: Request,
    user_ids: str = Form(...),
    action: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    if action in _SUPERADMIN_BULK_ACTIONS:
        try:
            await require_superadmin(request, db)
        except Forbidden as e:
            return RedirectResponse(f"/admin/members?error={quote(e.message)}", status_code=303)

    try:
        ids = [int(x) for x in user_ids.split(",") if x.strip()]
    except ValueError:
        ids = []
    if not ids:
        return RedirectResponse("/admin/members", status_code=303)

    members = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()

    if action == "activate":
        for m in members:
            m.status = "active"
    elif action in ("deactivate", "delete"):
        # No hard-delete for users: dues/transaction/RSVP history hangs off
        # this row, so "delete" here means the same soft-deactivate as a
        # single-member delete, just gated more strictly given the blast radius.
        for m in members:
            m.status = "inactive"
    elif action == "role_member":
        for m in members:
            m.role = "member"
    elif action == "role_admin":
        for m in members:
            m.role = "admin"
    else:
        return RedirectResponse(
            f"/admin/members?error={quote('Unknown bulk action')}", status_code=303
        )

    await db.commit()
    return RedirectResponse("/admin/members", status_code=303)


async def _member_detail_context(
    db: AsyncSession,
    *,
    user_id: int,
    admin: User,
    error: str | None = None,
    created: str | None = None,
    email_failed: str | None = None,
    revealed: dict | None = None,
) -> dict | None:
    member = await db.get(User, user_id)
    if not member:
        return None

    dues_records = (
        await db.execute(
            select(DuesRecord).where(DuesRecord.user_id == user_id).order_by(DuesRecord.due_date.desc())
        )
    ).scalars().all()

    computed_level = academic.current_level_for_member(member.student_id, member.program_category)

    return {
        "admin": admin,
        "active_nav": "members",
        "member": member,
        "dues_records": dues_records,
        "computed_level": computed_level,
        "effective_level": academic.effective_level(member),
        "effective_grad_year": academic.effective_grad_year(member),
        "error": error,
        "created": created,
        "email_failed": email_failed,
        "revealed": revealed,
    }


@router.get("/members/{user_id}")
async def member_detail(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    context = await _member_detail_context(
        db,
        user_id=user_id,
        admin=admin,
        error=request.query_params.get("error"),
        created=request.query_params.get("created"),
        email_failed=request.query_params.get("email_failed"),
    )
    if context is None:
        return RedirectResponse("/admin/members", status_code=302)

    return templates.TemplateResponse(
        request=request, name="admin/member_detail.html", context=context
    )


@router.post("/members/{user_id}/reset-password")
async def member_reset_password(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Issues a brand-new temp password and shows it once, directly on this
    page, for an admin to relay to the member by phone/in person/WhatsApp --
    for when email delivery can't be trusted at all rather than only being
    slow. Never puts the plaintext password in a URL/redirect/log."""
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    member = await db.get(User, user_id)
    if member is None:
        return RedirectResponse("/admin/members", status_code=302)

    temp_password, email_sent = await member_provisioning.reset_password_for_admin_reveal(db, member)

    context = await _member_detail_context(
        db,
        user_id=user_id,
        admin=admin,
        revealed={"user": member, "password": temp_password, "email_sent": email_sent},
    )
    if context is None:
        return RedirectResponse("/admin/members", status_code=302)

    return templates.TemplateResponse(
        request=request, name="admin/member_detail.html", context=context
    )


def _safe_members_redirect(next_path: str | None) -> str:
    if next_path and next_path.startswith("/admin/members"):
        return next_path
    return "/admin/members"


@router.post("/members/{user_id}")
async def update_member(
    user_id: int,
    request: Request,
    role: str = Form(None),
    status: str = Form(None),
    title: str = Form(None),
    level_override: str = Form(None),
    grad_year_override: str = Form(None),
    academic_override_note: str = Form(None),
    next: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    destination = _safe_members_redirect(next)

    member = await db.get(User, user_id)
    if member:
        if role and role != member.role:
            try:
                await require_superadmin(request, db)
            except Forbidden as e:
                return RedirectResponse(
                    f"/admin/members/{user_id}?error={quote(e.message)}", status_code=303
                )
            member.role = role
        if status:
            member.status = status
        if title is not None:
            member.title = title.strip() or None
        if level_override is not None:
            member.level_override = int(level_override) if level_override.strip() else None
        if grad_year_override is not None:
            member.grad_year_override = (
                int(grad_year_override) if grad_year_override.strip() else None
            )
        if academic_override_note is not None:
            member.academic_override_note = academic_override_note.strip() or None
        await db.commit()
    return RedirectResponse(destination, status_code=303)


# ─────────────────────────────────────────────
# FINANCE
# ─────────────────────────────────────────────

@router.get("/finance")
async def finance_redirect():
    return RedirectResponse("/admin/finance/income", status_code=307)


async def _finance_totals(db: AsyncSession):
    total_income = (
        await db.execute(
            select(func.sum(Transaction.amount)).where(Transaction.status == "success")
        )
    ).scalar() or 0
    total_expenses = (
        await db.execute(select(func.sum(Expense.amount)))
    ).scalar() or 0
    return total_income, total_expenses, total_income - total_expenses


@router.get("/finance/income")
async def finance_income(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(Transaction).order_by(Transaction.created_at.desc())
    transactions, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )
    total_income, total_expenses, net_balance = await _finance_totals(db)

    # Table-wide totals, not a tally of whichever page happens to be showing --
    # same pattern as _finance_totals, run as separate aggregate queries
    # instead of iterating the (now paginated) `transactions` list.
    success_count = (
        await db.execute(
            select(func.count(Transaction.id)).where(Transaction.status == "success")
        )
    ).scalar() or 0
    pending_count = (
        await db.execute(
            select(func.count(Transaction.id)).where(Transaction.status == "pending")
        )
    ).scalar() or 0
    failed_count = (
        await db.execute(
            select(func.count(Transaction.id)).where(Transaction.status == "failed")
        )
    ).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="admin/finance/income.html",
        context={
            "admin": admin,
            "active_nav": "finance",
            "active_page": "income",
            "transactions": transactions,
            "total_income": total_income,
            "success_count": success_count,
            "pending_count": pending_count,
            "failed_count": failed_count,
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/finance/expenses")
async def finance_expenses(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(Expense).order_by(Expense.date.desc())
    expenses, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )
    _total_income, total_expenses, _net = await _finance_totals(db)

    return templates.TemplateResponse(
        request=request,
        name="admin/finance/expenses.html",
        context={
            "admin": admin,
            "active_nav": "finance",
            "active_page": "expenses",
            "expenses": expenses,
            "total_expenses": total_expenses,
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/finance/momo")
async def finance_momo(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    momo_pending = (
        await db.execute(
            select(Transaction)
            .where(Transaction.method == "momo_manual", Transaction.status == "pending")
            .order_by(Transaction.created_at)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="admin/finance/momo.html",
        context={
            "admin": admin,
            "active_nav": "finance",
            "active_page": "momo",
            "momo_pending": momo_pending,
        },
    )


def _default_report_range(today: date_type | None = None) -> tuple[date_type, date_type]:
    """Default date range for the downloadable financial statement.

    There's no stored "semester start/end" anywhere in this codebase --
    current_semester_label() (app/services/audience.py) only derives a label
    (e.g. "Spring 2026") from a Jan-Jun / Jul-Dec split. Mirror that same
    cutoff here so "current semester" means the same thing everywhere, and
    run it through to today rather than a future semester-end date.
    """
    today = today or date_type.today()
    start = date_type(today.year, 1, 1) if today.month <= 6 else date_type(today.year, 7, 1)
    return start, today


@router.get("/finance/reports")
async def finance_reports(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    # Never needs individual Expense rows -- reports.html only shows a
    # category-breakdown chart, so aggregate in SQL (GROUP BY) rather than
    # pulling a capped set of rows and summing them in Jinja, which silently
    # produced a wrong/incomplete breakdown once total expenses passed 300.
    expense_by_cat = dict(
        (
            await db.execute(
                select(Expense.category, func.sum(Expense.amount)).group_by(Expense.category)
            )
        ).all()
    )
    total_income, total_expenses, net_balance = await _finance_totals(db)
    default_start, default_end = _default_report_range()

    return templates.TemplateResponse(
        request=request,
        name="admin/finance/reports.html",
        context={
            "admin": admin,
            "active_nav": "finance",
            "active_page": "reports",
            "expense_by_cat": expense_by_cat,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "net_balance": net_balance,
            "default_start": default_start,
            "default_end": default_end,
            "error": request.query_params.get("error"),
        },
    )


@router.get("/finance/reports/download")
async def finance_reports_download(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    default_start, default_end = _default_report_range()
    try:
        start = date_type.fromisoformat(start_date) if start_date else default_start
        end = date_type.fromisoformat(end_date) if end_date else default_end
    except ValueError:
        return RedirectResponse(
            "/admin/finance/reports?error=" + quote("Invalid date format."), status_code=303
        )

    if start > end:
        return RedirectResponse(
            "/admin/finance/reports?error="
            + quote("Start date must be on or before the end date."),
            status_code=303,
        )

    org = await db.get(OrgSettings, 1)
    if org is None:
        org = OrgSettings(id=1)

    transactions = (
        await db.execute(
            select(Transaction)
            .options(selectinload(Transaction.user))
            .where(
                Transaction.status == "success",
                Transaction.created_at >= datetime.combine(start, time.min),
                Transaction.created_at <= datetime.combine(end, time.max),
            )
            .order_by(Transaction.created_at)
        )
    ).scalars().all()
    expenses = (
        await db.execute(
            select(Expense)
            .where(Expense.date >= start, Expense.date <= end)
            .order_by(Expense.date)
        )
    ).scalars().all()

    pdf_bytes = _render_financial_statement_pdf(org, start, end, transactions, expenses)
    filename = f"GMSA-Financial-Statement-{start}-to-{end}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _transaction_display_name(tx: Transaction) -> str:
    """Who to show as the source of an income line -- same fallback chain as
    list_transactions() in app/api/v1/routes/finance.py: a logged-in donor's
    account name wins, then the free-text donor_name captured at payment
    time, then whatever description was recorded, else "Anonymous".
    """
    if tx.user:
        return tx.user.name
    return tx.donor_name or tx.description or "Anonymous"


def _render_financial_statement_pdf(org, start_date, end_date, transactions, expenses) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    from app.services import storage

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    forest = HexColor("#1E4F3A")  # --forest-700, matches the app's admin UI green
    ink = HexColor("#111827")
    gray = HexColor("#6B7280")

    left = 20 * mm
    right = width - 20 * mm
    bottom_margin = 30 * mm
    row_h = 7 * mm

    org_name = (org.full_name if org else None) or "GMSA UTAS"
    tagline = (org.tagline if org else None) or ""
    address = (org.address if org else None) or ""

    def draw_letterhead() -> float:
        """Draws the green letterhead + period line, returns the Y cursor
        just below it so callers can start printing table rows."""
        top = height - 20 * mm
        logo_path = storage.STATIC_DIR / "img" / "logo.png"
        text_x = left
        try:
            if logo_path.exists():
                c.drawImage(
                    str(logo_path),
                    left,
                    top - 16 * mm,
                    width=16 * mm,
                    height=16 * mm,
                    preserveAspectRatio=True,
                    mask="auto",
                )
                text_x = left + 20 * mm
        except Exception:
            # A malformed/unsupported image must never break report generation --
            # fall back to a text-only letterhead.
            text_x = left

        c.setFillColor(forest)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(text_x, top - 5 * mm, org_name)
        c.setFont("Helvetica", 9)
        c.setFillColor(gray)
        if tagline:
            c.drawString(text_x, top - 10 * mm, tagline)
        if address:
            c.drawString(text_x, top - 14.5 * mm, address)

        c.setStrokeColor(forest)
        c.setLineWidth(1.2)
        c.line(left, top - 18 * mm, right, top - 18 * mm)

        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(
            width / 2,
            top - 25 * mm,
            f"FINANCIAL STATEMENT — {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}",
        )
        c.setFont("Helvetica", 8)
        c.setFillColor(gray)
        c.drawRightString(right, top - 25 * mm - 4.5 * mm, f"Generated {date_type.today():%d %B %Y}")

        return top - 32 * mm

    def draw_section_header(y: float, title: str) -> float:
        c.setFillColor(forest)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, title)
        c.setStrokeColor(forest)
        c.setLineWidth(0.6)
        c.line(left, y - 2 * mm, right, y - 2 * mm)
        return y - 8 * mm

    def draw_income_columns(y: float, continued: bool = False) -> float:
        y = draw_section_header(y, "TABLE OF INCOME" + (" (continued)" if continued else ""))
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(ink)
        c.drawString(left, y, "Date")
        c.drawString(left + 25 * mm, y, "Description")
        c.drawRightString(right, y, "Amount")
        return y - row_h

    def draw_expenditure_columns(y: float, continued: bool = False) -> float:
        y = draw_section_header(y, "EXPENDITURES" + (" (continued)" if continued else ""))
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(ink)
        c.drawString(left, y, "Date")
        c.drawString(left + 25 * mm, y, "Description")
        c.drawString(left + 110 * mm, y, "Category")
        c.drawRightString(right, y, "Amount")
        return y - row_h

    def ensure_space(y: float, header_fn=None) -> float:
        """Starts a fresh page (with the letterhead and, if given, the table's
        column header repeated) if the next row would land inside the bottom
        margin -- reportlab's canvas has no built-in pagination, so every
        table loop below must call this before drawing a row."""
        if y < bottom_margin:
            c.showPage()
            y = draw_letterhead()
            if header_fn:
                y = header_fn(y, True)
        return y

    y = draw_letterhead()

    # ── Income ───────────────────────────────────────────────────────────
    y = draw_income_columns(y)

    total_income = 0
    for tx in transactions:
        y = ensure_space(y, draw_income_columns)
        c.setFont("Helvetica", 9)
        c.setFillColor(ink)
        c.drawString(left, y, tx.created_at.strftime("%d %b %Y"))
        c.drawString(left + 25 * mm, y, _transaction_display_name(tx)[:45])
        c.drawRightString(right, y, f"{tx.currency} {float(tx.amount):,.2f}")
        total_income += float(tx.amount)
        y -= row_h

    y = ensure_space(y, draw_income_columns)
    c.setStrokeColor(forest)
    c.line(left, y + 2 * mm, right, y + 2 * mm)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(ink)
    c.drawString(left, y, "TOTAL INCOME")
    c.drawRightString(right, y, f"GHS {total_income:,.2f}")
    y -= 12 * mm

    # ── Expenditure ──────────────────────────────────────────────────────
    y = ensure_space(y)
    y = draw_expenditure_columns(y)

    total_expenses = 0
    for ex in expenses:
        y = ensure_space(y, draw_expenditure_columns)
        c.setFont("Helvetica", 9)
        c.setFillColor(ink)
        c.drawString(left, y, ex.date.strftime("%d %b %Y"))
        c.drawString(left + 25 * mm, y, (ex.description or "")[:38])
        c.drawString(left + 110 * mm, y, (ex.category or "")[:20])
        c.drawRightString(right, y, f"{ex.currency} {float(ex.amount):,.2f}")
        total_expenses += float(ex.amount)
        y -= row_h

    y = ensure_space(y, draw_expenditure_columns)
    c.setStrokeColor(forest)
    c.line(left, y + 2 * mm, right, y + 2 * mm)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(ink)
    c.drawString(left, y, "TOTAL EXPENDITURE")
    c.drawRightString(right, y, f"GHS {total_expenses:,.2f}")
    y -= 12 * mm

    # ── Summary ──────────────────────────────────────────────────────────
    y = ensure_space(y)
    net_balance = total_income - total_expenses
    c.setFillColor(forest)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "AMOUNT LEFT (NET BALANCE)")
    c.drawRightString(right, y, f"GHS {net_balance:,.2f}")

    c.showPage()
    c.save()
    return buffer.getvalue()


@router.post("/finance/momo/{transaction_id}/approve")
async def approve_momo_transaction(
    transaction_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    tx = await db.get(Transaction, transaction_id)
    if tx:
        tx.status = "success"
        tx.verified_by_id = admin.id
        tx.verified_at = datetime.utcnow()

        due_result = await db.execute(
            select(DuesRecord).where(DuesRecord.transaction_id == tx.id)
        )
        due = due_result.scalar_one_or_none()
        if due:
            due.status = "paid"
            due.paid_at = tx.verified_at

        await db.commit()
    return RedirectResponse("/admin/finance/momo", status_code=303)


@router.post("/finance/momo/{transaction_id}/reject")
async def reject_momo_transaction(
    transaction_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    tx = await db.get(Transaction, transaction_id)
    if tx:
        tx.status = "failed"
        tx.verified_by_id = admin.id
        tx.verified_at = datetime.utcnow()

        due_result = await db.execute(
            select(DuesRecord).where(DuesRecord.transaction_id == tx.id)
        )
        due = due_result.scalar_one_or_none()
        if due:
            due.status = "unpaid"

        await db.commit()
    return RedirectResponse("/admin/finance/momo", status_code=303)


@router.post("/finance/expenses")
async def create_expense(
    request: Request,
    date: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    receipt_url: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    expense = Expense(
        date=date_type.fromisoformat(date),
        description=description,
        category=category,
        amount=amount,
        currency="GHS",
        recorded_by_id=admin.id,
        receipt_url=receipt_url or None,
    )
    db.add(expense)
    await db.commit()
    return RedirectResponse("/admin/finance/expenses", status_code=303)


# ─────────────────────────────────────────────
# PROJECTS
# ─────────────────────────────────────────────

@router.get("/projects")
async def projects(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    projects = (
        await db.execute(select(Project).order_by(Project.id.desc()))
    ).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="admin/projects.html",
        context={
            "admin": admin,
            "active_nav": "projects",
            "projects": projects,
        },
    )


@router.post("/projects")
async def create_project(
    request: Request,
    title: str = Form(...),
    slug: str = Form(...),
    category: str = Form(...),
    icon: str = Form("target"),
    summary: str = Form(...),
    target: float = Form(...),
    currency: str = Form("GHS"),
    status: str = Form("open"),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    project = Project(
        title=title,
        slug=slug.lower().replace(" ", "-"),
        category=category,
        icon=icon,
        summary=summary,
        target=target,
        current=0,
        currency=currency,
        status=status,
    )
    db.add(project)
    await db.commit()
    return RedirectResponse("/admin/projects", status_code=303)


# ─────────────────────────────────────────────
# CONTENT
# ─────────────────────────────────────────────

@router.get("/content")
async def content_redirect():
    return RedirectResponse("/admin/content/blog", status_code=307)


@router.get("/content/blog")
async def content_blog(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(BlogPost).order_by(BlogPost.date.desc())
    posts, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/content/blog.html",
        context={
            "admin": admin,
            "active_nav": "content",
            "active_page": "blog",
            "posts": posts,
            "error": request.query_params.get("error"),
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/content/events")
async def content_events(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(Event).order_by(Event.date.desc())
    events, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/content/events.html",
        context={
            "admin": admin,
            "active_nav": "content",
            "active_page": "events",
            "events": events,
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/content/announcements")
async def content_announcements(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(Announcement).order_by(Announcement.date.desc())
    announcements, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/content/announcements.html",
        context={
            "admin": admin,
            "active_nav": "content",
            "active_page": "announcements",
            "announcements": announcements,
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/content/resources")
async def content_resources(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(Resource).order_by(Resource.date.desc())
    resources, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/content/resources.html",
        context={
            "admin": admin,
            "active_nav": "content",
            "active_page": "resources",
            "resources": resources,
            "error": request.query_params.get("error"),
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/content/prayer-times")
async def content_prayer_times(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    prayer_times = await db.get(PrayerTimes, 1)
    if prayer_times is None:
        prayer_times = PrayerTimes(id=1)
        db.add(prayer_times)
        await db.commit()

    return templates.TemplateResponse(
        request=request,
        name="admin/content/prayer_times.html",
        context={
            "admin": admin,
            "active_nav": "content",
            "active_page": "prayer-times",
            "prayer_times": prayer_times,
            "saved": request.query_params.get("saved"),
        },
    )


@router.post("/content/prayer-times")
async def content_prayer_times_update(
    request: Request,
    fajr: str = Form(""),
    sunrise: str = Form(""),
    dhuhr: str = Form(""),
    asr: str = Form(""),
    maghrib: str = Form(""),
    isha: str = Form(""),
    location_label: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    def parse_time(value: str) -> time | None:
        if not value:
            return None
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))

    prayer_times = await db.get(PrayerTimes, 1)
    if prayer_times is None:
        prayer_times = PrayerTimes(id=1)
        db.add(prayer_times)

    prayer_times.fajr = parse_time(fajr)
    prayer_times.sunrise = parse_time(sunrise)
    prayer_times.dhuhr = parse_time(dhuhr)
    prayer_times.asr = parse_time(asr)
    prayer_times.maghrib = parse_time(maghrib)
    prayer_times.isha = parse_time(isha)
    prayer_times.location_label = location_label.strip() or None
    await db.commit()

    return RedirectResponse("/admin/content/prayer-times?saved=1", status_code=303)


@router.get("/content/pages")
async def content_pages(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    blocks_result = await db.execute(select(PageContentBlock))
    blocks = {b.key: b for b in blocks_result.scalars().all()}
    pillars = (
        await db.execute(select(AboutPillar).order_by(AboutPillar.order_index))
    ).scalars().all()
    milestones = (
        await db.execute(select(Milestone).order_by(Milestone.order_index))
    ).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="admin/content/pages.html",
        context={
            "admin": admin,
            "active_nav": "content",
            "active_page": "pages",
            "blocks": blocks,
            "pillars": pillars,
            "milestones": milestones,
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/content/pages/block/{key}")
async def content_pages_block_update(
    key: str,
    request: Request,
    eyebrow: str = Form(""),
    heading: str = Form(""),
    body: str = Form(""),
    bullets: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    block = (
        await db.execute(select(PageContentBlock).where(PageContentBlock.key == key))
    ).scalar_one_or_none()
    if block is None:
        block = PageContentBlock(key=key)
        db.add(block)

    block.eyebrow = eyebrow.strip() or None
    block.heading = heading.strip() or None
    block.body = body.strip() or None
    if bullets is not None:
        lines = [line.strip() for line in bullets.splitlines() if line.strip()]
        block.extra = {"bullets": lines} if lines else None
    await db.commit()

    return RedirectResponse("/admin/content/pages?saved=1", status_code=303)


@router.post("/content/pages/pillars")
async def content_pillar_create(
    request: Request,
    icon: str = Form("check-circle-2"),
    title: str = Form(...),
    description: str = Form(...),
    order_index: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    db.add(
        AboutPillar(
            icon=icon.strip() or "check-circle-2",
            title=title.strip(),
            description=description.strip(),
            order_index=order_index,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/content/pages?saved=1", status_code=303)


@router.post("/content/pages/pillars/{pillar_id}/delete")
async def content_pillar_delete(pillar_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    pillar = await db.get(AboutPillar, pillar_id)
    if pillar:
        await db.delete(pillar)
        await db.commit()
    return RedirectResponse("/admin/content/pages?saved=1", status_code=303)


@router.post("/content/pages/milestones")
async def content_milestone_create(
    request: Request,
    year: str = Form(...),
    description: str = Form(...),
    order_index: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    db.add(
        Milestone(
            year=year.strip(),
            description=description.strip(),
            order_index=order_index,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/content/pages?saved=1", status_code=303)


@router.post("/content/pages/milestones/{milestone_id}/delete")
async def content_milestone_delete(
    milestone_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    milestone = await db.get(Milestone, milestone_id)
    if milestone:
        await db.delete(milestone)
        await db.commit()
    return RedirectResponse("/admin/content/pages?saved=1", status_code=303)


def _parse_paragraphs(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "post"


async def _unique_blog_slug(db: AsyncSession, title: str) -> str:
    base = _slugify(title)
    slug = base
    suffix = 2
    while (
        await db.execute(select(BlogPost.id).where(BlogPost.slug == slug))
    ).scalar_one_or_none() is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _auto_excerpt(paragraphs: list[str], max_length: int = 200) -> str:
    first = paragraphs[0] if paragraphs else ""
    if len(first) <= max_length:
        return first
    return first[:max_length].rsplit(" ", 1)[0] + "…"


@router.post("/content/blog")
async def create_blog_post_web(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    date: str = Form(...),
    read_time: int = Form(3),
    icon: str = Form("book-open"),
    content: str = Form(...),
    content_ar: str = Form(""),
    status: str = Form("published"),
    image: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    image_url = None
    if image and image.filename:
        try:
            image_url, _size = await storage.save_upload(image, "blog")
        except storage.UploadError as err:
            return RedirectResponse(
                f"/admin/content/blog?error={quote(str(err))}", status_code=303
            )

    paragraphs = _parse_paragraphs(content)
    post = BlogPost(
        slug=await _unique_blog_slug(db, title),
        title=title,
        excerpt=_auto_excerpt(paragraphs),
        category=category,
        author_name=admin.name,
        author_role=admin.title,
        date=date_type.fromisoformat(date),
        read_time=read_time,
        icon=icon,
        content=paragraphs,
        content_ar=_parse_paragraphs(content_ar) or None,
        image_url=image_url,
        status=status,
    )
    db.add(post)
    await db.commit()
    return RedirectResponse("/admin/content/blog", status_code=303)


@router.post("/content/blog/{post_id}/edit")
async def edit_blog_post_web(
    post_id: int,
    request: Request,
    title: str = Form(...),
    slug: str = Form(...),
    excerpt: str = Form(...),
    category: str = Form(...),
    date: str = Form(...),
    read_time: int = Form(3),
    icon: str = Form("book-open"),
    content: str = Form(...),
    content_ar: str = Form(""),
    status: str = Form("published"),
    image: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    post = await db.get(BlogPost, post_id)
    if post is None:
        return RedirectResponse("/admin/content/blog", status_code=303)

    if image and image.filename:
        try:
            post.image_url, _size = await storage.save_upload(image, "blog")
        except storage.UploadError as err:
            return RedirectResponse(
                f"/admin/content/blog?error={quote(str(err))}", status_code=303
            )

    # author_name/author_role are intentionally left untouched here — they're
    # set once from the creating admin's profile and shouldn't be reassigned
    # just because a different admin later edits a typo.
    post.slug = _slugify(slug)
    post.title = title
    post.excerpt = excerpt
    post.category = category
    post.date = date_type.fromisoformat(date)
    post.read_time = read_time
    post.icon = icon
    post.content = _parse_paragraphs(content)
    post.content_ar = _parse_paragraphs(content_ar) or None
    post.status = status
    await db.commit()
    return RedirectResponse("/admin/content/blog", status_code=303)


@router.post("/content/blog/{post_id}/delete")
async def delete_blog_post_web(
    post_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    post = await db.get(BlogPost, post_id)
    if post:
        if post.image_url and post.image_url.startswith("/static/uploads/"):
            (storage.STATIC_DIR / post.image_url[len("/static/"):]).unlink(missing_ok=True)
        await db.delete(post)
        await db.commit()
    return RedirectResponse("/admin/content/blog", status_code=303)


@router.post("/content/announcements")
async def create_announcement(
    request: Request,
    title: str = Form(...),
    body: str = Form(...),
    date: str = Form(...),
    audience: str = Form("all"),
    link_url: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    ann = Announcement(
        title=title,
        body=body,
        date=date_type.fromisoformat(date),
        audience=audience,
        link_url=link_url or None,
    )
    db.add(ann)
    await db.commit()
    return RedirectResponse("/admin/content/announcements", status_code=303)


@router.post("/content/events")
async def create_event(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    date: str = Form(...),
    time: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    is_public: str = Form(None),
    capacity: int = Form(None),
    rsvp_required: str = Form(None),
    icon: str = Form("calendar"),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    event = Event(
        title=title,
        category=category,
        date=date_type.fromisoformat(date),
        time=time,
        location=location,
        description=description,
        is_public=is_public == "true",
        capacity=capacity,
        rsvp_required=rsvp_required == "true",
        icon=icon,
    )
    db.add(event)
    await db.commit()
    return RedirectResponse("/admin/content/events", status_code=303)


@router.post("/content/resources")
async def create_resource(
    request: Request,
    file: UploadFile,
    title: str = Form(...),
    category: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    try:
        file_url, size = await storage.save_upload(
            file, "resources", allowed_exts=storage.DOCUMENT_EXTS
        )
    except storage.UploadError as err:
        return RedirectResponse(
            f"/admin/content/resources?error={quote(str(err))}", status_code=303
        )
    file_type = Path(file.filename or "").suffix.lstrip(".").lower() or "file"

    resource = Resource(
        title=title,
        category=category,
        type=file_type,
        size=size,
        description=description,
        file_url=file_url,
    )
    db.add(resource)
    await db.commit()
    return RedirectResponse("/admin/content/resources", status_code=303)


@router.post("/content/resources/{resource_id}/delete")
async def delete_resource(
    resource_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    resource = await db.get(Resource, resource_id)
    if resource:
        if resource.file_url and resource.file_url.startswith("/static/uploads/"):
            (storage.STATIC_DIR / resource.file_url[len("/static/"):]).unlink(missing_ok=True)
        await db.delete(resource)
        await db.commit()
    return RedirectResponse("/admin/content/resources", status_code=303)


# ─────────────────────────────────────────────
# COMMUNICATIONS
# ─────────────────────────────────────────────

@router.get("/communications")
async def communications_redirect():
    return RedirectResponse("/admin/communications/sms", status_code=307)


@router.get("/communications/sms")
async def communications_sms(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(SmsCampaign).order_by(SmsCampaign.created_at.desc())
    sms_campaigns, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/communications/sms.html",
        context={
            "admin": admin,
            "active_nav": "comms",
            "active_page": "sms",
            "sms_campaigns": sms_campaigns,
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.get("/communications/email")
async def communications_email(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    page, per_page = _parse_page_params(request)
    stmt = select(EmailCampaign).order_by(EmailCampaign.created_at.desc())
    email_campaigns, page, per_page, results_total, total_pages, has_prev, has_next = await _paginate(
        db, stmt, page=page, per_page=per_page
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/communications/email.html",
        context={
            "admin": admin,
            "active_nav": "comms",
            "active_page": "email",
            "email_campaigns": email_campaigns,
            "page": page,
            "per_page": per_page,
            "results_total": results_total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
        },
    )


@router.post("/communications/sms")
async def send_sms(
    request: Request,
    audience: str = Form(...),
    message: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    from app.services.audience import resolve_audience
    from app.services.arkesel import send_sms as arkesel_send

    recipients = await resolve_audience(db, audience)
    phones = [u.phone for u in recipients if u.phone]

    status = "sent"
    count = len(phones)
    try:
        if phones and settings.arkesel_api_key:
            await arkesel_send(phones, message)
    except Exception:
        status = "failed"

    campaign = SmsCampaign(
        audience=audience,
        message=message,
        status=status,
        recipients_count=count,
        sent_by_id=admin.id,
    )
    db.add(campaign)
    await db.commit()
    return RedirectResponse("/admin/communications/sms", status_code=303)


@router.post("/communications/email")
async def send_email(
    request: Request,
    audience: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    from app.services.audience import resolve_audience
    from app.services.resend_client import send_email as resend_send

    recipients = await resolve_audience(db, audience)
    emails = [u.email for u in recipients if u.email]

    status = "sent"
    count = len(emails)
    try:
        if emails:
            await resend_send(to=emails, subject=subject, html=f"<p>{body}</p>")
    except Exception:
        status = "failed"

    campaign = EmailCampaign(
        audience=audience,
        subject=subject,
        body=body,
        status=status,
        recipients_count=count,
        sent_by_id=admin.id,
    )
    db.add(campaign)
    await db.commit()
    return RedirectResponse("/admin/communications/email", status_code=303)


# ─────────────────────────────────────────────
# LEADERSHIP
# ─────────────────────────────────────────────

@router.get("/leadership")
async def leadership(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    boards = (
        await db.execute(
            select(LeadershipBoard)
            .options(
                selectinload(LeadershipBoard.members),
                selectinload(LeadershipBoard.committees).selectinload(Committee.members),
            )
            .order_by(LeadershipBoard.term.desc())
        )
    ).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="admin/leadership.html",
        context={
            "admin": admin,
            "active_nav": "leadership",
            "boards": boards,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/leadership/boards")
async def create_board(
    request: Request,
    term: str = Form(...),
    is_current: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    make_current = is_current == "true"
    if make_current:
        await db.execute(update(LeadershipBoard).values(is_current=False))

    board = LeadershipBoard(term=term, is_current=make_current)
    db.add(board)
    await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


@router.post("/leadership/boards/{board_id}/set-current")
async def set_current_board(
    board_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    await db.execute(update(LeadershipBoard).values(is_current=False))
    board = await db.get(LeadershipBoard, board_id)
    if board:
        board.is_current = True
        await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


@router.post("/leadership/boards/{board_id}/members")
async def create_leadership_member_web(
    board_id: int,
    request: Request,
    name: str = Form(None),
    role: str = Form(...),
    phone: str = Form(None),
    photo_url: str = Form(None),
    order_index: int = Form(0),
    user_id: int = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    linked_user = await db.get(User, user_id) if user_id else None
    if linked_user is not None:
        name = linked_user.name
        phone = phone or linked_user.phone
        photo_url = photo_url or linked_user.profile_picture_url
    elif not name:
        return RedirectResponse(
            "/admin/leadership?error=" + quote("Name is required for a manual entry."),
            status_code=303,
        )

    name_parts = [p for p in name.split() if p]
    if len(name_parts) == 1:
        initials = name_parts[0][:2].upper()
    else:
        initials = (name_parts[0][0] + name_parts[-1][0]).upper()
    member = LeadershipMember(
        board_id=board_id,
        user_id=linked_user.id if linked_user else None,
        name=name,
        role=role,
        phone=phone or None,
        photo_url=photo_url or None,
        initials=initials,
        order_index=order_index,
    )
    db.add(member)
    await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


@router.post("/leadership/members/{member_id}/delete")
async def delete_leadership_member_web(
    member_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    member = await db.get(LeadershipMember, member_id)
    if member:
        await db.delete(member)
        await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


@router.post("/leadership/boards/{board_id}/committees")
async def create_committee_web(
    board_id: int,
    request: Request,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    db.add(Committee(board_id=board_id, name=name))
    await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


@router.post("/leadership/committees/{committee_id}/delete")
async def delete_committee_web(
    committee_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    committee = await db.get(Committee, committee_id)
    if committee:
        await db.delete(committee)
        await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


@router.post("/leadership/committees/{committee_id}/members")
async def create_committee_member_web(
    committee_id: int,
    request: Request,
    name: str = Form(None),
    role: str = Form(...),
    phone: str = Form(None),
    order_index: int = Form(0),
    user_id: int = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    linked_user = await db.get(User, user_id) if user_id else None
    if linked_user is not None:
        name = linked_user.name
        phone = phone or linked_user.phone
    elif not name:
        return RedirectResponse(
            "/admin/leadership?error=" + quote("Name is required for a manual entry."),
            status_code=303,
        )

    db.add(
        CommitteeMember(
            committee_id=committee_id,
            user_id=linked_user.id if linked_user else None,
            name=name,
            role=role,
            phone=phone or None,
            order_index=order_index,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


@router.post("/leadership/committee-members/{member_id}/delete")
async def delete_committee_member_web(
    member_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    member = await db.get(CommitteeMember, member_id)
    if member:
        await db.delete(member)
        await db.commit()
    return RedirectResponse("/admin/leadership", status_code=303)


# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────

@router.get("/settings")
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    org = await db.get(OrgSettings, 1)
    if org is None:
        org = OrgSettings(id=1)
        db.add(org)
        await db.commit()

    flash = (
        "Settings saved." if request.query_params.get("saved") else None
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/settings.html",
        context={
            "admin": admin,
            "active_nav": "settings",
            "org": org,
            "flash": flash,
            "error": request.query_params.get("error"),
            "env_settings": settings,
        },
    )


@router.post("/settings")
async def update_settings(
    request: Request,
    name: str = Form(...),
    full_name: str = Form(...),
    tagline: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
    founding_year: int = Form(...),
    instagram: str = Form(""),
    facebook: str = Form(""),
    twitter: str = Form(""),
    whatsapp: str = Form(""),
    youtube: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    org = await db.get(OrgSettings, 1)
    if org is None:
        org = OrgSettings(id=1)
        db.add(org)

    org.name = name
    org.full_name = full_name
    org.tagline = tagline
    org.email = email
    org.phone = phone
    org.address = address
    org.founding_year = founding_year
    org.social = {
        "instagram": instagram or "",
        "facebook": facebook or "",
        "twitter": twitter or "",
        "whatsapp": whatsapp or "",
        "youtube": youtube or "",
    }
    await db.commit()

    return RedirectResponse("/admin/settings?saved=1", status_code=303)


@router.post("/settings/payment")
async def update_settings_payment(
    request: Request,
    momo_number: str = Form(None),
    momo_name: str = Form(None),
    bank_name: str = Form(None),
    bank_account_name: str = Form(None),
    bank_account_number: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_superadmin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)
    except Forbidden as e:
        return RedirectResponse(f"/admin/settings?error={quote(e.message)}", status_code=303)

    org = await db.get(OrgSettings, 1)
    if org is None:
        org = OrgSettings(id=1)
        db.add(org)

    org.momo_number = momo_number or None
    org.momo_name = momo_name or None
    org.bank_name = bank_name or None
    org.bank_account_name = bank_account_name or None
    org.bank_account_number = bank_account_number or None
    await db.commit()

    return RedirectResponse("/admin/settings?saved=1", status_code=303)


@router.post("/settings/email")
async def update_settings_email(
    request: Request,
    email_provider: str = Form(""),
    brevo_from_email: str = Form(""),
    arkesel_sender_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    # Blast-radius justifies gating this behind superadmin, same as the
    # payment section above -- a wrong flip here silently breaks every
    # outbound email/SMS in the app (this is exactly the failure class the
    # Phase 0 hotfix was written to catch after it happened for real).
    try:
        await require_superadmin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)
    except Forbidden as e:
        return RedirectResponse(f"/admin/settings?error={quote(e.message)}", status_code=303)

    org = await db.get(OrgSettings, 1)
    if org is None:
        org = OrgSettings(id=1)
        db.add(org)

    org.email_provider = email_provider.strip().lower() or None
    org.brevo_from_email = brevo_from_email.strip() or None
    org.arkesel_sender_id = arkesel_sender_id.strip() or None
    await db.commit()

    # Refresh the in-process cache immediately so the next send actually uses
    # the new value -- no redeploy, no restart.
    await org_settings_cache.load_cache(db)

    return RedirectResponse("/admin/settings?saved=1", status_code=303)


@router.post("/settings/dues")
async def update_settings_dues(
    request: Request,
    dues_amount_ghs: str = Form(""),
    dues_amount_level_100: str = Form(""),
    dues_amount_continuing: str = Form(""),
    dues_amount_final_year: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    # Routine per-semester adjustment -- admin-editable, not superadmin-gated.
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    def _parse_int(value: str) -> int | None:
        value = value.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    org = await db.get(OrgSettings, 1)
    if org is None:
        org = OrgSettings(id=1)
        db.add(org)

    org.dues_amount_ghs = _parse_int(dues_amount_ghs)
    org.dues_amount_level_100 = _parse_int(dues_amount_level_100)
    org.dues_amount_continuing = _parse_int(dues_amount_continuing)
    org.dues_amount_final_year = _parse_int(dues_amount_final_year)
    await db.commit()

    await org_settings_cache.load_cache(db)

    return RedirectResponse("/admin/settings?saved=1", status_code=303)
