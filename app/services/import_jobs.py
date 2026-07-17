"""In-memory tracker for long-running admin bulk-import jobs (election voter
register upload, plain member bulk upload -- see
app.web.elections_web.register_upload / app.web.admin_web.member_import_submit)
that run as a FastAPI BackgroundTask instead of inline in the request/response
cycle.

WHY this exists: both import flows commit per-row and, for each row, await a
real outbound transactional email send (app.services.resend_client.send_email)
before moving to the next row -- deliberately sequential, never parallelized
(see resend_client's Gmail path, which relies on strict per-account send
ordering/pacing to avoid tripping Google's abuse detection). For a register of
a couple thousand rows that's minutes of sequential awaited I/O, comfortably
past a reverse-proxy's request timeout (Render/Railway, typically 30-100s) if
run inline. Moving the row loop into a background task fixes the timeout, but
then the admin's browser has nothing to render immediately -- this module is
the minimal state needed to redirect them to a status page that polls for
progress instead. Hand-rolled rather than pulling in Celery/RQ/Redis, matching
this codebase's existing house style for small, focused infra (see
app.core.rate_limit, app.services.org_settings_cache).

KNOWN LIMITATION: state lives in this process's memory only, so it's lost on
every deploy/restart (an admin mid-import would see "job not found" and would
need to re-check the voters/members list to see how far it got -- the
per-row-commit design means no data is lost, just this progress tracker). Fine
for a single-instance deployment (this app currently runs as one Render/
Railway web service); if it's ever scaled to multiple instances behind a load
balancer, a status-page request could land on an instance that never ran the
job. Revisit with a shared store (Redis, or the Postgres DB itself) if that
ever happens -- same tradeoff app.core.rate_limit already documents for its
own in-memory state.
"""

import secrets
import time

# How long a finished (completed/failed) job's entry is kept around before a
# cleanup sweep drops it -- long enough that an admin who starts an import and
# comes back a while later can still see the result, short enough that the
# dict doesn't grow forever across a long-running process's many imports.
_JOB_MAX_AGE_SECONDS = 4 * 60 * 60  # 4 hours

# How often (in seconds) creating a job also sweeps the whole table for
# expired entries -- mirrors app.core.rate_limit's _cleanup_expired: a simple
# opportunistic sweep on the one code path that grows the dict, not a
# background thread/task of its own.
_CLEANUP_INTERVAL_SECONDS = 30 * 60

# job_id -> {kind, status, total, processed, result, error, created_at}.
# created_at uses time.monotonic() -- only ever compared to itself, never
# persisted or shown, so wall-clock jumps (NTP, DST) can't extend or shrink
# how long a finished job stays visible.
_jobs: dict[str, dict] = {}
_last_cleanup: float = 0.0


def _cleanup_expired(now: float) -> None:
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup = now
    expired = [
        job_id
        for job_id, job in _jobs.items()
        if now - job["created_at"] >= _JOB_MAX_AGE_SECONDS
    ]
    for job_id in expired:
        _jobs.pop(job_id, None)


def create_job(kind: str) -> str:
    """Registers a new job in "running" state and returns its id. `kind` is
    just a label (e.g. "register_import", "member_import") for anyone
    inspecting the store -- nothing here branches on it."""
    now = time.monotonic()
    _cleanup_expired(now)
    job_id = secrets.token_urlsafe(16)
    _jobs[job_id] = {
        "kind": kind,
        "status": "running",
        "total": 0,
        "processed": 0,
        "result": None,
        "error": None,
        "created_at": now,
    }
    return job_id


def set_total(job_id: str, total: int) -> None:
    job = _jobs.get(job_id)
    if job is not None:
        job["total"] = total


def update_progress(job_id: str, processed: int) -> None:
    """Called from the row loop's on_row callback after each row -- `processed`
    is the 1-based index of the row just finished, matching on_row's own
    (index, total, info) contract in import_register/import_members."""
    job = _jobs.get(job_id)
    if job is not None:
        job["processed"] = processed


def complete_job(job_id: str, result) -> None:
    """`result` is the RegisterImportResult/MemberImportResult dataclass
    instance itself -- stored as-is (not serialized) so the status route can
    render it straight through the existing result templates."""
    job = _jobs.get(job_id)
    if job is not None:
        job["status"] = "completed"
        job["result"] = result
        job["processed"] = job["total"]


def fail_job(job_id: str, error: str) -> None:
    """Call from the background task's outermost except clause -- an
    unexpected exception must never leave a job stuck at "running" forever
    with no way for the admin to tell it's dead."""
    job = _jobs.get(job_id)
    if job is not None:
        job["status"] = "failed"
        job["error"] = error


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)
