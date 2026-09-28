"""Post feed, search/filter, and review status endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import BigInteger, String, and_, case, cast, delete, func, or_, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import FunctionElement

from app.api.schemas import PostPage, PostRead, PostStatusUpdate
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Keyword, Post
from app.services.threads_post_parser import extraction_diagnostic

router = APIRouter(prefix="/posts", tags=["posts"], dependencies=[Depends(get_current_user)])
DbDep = Annotated[AsyncSession, Depends(get_db)]
_MAX_SAFE_REPOSTS = 999_999_999_999_999_999


class _SearchRepostsJsonType(FunctionElement):
    """Check the original JSON type before casting a search-card count."""

    type = String()
    inherit_cache = True


@compiles(_SearchRepostsJsonType, "postgresql")
def _postgres_reposts_type(element, compiler, **kwargs) -> str:
    column = compiler.process(list(element.clauses)[0], **kwargs)
    return f"json_typeof(({column} -> 'search_engagement') -> 'reposts_count')"


@compiles(_SearchRepostsJsonType, "sqlite")
def _sqlite_reposts_type(element, compiler, **kwargs) -> str:
    column = compiler.process(list(element.clauses)[0], **kwargs)
    return f"json_type({column}, '$.search_engagement.reposts_count')"


def _safe_reposts_order_value():
    raw_value = Post.raw_data["search_engagement"]["reposts_count"]
    raw_text = cast(raw_value, String)
    integer_json = or_(
        _SearchRepostsJsonType(Post.raw_data) == "number",
        _SearchRepostsJsonType(Post.raw_data) == "integer",
    )
    return case(
        (and_(integer_json, raw_text.regexp_match(r"^[0-9]{1,18}$")), cast(raw_text, BigInteger)),
        else_=0,
    )


def _post_read(post: Post, phrase: str | None) -> PostRead:
    diagnostic = extraction_diagnostic(post.raw_data, post.threads_post_id)
    raw_data = post.raw_data if isinstance(post.raw_data, dict) else {}
    last_attempt = raw_data.get("extraction_last_attempt")
    search_engagement = raw_data.get("search_engagement")
    raw_reposts_count = (
        search_engagement.get("reposts_count") if isinstance(search_engagement, dict) else None
    )
    reposts_count = (
        raw_reposts_count
        if isinstance(raw_reposts_count, int)
        and not isinstance(raw_reposts_count, bool)
        and 0 <= raw_reposts_count <= _MAX_SAFE_REPOSTS
        else 0
    )
    status_value = str(diagnostic.get("status") or "legacy_unverified")
    safe_diagnostic = {
        "post_id": post.threads_post_id,
        "source": str(diagnostic.get("source") or "unknown"),
        "parser_version": str(diagnostic.get("parser_version") or "legacy"),
        "status": status_value,
        "reason": diagnostic.get("reason") if isinstance(diagnostic.get("reason"), str) else None,
        "attempted_at": diagnostic.get("attempted_at") if isinstance(diagnostic.get("attempted_at"), str) else None,
    }
    safe_raw_data = {"source": safe_diagnostic["source"], "extraction": safe_diagnostic}
    if isinstance(last_attempt, dict):
        safe_raw_data["extraction_last_attempt"] = {
            "post_id": post.threads_post_id,
            "source": str(last_attempt.get("source") or "unknown"),
            "parser_version": str(last_attempt.get("parser_version") or "unknown"),
            "status": str(last_attempt.get("status") or "unknown"),
            "reason": last_attempt.get("reason") if isinstance(last_attempt.get("reason"), str) else None,
            "attempted_at": last_attempt.get("attempted_at") if isinstance(last_attempt.get("attempted_at"), str) else None,
        }
    return PostRead(
        id=post.id,
        threads_post_id=post.threads_post_id,
        # Old heuristic text remains in storage for audit/recovery, but is never
        # represented to clients as verified content.
        text=post.text if status_value == "success" else "",
        author_username=post.author_username,
        permalink=post.permalink,
        post_timestamp=post.post_timestamp,
        likes_count=post.likes_count,
        replies_count=post.replies_count,
        reposts_count=reposts_count,
        matched_keyword_id=post.matched_keyword_id,
        matched_keyword=phrase,
        is_relevant=post.is_relevant,
        is_duplicate=post.is_duplicate,
        duplicate_of_id=post.duplicate_of_id,
        is_read=post.is_read,
        raw_data=safe_raw_data,
        created_at=post.created_at,
        extraction_status=status_value,
        extraction_source=str(diagnostic.get("source") or "unknown"),
        extraction_parser_version=str(diagnostic.get("parser_version") or "legacy"),
        extraction_reason=diagnostic.get("reason") if isinstance(diagnostic.get("reason"), str) else None,
        extraction_last_attempt_status=(
            str(last_attempt.get("status"))
            if isinstance(last_attempt, dict) and isinstance(last_attempt.get("status"), str)
            else None
        ),
        extraction_last_attempt_reason=(
            last_attempt.get("reason")
            if isinstance(last_attempt, dict) and isinstance(last_attempt.get("reason"), str)
            else None
        ),
    )


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
    sort_by: Literal["newest", "engagement"] = Query(default="newest"),
) -> PostPage:
    """Return a paginated feed with optional filters and order."""
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
        verified_text = and_(
            Post.raw_data["extraction"]["status"].as_string() == "success",
            Post.raw_data["extraction"]["post_id"].as_string() == Post.threads_post_id,
            Post.text.ilike(term),
        )
        filters.append(verified_text | Post.author_username.ilike(term))

    total = int(await db.scalar(select(func.count(Post.id)).where(*filters)) or 0)
    order_by = [Post.post_timestamp.desc().nulls_last(), Post.id.desc()]
    if sort_by == "engagement":
        reposts_count = _safe_reposts_order_value()
        engagement = Post.likes_count + Post.replies_count + reposts_count
        order_by.insert(0, engagement.desc())
    rows = (
        await db.execute(
            select(Post, Keyword.phrase)
            .outerjoin(Keyword, Keyword.id == Post.matched_keyword_id)
            .where(*filters)
            .order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [_post_read(post, phrase) for post, phrase in rows]
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
    return _post_read(post, phrase)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_posts(db: DbDep) -> None:
    """Delete every collected post, regardless of feed filters."""
    await db.execute(delete(Post))
    await db.commit()


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: int, db: DbDep) -> None:
    """Delete a collected post from the feed."""
    post = await db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    await db.delete(post)
    await db.commit()
