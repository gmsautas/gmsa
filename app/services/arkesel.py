"""Thin client around the Arkesel SMS v2 API.

Docs: https://developers.arkesel.com/sms-api
"""

import math

import httpx

from app.core.config import settings
from app.services import org_settings_cache, secrets_store

ARKESEL_SMS_URL = "https://sms.arkesel.com/api/v2/sms/send"

# Standard GSM-7 single-segment length is 160 chars; concatenated segments
# carry a 7-char UDH header, leaving 153 chars per segment.
SMS_SINGLE_SEGMENT_LIMIT = 160
SMS_MULTI_SEGMENT_LIMIT = 153


class ArkeselError(Exception):
    pass


def count_segments(message: str) -> int:
    length = len(message)
    if length <= SMS_SINGLE_SEGMENT_LIMIT:
        return 1
    return math.ceil(length / SMS_MULTI_SEGMENT_LIMIT)


async def send_sms(recipients: list[str], message: str) -> dict:
    """Sends `message` to every number in `recipients` in a single API call.

    Finding (Phase 7 of the remediation plan, see app/services/campaign_sender.py):
    per Arkesel's v2 docs and how SMS aggregators generally work, `recipients`
    is a distribution list -- Arkesel delivers `message` as its own separate
    text to each number server-side. There is no shared "to" header visible
    to recipients the way there is with email's `to` array, so passing many
    recipients in one call here is NOT a privacy leak. It IS still a
    rate-limiting / request-size concern for very large audiences, which is
    why campaign_sender.send_sms_campaign chunks the audience into bounded
    batches (SMS_CHUNK_SIZE) before calling this function, rather than
    passing the entire audience in one call.
    """
    api_key = secrets_store.get_secret("arkesel_api_key", settings.arkesel_api_key)
    if not api_key:
        raise ArkeselError("ARKESEL_API_KEY is not configured")
    if not recipients:
        raise ArkeselError("No recipients provided")

    sender_id = org_settings_cache.get("arkesel_sender_id") or settings.arkesel_sender_id
    payload = {
        "sender": sender_id,
        "message": message,
        "recipients": recipients,
    }
    headers = {"api-key": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(ARKESEL_SMS_URL, json=payload, headers=headers)
    try:
        data = response.json()
    except ValueError as err:
        raise ArkeselError(
            f"Unexpected response from Arkesel (status {response.status_code})"
        ) from err
    if not response.is_success or data.get("code") not in ("ok", "success"):
        raise ArkeselError(data.get("message", "Failed to send SMS"))
    return data
