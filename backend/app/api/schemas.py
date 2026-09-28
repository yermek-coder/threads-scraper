"""Pydantic request and response models."""

import json
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


class ConversationPostRead(BaseModel):
    """A verified preceding post displayed alongside a collected reply."""

    post_id: str
    username: str
    permalink: str
    text: str


class PostRead(BaseModel):
    id: int
    threads_post_id: str
    text: str
    author_username: str
    permalink: str
    post_timestamp: datetime | None
    likes_count: int
    replies_count: int
    reposts_count: int
    matched_keyword_id: int | None
    matched_keyword: str | None = None
    is_relevant: bool
    is_duplicate: bool
    duplicate_of_id: int | None
    is_read: bool
    raw_data: dict[str, Any]
    created_at: datetime
    conversation_context: list[ConversationPostRead] = Field(default_factory=list)
    extraction_status: str = "legacy_unverified"
    extraction_source: str = "unknown"
    extraction_parser_version: str = "legacy"
    extraction_reason: str | None = None
    extraction_last_attempt_status: str | None = None
    extraction_last_attempt_reason: str | None = None


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
    threads_collection_method: Literal["web", "api"] | None = None
    openrouter_api_key: str | None = Field(default=None, max_length=4096)
    jev_relevance_enabled: bool | None = None
    jev_relevance_prompt: str | None = Field(default=None, min_length=1, max_length=8000)
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

    @field_validator("jev_relevance_prompt")
    @classmethod
    def clean_jev_relevance_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Jev relevance prompt cannot be blank")
        return cleaned


class SettingsRead(BaseModel):
    threads_app_id_configured: bool
    threads_access_token_configured: bool
    threads_collection_method: Literal["web", "api"]
    threads_web_session_configured: bool
    openrouter_api_key_configured: bool
    jev_relevance_enabled: bool
    jev_relevance_prompt: str
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


class ThreadsWebSessionImport(BaseModel):
    storage_state: dict[str, Any] | list[dict[str, Any]]

    @field_validator("storage_state")
    @classmethod
    def validate_storage_state(cls, value: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        if isinstance(value, list):
            value = {"cookies": value, "origins": []}
        elif "origins" not in value:
            value = {**value, "origins": []}
        cookies = value.get("cookies")
        origins = value.get("origins")
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise ValueError("Expected a Playwright storage state with cookies and origins")
        accepted_domains = ("threads.com", "threads.net", "instagram.com")
        has_session_cookie = any(
            isinstance(cookie, dict)
            and cookie.get("name") == "sessionid"
            and isinstance(cookie.get("domain"), str)
            and any(
                cookie["domain"].lower().lstrip(".") == domain
                or cookie["domain"].lower().lstrip(".").endswith(f".{domain}")
                for domain in accepted_domains
            )
            for cookie in cookies
        )
        if not has_session_cookie:
            raise ValueError("Storage state must include the signed-in Threads or Instagram sessionid cookie")
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            same_site = cookie.get("sameSite")
            if isinstance(same_site, str):
                normalized_same_site = {
                    "no_restriction": "None",
                    "none": "None",
                    "lax": "Lax",
                    "strict": "Strict",
                }.get(same_site.lower())
                if normalized_same_site:
                    cookie["sameSite"] = normalized_same_site
                else:
                    cookie.pop("sameSite", None)
            if "expires" not in cookie and isinstance(cookie.get("expirationDate"), (float, int)):
                cookie["expires"] = int(cookie.pop("expirationDate"))
            for unsupported_key in ("hostOnly", "session", "storeId", "expirationDate"):
                cookie.pop(unsupported_key, None)
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            raise ValueError("Storage state must contain valid JSON data") from None
        if len(encoded.encode("utf-8")) > 1_000_000:
            raise ValueError("Storage state must be no larger than 1 MB")
        return value


class TestNotificationRequest(BaseModel):
    channel: Literal["email", "whatsapp"]


class CollectResponse(BaseModel):
    keyword_id: int | None
    scanned: int = 0
    saved: int = 0
    updated: int = 0
    extraction_failures: int = 0
    discovery_complete: bool = True
    discovery_reason: str | None = None
    error: str | None = None
