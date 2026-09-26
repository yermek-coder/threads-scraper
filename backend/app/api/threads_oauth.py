"""Threads OAuth and Meta account lifecycle callbacks."""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ThreadsWebSessionImport
from app.core.config import config
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import MetaDeletionRequest, Post, Setting, User
from app.services.settings_service import get_setting, save_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/threads", tags=["threads-oauth"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

OAUTH_SCOPE = "threads_basic,threads_keyword_search"
OAUTH_STATE_KEY = "threads_oauth_state"
SIGNED_REQUEST_LIMIT = 16 * 1024


def _meta_error_marker(response: httpx.Response) -> str:
    """Extract only non-sensitive Meta error metadata for operational logs."""
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return f"http={response.status_code}"
    code = error.get("code")
    subcode = error.get("error_subcode")
    error_type = error.get("type")
    markers = [f"http={response.status_code}"]
    if isinstance(code, (str, int)):
        markers.append(f"meta_code={code}")
    if isinstance(subcode, (str, int)):
        markers.append(f"meta_subcode={subcode}")
    if isinstance(error_type, str) and error_type.isascii() and error_type.replace("_", "").isalnum():
        markers.append(f"meta_type={error_type}")
    return " ".join(markers)


def _callback_url() -> str:
    return f"{config.public_url.rstrip('/')}/api/threads/oauth/callback"


def _dashboard_redirect(result: str) -> RedirectResponse:
    url = f"{config.public_url.rstrip('/')}/?threads_oauth={result}"
    return RedirectResponse(url, status_code=303)


@router.get("/oauth/start")
async def start_threads_oauth(_: CurrentUserDep, db: DbDep) -> dict[str, str]:
    """Create a one-use OAuth state and return Meta's authorization URL."""
    app_id = config.threads_app_id.strip()
    if not app_id or not config.threads_app_secret.get_secret_value():
        raise HTTPException(status_code=503, detail="Threads App ID or App Secret is not configured on the server")

    redirect_uri = _callback_url()
    parsed_uri = urlparse(redirect_uri)
    if parsed_uri.scheme != "https" and parsed_uri.hostname not in {"localhost", "127.0.0.1"}:
        raise HTTPException(status_code=503, detail="Threads OAuth requires a public HTTPS URL")

    state = secrets.token_urlsafe(32)
    await save_setting(
        db,
        OAUTH_STATE_KEY,
        {"digest": hashlib.sha256(state.encode("utf-8")).hexdigest(), "expires_at": int(time.time()) + 600},
    )
    await db.commit()

    params = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "scope": OAUTH_SCOPE,
            "response_type": "code",
            "state": state,
        }
    )
    return {"authorization_url": f"https://threads.net/oauth/authorize?{params}"}


@router.post("/web-session")
async def import_threads_web_session(
    payload: ThreadsWebSessionImport,
    _: CurrentUserDep,
    db: DbDep,
) -> dict[str, bool]:
    """Store an authenticated Playwright browser state for web-based collection."""
    await save_setting(db, "threads_web_storage_state", payload.storage_state)
    await db.commit()
    return {"configured": True}


@router.delete("/web-session", status_code=204)
async def clear_threads_web_session(_: CurrentUserDep, db: DbDep) -> None:
    """Remove the saved Threads browser state."""
    setting = await db.get(Setting, "threads_web_storage_state")
    if setting is not None:
        await db.delete(setting)
    await db.commit()


