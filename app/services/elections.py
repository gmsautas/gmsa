"""Election module: register import, voter token issuance, ballot casting,
and vote nullification/reissue.
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import extract, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.models import Election, Position, User, Vote, Voter, VoterToken
from app.services import academic, email_failures, member_provisioning, resend_client


class ElectionError(Exception):
    pass


def effective_status(election: Election) -> str:
    """The status to actually gate/display on, layering the schedule and a
    manual pause on top of the admin-set `status` column. `paused` and a
    manually-set `closed` always win over the schedule; `auto_publish=False`
    opts an election out of automatic start/end entirely.
    """
    if election.status == "paused":
        return "paused"
    if not election.auto_publish:
        return election.status
    now = datetime.utcnow()
    if now < election.starts_at:
        return "draft"
    if now > election.ends_at:
        return "closed"
    return "open"


def pause_election(election: Election) -> None:
    if election.status != "open":
        raise ElectionError("Only an open election can be paused")
    election.status = "paused"


def resume_election(election: Election) -> None:
    if election.status != "paused":
        raise ElectionError("Only a paused election can be resumed")
    election.status = "open"


def assert_election_deletable(election: Election) -> None:
    """An election can only be deleted while it's still a draft — once it has
    ever been open, deleting it would erase a real voting record rather than
    just a mistaken setup. (A vote row referencing it, if any ever exists
    despite that, is still a hard DB foreign-key stop as a second backstop.)

    Test/sandbox elections (`is_test=True`) are exempt from this entirely —
    they exist specifically to be created, exercised (candidates, voters,
    votes) and torn down again, so deletion is allowed regardless of status.
    `Election.votes`'s cascade makes this safe at the DB level too."""
    if election.is_test:
        return
    if election.status != "draft":
        raise ElectionError(
            f"Only a draft election can be deleted — this one is {election.status}."
        )


def assert_ballot_editable(election: Election) -> None:
    """The ballot definition (positions & candidates) may only be changed while
    the election is still a draft. Once voting can happen — open, paused, or
    closed — the ballot is frozen so every voter faces the same choices."""
    status = effective_status(election)
    if status != "draft":
        raise ElectionError(
            "The ballot is locked once voting has started — this election is "
            f"currently {status}. Reset it to draft to change positions or candidates."
        )


async def assert_year_available(
    db: AsyncSession,
    starts_at: datetime,
    *,
    exclude_election_id: int | None = None,
    is_test: bool = False,
) -> None:
    """Only one non-test election may be scheduled per calendar year. Test/
    sandbox elections are exempt entirely -- a test election being created
    doesn't need a free year (skip the check outright), and an existing test
    election never counts as a clash against a real election being
    created/edited for that same year (filtered out of the query below)."""
    if is_test:
        return
    year = starts_at.year
    query = select(Election).where(
        extract("year", Election.starts_at) == year, Election.is_test.is_(False)
    )
    if exclude_election_id is not None:
        query = query.where(Election.id != exclude_election_id)
    clashing = (await db.execute(query)).scalars().first()
    if clashing is not None:
        raise ElectionError(
            f"An election already exists for {year} ({clashing.title}). "
            f"Only one election can run per year."
        )


# ── Token & temp-password generation ────────────────────────────────────────

# No 0/O/1/I/L — avoids characters voters could misread off an email on a phone.
_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_voter_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(6))


def hash_token(token: str) -> str:
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


# ── Token issuance & email ──────────────────────────────────────────────────

async def issue_voter_token(db: AsyncSession, voter: Voter) -> str:
    plaintext = generate_voter_token()
    db.add(
        VoterToken(
            voter_id=voter.id,
            token_hash=hash_token(plaintext),
            is_used=False,
            is_nullified=False,
        )
    )
    return plaintext


