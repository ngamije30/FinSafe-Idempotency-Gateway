"""
Full acceptance-test suite for the Idempotency Gateway.
Each test maps directly to one of the five user stories in the spec.
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

BASE = "http://test"
HEADERS_JSON = {"Content-Type": "application/json"}


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


# ── System ──────────────────────────────────────────────────────────────────

async def test_health_check():
    async with client() as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# ── User Story 1: Happy Path ─────────────────────────────────────────────────

async def test_first_payment_returns_201():
    async with client() as c:
        resp = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "us1-key-001"},
            json={"amount": 100, "currency": "GHS"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "success"
    assert data["message"] == "Charged 100.0 GHS"
    assert "transaction_id" in data
    assert resp.headers["X-Cache-Hit"] == "false"


async def test_missing_idempotency_key_returns_400():
    async with client() as c:
        resp = await c.post("/process-payment", json={"amount": 100, "currency": "GHS"})
    assert resp.status_code == 400


async def test_invalid_amount_returns_422():
    async with client() as c:
        resp = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "bad-amount"},
            json={"amount": -50, "currency": "GHS"},
        )
    assert resp.status_code == 422


# ── User Story 2: Duplicate Detection ────────────────────────────────────────

async def test_duplicate_returns_cached_response():
    async with client() as c:
        first = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "us2-key-001"},
            json={"amount": 50, "currency": "USD"},
        )
        second = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "us2-key-001"},
            json={"amount": 50, "currency": "USD"},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers["X-Cache-Hit"] == "true"
    # Exact same transaction_id proves no second charge was made
    assert first.json()["transaction_id"] == second.json()["transaction_id"]


async def test_duplicate_response_is_identical():
    """Every field in the response must be bit-for-bit identical on retries."""
    async with client() as c:
        first = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "us2-key-002"},
            json={"amount": 200, "currency": "EUR"},
        )
        second = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "us2-key-002"},
            json={"amount": 200, "currency": "EUR"},
        )
    assert first.json() == second.json()


# ── User Story 3: Conflict Detection ─────────────────────────────────────────

async def test_same_key_different_body_returns_422():
    async with client() as c:
        await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "conflict-key"},
            json={"amount": 100, "currency": "GHS"},
        )
        resp = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "conflict-key"},
            json={"amount": 500, "currency": "GHS"},  # different amount
        )
    assert resp.status_code == 422
    assert "different request body" in resp.json()["detail"]


async def test_same_key_different_currency_returns_422():
    async with client() as c:
        await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "conflict-key-2"},
            json={"amount": 100, "currency": "GHS"},
        )
        resp = await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "conflict-key-2"},
            json={"amount": 100, "currency": "USD"},  # different currency
        )
    assert resp.status_code == 422


# ── Bonus: Race Condition Prevention ─────────────────────────────────────────

async def test_concurrent_requests_return_same_transaction_id():
    """
    Fire two requests with the same key at the same time.
    Both must receive the SAME transaction_id — only one payment processed.
    """
    async with client() as c:
        results = await asyncio.gather(
            c.post(
                "/process-payment",
                headers={"Idempotency-Key": "race-key-001"},
                json={"amount": 75, "currency": "EUR"},
            ),
            c.post(
                "/process-payment",
                headers={"Idempotency-Key": "race-key-001"},
                json={"amount": 75, "currency": "EUR"},
            ),
        )

    transaction_ids = {r.json()["transaction_id"] for r in results if r.is_success}
    assert len(transaction_ids) == 1, "Race condition: two distinct transaction IDs returned"


# ── Admin endpoints ───────────────────────────────────────────────────────────

async def test_stats_counts_correctly():
    async with client() as c:
        await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "stats-key"},
            json={"amount": 100, "currency": "GHS"},
        )
        # One duplicate
        await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "stats-key"},
            json={"amount": 100, "currency": "GHS"},
        )
        stats = await c.get("/stats")

    data = stats.json()
    assert data["total_requests"] == 1
    assert data["cache_hits"] == 1
    assert data["conflicts"] == 0


async def test_transactions_ledger_contains_record():
    async with client() as c:
        await c.post(
            "/process-payment",
            headers={"Idempotency-Key": "ledger-key"},
            json={"amount": 300, "currency": "NGN"},
        )
        txns = await c.get("/transactions")

    assert txns.status_code == 200
    assert any(r["key"] == "ledger-key" for r in txns.json())
