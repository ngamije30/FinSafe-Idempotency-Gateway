import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas import PaymentRequest
from app.store import idempotency_store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/process-payment", status_code=201, tags=["Payments"])
async def process_payment(
    payment: PaymentRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> JSONResponse:
    """
    Process a payment exactly once, regardless of how many times the client retries.

    - First call  → processes and caches the result.
    - Retry       → returns the cached result instantly (X-Cache-Hit: true).
    - Conflict    → rejects reuse of the same key for a different payload (422).
    - Race        → blocks duplicate in-flight requests until the first one completes.
    """
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required.")

    request_body = payment.model_dump()
    request_hash = idempotency_store.hash_request(request_body)

    existing = idempotency_store.get(idempotency_key)

    if existing:
        # ── User Story 3: Conflict — same key, different payload ───────────
        if existing.request_hash != request_hash:
            idempotency_store.record_conflict()
            logger.warning("Conflict on key %.8s…: hash mismatch", idempotency_key)
            raise HTTPException(
                status_code=422,
                detail="Idempotency key already used for a different request body.",
            )

        # ── Bonus: In-flight — wait for the first request to finish ────────
        if existing.status == "processing":
            idempotency_store.record_in_flight_wait()
            logger.info("In-flight wait on key %.8s…", idempotency_key)
            # Acquiring the lock blocks until the original request releases it.
            async with existing.lock:
                pass
            # Re-fetch so we see the completed record.
            existing = idempotency_store.get(idempotency_key)

        # ── User Story 2: Cache hit — return identical stored response ──────
        idempotency_store.record_cache_hit()
        logger.info("Cache hit for key %.8s…", idempotency_key)
        response = JSONResponse(content=existing.response, status_code=existing.status_code)
        response.headers["X-Cache-Hit"] = "true"
        response.headers["X-Idempotency-Key"] = idempotency_key
        return response

    # ── User Story 1: First request — create, lock, process, store ─────────
    record = idempotency_store.create(idempotency_key, request_hash)
    logger.info("Processing new payment for key %.8s…", idempotency_key)

    # The lock is held for the entire duration of processing so that any
    # concurrent request with the same key blocks here instead of forking a
    # second payment (see Bonus user story above).
    async with record.lock:
        await asyncio.sleep(settings.processing_delay)

        response_body = {
            "status": "success",
            "message": f"Charged {payment.amount} {payment.currency}",
            "transaction_id": str(uuid.uuid4()),
            "amount": payment.amount,
            "currency": payment.currency,
            "processed_at": time.time(),
        }
        idempotency_store.complete(idempotency_key, response_body, 201)

    logger.info("Payment complete for key %.8s…", idempotency_key)
    response = JSONResponse(content=response_body, status_code=201)
    response.headers["X-Cache-Hit"] = "false"
    response.headers["X-Idempotency-Key"] = idempotency_key
    return response


@router.get("/transactions", tags=["Admin"])
async def list_transactions() -> list:
    """Return all active (non-expired) idempotency records."""
    return idempotency_store.get_all_records()


@router.get("/stats", tags=["Admin"])
async def get_stats() -> dict:
    """Live counters: requests, cache hits, conflicts, in-flight waits."""
    return idempotency_store.get_stats()


@router.get("/health", tags=["System"])
async def health_check() -> dict:
    return {"status": "healthy", "service": "Idempotency Gateway", "version": "1.0.0"}
