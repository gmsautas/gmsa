import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services.audience import current_dues_period_label

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.auto_reload = settings.environment == "development"


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
