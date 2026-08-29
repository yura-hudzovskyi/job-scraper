"""Registration/login — see docs/api.md.

Open self-signup, no email verification, no password reset: deliberately minimal
for a small friends-scale app. See app/services/auth_service.py.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import get_auth_service, get_current_user_id
from app.services.auth_service import AuthService, EmailAlreadyRegistered, InvalidCredentials

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class MeResponse(BaseModel):
    user_id: str
    email: str


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        user, token = await auth_service.register(payload.email, payload.password)
    except EmailAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail="email already registered") from exc
    return TokenResponse(access_token=token, user_id=user.id, email=user.email)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        user, token = await auth_service.login(payload.email, payload.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail="invalid email or password") from exc
    return TokenResponse(access_token=token, user_id=user.id, email=user.email)


@router.get("/me", response_model=MeResponse)
async def me(
    user_id: uuid.UUID = Depends(get_current_user_id),
    auth_service: AuthService = Depends(get_auth_service),
) -> MeResponse:
    user = await auth_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return MeResponse(user_id=user.id, email=user.email)
