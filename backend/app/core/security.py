"""Password hashing and JWT authentication helpers."""

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.core.config import config
from app.core.database import get_db
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a password with bcrypt, rejecting values bcrypt would truncate."""
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Compare a password with its bcrypt hash without leaking parse errors."""
    if len(password.encode("utf-8")) > 72:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(username: str) -> str:
    """Create a signed JWT for a dashboard user."""
    secret = config.secret_key.get_secret_value()
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.jwt_access_token_expire_minutes)
    return jwt.encode({"sub": username, "exp": expires_at}, secret, algorithm=config.jwt_algorithm)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Decode a bearer token and load its user, returning 401 for invalid credentials."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials,
            config.secret_key.get_secret_value(),
            algorithms=[config.jwt_algorithm],
            options={"require_sub": True, "require_exp": True},
        )
        username = payload.get("sub")
        if not isinstance(username, str) or not username:
            raise unauthorized
    except (JWTError, ValueError):
        raise unauthorized from None
    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        raise unauthorized
    return user
