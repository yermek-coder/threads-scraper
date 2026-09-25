"""Database entities for users, search configuration, posts, and notifications."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class User(Base):
    """A dashboard user with a bcrypt password hash."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Keyword(Base):
    """A phrase polled against public Threads posts."""

    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("phrase", name="uq_keywords_phrase"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phrase: Mapped[str] = mapped_column(String(255), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StopWord(Base):
    """A word or phrase that makes a post irrelevant."""

    __tablename__ = "stopwords"
    __table_args__ = (UniqueConstraint("word_or_phrase", name="uq_stopwords_word_or_phrase"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    word_or_phrase: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Post(Base):
    """A discovered Threads post and its review/deduplication state."""

    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("threads_post_id", name="uq_posts_threads_post_id"),
        Index("ix_posts_post_timestamp", "post_timestamp"),
        Index("ix_posts_review_filters", "is_read", "is_relevant", "is_duplicate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    threads_post_id: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    author_username: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    permalink: Mapped[str] = mapped_column(Text, default="", nullable=False)
    post_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    likes_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replies_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matched_keyword_id: Mapped[int | None] = mapped_column(
        ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_relevant: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """A persisted key/value setting; secret values are never returned by the API."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NotificationLog(Base):
    """Delivery attempt audit trail for email and WhatsApp notifications."""

    __tablename__ = "notification_logs"
    __table_args__ = (Index("ix_notification_logs_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
