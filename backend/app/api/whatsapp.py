"""Authenticated proxy endpoints for the private WhatsApp bridge."""

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.config import config
from app.core.security import get_current_user

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"], dependencies=[Depends(get_current_user)])


async def _bridge_request(method: str, path: str) -> httpx.Response:
    """Call the bridge and convert transport failures into a gateway error."""
    url = f"{config.whatsapp_bridge_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
            return await client.request(method, url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="WhatsApp bridge is unavailable") from exc


@router.get("/status")
async def whatsapp_status() -> Any:
    """Return the bridge's connection and login state."""
    response = await _bridge_request("GET", "/api/status")
    if response.is_error:
        raise HTTPException(status_code=502, detail="WhatsApp bridge returned an error")
    return response.json()


@router.get("/qr")
async def whatsapp_qr() -> Response:
    """Return the active pairing QR code as a PNG image."""
    response = await _bridge_request("GET", "/api/qr")
    if response.is_error:
        detail = "WhatsApp QR code is not currently available"
        try:
            detail = response.json().get("error", detail)
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    return Response(content=response.content, media_type=response.headers.get("content-type", "image/png"))


@router.post("/logout")
async def whatsapp_logout() -> Any:
    """Unlink the paired WhatsApp account through the bridge."""
    response = await _bridge_request("POST", "/api/logout")
    if response.is_error:
        raise HTTPException(status_code=502, detail="WhatsApp bridge could not log out")
    return response.json()
