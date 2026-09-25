"""Notification test endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TestNotificationRequest
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.notifier import send_test_notification

router = APIRouter(
    prefix="/test-notification",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)],
)
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.post("")
async def test_notification(payload: TestNotificationRequest, db: DbDep) -> dict[str, int | str]:
    """Send a test message to every configured recipient for the chosen channel."""
    try:
        count = await send_test_notification(db, payload.channel)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"channel": payload.channel, "sent": count}
