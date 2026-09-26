"""FastAPI application entry point and service lifecycle."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import auth, keywords, posts, settings, stopwords, threads_oauth, whatsapp, webhooks
from app.api import notifications
from app.core.config import config
from app.core.database import Base, SessionFactory, engine
from app.core.security import hash_password
from app.models import User
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.threads_web_client import close_threads_web_browser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


class OAuthCallbackAccessLogFilter(logging.Filter):
    """Hide one-time OAuth codes and state values from Uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            request_path = args[2]
            if isinstance(request_path, str) and request_path.startswith("/api/threads/oauth/callback?"):
                args[2] = "/api/threads/oauth/callback?[redacted]"
                record.args = tuple(args)
        return True


# HTTPX logs complete request URLs at INFO, which would expose token-exchange
# parameters such as client_secret and access_token. Keep those logs suppressed.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").addFilter(OAuthCallbackAccessLogFilter())


async def _initialize_database() -> None:
    """Create the initial schema and bootstrap the first admin user once."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with SessionFactory() as db:
        existing = await db.scalar(select(User.id).limit(1))
        if existing is not None:
            return
        username = config.admin_username.strip()
        password = config.admin_password.get_secret_value()
        if not username or not password:
            raise RuntimeError("ADMIN_USERNAME and ADMIN_PASSWORD are required to bootstrap the first user")
        if len(password.encode("utf-8")) < 12:
            raise RuntimeError("ADMIN_PASSWORD must contain at least 12 UTF-8 bytes")
        db.add(User(username=username, password_hash=hash_password(password)))
        await db.commit()
        logger.info("Bootstrapped the initial dashboard user")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize persistent state and start/stop recurring background jobs."""
    secret_key = config.secret_key.get_secret_value()
    if len(secret_key) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters")
    await _initialize_database()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        await close_threads_web_browser()
        await engine.dispose()


app = FastAPI(
    title="Threads Brand Monitoring API",
    description="Private API for Threads keyword monitoring and notification delivery.",
    version="1.0.0",
    lifespan=lifespan,
)

cors_origins = [origin.strip() for origin in config.cors_origins.split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.include_router(auth.router, prefix="/api")
app.include_router(posts.router, prefix="/api")
app.include_router(keywords.router, prefix="/api")
app.include_router(stopwords.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(whatsapp.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")
app.include_router(threads_oauth.router, prefix="/api")


@app.get("/health", tags=["health"])
@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness endpoint for Docker and reverse-proxy health checks."""
    return {"status": "ok"}
