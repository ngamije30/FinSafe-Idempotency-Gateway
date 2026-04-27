import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes.payments import router
from app.store import idempotency_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def _cleanup_loop() -> None:
    """
    Developer's Choice feature: background task that evicts expired idempotency
    keys so the in-memory store never grows without bound.  TTL defaults to
    24 h, matching the Stripe / Adyen industry convention.
    """
    while True:
        await asyncio.sleep(settings.cleanup_interval)
        removed = idempotency_store.cleanup_expired()
        if removed:
            logger.info("TTL cleanup: evicted %d expired keys", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Idempotency Gateway starting up …")
    cleanup_task = asyncio.create_task(_cleanup_loop())
    yield
    cleanup_task.cancel()
    logger.info("Idempotency Gateway shut down cleanly.")


app = FastAPI(
    title="Idempotency Gateway — FinSafe Pay-Once Protocol",
    description=(
        "A payment processing middleware that guarantees every transaction is "
        "executed exactly once, no matter how many times the client retries."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Cache-Hit", "X-Idempotency-Key"],
)

app.include_router(router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def serve_ui() -> FileResponse:
    return FileResponse("static/index.html")
