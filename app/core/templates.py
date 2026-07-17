import datetime
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services.audience import current_dues_period_label

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.auto_reload = settings.environment == "development"


@lru_cache(maxsize=None)
def _static_version(rel_path: str) -> int:
    """Cache-busting query param for a /static asset -- its own mtime. The
    StaticFiles mount has no cache-busting of its own, so without this a
    browser (or any CDN in front of the deploy) that already has an old
    styles.css/main.js cached keeps serving it after a deploy that changes
    those files, silently rendering new template markup against old CSS/JS
    (missing classes, stale layout) until the user hard-refreshes."""
    try:
        return int((STATIC_DIR / rel_path).stat().st_mtime)
    except OSError:
        return 0


def static_url(rel_path: str) -> str:
    return f"/static/{rel_path}?v={_static_version(rel_path)}"


templates.env.globals["static_url"] = static_url


def _datetimeformat(value, fmt: str = "%d %b %Y") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime(fmt)


def _money(value) -> str:
    if value is None:
        return "GHS 0.00"
    return f"GHS {float(value):,.2f}"


def _abbreviate_money(value) -> str:
    """Full precision under GHS 1,000; K+/M+ above that. For headline/summary
    figures only — itemized transaction rows and the Reports page always use
    the exact `money` filter, since reconciliation needs the real pesewas."""
    if value is None:
        return "GHS 0.00"
    amount = float(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000:
        return f"{sign}GHS {amount / 1_000_000:.1f}M+"
    if amount >= 1_000:
        return f"{sign}GHS {amount / 1_000:.1f}K+"
    return f"{sign}GHS {amount:,.2f}"


templates.env.filters["datetimeformat"] = _datetimeformat
templates.env.filters["money"] = _money
templates.env.filters["abbreviate_money"] = _abbreviate_money
templates.env.filters["timeformat"] = lambda v, f="%I:%M %p": (
    v.strftime(f) if v and not isinstance(v, str) else (v or "")
)

templates.env.globals["current_year"] = lambda: datetime.date.today().year
templates.env.globals["current_dues_period_label"] = current_dues_period_label
