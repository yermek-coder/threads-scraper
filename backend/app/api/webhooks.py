"""Meta Threads webhook challenge verification and signed event intake."""

import hashlib
import hmac
import json
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.services.webhook_service import process_webhook_in_background

router = APIRouter(prefix="/webhooks/threads", tags=["webhooks"])
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024


@router.get("")
async def verify_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    """Answer Meta's webhook subscription challenge when its verify token matches."""
    expected = config.threads_verify_token.get_secret_value()
    if (
        hub_mode != "subscribe"
        or not expected
        or hub_verify_token is None
        or not hmac.compare_digest(hub_verify_token.encode("utf-8"), expected.encode("utf-8"))
        or hub_challenge is None
    ):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return PlainTextResponse(hub_challenge)


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, str]:
    """Validate Meta's HMAC-SHA256 signature, then ingest mention/reply events."""
    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="Webhook body is too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    secret = config.threads_app_secret.get_secret_value()
    if not secret:
        raise HTTPException(status_code=503, detail="Threads webhook app secret is not configured")
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")
    background_tasks.add_task(process_webhook_in_background, payload)
    return {"status": "EVENT_RECEIVED"}
