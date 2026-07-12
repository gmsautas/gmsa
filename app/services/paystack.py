"""Thin client around the Paystack Transactions API.

Docs: https://paystack.com/docs/api/transaction/
All amounts are expressed in the major currency unit (e.g. GHS) at the
call site and converted to the minor unit (pesewas/kobo) expected by
Paystack here.
"""

import hashlib
import hmac
from decimal import Decimal

import httpx

from app.core.config import settings

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    pass


def _headers() -> dict[str, str]:
    if not settings.paystack_secret_key:
        raise PaystackError("PAYSTACK_SECRET_KEY is not configured")
    return {
        "Authorization": f"Bearer {settings.paystack_secret_key}",
        "Content-Type": "application/json",
    }


def _parse_json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except ValueError as err:
        raise PaystackError(
            f"Unexpected response from Paystack (status {response.status_code})"
        ) from err


async def initialize_transaction(
    *,
    email: str,
    amount: Decimal,
    reference: str,
    callback_url: str | None = None,
    metadata: dict | None = None,
) -> dict:
    payload = {
        "email": email,
        "amount": int(amount * 100),
        "reference": reference,
        "currency": "GHS",
        "callback_url": callback_url or settings.paystack_callback_url,
        "metadata": metadata or {},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{PAYSTACK_BASE_URL}/transaction/initialize", json=payload, headers=_headers()
        )
    data = _parse_json(response)
    if not response.is_success or not data.get("status"):
        raise PaystackError(data.get("message", "Failed to initialize transaction"))
    return data["data"]


async def verify_transaction(reference: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}", headers=_headers()
        )
    data = _parse_json(response)
    if not response.is_success or not data.get("status"):
        raise PaystackError(data.get("message", "Failed to verify transaction"))
    return data["data"]


def verify_webhook_signature(payload_body: bytes, signature: str | None) -> bool:
    if not signature or not settings.paystack_secret_key:
        return False
    computed = hmac.new(
        settings.paystack_secret_key.encode("utf-8"), payload_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(computed, signature)