async def send_account_setup_and_voter_token_email(
    email: str, election: Election, account_setup_url: str, plaintext_token: str
) -> None:
    """One email covering both steps a brand-new voter needs, instead of the
    two separate messages (account-setup link, then voter token) they'd get
    otherwise -- less confusing, and halves the send volume for a fresh
    register import."""
    html = (
        f"<p>Assalamu alaikum,</p>"
        f"<p>An account has been created for you on the GMSA UTAS member portal "
        f"(<strong>{email}</strong>), and you're eligible to vote in "
        f"<strong>{election.title}</strong>.</p>"
        f"<p><strong>Step 1 — set your password:</strong></p>"
        f"<p style='margin:12px 0 28px'>"
        f"<a href='{account_setup_url}' "
        f"style='background:#0f5132;color:#ffffff;padding:12px 28px;border-radius:8px;"
        f"text-decoration:none;font-weight:bold;display:inline-block;font-family:sans-serif'>"
        f"Set My Password</a></p>"
        f"<p style='font-size:12px;color:#666'>Or paste this link into your browser: "
        f"<a href='{account_setup_url}'>{account_setup_url}</a><br>"
        f"This link is valid for {settings.password_reset_token_expire_minutes} minutes and can "
        f"only be used once.</p>"
        f"<p><strong>Step 2 — vote:</strong> once you've logged in, go to Elections and enter "
        f"your student ID and this voter token when prompted:</p>"
        f"<p style='font-size:1.2em;font-weight:bold;letter-spacing:1px'>{plaintext_token}</p>"
        f"<p>This token can only be used once.</p>"
    )
    await resend_client.send_email(
        to=[email],
        subject=f"Welcome to GMSA UTAS — set up your account & vote in {election.title}",
        html=html,
    )


async def send_voter_token_email(email: str, election: Election, plaintext_token: str) -> None:
    html = (
        f"<p>Assalamu alaikum,</p>"
        f"<p>You are eligible to vote in <strong>{election.title}</strong>.</p>"
        f"<p>Your voter token is:</p>"
        f"<p style='font-size:1.2em;font-weight:bold;letter-spacing:1px'>{plaintext_token}</p>"
        f"<p>To vote, log in to your member dashboard, go to Elections, and enter your "
        f"student ID and this token when prompted. This token can only be used once.</p>"
    )
    await resend_client.send_email(
        to=[email], subject=f"Your voter token — {election.title}", html=html
    )


async def send_vote_nullified_email(
    email: str, election: Election, plaintext_token: str, reason: str
) -> None:
    html = (
        f"<p>Assalamu alaikum,</p>"
        f"<p>Your previously recorded vote in <strong>{election.title}</strong> has been "
        f"invalidated by an election administrator and no longer counts.</p>"
        f"<p><strong>Reason given:</strong> {reason}</p>"
        f"<p>So that you are not disenfranchised, a new one-time voter token has been issued "
        f"to you:</p>"
        f"<p style='font-size:1.2em;font-weight:bold;letter-spacing:1px'>{plaintext_token}</p>"
        f"<p>Please log in to your member dashboard, go to Elections, and cast your vote again "
        f"using your student ID and this new token while voting is still open. If you believe "
        f"this was done in error, contact the election committee.</p>"
    )
    await resend_client.send_email(
        to=[email],
        subject=f"Your vote was invalidated — please re-vote — {election.title}",
        html=html,
    )


# ── Register import ─────────────────────────────────────────────────────────

@dataclass
class RegisterImportResult:
    created_users: int = 0
    linked_users: int = 0
    voters_created: int = 0
    skipped_duplicates: int = 0
    conflicts: list[dict] = field(default_factory=list)
    email_failures: list[str] = field(default_factory=list)


