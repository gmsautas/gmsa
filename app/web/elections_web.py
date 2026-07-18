import asyncio
import base64
import io
import math
import re
from datetime import datetime
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal, get_db
from app.core.deps_web import (
    Forbidden,
    PageRedirect,
    _user_from_cookie,
    require_admin,
    require_member,
    require_superadmin,
)
from app.core.templates import templates
from app.models.models import (
    ELECTION_STATUSES,
    Candidate,
    Election,
    OrgSettings,
    Position,
    User,
    Vote,
    Voter,
)
from app.services import elections, import_jobs, member_provisioning, resend_client
from app.services import storage
from app.services.elections import ElectionError

admin_router = APIRouter(tags=["admin-elections"])
member_router = APIRouter(tags=["web-member-elections"])


# ─────────────────────────────────────────────
# ADMIN — Elections list & detail
# ─────────────────────────────────────────────

@admin_router.get("", name="admin_elections_list")
async def elections_list(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    result = await db.execute(select(Election).order_by(Election.created_at.desc()))
    all_elections = result.scalars().all()
    for e in all_elections:
        e.display_status = elections.effective_status(e)

    return templates.TemplateResponse(
        request=request,
        name="admin/elections.html",
        context={
            "admin": admin,
            "active_nav": "elections",
            "elections": all_elections,
            "error": request.query_params.get("error"),
        },
    )


@admin_router.post("", name="admin_elections_create")
async def elections_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    is_test: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    is_test_election = bool(is_test)
    starts_dt = datetime.fromisoformat(starts_at)
    try:
        await elections.assert_year_available(db, starts_dt, is_test=is_test_election)
    except ElectionError as e:
        return RedirectResponse(f"/admin/elections?error={quote(str(e))}", status_code=303)

    election = Election(
        title=title,
        description=description or None,
        status="draft",
        starts_at=starts_dt,
        ends_at=datetime.fromisoformat(ends_at),
        is_test=is_test_election,
        created_by_id=admin.id,
    )
    db.add(election)
    await db.commit()
    await db.refresh(election)
    return RedirectResponse(f"/admin/elections/{election.id}", status_code=303)


@admin_router.get("/history", name="admin_elections_history")
async def elections_history(request: Request, db: AsyncSession = Depends(get_db)):
    """Read-only list of closed, non-test elections, linking into the existing
    results/turnout view (admin_election_results) -- no new tally logic here,
    just a dedicated place to browse past real elections instead of digging
    through the main (mixed draft/open/test) elections list."""
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    result = await db.execute(
        select(Election)
        .where(Election.status == "closed", Election.is_test.is_(False))
        .order_by(Election.ends_at.desc())
    )
    past_elections = result.scalars().all()
    for e in past_elections:
        e.display_status = elections.effective_status(e)

    return templates.TemplateResponse(
        request=request,
        name="admin/election_history.html",
        context={
            "admin": admin,
            "active_nav": "elections",
            "elections": past_elections,
        },
    )


@admin_router.get("/{election_id}", name="admin_election_detail")
async def election_detail(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    election.display_status = elections.effective_status(election)

    return templates.TemplateResponse(
        request=request,
        name="admin/election_settings.html",
        context={
            "admin": admin,
            "active_nav": "elections",
            "active_tab": "settings",
            "election": election,
            "election_statuses": ELECTION_STATUSES,
            "error": request.query_params.get("error"),
        },
    )


@admin_router.get("/{election_id}/ballot", name="admin_election_ballot")
async def election_ballot(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(
        Election,
        election_id,
        options=[selectinload(Election.positions).selectinload(Position.candidates)],
    )
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    election.display_status = elections.effective_status(election)

    return templates.TemplateResponse(
        request=request,
        name="admin/election_ballot.html",
        context={
            "admin": admin,
            "active_nav": "elections",
            "active_tab": "ballot",
            "election": election,
            "error": request.query_params.get("error"),
        },
    )


@admin_router.post("/{election_id}", name="admin_election_update")
async def election_update(
    election_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    status: str = Form(...),
    auto_publish: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    starts_dt = datetime.fromisoformat(starts_at)
    try:
        await elections.assert_year_available(
            db, starts_dt, exclude_election_id=election_id, is_test=election.is_test
        )
    except ElectionError as e:
        return RedirectResponse(
            f"/admin/elections/{election_id}?error={quote(str(e))}", status_code=303
        )

    if status in ELECTION_STATUSES:
        election.status = status
    election.title = title
    election.description = description or None
    election.starts_at = starts_dt
    election.ends_at = datetime.fromisoformat(ends_at)
    election.auto_publish = bool(auto_publish)
    await db.commit()
    return RedirectResponse(f"/admin/elections/{election_id}", status_code=303)


@admin_router.post("/{election_id}/delete", name="admin_election_delete")
async def election_delete(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        await require_superadmin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)
    except Forbidden as e:
        return RedirectResponse(
            f"/admin/elections/{election_id}?error={quote(e.message)}", status_code=303
        )

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    try:
        elections.assert_election_deletable(election)
    except ElectionError as e:
        return RedirectResponse(
            f"/admin/elections/{election_id}?error={quote(str(e))}", status_code=303
        )

    await db.delete(election)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            f"/admin/elections/{election_id}?error="
            + quote("Cannot delete an election that already has votes recorded."),
            status_code=303,
        )
    return RedirectResponse("/admin/elections", status_code=303)


def _safe_tab_redirect(next_path: str | None, election_id: int) -> str:
    """Send the admin back to whichever election tab they clicked pause/resume
    from, falling back to the settings tab if `next` is missing or forged."""
    prefix = f"/admin/elections/{election_id}"
    if next_path and (next_path == prefix or next_path.startswith(prefix + "/")):
        return next_path
    return prefix


@admin_router.post("/{election_id}/pause", name="admin_election_pause")
async def election_pause(
    election_id: int,
    request: Request,
    next: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    destination = _safe_tab_redirect(next, election_id)
    try:
        elections.pause_election(election)
        await db.commit()
    except ElectionError as e:
        return RedirectResponse(f"{destination}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(destination, status_code=303)


@admin_router.post("/{election_id}/resume", name="admin_election_resume")
async def election_resume(
    election_id: int,
    request: Request,
    next: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    destination = _safe_tab_redirect(next, election_id)
    try:
        elections.resume_election(election)
        await db.commit()
    except ElectionError as e:
        return RedirectResponse(f"{destination}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(destination, status_code=303)


# ─────────────────────────────────────────────
# ADMIN — Positions & candidates
# ─────────────────────────────────────────────

@admin_router.post("/{election_id}/positions", name="admin_election_position_create")
async def position_create(
    election_id: int,
    request: Request,
    title: str = Form(...),
    order_index: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)
    try:
        elections.assert_ballot_editable(election)
    except ElectionError as e:
        return RedirectResponse(
            f"/admin/elections/{election_id}/ballot?error={quote(str(e))}", status_code=303
        )

    db.add(Position(election_id=election_id, title=title, order_index=order_index))
    await db.commit()
    return RedirectResponse(f"/admin/elections/{election_id}/ballot", status_code=303)


@admin_router.post("/positions/{position_id}/delete", name="admin_election_position_delete")
async def position_delete(position_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    position = await db.get(Position, position_id)
    if position is None:
        return RedirectResponse("/admin/elections", status_code=303)
    election_id = position.election_id

    election = await db.get(Election, election_id)
    try:
        elections.assert_ballot_editable(election)
    except ElectionError as e:
        return RedirectResponse(
            f"/admin/elections/{election_id}/ballot?error={quote(str(e))}", status_code=303
        )

    await db.delete(position)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            f"/admin/elections/{election_id}/ballot?error="
            + quote("Cannot delete a position that already has votes recorded against it."),
            status_code=303,
        )
    return RedirectResponse(f"/admin/elections/{election_id}/ballot", status_code=303)


@admin_router.get("/members/search", name="admin_election_member_search")
async def candidate_member_search(request: Request, db: AsyncSession = Depends(get_db)):
    """Small JSON lookup backing the "link to an existing member" candidate
    picker -- searches by name or email, same case-insensitive `ilike`
    pattern as the admin members list (app.web.admin_web.members_list)."""
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return JSONResponse({"results": []})

    stmt = (
        select(User)
        .where(User.name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
        .order_by(User.name)
        .limit(15)
    )
    members = (await db.execute(stmt)).scalars().all()
    return JSONResponse(
        {
            "results": [
                {
                    "id": m.id,
                    "name": m.name,
                    "email": m.email,
                    "photo_url": m.profile_picture_url,
                }
                for m in members
            ]
        }
    )


@admin_router.post("/positions/{position_id}/candidates", name="admin_election_candidate_create")
async def candidate_create(
    position_id: int,
    request: Request,
    name: str = Form(...),
    bio: str = Form(""),
    order_index: int = Form(0),
    user_id: int | None = Form(None),
    photo_url: str = Form(""),
    photo: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    position = await db.get(Position, position_id)
    if position is None:
        return RedirectResponse("/admin/elections", status_code=303)

    election = await db.get(Election, position.election_id)
    try:
        elections.assert_ballot_editable(election)
    except ElectionError as e:
        return RedirectResponse(
            f"/admin/elections/{position.election_id}/ballot?error={quote(str(e))}", status_code=303
        )

    linked_user_id = None
    if user_id:
        linked_user = await db.get(User, user_id)
        if linked_user is None:
            return RedirectResponse(
                f"/admin/elections/{position.election_id}/ballot?error="
                + quote("Selected member could not be found."),
                status_code=303,
            )
        linked_user_id = linked_user.id

    # A freshly uploaded file always wins; otherwise fall back to the
    # (editable) photo_url the admin pre-filled/typed from the member picker.
    final_photo_url = photo_url.strip() or None
    if photo is not None and photo.filename:
        try:
            final_photo_url, _ = await storage.save_upload(photo, "candidates")
        except storage.UploadError as err:
            return RedirectResponse(
                f"/admin/elections/{position.election_id}/ballot?error={quote(str(err))}",
                status_code=303,
            )

    db.add(
        Candidate(
            position_id=position_id,
            user_id=linked_user_id,
            name=name,
            bio=bio or None,
            photo_url=final_photo_url,
            order_index=order_index,
        )
    )
    await db.commit()
    return RedirectResponse(f"/admin/elections/{position.election_id}/ballot", status_code=303)


@admin_router.post("/candidates/{candidate_id}/delete", name="admin_election_candidate_delete")
async def candidate_delete(candidate_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        return RedirectResponse("/admin/elections", status_code=303)
    position = await db.get(Position, candidate.position_id)

    election = await db.get(Election, position.election_id)
    try:
        elections.assert_ballot_editable(election)
    except ElectionError as e:
        return RedirectResponse(
            f"/admin/elections/{position.election_id}/ballot?error={quote(str(e))}", status_code=303
        )

    await db.delete(candidate)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            f"/admin/elections/{position.election_id}/ballot?error="
            + quote("Cannot remove a candidate who already has votes recorded."),
            status_code=303,
        )
    return RedirectResponse(f"/admin/elections/{position.election_id}/ballot", status_code=303)


# ─────────────────────────────────────────────
# ADMIN — Register import
# ─────────────────────────────────────────────

async def _run_register_import_job(
    *,
    job_id: str,
    election_id: int,
    rows: list[dict],
    admin_id: int,
    base_url: str,
    send_emails: bool,
) -> None:
    """Runs the actual (slow -- one commit and, usually, one awaited email
    send per row) import off the request/response cycle; see register_upload,
    which schedules this via BackgroundTasks instead of awaiting it inline.

    Opens its OWN session rather than reusing the route's `db` -- FastAPI only
    runs background tasks after the response has been sent, and the request's
    `Depends(get_db)` session is already closed by then. `election`/`admin`
    are therefore re-fetched by id here too: ORM objects loaded on one
    AsyncSession can't be carried over into another.

    Wrapped in a broad try/except so any unexpected error still marks the job
    "failed" instead of leaving it stuck at "running" forever with no way for
    the admin to tell it died.
    """
    try:
        async with AsyncSessionLocal() as db:
            election = await db.get(Election, election_id)
            admin = await db.get(User, admin_id)
            if election is None or admin is None:
                import_jobs.fail_job(job_id, "Election or admin account no longer exists.")
                return

            def on_row(index: int, total: int, _info: dict) -> None:
                import_jobs.update_progress(job_id, index)

            result = await elections.import_register(
                db,
                election,
                rows,
                admin,
                base_url=base_url,
                send_emails=send_emails,
                on_row=on_row,
            )
            import_jobs.complete_job(job_id, result)
    except Exception as err:  # noqa: BLE001 -- must never leave the job stuck at "running"
        import_jobs.fail_job(job_id, str(err))


@admin_router.post("/{election_id}/register/upload", name="admin_election_register_upload")
async def register_upload(
    election_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    skip_emails: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    contents = await file.read()
    try:
        rows = member_provisioning.parse_register_file(file.filename or "", contents)
    except member_provisioning.RegisterFileError as err:
        return RedirectResponse(
            f"/admin/elections/{election_id}/voters?error={quote(str(err))}", status_code=303
        )

    # Parsing the file is fast (no I/O beyond the already-read upload) and
    # stays inline; only the row loop below -- one commit and, usually, one
    # awaited email send per row -- is slow enough to risk outrunning the
    # hosting platform's reverse-proxy request timeout on a large register,
    # so that part alone moves to a background task (see
    # _run_register_import_job). The admin is redirected to a status page
    # immediately instead of waiting on the response.
    send_emails = not bool(skip_emails)
    job_id = import_jobs.create_job("register_import")
    import_jobs.set_total(job_id, len(rows))
    background_tasks.add_task(
        _run_register_import_job,
        job_id=job_id,
        election_id=election_id,
        rows=rows,
        admin_id=admin.id,
        base_url=str(request.base_url),
        send_emails=send_emails,
    )
    return RedirectResponse(
        f"/admin/elections/{election_id}/register/import-jobs/{job_id}", status_code=303
    )


@admin_router.get(
    "/{election_id}/register/import-jobs/{job_id}", name="admin_election_register_import_job_status"
)
async def register_import_job_status(
    election_id: int, job_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    job = import_jobs.get_job(job_id)
    voters_url = f"/admin/elections/{election_id}/voters"
    if job is None:
        return RedirectResponse(
            f"{voters_url}?error="
            + quote("This import job's progress is no longer available (it may have finished a "
                    "while ago, or the server restarted) — check the voters list to see what came through."),
            status_code=303,
        )

    if job["status"] == "completed":
        result = job["result"]
        # Rebuilt here, not stored on the job -- same "generated only for
        # this page view, never persisted" handling register_upload used to
        # do inline. Only ever non-empty when the import ran with
        # send_emails=False (see RegisterImportResult.credentials).
        credentials_b64 = None
        if result.credentials:
            workbook_bytes = member_provisioning.build_credentials_workbook(
                result.credentials, include_voter_token=True
            )
            credentials_b64 = base64.b64encode(workbook_bytes).decode()

        return templates.TemplateResponse(
            request=request,
            name="admin/election_register_result.html",
            context={
                "admin": admin,
                "active_nav": "elections",
                "election": election,
                "result": result,
                "credentials_b64": credentials_b64,
            },
        )

    return templates.TemplateResponse(
        request=request,
        name="admin/import_job_status.html",
        context={
            "admin": admin,
            "active_nav": "elections",
            "job": job,
            "back_url": voters_url,
        },
    )


# ─────────────────────────────────────────────
# ADMIN — Voters & nullification
# ─────────────────────────────────────────────

DEFAULT_VOTERS_PER_PAGE = 50
MAX_VOTERS_PER_PAGE = 200


async def _voters_list_context(
    db: AsyncSession,
    *,
    election_id: int,
    admin: User,
    error: str | None,
    revealed: dict | None = None,
    page: int = 1,
    per_page: int = DEFAULT_VOTERS_PER_PAGE,
) -> dict | None:
    """Real page/offset pagination -- the voter list for a single election can
    run into the thousands (a full member register), so this must never load
    every row into memory/DOM the way the old `.scalars().all()` did.
    `voters_count`/`voted_count` are still election-wide totals (for the tab
    label and turnout math), computed with separate COUNT queries rather than
    len() on the page slice."""
    election = await db.get(Election, election_id)
    if election is None:
        return None

    page = max(page, 1)
    per_page = min(max(per_page, 1), MAX_VOTERS_PER_PAGE)

    voters_count = (
        await db.execute(select(func.count(Voter.id)).where(Voter.election_id == election_id))
    ).scalar() or 0
    voted_count = (
        await db.execute(
            select(func.count(Voter.id)).where(
                Voter.election_id == election_id, Voter.has_voted.is_(True)
            )
        )
    ).scalar() or 0

    total_pages = max(1, math.ceil(voters_count / per_page)) if voters_count else 1
    page = min(page, total_pages)

    result = await db.execute(
        select(Voter)
        .where(Voter.election_id == election_id)
        .options(selectinload(Voter.user))
        .order_by(Voter.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    page_voters = result.scalars().all()

    election.display_status = elections.effective_status(election)

    return {
        "admin": admin,
        "active_nav": "elections",
        "active_tab": "voters",
        "election": election,
        "voters": page_voters,
        "voters_count": voters_count,
        "voted_count": voted_count,
        "error": error,
        "revealed": revealed,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


@admin_router.get("/{election_id}/voters", name="admin_election_voters")
async def voters_list(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    try:
        page = int(request.query_params.get("page", 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.query_params.get("per_page", DEFAULT_VOTERS_PER_PAGE))
    except ValueError:
        per_page = DEFAULT_VOTERS_PER_PAGE

    context = await _voters_list_context(
        db,
        election_id=election_id,
        admin=admin,
        error=request.query_params.get("error"),
        page=page,
        per_page=per_page,
    )
    if context is None:
        return RedirectResponse("/admin/elections", status_code=303)

    return templates.TemplateResponse(
        request=request, name="admin/election_voters.html", context=context
    )


@admin_router.post("/voters/{voter_id}/reset-password", name="admin_election_voter_reset_password")
async def voter_reset_password(
    voter_id: int,
    request: Request,
    election_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Issues a brand-new temp password and shows it once, directly on this
    page, for an admin to relay to the voter by phone/in person/WhatsApp --
    for when email delivery can't be trusted at all rather than only being
    slow. Never puts the plaintext password in a URL/redirect/log."""
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    voter = await db.get(Voter, voter_id)
    if voter is None or voter.election_id != election_id:
        return RedirectResponse(f"/admin/elections/{election_id}/voters", status_code=303)

    user = await db.get(User, voter.user_id)
    temp_password, email_sent = await member_provisioning.reset_password_for_admin_reveal(db, user)

    context = await _voters_list_context(
        db,
        election_id=election_id,
        admin=admin,
        error=None,
        revealed={"user": user, "password": temp_password, "email_sent": email_sent},
    )
    if context is None:
        return RedirectResponse("/admin/elections", status_code=303)

    return templates.TemplateResponse(
        request=request, name="admin/election_voters.html", context=context
    )


@admin_router.post("/voters/{voter_id}/nullify", name="admin_election_voter_nullify")
async def voter_nullify(
    voter_id: int,
    request: Request,
    election_id: int = Form(...),
    reason: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        admin = await require_superadmin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)
    except Forbidden as e:
        return RedirectResponse(
            f"/admin/elections/{election_id}/voters?error={quote(e.message)}", status_code=303
        )

    try:
        await elections.nullify_vote(
            db, admin=admin, voter_id=voter_id, election_id=election_id, reason=reason
        )
    except ElectionError as e:
        return RedirectResponse(
            f"/admin/elections/{election_id}/voters?error={quote(str(e))}", status_code=303
        )

    return RedirectResponse(f"/admin/elections/{election_id}/voters", status_code=303)


@admin_router.post("/voters/{voter_id}/resend-token", name="admin_election_voter_resend_token")
async def voter_resend_token(
    voter_id: int,
    request: Request,
    election_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    voter = await db.get(Voter, voter_id)
    if voter is None or voter.election_id != election_id:
        return RedirectResponse(f"/admin/elections/{election_id}/voters", status_code=303)

    try:
        await elections.resend_token(db, voter=voter)
    except resend_client.ResendError as err:
        return RedirectResponse(
            f"/admin/elections/{election_id}/voters?error="
            + quote(f"New token was issued but the email failed to send: {err}"),
            status_code=303,
        )
    return RedirectResponse(f"/admin/elections/{election_id}/voters", status_code=303)


# Bulk actions requiring a super admin -- mirrors the single-row nullify gate
# above (admin_election_voter_nullify) and admin_web.py's _SUPERADMIN_BULK_ACTIONS.
_SUPERADMIN_VOTER_BULK_ACTIONS = {"nullify"}


@admin_router.post("/{election_id}/voters/bulk", name="admin_election_voters_bulk")
async def voters_bulk_action(
    election_id: int,
    request: Request,
    voter_ids: str = Form(...),
    action: str = Form(...),
    reason: str = Form(""),
    page: int = Form(1),
    db: AsyncSession = Depends(get_db),
):
    """Bulk variants of the existing single-row reset-password / nullify /
    resend-token endpoints above, reusing the same service functions in a
    per-row loop. Follows the exact commit-per-row, partial-failure-tolerant
    pattern already used by elections.import_register: each row's outcome is
    independent, one bad row (e.g. a vote that's no longer nullifiable) never
    aborts the rest of the batch, and failures are summarized back to the
    admin instead of silently dropped."""
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    redirect_base = f"/admin/elections/{election_id}/voters?page={page}"

    if action in _SUPERADMIN_VOTER_BULK_ACTIONS:
        try:
            admin = await require_superadmin(request, db)
        except Forbidden as e:
            return RedirectResponse(f"{redirect_base}&error={quote(e.message)}", status_code=303)

    if action not in ("reset_password", "nullify", "resend_token"):
        return RedirectResponse(
            f"{redirect_base}&error={quote('Unknown bulk action')}", status_code=303
        )
    if action == "nullify" and not reason.strip():
        return RedirectResponse(
            f"{redirect_base}&error="
            + quote("A reason is required to bulk-nullify votes."),
            status_code=303,
        )

    try:
        ids = [int(x) for x in voter_ids.split(",") if x.strip()]
    except ValueError:
        ids = []
    if not ids:
        return RedirectResponse(redirect_base, status_code=303)

    voters = (
        (
            await db.execute(
                select(Voter)
                .where(Voter.id.in_(ids), Voter.election_id == election_id)
                .options(selectinload(Voter.user))
            )
        )
        .scalars()
        .all()
    )

    succeeded = 0
    failures: list[str] = []
    for voter in voters:
        label = voter.user.name if voter.user else f"voter #{voter.id}"
        try:
            if action == "reset_password":
                user = await db.get(User, voter.user_id)
                if user is None:
                    raise ElectionError("No linked account for this voter")
                await member_provisioning.reset_password_for_admin_reveal(db, user)
            elif action == "nullify":
                await elections.nullify_vote(
                    db, admin=admin, voter_id=voter.id, election_id=election_id, reason=reason
                )
            elif action == "resend_token":
                await elections.resend_token(db, voter=voter)
            succeeded += 1
        except (ElectionError, resend_client.ResendError) as err:
            await db.rollback()
            failures.append(f"{label}: {err}")

    error = None
    if failures:
        shown = failures[:5]
        error = f"{succeeded} succeeded, {len(failures)} failed — " + "; ".join(shown)
        if len(failures) > len(shown):
            error += f" (+{len(failures) - len(shown)} more)"

    redirect = redirect_base
    if error:
        redirect += f"&error={quote(error)}"
    return RedirectResponse(redirect, status_code=303)


# ─────────────────────────────────────────────
# ADMIN — Results
# ─────────────────────────────────────────────

@admin_router.get("/{election_id}/results", name="admin_election_results")
async def election_results(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        admin = await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    voters_count = (
        await db.execute(select(func.count(Voter.id)).where(Voter.election_id == election_id))
    ).scalar() or 0
    voted_count = (
        await db.execute(
            select(func.count(Voter.id)).where(
                Voter.election_id == election_id, Voter.has_voted.is_(True)
            )
        )
    ).scalar() or 0
    election.display_status = elections.effective_status(election)
    results = await elections.compute_results(db, election_id)

    return templates.TemplateResponse(
        request=request,
        name="admin/election_results.html",
        context={
            "admin": admin,
            "active_nav": "elections",
            "active_tab": "results",
            "election": election,
            "results": results,
            "voters_count": voters_count,
            "voted_count": voted_count,
            "turnout_percent": round((voted_count / voters_count * 100), 1) if voters_count else 0.0,
        },
    )


@admin_router.get("/{election_id}/results/download", name="admin_election_results_download")
async def election_results_download(
    election_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    try:
        await require_admin(request, db)
    except PageRedirect as e:
        return RedirectResponse(e.url, status_code=302)

    election = await db.get(Election, election_id)
    if election is None:
        return RedirectResponse("/admin/elections", status_code=303)

    voters_count = (
        await db.execute(select(func.count(Voter.id)).where(Voter.election_id == election_id))
    ).scalar() or 0
    voted_count = (
        await db.execute(
            select(func.count(Voter.id)).where(
                Voter.election_id == election_id, Voter.has_voted.is_(True)
            )
        )
    ).scalar() or 0
    results = await elections.compute_results(db, election_id)
    org = await db.get(OrgSettings, 1)

    pdf_bytes = _render_election_results_pdf(
        election=election,
        org=org,
        voters_count=voters_count,
        voted_count=voted_count,
        results=results,
    )
    safe_title = re.sub(r"[^A-Za-z0-9]+", "-", election.title).strip("-") or "Election"
    filename = f"GMSA-{safe_title}-Results.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _resolve_candidate_photo(photo_url: str | None):
    """Returns a reportlab ImageReader for a candidate's ballot photo, or None
    if there isn't one or it can't be loaded. Uploaded photos are served from
    our own /static/uploads/... and are read straight off disk; anything else
    (an admin-pasted external URL) is fetched over HTTP with a short timeout
    so one broken link can't hang report generation."""
    if not photo_url:
        return None
    from reportlab.lib.utils import ImageReader

    try:
        if photo_url.startswith("/static/"):
            path = storage.STATIC_DIR / photo_url[len("/static/") :]
            if not path.exists():
                return None
            return ImageReader(str(path))
        if photo_url.startswith(("http://", "https://")):
            import httpx

            resp = httpx.get(photo_url, timeout=3.0)
            resp.raise_for_status()
            return ImageReader(io.BytesIO(resp.content))
    except Exception:
        return None
    return None


def _render_election_results_pdf(
    *, election: Election, org, voters_count: int, voted_count: int, results: list[dict]
) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    forest = HexColor("#1E4F3A")  # --forest-700, matches the app's admin UI green
    ink = HexColor("#111827")
    gray = HexColor("#6B7280")
    green = HexColor("#15803D")
    line_gray = HexColor("#E5E7EB")
    zebra_fill = HexColor("#F3F4F6")
    white = HexColor("#FFFFFF")

    left = 20 * mm
    right = width - 20 * mm
    bottom_margin = 30 * mm

    org_name = (org.full_name if org else None) or "GMSA UTAS"

    is_closed = elections.effective_status(election) == "closed"
    turnout_percent = round((voted_count / voters_count * 100), 1) if voters_count else 0.0

    def draw_letterhead() -> float:
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
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(forest)
        c.drawString(text_x, top - 10.5 * mm, "GMSA ELECTORAL COMMISSION")

        c.setStrokeColor(forest)
        c.setLineWidth(1.2)
        c.line(left, top - 18 * mm, right, top - 18 * mm)

        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(width / 2, top - 25 * mm, f"ELECTION RESULTS — {election.title}".upper())
        c.setFont("Helvetica", 8)
        c.setFillColor(gray)
        status_label = "FINAL (CLOSED)" if is_closed else "LIVE / IN PROGRESS"
        c.drawString(left, top - 25 * mm - 4.5 * mm, f"Status: {status_label}")
        c.drawRightString(right, top - 25 * mm - 4.5 * mm, f"Generated {datetime.utcnow():%d %B %Y %H:%M} UTC")

        return top - 44 * mm

    def ensure_space(y: float, header_fn=None) -> float:
        """Starts a fresh page (with the letterhead and, if given, the current
        position's header repeated) if the next row would land inside the
        bottom margin -- reportlab's canvas has no built-in pagination."""
        if y < bottom_margin:
            c.showPage()
            y = draw_letterhead()
            if header_fn:
                y = header_fn(y, True)
        return y

    y = draw_letterhead()

    # ── Summary ──────────────────────────────────────────────────────────
    c.setFillColor(forest)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "SUMMARY")
    c.setStrokeColor(forest)
    c.setLineWidth(0.6)
    c.line(left, y - 2 * mm, right, y - 2 * mm)
    y -= 10 * mm

    c.setFont("Helvetica", 9)
    c.setFillColor(ink)
    col_w = (right - left) / 3
    c.drawString(left, y, f"Registered Voters: {voters_count}")
    c.drawString(left + col_w, y, f"Votes Cast: {voted_count}")
    c.drawString(left + 2 * col_w, y, f"Turnout: {turnout_percent}%")
    y -= 24 * mm

    # ── Per-position results, each rendered as a bordered table ────────────
    PHOTO = 9 * mm
    HEADER_H = 8 * mm
    ROW_H = 13 * mm

    col_photo_x = left
    col_photo_w = 16 * mm

    for r in results:
        position = r["position"]

        def draw_position_title(yy: float, continued: bool = False) -> float:
            c.setFillColor(forest)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(left, yy, position.title.upper() + (" (continued)" if continued else ""))
            c.setStrokeColor(forest)
            c.setLineWidth(0.6)
            c.line(left, yy - 2 * mm, right, yy - 2 * mm)
            return yy - 9 * mm

        def draw_photo_cell(img, row_bottom: float, candidate_name: str) -> None:
            photo_x = col_photo_x + (col_photo_w - PHOTO) / 2
            photo_y = row_bottom + (ROW_H - PHOTO) / 2
            if img is not None:
                c.saveState()
                try:
                    # Clip to a circle so a real ballot photo reads the same as
                    # the initials fallback below, matching the round avatars
                    # used everywhere else in the app's UI.
                    clip = c.beginPath()
                    clip.circle(photo_x + PHOTO / 2, photo_y + PHOTO / 2, PHOTO / 2)
                    c.clipPath(clip, stroke=0, fill=0)
                    c.drawImage(
                        img, photo_x, photo_y, width=PHOTO, height=PHOTO,
                        preserveAspectRatio=True, anchor="c", mask="auto",
                    )
                    return
                except Exception:
                    pass
                finally:
                    c.restoreState()
            c.setFillColor(HexColor("#D1D5DB"))
            c.circle(photo_x + PHOTO / 2, photo_y + PHOTO / 2, PHOTO / 2, fill=1, stroke=0)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 7.5)
            initials = "".join(w[0] for w in candidate_name.split()[:2]).upper() or "?"
            c.drawCentredString(photo_x + PHOTO / 2, photo_y + PHOTO / 2 - 2.6, initials)

        y = ensure_space(y)
        y = draw_position_title(y)

        if not r["contested"]:
            candidate = r["candidate"]
            yes, no, total = r["yes_votes"], r["no_votes"], r["total_votes"]
            if total > 0 and yes > no:
                outcome = "ELECTED" if is_closed else "PASSING"
            elif total > 0 and no > yes:
                outcome = "REJECTED" if is_closed else "FAILING"
            elif total > 0:
                outcome = "TIED"
            else:
                outcome = "NO VOTES"

            name_x = col_photo_x + col_photo_w
            yes_x = left + 90 * mm
            no_x = left + 122 * mm
            status_x = left + 148 * mm

            y = ensure_space(y, draw_position_title)
            c.setFillColor(forest)
            c.rect(left, y - HEADER_H, right - left, HEADER_H, fill=1, stroke=0)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 8)
            text_y = y - HEADER_H + 2.6 * mm
            c.drawString(name_x + 2 * mm, text_y, "CANDIDATE (UNCONTESTED)")
            c.drawString(yes_x, text_y, "YES")
            c.drawString(no_x, text_y, "NO")
            c.drawString(status_x, text_y, "RESULT")
            y -= HEADER_H

            row_bottom = y - ROW_H
            c.setFillColor(zebra_fill)
            c.rect(left, row_bottom, right - left, ROW_H, fill=1, stroke=0)
            c.setStrokeColor(line_gray)
            c.setLineWidth(0.4)
            c.rect(left, row_bottom, right - left, ROW_H, fill=0, stroke=1)
            for x in (name_x, yes_x - 2 * mm, no_x - 2 * mm, status_x - 2 * mm):
                c.line(x, row_bottom, x, y)

            draw_photo_cell(_resolve_candidate_photo(candidate.photo_url), row_bottom, candidate.name)

            text_mid_y = row_bottom + ROW_H / 2 - 1.2 * mm
            yes_pct = round((yes / total * 100), 1) if total else 0.0
            no_pct = round((no / total * 100), 1) if total else 0.0
            c.setFillColor(ink)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(name_x + 2 * mm, text_mid_y, candidate.name[:34])
            c.setFont("Helvetica", 9)
            c.drawString(yes_x, text_mid_y, f"{yes} ({yes_pct}%)")
            c.drawString(no_x, text_mid_y, f"{no} ({no_pct}%)")
            c.setFillColor(green if outcome in ("ELECTED", "PASSING") else gray)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(status_x, text_mid_y, outcome)
            y = row_bottom
        else:
            candidates = r["candidates"]
            winner_votes = candidates[0]["votes"] if candidates else 0

            name_x = col_photo_x + col_photo_w
            votes_x = left + 105 * mm
            pct_x = left + 130 * mm
            status_x = left + 150 * mm

            def draw_table_header(yy: float, continued: bool = False) -> float:
                yy = draw_position_title(yy, continued) if continued else yy
                c.setFillColor(forest)
                c.rect(left, yy - HEADER_H, right - left, HEADER_H, fill=1, stroke=0)
                c.setFillColor(white)
                c.setFont("Helvetica-Bold", 8)
                text_y = yy - HEADER_H + 2.6 * mm
                c.drawString(name_x + 2 * mm, text_y, "CANDIDATE")
                c.drawString(votes_x, text_y, "VOTES")
                c.drawString(pct_x, text_y, "%")
                c.drawString(status_x, text_y, "RESULT")
                return yy - HEADER_H

            y = ensure_space(y, draw_position_title)
            y = draw_table_header(y)

            for rank, cand_row in enumerate(candidates, start=1):
                y = ensure_space(y, draw_table_header)
                is_winner = cand_row["votes"] > 0 and cand_row["votes"] == winner_votes
                row_bottom = y - ROW_H

                if rank % 2 == 0:
                    c.setFillColor(zebra_fill)
                    c.rect(left, row_bottom, right - left, ROW_H, fill=1, stroke=0)
                c.setStrokeColor(line_gray)
                c.setLineWidth(0.4)
                c.rect(left, row_bottom, right - left, ROW_H, fill=0, stroke=1)
                for x in (name_x, votes_x - 2 * mm, pct_x - 2 * mm, status_x - 2 * mm):
                    c.line(x, row_bottom, x, y)

                draw_photo_cell(
                    _resolve_candidate_photo(cand_row["candidate"].photo_url),
                    row_bottom,
                    cand_row["candidate"].name,
                )

                text_mid_y = row_bottom + ROW_H / 2 - 1.2 * mm
                c.setFillColor(ink)
                c.setFont("Helvetica-Bold" if is_winner else "Helvetica", 9)
                c.drawString(name_x + 2 * mm, text_mid_y, f"{rank}. {cand_row['candidate'].name}"[:36])
                c.setFont("Helvetica", 9)
                c.drawString(votes_x, text_mid_y, str(cand_row["votes"]))
                c.drawString(pct_x, text_mid_y, f"{cand_row['percent']}%")
                if is_winner:
                    c.setFillColor(green if is_closed else gray)
                    c.setFont("Helvetica-Bold", 8)
                    c.drawString(status_x, text_mid_y, "WINNER" if is_closed else "LEADING")
                y = row_bottom

            y -= 5 * mm
            y = ensure_space(y, draw_position_title)
            c.setFont("Helvetica", 8)
            c.setFillColor(gray)
            c.drawString(left, y, f"Total votes cast for this position: {r['total_votes']}")
            y -= 6 * mm

        y -= 12 * mm

    c.showPage()
    c.save()
    return buffer.getvalue()


# Push interval for the results WebSocket below -- short enough that a vote
# cast during a close election night shows up within a few seconds, long
# enough that an open results tab left running doesn't hammer the DB.
RESULTS_LIVE_INTERVAL_SECONDS = 3


async def _election_results_snapshot(election_id: int) -> dict | None:
    """One JSON-safe results payload, in its own short-lived session -- keeps
    each poll tick independent so a slow client doesn't hold a DB connection
    open between pushes. Returns None if the election no longer exists."""
    async with AsyncSessionLocal() as db:
        election = await db.get(Election, election_id)
        if election is None:
            return None
        voters_count = (
            await db.execute(select(func.count(Voter.id)).where(Voter.election_id == election_id))
        ).scalar() or 0
        voted_count = (
            await db.execute(
                select(func.count(Voter.id)).where(
                    Voter.election_id == election_id, Voter.has_voted.is_(True)
                )
            )
        ).scalar() or 0
        results = await elections.compute_results(db, election_id)
        return elections.serialize_results_payload(
            voters_count=voters_count, voted_count=voted_count, results=results
        )


@admin_router.websocket("/{election_id}/results/ws")
async def election_results_live(websocket: WebSocket, election_id: int):
    """Pushes a fresh results snapshot to the open Results page every few
    seconds so turnout and tallies update without a manual refresh -- votes
    can land right up to an election's close, and an admin often has this
    tab open while that happens. Polls the DB itself rather than subscribing
    to a broadcast on vote-cast, since that would need a process-wide pub/sub
    layer for what's a low-traffic, small-association election; only sends a
    frame when the payload actually changed, so an idle socket is silent."""
    async with AsyncSessionLocal() as db:
        admin = await _user_from_cookie(websocket, db)
    if admin is None or admin.role not in ("admin", "superadmin"):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    last_payload: dict | None = None
    try:
        while True:
            payload = await _election_results_snapshot(election_id)
            if payload is None:
                await websocket.close(code=4404)
                return
            if payload != last_payload:
                await websocket.send_json(payload)
                last_payload = payload
            await asyncio.sleep(RESULTS_LIVE_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return


# ─────────────────────────────────────────────
# MEMBER — Eligible elections & ballot
# ─────────────────────────────────────────────

@member_router.get("", name="member_elections_list")
async def member_elections_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    result = await db.execute(
        select(Voter, Election)
        .join(Election, Voter.election_id == Election.id)
        .where(Voter.user_id == user.id)
        .order_by(Election.starts_at.desc())
    )
    rows = result.all()
    for _voter, election in rows:
        election.display_status = elections.effective_status(election)

    return templates.TemplateResponse(
        request=request,
        name="member/elections.html",
        context={
            "user": user,
            "active_nav": "elections",
            "rows": rows,
            "voted": request.query_params.get("voted"),
            "error": request.query_params.get("error"),
        },
    )


@member_router.get("/{election_id}/ballot", name="member_election_ballot")
async def member_ballot_page(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    voter = (
        await db.execute(
            select(Voter).where(Voter.election_id == election_id, Voter.user_id == user.id)
        )
    ).scalar_one_or_none()
    if voter is None:
        return RedirectResponse("/member/elections?error=not-eligible", status_code=303)

    election = await db.get(
        Election,
        election_id,
        options=[selectinload(Election.positions).selectinload(Position.candidates)],
    )
    if election is None or elections.effective_status(election) != "open":
        return RedirectResponse("/member/elections?error=not-open", status_code=303)
    if voter.has_voted:
        return RedirectResponse("/member/elections?error=already-voted", status_code=303)

    sorted_positions = sorted(election.positions, key=lambda p: p.order_index)
    positions_data = [
        {
            "id": p.id,
            "title": p.title,
            "candidates": [
                {"id": c.id, "name": c.name}
                for c in sorted(p.candidates, key=lambda c: c.order_index)
            ],
        }
        for p in sorted_positions
    ]

    return templates.TemplateResponse(
        request=request,
        name="member/election_ballot.html",
        context={
            "user": user,
            "active_nav": "elections",
            "election": election,
            "positions_data": positions_data,
            "error": request.query_params.get("error"),
        },
    )


@member_router.post("/{election_id}/ballot/verify", name="member_election_ballot_verify")
async def member_ballot_verify(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    form = await request.form()
    student_id = str(form.get("student_id") or "").strip()
    token = str(form.get("voter_token") or "").strip()

    try:
        await elections.verify_voter_credentials(
            db, member=user, election_id=election_id, student_id=student_id, token=token
        )
    except ElectionError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@member_router.post("/{election_id}/ballot", name="member_election_ballot_submit")
async def member_ballot_submit(election_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_member(request, db)

    form = await request.form()
    student_id = str(form.get("student_id") or "").strip()
    token = str(form.get("voter_token") or "").strip()

    positions = (
        (await db.execute(select(Position).where(Position.election_id == election_id)))
        .scalars()
        .all()
    )
    selections: dict[int, str] = {}
    for position in positions:
        value = form.get(f"position_{position.id}")
        if value:
            selections[position.id] = str(value)

    try:
        await elections.cast_vote(
            db,
            member=user,
            election_id=election_id,
            student_id=student_id,
            token=token,
            selections=selections,
        )
    except ElectionError as e:
        return RedirectResponse(
            f"/member/elections/{election_id}/ballot?error={quote(str(e))}", status_code=303
        )

    return RedirectResponse("/member/elections?voted=1", status_code=303)
