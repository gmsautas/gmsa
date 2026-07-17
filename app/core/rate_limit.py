"""In-memory brute-force throttle for the three login surfaces (POST /login,
POST /admin/login, POST /api/auth/login — see app.web.auth_web and
app.api.v1.routes.auth). Hand-rolled rather than pulling in slowapi/limits,
matching this codebase's existing house style for small, focused infra (see
app.services.org_settings_cache, app.services.email_failures).

KNOWN LIMITATION: state lives in this process's memory only, so it resets on
every deploy and isn't shared across instances. Fine for a single-instance
deployment (this app currently runs as one Render/Railway web service); if
it's ever scaled to multiple instances behind a load balancer, each instance
tracks attempts independently and the effective threshold becomes
MAX_ATTEMPTS * instance_count. Revisit with a shared store (Redis, or the
Postgres DB itself) if that ever happens.
"""

import time

# 8 failed attempts per 15 minutes, per (IP, email) pair. Generous enough
# that a member who fat-fingers their password a few times in a row never
# gets locked out, but tight enough to make credential stuffing impractical
# against a small student org's user base (a few hundred accounts at most).
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 15 * 60

# How often (in seconds) a call to record_failure also sweeps the whole
# table for expired entries, so keys that are never looked up again (e.g.
# an attacker who tries many different (ip, email) pairs and abandons each
# one) don't accumulate forever. is_locked_out already evicts the specific
# key it's asked about when that key's window has expired; this sweep is
# just a backstop for keys that never get looked up again at all.
_CLEANUP_INTERVAL_SECONDS = 10 * 60

# key -> (failure_count, window_started_at). Both timestamps use
# time.monotonic() -- only ever compared to each other, never persisted or
# shown, so wall-clock jumps (NTP, DST) can't extend or shrink a window.
_attempts: dict[str, tuple[int, float]] = {}
_last_cleanup: float = 0.0


def make_key(client_ip: str, email: str) -> str:
    """Combine IP + normalized email into one throttle key -- keyed on the
    pair (not just IP, not just email) so one attacker spraying guesses
    across many IPs can't lock out a real user's account, and one IP
    guessing many different accounts can't be throttled away by a single
    per-IP counter either."""
    return f"{client_ip}:{(email or '').strip().lower()}"


def _cleanup_expired(now: float) -> None:
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup = now
    expired = [key for key, (_, started) in _attempts.items() if now - started >= WINDOW_SECONDS]
    for key in expired:
        _attempts.pop(key, None)


def is_locked_out(key: str) -> bool:
    """Check-only -- never records an attempt by itself. Evicts `key` if its
    own window has already expired, so a stale entry doesn't linger just
    because nothing ever calls record_failure/record_success on it again."""
    now = time.monotonic()
    _cleanup_expired(now)
    entry = _attempts.get(key)
    if entry is None:
        return False
    count, started = entry
    if now - started >= WINDOW_SECONDS:
        _attempts.pop(key, None)
        return False
    return count >= MAX_ATTEMPTS


def record_failure(key: str) -> None:
    """Call after a failed credential check. Starts a fresh window if there
    was no entry yet, or the previous one has expired."""
    now = time.monotonic()
    _cleanup_expired(now)
    entry = _attempts.get(key)
    if entry is None or now - entry[1] >= WINDOW_SECONDS:
        _attempts[key] = (1, now)
    else:
        count, started = entry
        _attempts[key] = (count + 1, started)


def record_success(key: str) -> None:
    """Call after a successful credential check -- a correct password must
    never be penalized by an earlier mistake, so this clears the key's
    window entirely rather than just decrementing it."""
    _attempts.pop(key, None)
