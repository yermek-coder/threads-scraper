"""Post feed, search/filter, and review status endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PostPage, PostRead, PostStatusUpdate
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Keyword, Post

router = APIRouter(prefix="/posts", tags=["posts"], dependencies=[Depends(get_current_user)])
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=PostPage)
async def list_posts(
    db: DbDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword_id: int | None = Query(default=None, ge=1),
    unread_only: bool = False,
    hide_irrelevant: bool = False,
    include_duplicates: bool = False,
    q: str | None = Query(default=None, max_length=500),
) -> PostPage:
    """Return a paginated feed with optional review, keyword, and text filters."""
    filters = []
    if keyword_id is not None:
        filters.append(Post.matched_keyword_id == keyword_id)
    if unread_only:
        filters.append(Post.is_read.is_(False))
    if hide_irrelevant:
        filters.append(Post.is_relevant.is_(True))
    if not include_duplicates:
        filters.append(Post.is_duplicate.is_(False))
    if q and q.strip():
        term = f"%{q.strip()}%"
        filters.append((Post.text.ilike(term)) | (Post.author_username.ilike(term)))

    total = int(await db.scalar(select(func.count(Post.id)).where(*filters)) or 0)
    rows = (
        await db.execute(
            select(Post, Keyword.phrase)
            .outerjoin(Keyword, Keyword.id == Post.matched_keyword_id)
            .where(*filters)
            .order_by(Post.post_timestamp.desc().nulls_last(), Post.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        PostRead(
            id=post.id,
            threads_post_id=post.threads_post_id,
            text=post.text,
            author_username=post.author_username,
            permalink=post.permalink,
            post_timestamp=post.post_timestamp,
            likes_count=post.likes_count,
            replies_count=post.replies_count,
            matched_keyword_id=post.matched_keyword_id,
            matched_keyword=phrase,
            is_relevant=post.is_relevant,
            is_duplicate=post.is_duplicate,
            duplicate_of_id=post.duplicate_of_id,
            is_read=post.is_read,
            raw_data=post.raw_data,
            created_at=post.created_at,
        )
        for post, phrase in rows
    ]
    return PostPage(items=items, total=total, page=page, page_size=page_size)


@router.patch("/{post_id}/status", response_model=PostRead)
async def update_post_status(post_id: int, payload: PostStatusUpdate, db: DbDep) -> PostRead:
    """Update a post's read and relevance state."""
    post = await db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide is_read and/or is_relevant")
    for key, value in changes.items():
        setattr(post, key, value)
    await db.commit()
    await db.refresh(post)
    phrase = await db.scalar(select(Keyword.phrase).where(Keyword.id == post.matched_keyword_id))
    return PostRead(
        id=post.id,
        threads_post_id=post.threads_post_id,
        text=post.text,
        author_username=post.author_username,
        permalink=post.permalink,
        post_timestamp=post.post_timestamp,
        likes_count=post.likes_count,
        replies_count=post.replies_count,
        matched_keyword_id=post.matched_keyword_id,
        matched_keyword=phrase,
        is_relevant=post.is_relevant,
        is_duplicate=post.is_duplicate,
        duplicate_of_id=post.duplicate_of_id,
        is_read=post.is_read,
        raw_data=post.raw_data,
        created_at=post.created_at,
    )
