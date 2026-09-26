"""Settings and notification-test endpoints."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SettingsRead, SettingsUpdate
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.filter_service import reprocess_recent_posts_for_relevance
from app.services.settings_service import DEFAULT_JEV_RELEVANCE_PROMPT, get_setting, read_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=SettingsRead)
async def get_settings(db: DbDep) -> SettingsRead:
    """Read integration settings while returning only secret-presence flags."""
    return await read_settings(db)


@router.put("", response_model=SettingsRead)
async def put_settings(payload: SettingsUpdate, db: DbDep, background_tasks: BackgroundTasks) -> SettingsRead:
    """Update integration settings; empty secret values leave existing values intact."""
    previous_prompt = await get_setting(db, "jev_relevance_prompt") or DEFAULT_JEV_RELEVANCE_PROMPT
    prompt_changed = payload.jev_relevance_prompt is not None and payload.jev_relevance_prompt != previous_prompt
    settings = await update_settings(db, payload)
    if prompt_changed and settings.jev_relevance_enabled and settings.openrouter_api_key_configured:
        background_tasks.add_task(reprocess_recent_posts_for_relevance)
    return settings
