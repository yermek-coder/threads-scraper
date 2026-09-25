"""Pydantic request and response models."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class KeywordCreate(BaseModel):
    phrase: str = Field(min_length=1, max_length=255)
    is_active: bool = True

    @field_validator("phrase")
    @classmethod
    def clean_phrase(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Phrase cannot be blank")
        return value


class KeywordUpdate(BaseModel):
    phrase: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None

    @field_validator("phrase")
    @classmethod
    def clean_phrase(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Phrase cannot be blank")
        return cleaned


class KeywordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phrase: str
    is_active: bool
    last_polled_at: datetime | None
    created_at: datetime


class StopWordCreate(BaseModel):
    word_or_phrase: str = Field(min_length=1, max_length=255)

    @field_validator("word_or_phrase")
    @classmethod
    def clean_word(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Stop word cannot be blank")
        return cleaned


class StopWordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    word_or_phrase: str
    created_at: datetime


class PostRead(BaseModel):
    id: int
    threads_post_id: str
    text: str
    author_username: str
    permalink: str
    post_timestamp: datetime | None
    likes_count: int
    replies_count: int
    matched_keyword_id: int | None
    matched_keyword: str | None = None
    is_relevant: bool
    is_duplicate: bool
    duplicate_of_id: int | None
    is_read: bool
    raw_data: dict[str, Any]
    created_at: datetime


class PostPage(BaseModel):
    items: list[PostRead]
    total: int
    page: int
    page_size: int


class PostStatusUpdate(BaseModel):
    is_read: bool | None = None
    is_relevant: bool | None = None


class SettingsUpdate(BaseModel):
    threads_access_token: str | None = Field(default=None, max_length=4096)
    threads_user_id: str | None = Field(default=None, max_length=255)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_user: str | None = Field(default=None, max_length=320)
    smtp_pass: str | None = Field(default=None, max_length=4096)
    smtp_from: str | None = Field(default=None, max_length=320)
    smtp_use_tls: bool | None = None
    whatsapp_recipients: list[str] | None = None
    email_recipients: list[str] | None = None

    @field_validator("whatsapp_recipients", "email_recipients")
    @classmethod
    def clean_recipients(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class SettingsRead(BaseModel):
    threads_access_token_configured: bool
    threads_user_id: str
    threads_verify_token: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass_configured: bool
    smtp_from: str
    smtp_use_tls: bool
    whatsapp_recipients: list[str]
    email_recipients: list[str]


class TestNotificationRequest(BaseModel):
    channel: Literal["email", "whatsapp"]


class CollectResponse(BaseModel):
    keyword_id: int | None
    scanned: int = 0
    saved: int = 0
    error: str | None = None
