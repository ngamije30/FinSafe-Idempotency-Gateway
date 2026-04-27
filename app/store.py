import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class IdempotencyRecord:
    status: str  # "processing" | "completed"
    request_hash: str
    response: Optional[Dict[str, Any]] = None
    status_code: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    # One lock per record — Request B waits here while Request A is in-flight
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class IdempotencyStore:
    def __init__(self, ttl_seconds: int = 86400) -> None:
        self._store: Dict[str, IdempotencyRecord] = {}
        self._ttl = ttl_seconds
        self._stats: Dict[str, int] = {
            "total_requests": 0,
            "cache_hits": 0,
            "conflicts": 0,
            "in_flight_waits": 0,
        }

    # ── Hashing ────────────────────────────────────────────────────────────
    def hash_request(self, body: dict) -> str:
        """Deterministic SHA-256 of the canonicalised request body."""
        return hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest()

    # ── CRUD ───────────────────────────────────────────────────────────────
    def get(self, key: str) -> Optional[IdempotencyRecord]:
        record = self._store.get(key)
        if record is None:
            return None
        if time.time() > record.expires_at:
            del self._store[key]
            logger.debug("Expired key evicted: %.8s…", key)
            return None
        return record

    def create(self, key: str, request_hash: str) -> IdempotencyRecord:
        record = IdempotencyRecord(
            status="processing",
            request_hash=request_hash,
            expires_at=time.time() + self._ttl,
        )
        self._store[key] = record
        self._stats["total_requests"] += 1
        return record

    def complete(self, key: str, response: dict, status_code: int) -> None:
        record = self._store.get(key)
        if record:
            record.status = "completed"
            record.response = response
            record.status_code = status_code

    # ── Stats ──────────────────────────────────────────────────────────────
    def record_cache_hit(self) -> None:
        self._stats["cache_hits"] += 1

    def record_conflict(self) -> None:
        self._stats["conflicts"] += 1

    def record_in_flight_wait(self) -> None:
        self._stats["in_flight_waits"] += 1

    def get_stats(self) -> dict:
        active = sum(1 for v in self._store.values() if time.time() <= v.expires_at)
        return {**self._stats, "active_keys": active}

    def get_all_records(self) -> list:
        now = time.time()
        return [
            {
                "key": k,
                "status": v.status,
                "created_at": v.created_at,
                "expires_at": v.expires_at,
                "response": v.response,
            }
            for k, v in self._store.items()
            if now <= v.expires_at
        ]

    # ── Background TTL cleanup ─────────────────────────────────────────────
    def cleanup_expired(self) -> int:
        """Remove all keys past their TTL. Called by the background task."""
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
        if expired:
            logger.info("Background cleanup removed %d expired keys", len(expired))
        return len(expired)


# Module-level singleton — shared across the entire application process
idempotency_store = IdempotencyStore()