async def import_register(
    db: AsyncSession,
    election: Election,
    rows: list[dict],
    _admin: User,
    *,
    base_url: str,
    on_row: Callable[[int, int, dict], None] | None = None,
) -> RegisterImportResult:
    """`on_row`, if given, is called synchronously after each row finishes
    processing as `on_row(index, total, info)` (1-based index) -- lets a
    caller like scripts/local_gmail_import.py print live per-row progress
    instead of only a summary once every row is done. `info` always has
    "email" and "outcome" ("imported" | "skipped_duplicate" | "conflict")
    plus, when "outcome" is "imported": "created" (bool), "voter_created"
    (bool), "account_email_failed" (bool), "token_email_failed" (bool).

    Commits after every row rather than once at the end -- for a large
    register this runs as one long web request, and each row already sends
    real email(s) before the row finishes; committing per-row means a
    request that gets cut off partway (timeout, deploy, worker recycle)
    only loses the rows after the cutoff, never rolls back rows whose
    emails already went out.
    """
    result = RegisterImportResult()
    seen: set[tuple[str, str]] = set()
    total = len(rows)

    for index, row in enumerate(rows, start=1):
        student_id = str(row.get("student_id") or "").strip()
        email = str(row.get("email") or "").strip().lower()
        name = str(row.get("name") or "").strip()
        phone = member_provisioning.normalize_phone(str(row.get("phone") or ""))
        program = str(row.get("program") or "").strip()
        program_category = str(row.get("program_category") or "").strip().lower()

        if not student_id or not email:
            result.conflicts.append({"row": row, "reason": "Missing student_id or email"})
            if on_row:
                on_row(index, total, {"email": email, "outcome": "conflict"})
            continue

        key = (student_id, email)
        if key in seen:
            result.skipped_duplicates += 1
            if on_row:
                on_row(index, total, {"email": email, "outcome": "skipped_duplicate"})
            continue
        seen.add(key)

        try:
            # send_setup_email=False -- always defer. If this row also ends up
            # creating a voter (the common case), we want ONE combined email
            # (account setup + voter token) instead of two separate ones; see
            # below.
            user, created, _deferred, account_setup_url = await member_provisioning.find_or_create_member(
                db, student_id=student_id, email=email, name=name, base_url=base_url,
                send_setup_email=False,
            )
        except member_provisioning.ProvisioningConflict as err:
            result.conflicts.append({"row": row, "reason": str(err)})
            if on_row:
                on_row(index, total, {"email": email, "outcome": "conflict"})
            continue

        if created:
            result.created_users += 1
            if phone:
                user.phone = phone
            if program:
                user.program = program
            if program_category in academic.PROGRAM_CATEGORIES:
                user.program_category = program_category
                user.grad_year = academic.graduation_year(student_id, program_category)
        else:
            result.linked_users += 1

        existing_voter = (
            await db.execute(
                select(Voter).where(
                    Voter.election_id == election.id, Voter.user_id == user.id
                )
            )
        ).scalar_one_or_none()

        if existing_voter is not None:
            # `created` is always False here -- a brand-new user can't
            # already have a voter row for this election, so there's never a
            # deferred account-setup link left unsent in this branch.
            await db.commit()
            if on_row:
                on_row(
                    index,
                    total,
                    {
                        "email": email,
                        "outcome": "imported",
                        "created": created,
                        "voter_created": False,
                        "account_email_failed": False,
                        "token_email_failed": False,
                    },
                )
            continue

        voter = Voter(election_id=election.id, user_id=user.id, has_voted=False)
        db.add(voter)
        await db.flush()
        result.voters_created += 1

        plaintext_token = await issue_voter_token(db, voter)
        # Commit (not just flush) before sending -- the email contains this
        # token (and, for a new account, the setup link), so both must
        # already be durably saved before anything goes out, or an
        # interrupted process could leave a voter holding an emailed
        # token/link that got rolled back and no longer exists.
        await db.commit()
        email_failed = False
        try:
            if created:
                await send_account_setup_and_voter_token_email(
                    user.email, election, account_setup_url, plaintext_token
                )
            else:
                await send_voter_token_email(user.email, election, plaintext_token)
        except resend_client.ResendError as err:
            label = "welcome + voter token" if created else "voter token"
            purpose = "account_setup_and_voter_token" if created else "voter_token"
            result.email_failures.append(f"{label} email to {user.email}")
            email_failed = True
            await email_failures.record_failure(
                db, recipient=user.email, purpose=purpose, error=err
            )

        if on_row:
            on_row(
                index,
                total,
                {
                    "email": email,
                    "outcome": "imported",
                    "created": created,
                    "voter_created": True,
                    "account_email_failed": email_failed if created else False,
                    "token_email_failed": email_failed,
                },
            )

    await db.commit()
    return result


# ── Vote casting ─────────────────────────────────────────────────────────────

