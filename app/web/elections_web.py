import base64
import math
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps_web import (
    Forbidden,
    PageRedirect,
    require_admin,
    require_member,
    require_superadmin,
)
from app.core.templates import templates
from app.models.models import ELECTION_STATUSES, Candidate, Election, Position, User, Vote, Voter
from app.services import elections, member_provisioning, resend_client
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

@admin_router.post("/{election_id}/register/upload", name="admin_election_register_upload")
async def register_upload(
    election_id: int,
    request: Request,
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

    send_emails = not bool(skip_emails)
    result = await elections.import_register(
        db, election, rows, admin, base_url=str(request.base_url), send_emails=send_emails
    )

    # Built here (not stored) and offered as an immediate one-time download --
    # same "shown once, never persisted" handling as the admin password-reveal
    # flow elsewhere in this file, just as a file instead of on-screen text.
    credentials_b64 = None
    if not send_emails and result.credentials:
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
