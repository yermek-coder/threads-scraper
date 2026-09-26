"""CRUD and manual collection endpoints for search phrases."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CollectResponse, KeywordCreate, KeywordRead, KeywordUpdate
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Keyword, Post
from app.services.collector import collect_keyword
from app.services.notifier import send_posts
from app.services.threads_errors import ThreadsCollectionError

router = APIRouter(prefix="/keywords", tags=["keywords"], dependencies=[Depends(get_current_user)])
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[KeywordRead])
async def list_keywords(db: DbDep) -> list[Keyword]:
    """List search phrases in creation order."""
    return list((await db.scalars(select(Keyword).order_by(Keyword.id))).all())


@router.post("", response_model=KeywordRead, status_code=status.HTTP_201_CREATED)
async def create_keyword(payload: KeywordCreate, db: DbDep) -> Keyword:
    """Create a unique keyword phrase."""
    keyword = Keyword(phrase=payload.phrase, is_active=payload.is_active)
    db.add(keyword)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Keyword already exists") from None
    await db.refresh(keyword)
    return keyword


@router.patch("/{keyword_id}", response_model=KeywordRead)
async def update_keyword(keyword_id: int, payload: KeywordUpdate, db: DbDep) -> Keyword:
    """Update a keyword phrase or its active state."""
    keyword = await db.get(Keyword, keyword_id)
    if keyword is None:
        raise HTTPException(status_code=404, detail="Keyword not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(keyword, key, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Keyword already exists") from None
    await db.refresh(keyword)
    return keyword


@router.delete("/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_keyword(keyword_id: int, db: DbDep) -> None:
    """Delete a keyword; already-collected posts remain in the feed."""
    keyword = await db.get(Keyword, keyword_id)
    if keyword is None:
        raise HTTPException(status_code=404, detail="Keyword not found")
    await db.delete(keyword)
    await db.commit()


@router.post("/{keyword_id}/collect", response_model=CollectResponse)
async def collect_one_keyword(keyword_id: int, db: DbDep) -> CollectResponse:
    """Run an immediate search for one keyword."""
    keyword = await db.get(Keyword, keyword_id)
    if keyword is None:
        raise HTTPException(status_code=404, detail="Keyword not found")
    try:
        result = await collect_keyword(db, keyword)
    except ThreadsCollectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CollectResponse(keyword_id=keyword_id, **result)


@router.post("/collect", response_model=list[CollectResponse])
async def collect_active_keywords(db: DbDep) -> list[CollectResponse]:
    """Run an immediate search for every active keyword."""
    keyword_ids = list((await db.scalars(select(Keyword.id).where(Keyword.is_active.is_(True)))).all())
    results: list[CollectResponse] = []
    notification_batch: list[int] = []
    for keyword_id in keyword_ids:
        try:
            keyword = await db.get(Keyword, keyword_id)
            if keyword is None or not keyword.is_active:
                continue
            result = await collect_keyword(db, keyword, notify=False, notification_batch=notification_batch)
        except ThreadsCollectionError as exc:
            await db.rollback()
            results.append(CollectResponse(keyword_id=keyword_id, error=str(exc)))
            continue
        results.append(CollectResponse(keyword_id=keyword_id, **result))
    if notification_batch:
        posts = list((await db.scalars(select(Post).where(Post.id.in_(notification_batch)))).all())
        await send_posts(db, posts)
    return results