async def _resolve_voter_and_token(
    db: AsyncSession,
    *,
    member: User,
    election_id: int,
    student_id: str,
    token: str,
    for_update: bool = False,
) -> tuple[Voter, VoterToken]:
    election = await db.get(Election, election_id)
    if election is None or effective_status(election) != "open":
        raise ElectionError("Voting is not currently open for this election")

    voter = (
        await db.execute(
            select(Voter).where(Voter.election_id == election_id, Voter.user_id == member.id)
        )
    ).scalar_one_or_none()
    if voter is None:
        raise ElectionError("You are not eligible to vote in this election")
    if voter.has_voted:
        raise ElectionError("You have already voted in this election")

    if not student_id or student_id.strip() != (member.student_id or ""):
        raise ElectionError("Student ID does not match your account")

    # Tokens are generated from an uppercase-only alphabet; normalize typed
    # input so a lowercase entry (common on mobile keyboards) still matches.
    token_query = select(VoterToken).where(
        VoterToken.voter_id == voter.id,
        VoterToken.token_hash == hash_token(token.strip().upper()),
    )
    if for_update:
        token_query = token_query.with_for_update()
    token_row = (await db.execute(token_query)).scalar_one_or_none()
    if token_row is None:
        raise ElectionError("Invalid voter token")
    if token_row.is_nullified:
        raise ElectionError("This voter token has been nullified — request a new one")
    if token_row.is_used:
        raise ElectionError("This voter token has already been used")

    return voter, token_row


async def verify_voter_credentials(
    db: AsyncSession, *, member: User, election_id: int, student_id: str, token: str
) -> None:
    """Read-only check used by the ballot wizard to validate the student ID +
    token pair before the voter proceeds, without consuming the token."""
    await _resolve_voter_and_token(
        db, member=member, election_id=election_id, student_id=student_id, token=token
    )


async def cast_vote(
    db: AsyncSession,
    *,
    member: User,
    election_id: int,
    student_id: str,
    token: str,
    selections: dict[int, str],
) -> None:
    voter, token_row = await _resolve_voter_and_token(
        db,
        member=member,
        election_id=election_id,
        student_id=student_id,
        token=token,
        for_update=True,
    )
    now = datetime.utcnow()

    positions = (
        (
            await db.execute(
                select(Position)
                .where(Position.election_id == election_id)
                .options(selectinload(Position.candidates))
                .order_by(Position.order_index)
            )
        )
        .scalars()
        .all()
    )
    if not positions:
        raise ElectionError("This election has no positions to vote on")

    votes_to_add: list[Vote] = []
    for position in positions:
        candidates = sorted(position.candidates, key=lambda c: c.order_index)
        if not candidates:
            raise ElectionError(f"{position.title} has no candidates configured")

        raw = selections.get(position.id)
        if not raw:
            raise ElectionError(f"Please select a candidate for {position.title}")

        if len(candidates) == 1:
            # Uncontested position — the ballot presents a yes/no (elect /
            # reject) choice for the sole candidate instead of a pick-list.
            if raw not in ("yes", "no"):
                raise ElectionError(f"Please vote yes or no for {position.title}")
            candidate = candidates[0]
            is_no = raw == "no"
        else:
            try:
                candidate_id = int(raw)
            except ValueError:
                raise ElectionError(f"Invalid candidate selection for {position.title}") from None
            candidate = next((c for c in candidates if c.id == candidate_id), None)
            if candidate is None:
                raise ElectionError(f"Invalid candidate selection for {position.title}")
            is_no = False

        votes_to_add.append(
            Vote(
                election_id=election_id,
                position_id=position.id,
                candidate_id=candidate.id,
                voter_id=voter.id,
                voter_token_id=token_row.id,
                is_no=is_no,
            )
        )

    for vote in votes_to_add:
        db.add(vote)
    token_row.is_used = True
    token_row.used_at = now
    voter.has_voted = True

    try:
        await db.commit()
    except IntegrityError as err:
        await db.rollback()
        raise ElectionError("Vote already recorded") from err


# ── Nullify & reissue ────────────────────────────────────────────────────────

