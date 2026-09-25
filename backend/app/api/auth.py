"""Login and authenticated-user endpoints."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import LoginRequest, TokenResponse, UserRead
from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, verify_password
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DbDep) -> TokenResponse:
    """Authenticate a username/password pair and return a bearer JWT."""
    user = await db.scalar(select(User).where(User.username == payload.username))
    if user is None or not await asyncio.to_thread(verify_password, payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    return TokenResponse(access_token=create_access_token(user.username))


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUserDep) -> User:
    """Return the currently authenticated dashboard user."""
    return current_user