@router.get("/oauth/callback")
async def threads_oauth_callback(
    db: DbDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Exchange Meta's authorization code and persist the long-lived token and user ID."""
    if error:
        return _dashboard_redirect("cancelled")
    if not code or not state:
        return _dashboard_redirect("failed")

    saved_state = await get_setting(db, OAUTH_STATE_KEY)
    await save_setting(db, OAUTH_STATE_KEY, None)
    await db.commit()
    if (
        not isinstance(saved_state, dict)
        or not isinstance(saved_state.get("digest"), str)
        or not isinstance(saved_state.get("expires_at"), int)
        or saved_state["expires_at"] < int(time.time())
        or not hmac.compare_digest(
            saved_state.get("digest", ""), hashlib.sha256(state.encode("utf-8")).hexdigest()
        )
    ):
        return _dashboard_redirect("failed")

    app_id = config.threads_app_id.strip()
    app_secret = config.threads_app_secret.get_secret_value()
    if not app_id or not app_secret:
        return _dashboard_redirect("failed")

    base_url = config.threads_api_base_url.rstrip("/")
    redirect_uri = _callback_url()
    timeout = httpx.Timeout(30.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            short_response = await client.post(
                f"{base_url}/oauth/access_token",
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            if not short_response.is_success:
                logger.warning("Threads OAuth code exchange rejected by Meta (%s)", _meta_error_marker(short_response))
                return _dashboard_redirect("failed")
            short_response.raise_for_status()
            short_payload: Any = short_response.json()
            if not isinstance(short_payload, dict):
                return _dashboard_redirect("failed")
            short_token = short_payload.get("access_token")
            user_id = short_payload.get("user_id")
            if not isinstance(short_token, str) or not short_token or user_id is None:
                return _dashboard_redirect("failed")

            long_response = await client.get(
                f"{base_url}/access_token",
                params={
                    "grant_type": "th_exchange_token",
                    "client_secret": app_secret,
                    "access_token": short_token,
                },
            )
            if not long_response.is_success:
                logger.warning(
                    "Threads long-lived token exchange rejected by Meta (%s)",
                    _meta_error_marker(long_response),
                )
                return _dashboard_redirect("failed")
            long_response.raise_for_status()
            long_payload: Any = long_response.json()
            if not isinstance(long_payload, dict):
                return _dashboard_redirect("failed")
            access_token = long_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                return _dashboard_redirect("failed")
    except (httpx.HTTPError, ValueError):
        logger.warning("Threads OAuth token exchange failed")
        return _dashboard_redirect("failed")

    await save_setting(db, "threads_access_token", access_token)
    await save_setting(db, "threads_user_id", str(user_id))
    expires_in = long_payload.get("expires_in")
    if isinstance(expires_in, int) and expires_in > 0:
        await save_setting(db, "threads_token_expires_at", int(time.time()) + expires_in)
    await db.commit()
    return _dashboard_redirect("connected")


def _decode_signed_request(value: str) -> dict[str, Any]:
    """Decode and verify a Meta signed_request using the Threads App Secret."""
    if len(value) > SIGNED_REQUEST_LIMIT:
        raise HTTPException(status_code=413, detail="Signed request is too large")
    parts = value.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="Malformed signed request")
    signature_part, payload_part = parts
    secret = config.threads_app_secret.get_secret_value()
    if not secret:
        raise HTTPException(status_code=503, detail="Threads App Secret is not configured")

    try:
        signature = base64.b64decode(
            signature_part + "=" * (-len(signature_part) % 4), altchars=b"-_", validate=True
        )
        payload_bytes = base64.b64decode(payload_part + "=" * (-len(payload_part) % 4), altchars=b"-_", validate=True)
        payload: Any = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Malformed signed request") from None

    expected = hmac.new(secret.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid signed request signature")
    if not isinstance(payload, dict) or payload.get("algorithm") != "HMAC-SHA256":
        raise HTTPException(status_code=400, detail="Unsupported signed request algorithm")
    if not isinstance(payload.get("user_id"), (str, int)):
        raise HTTPException(status_code=400, detail="Signed request has no user ID")
    return payload


async def _read_signed_request(request: Request) -> str:
    """Read Meta's URL-encoded signed_request field without accepting arbitrary uploads."""
    body = await request.body()
    if len(body) > SIGNED_REQUEST_LIMIT:
        raise HTTPException(status_code=413, detail="Callback body is too large")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Expected an URL-encoded callback")
    try:
        form = parse_qs(body.decode("ascii"), strict_parsing=True, max_num_fields=10)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed callback form") from None
    signed_values = form.get("signed_request", [])
    if len(signed_values) != 1 or not signed_values[0]:
        raise HTTPException(status_code=400, detail="Missing signed_request")
    return signed_values[0]


async def _clear_threads_account(db: AsyncSession, meta_user_id: str) -> bool:
    """Clear stored credentials only when the signed callback matches the connected account."""
    connected_user_id = await get_setting(db, "threads_user_id")
    if not connected_user_id or not hmac.compare_digest(str(connected_user_id), meta_user_id):
        return False
    await save_setting(db, "threads_access_token", "")
    await save_setting(db, "threads_user_id", "")
    await save_setting(db, "threads_token_expires_at", None)
    await save_setting(db, "threads_last_token_refresh", None)
    return True


@router.post("/meta/uninstall")
async def meta_uninstall_callback(request: Request, db: DbDep) -> dict[str, str]:
    """Handle a signed Meta app deauthorization callback idempotently."""
    signed_request = await _read_signed_request(request)
    payload = _decode_signed_request(signed_request)
    await _clear_threads_account(db, str(payload["user_id"]))
    await db.commit()
    return {"status": "ok"}


@router.post("/meta/delete")
async def meta_data_deletion_callback(request: Request, db: DbDep) -> dict[str, str]:
    """Revoke the connected account and purge collected posts for a verified deletion request."""
    signed_request = await _read_signed_request(request)
    payload = _decode_signed_request(signed_request)
    matched_account = await _clear_threads_account(db, str(payload["user_id"]))
    if matched_account:
        await db.execute(delete(Post))
        await save_setting(db, "threads_daily_usage", None)
    confirmation_code = secrets.token_urlsafe(24)
    db.add(MetaDeletionRequest(confirmation_code=confirmation_code, status="completed"))
    await db.commit()
    status_url = (
        f"{config.public_url.rstrip('/')}/api/threads/meta/delete/status/{confirmation_code}"
    )
    return {"url": status_url, "confirmation_code": confirmation_code}


@router.get("/meta/delete/status/{confirmation_code}", response_class=HTMLResponse)
async def meta_data_deletion_status(confirmation_code: str, db: DbDep) -> HTMLResponse:
    """Show the completion state for a Meta data deletion request."""
    record = await db.get(MetaDeletionRequest, confirmation_code)
    if record is None:
        raise HTTPException(status_code=404, detail="Deletion request not found")
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Data deletion status · Example Organization</title>"
        "<body style=\"font:16px/1.6 system-ui,sans-serif;max-width:680px;margin:12vh auto;padding:24px;color:#203038\">"
        "<h1>Data deletion request completed</h1>"
        "<p>The deletion request has been processed. Data associated with a connected Threads account was removed.</p>"
        f"<p>Confirmation code: <code>{record.confirmation_code}</code></p>"
        "</body></html>"
    )
