"""Settings and notification-test endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SettingsRead, SettingsUpdate
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.settings_service import read_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=SettingsRead)
async def get_settings(db: DbDep) -> SettingsRead:
    """Read integration settings while returning only secret-presence flags."""
    return await read_settings(db)


@router.put("", response_model=SettingsRead)
async def put_settings(payload: SettingsUpdate, db: DbDep) -> SettingsRead:
    """Update integration settings; empty secret values leave existing values intact."""
    return await update_settings(db, payload)