async def nullify_vote(
    db: AsyncSession, *, admin: User, voter_id: int, election_id: int, reason: str
) -> str:
    voter = await db.get(Voter, voter_id)
    if voter is None or voter.election_id != election_id:
        raise ElectionError("Voter not found for this election")

    election = await db.get(Election, election_id)
    if election is None:
        raise ElectionError("Election not found")
    # A nullification hands the voter a fresh token to re-vote with, which only
    # works while voting is still happening. Blocking it once the election has
    # closed prevents an admin from silently disenfranchising a voter against a
    # result that is already final.
    if effective_status(election) not in ("open", "paused"):
        raise ElectionError(
            "Votes can only be nullified while the election is open or paused — "
            "not once it has closed."
        )

    active_votes = (
        (
            await db.execute(
                select(Vote).where(
                    Vote.voter_id == voter.id,
                    Vote.election_id == election_id,
                    Vote.is_nullified.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    if not active_votes:
        raise ElectionError("No active vote to nullify for this voter")

    now = datetime.utcnow()
    for vote in active_votes:
        vote.is_nullified = True
        vote.nullified_at = now
        vote.nullified_by_id = admin.id
        vote.nullify_reason = reason

    active_token = (
        await db.execute(
            select(VoterToken).where(
                VoterToken.voter_id == voter.id, VoterToken.is_nullified.is_(False)
            )
        )
    ).scalar_one_or_none()
    if active_token is not None:
        active_token.is_nullified = True

    voter.has_voted = False
    await db.flush()

    user = await db.get(User, voter.user_id)
    new_token = await issue_voter_token(db, voter)
    await db.flush()

    try:
        await send_vote_nullified_email(user.email, election, new_token, reason)
    except resend_client.ResendError as err:
        await email_failures.record_failure(
            db, recipient=user.email, purpose="vote_nullified_reissue", error=err
        )

    await db.commit()
    return new_token


# ── Results (aggregate tallies only — never per-voter choice) ───────────────

async def compute_results(db: AsyncSession, election_id: int) -> list[dict]:
    """Per-position aggregate tallies for the admin results view. Deliberately
    returns only vote counts per candidate/choice — nothing here (or in any
    caller) may join back to voter_id, since a cast ballot's secrecy depends on
    the tally being the only thing ever surfaced from Vote rows in the UI."""
    positions = (
        (
            await db.execute(
                select(Position)
                .where(Position.election_id == election_id)
                .options(selectinload(Position.candidates))
                .order_by(Position.order_index)
            )
        )
        .scalars()
        .all()
    )

    results = []
    for position in positions:
        candidates = sorted(position.candidates, key=lambda c: c.order_index)

        tally_rows = (
            await db.execute(
                select(Vote.candidate_id, Vote.is_no, func.count(Vote.id))
                .where(Vote.position_id == position.id, Vote.is_nullified.is_(False))
                .group_by(Vote.candidate_id, Vote.is_no)
            )
        ).all()
        tally: dict[int, dict[str, int]] = {}
        for candidate_id, is_no, count in tally_rows:
            entry = tally.setdefault(candidate_id, {"yes": 0, "no": 0})
            entry["no" if is_no else "yes"] += count

        if len(candidates) == 1:
            candidate = candidates[0]
            entry = tally.get(candidate.id, {"yes": 0, "no": 0})
            results.append(
                {
                    "position": position,
                    "contested": False,
                    "candidate": candidate,
                    "yes_votes": entry["yes"],
                    "no_votes": entry["no"],
                    "total_votes": entry["yes"] + entry["no"],
                }
            )
        else:
            rows = []
            total = 0
            for candidate in candidates:
                votes = tally.get(candidate.id, {"yes": 0, "no": 0})["yes"]
                rows.append({"candidate": candidate, "votes": votes})
                total += votes
            for row in rows:
                row["percent"] = round((row["votes"] / total * 100), 1) if total else 0.0
            rows.sort(key=lambda r: r["votes"], reverse=True)
            results.append(
                {
                    "position": position,
                    "contested": True,
                    "candidates": rows,
                    "total_votes": total,
                }
            )

    return results


async def resend_token(db: AsyncSession, *, voter: Voter) -> str:
    """Reissue a voter's token without touching vote/has_voted state — for when the
    original token email never arrived. Does not nullify any cast vote.

    The new token is persisted (committed) even if the notification email
    fails to send, same as the original issuance — but unlike the original,
    a failed send here is re-raised as ResendError instead of swallowed, so
    callers that specifically care about "did they actually get it" (e.g.
    scripts/local_gmail_import.py's --resend-token-for) can detect and report
    it rather than wrongly assuming success."""
    existing = (
        await db.execute(
            select(VoterToken).where(
                VoterToken.voter_id == voter.id, VoterToken.is_nullified.is_(False)
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.is_nullified = True

    election = await db.get(Election, voter.election_id)
    user = await db.get(User, voter.user_id)
    new_token = await issue_voter_token(db, voter)
    await db.commit()

    await send_voter_token_email(user.email, election, new_token)
    return new_token
