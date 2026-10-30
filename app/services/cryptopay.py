"""Клиент CryptoPay API (@send / @CryptoBot) на httpx.AsyncClient.

Токен передается в заголовке Crypto-Pay-API-Token и не логируется.
При ошибке API в лог пишется status_code и тело ответа.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class CryptoPayError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    return {"Crypto-Pay-API-Token": settings.cryptopay_token}


async def _call(method: str, payload: dict) -> dict:
    if not settings.cryptopay_token:
        raise CryptoPayError("CRYPTOPAY_TOKEN is not set")
    url = f"{settings.cryptopay_base_url}/api/{method}"
    async with httpx.AsyncClient(timeout=settings.cryptopay_timeout) as client:
        try:
            response = await client.post(url, json=payload, headers=_headers())
        except httpx.HTTPError as exc:
            log.error("CryptoPay %s: network error: %s", method, type(exc).__name__)
            raise CryptoPayError("network") from exc
        if response.status_code >= 400:
            log.error(
                "CryptoPay %s: status=%s body=%s",
                method,
                response.status_code,
                response.text[:1000],
            )
            raise CryptoPayError(f"http_{response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            log.error("CryptoPay %s: not json, status=%s body=%s",
                      method, response.status_code, response.text[:1000])
            raise CryptoPayError("not_json") from exc
        if not data.get("ok"):
            log.error("CryptoPay %s: ok=false body=%s", method, response.text[:1000])
            raise CryptoPayError(str(data.get("error") or "api_error"))
        return data.get("result") or {}


async def create_invoice(amount: str, description: str, payload: str, expires_in: int) -> dict:
    body: dict = {
        "currency_type": "fiat",
        "fiat": "USD",
        "amount": amount,
        "description": description[:1024],
        "payload": payload,
        "expires_in": max(60, int(expires_in)),
        "allow_comments": False,
        "allow_anonymous": False,
    }
    if settings.cryptopay_asset:
        body["asset"] = settings.cryptopay_asset
    invoice = await _call("createInvoice", body)
    if not isinstance(invoice, dict) or "invoice_id" not in invoice:
        log.error("CryptoPay createInvoice: unexpected result %s", invoice)
        raise CryptoPayError("empty_result")
    log.info("CryptoPay: invoice %s for %s USD", invoice.get("invoice_id"), amount)
    return invoice


async def get_invoices(invoice_ids: list[int]) -> list[dict]:
    if not invoice_ids:
        return []
    result = await _call(
        "getInvoices",
        {"invoice_ids": ",".join(str(i) for i in invoice_ids[:1000]), "count": len(invoice_ids)},
    )
    items = result.get("items") if isinstance(result, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


async def get_invoice(invoice_id: int) -> dict | None:
    invoices = await get_invoices([invoice_id])
    return invoices[0] if invoices else None