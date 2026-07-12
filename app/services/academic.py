"""Derive a member's academic level, graduation year, and dues tier.

A member's student ID is expected to begin with the 4-digit academic year
they were admitted in (e.g. the "2022" in "20220404172" means they were
admitted in the 2022/2023 academic year). Combined with their program
category — which determines how many years the program runs for — this lets
us compute their current numeric level (100/200/300/...), expected
graduation year, and which dues tier they fall into, without any manual
record-keeping.
"""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.core.config import settings
from app.services import org_settings_cache

if TYPE_CHECKING:
    from app.models.models import User

PROGRAM_CATEGORIES: tuple[str, ...] = ("diploma", "degree", "postgraduate", "masters", "phd")

# Typical program length, in years, for each category.
PROGRAM_DURATION_YEARS: dict[str, int] = {
    "diploma": 2,
    "degree": 4,
    "postgraduate": 1,
    "masters": 2,
    "phd": 3,
}

DUES_TIERS: tuple[str, ...] = ("level_100", "continuing", "final_year")


def program_duration(category: str) -> int:
    return PROGRAM_DURATION_YEARS.get(category, PROGRAM_DURATION_YEARS["degree"])


def admission_year(student_id: str) -> int:
    return int(student_id[:4])


def current_academic_year_start(today: date | None = None) -> int:
    """The starting year of the current academic year (Sept-Aug cycle)."""
    today = today or date.today()
    return today.year if today.month >= 9 else today.year - 1


def current_level(student_id: str, category: str, today: date | None = None) -> int:
    duration = program_duration(category)
    year_index = current_academic_year_start(today) - admission_year(student_id) + 1
    year_index = max(1, min(year_index, duration))
    return year_index * 100


def current_level_for_member(
    student_id: str | None, category: str | None, today: date | None = None
) -> int | None:
    if not student_id or not category:
        return None
    return current_level(student_id, category, today)


def graduation_year(student_id: str, category: str) -> int:
    return admission_year(student_id) + program_duration(category)


def dues_tier(student_id: str, category: str, today: date | None = None) -> str:
    duration = program_duration(category)
    level = current_level(student_id, category, today)
    if level >= duration * 100:
        return "final_year"
    if level <= 100:
        return "level_100"
    return "continuing"


def dues_tier_for_member(
    student_id: str | None, category: str | None, today: date | None = None
) -> str | None:
    if not student_id or not category:
        return None
    return dues_tier(student_id, category, today)


def dues_amount_for_tier(tier: str) -> Decimal:
    # DB-backed OrgSettings values (set via /admin/settings' Dues Amounts
    # section) win when present; env var is the fallback default so a site
    # that never touches the new UI keeps behaving exactly as before.
    continuing_default = org_settings_cache.get("dues_amount_continuing") or settings.dues_amount_continuing
    amounts = {
        "level_100": org_settings_cache.get("dues_amount_level_100") or settings.dues_amount_level_100,
        "continuing": continuing_default,
        "final_year": org_settings_cache.get("dues_amount_final_year") or settings.dues_amount_final_year,
    }
    return Decimal(amounts.get(tier, continuing_default))


def dues_amount_for_member(
    student_id: str | None, category: str | None, today: date | None = None
) -> Decimal:
    tier = dues_tier_for_member(student_id, category, today)
    if tier is None:
        return Decimal(org_settings_cache.get("dues_amount_ghs") or settings.dues_amount_ghs)
    return dues_amount_for_tier(tier)


# ---------------------------------------------------------------------------
# Override-aware wrappers — prefer an admin-entered correction (level_override
# / grad_year_override on the User row) over the student_id/category formula
# above. Use these for anything shown to or billing a specific member; the
# pure functions above stay reserved for account-creation time, before a User
# row (and therefore an override) can exist yet.
# ---------------------------------------------------------------------------


def effective_level(user: "User", today: date | None = None) -> int | None:
    if user.level_override is not None:
        return user.level_override
    return current_level_for_member(user.student_id, user.program_category, today)


def effective_grad_year(user: "User") -> int | None:
    if user.grad_year_override is not None:
        return user.grad_year_override
    return user.grad_year


def effective_dues_tier(user: "User", today: date | None = None) -> str | None:
    level = effective_level(user, today)
    if level is None:
        return None
    duration = program_duration(user.program_category or "degree")
    if level >= duration * 100:
        return "final_year"
    if level <= 100:
        return "level_100"
    return "continuing"


def effective_dues_amount(user: "User", today: date | None = None) -> Decimal:
    tier = effective_dues_tier(user, today)
    if tier is None:
        return Decimal(org_settings_cache.get("dues_amount_ghs") or settings.dues_amount_ghs)
    return dues_amount_for_tier(tier)
