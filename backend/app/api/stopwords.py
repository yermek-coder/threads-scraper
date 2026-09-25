"""CRUD endpoints for stop-word rules."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import StopWordCreate, StopWordRead
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import StopWord

router = APIRouter(prefix="/stopwords", tags=["stopwords"], dependencies=[Depends(get_current_user)])
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[StopWordRead])
async def list_stopwords(db: DbDep) -> list[StopWord]:
    """List configured stop-word rules."""
    return list((await db.scalars(select(StopWord).order_by(StopWord.id))).all())


@router.post("", response_model=StopWordRead, status_code=status.HTTP_201_CREATED)
async def create_stopword(payload: StopWordCreate, db: DbDep) -> StopWord:
    """Create a unique stop-word rule."""
    stopword = StopWord(word_or_phrase=payload.word_or_phrase)
    db.add(stopword)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Stop word already exists") from None
    await db.refresh(stopword)
    return stopword


@router.delete("/{stopword_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stopword(stopword_id: int, db: DbDep) -> None:
    """Delete a stop-word rule."""
    stopword = await db.get(StopWord, stopword_id)
    if stopword is None:
        raise HTTPException(status_code=404, detail="Stop word not found")
    await db.delete(stopword)
    await db.commit()
