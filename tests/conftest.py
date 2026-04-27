import pytest


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Run every test with a zeroed store and instant processing delay."""
    import app.config
    from app.store import idempotency_store

    monkeypatch.setattr(app.config.settings, "processing_delay", 0.01)

    idempotency_store._store.clear()
    idempotency_store._stats = {
        "total_requests": 0,
        "cache_hits": 0,
        "conflicts": 0,
        "in_flight_waits": 0,
    }
    yield
