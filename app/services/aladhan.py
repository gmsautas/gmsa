"""Thin client around the Aladhan prayer times API.

The public site calls Aladhan directly from the browser (see
frontend/assets/js/prayer-times.js). This server-side client exists for
features that need prayer times outside a browser context, e.g. scheduling
SMS reminders.

Docs: https://aladhan.com/prayer-times-api
"""

import httpx

from app.core.config import settings

ALADHAN_BASE_URL = "https://api.aladhan.com/v1"


class AladhanError(Exception):
    pass


async def get_timings_by_city(city: str | None = None, country: str | None = None) -> dict:
    params = {
        "city": city or settings.prayer_default_city,
        "country": country or settings.prayer_default_country,
        "method": 2,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{ALADHAN_BASE_URL}/timingsByCity", params=params)
    data = response.json()
    if not response.is_success or data.get("code") != 200:
        raise AladhanError(data.get("status", "Failed to fetch prayer times"))
    return data["data"]
