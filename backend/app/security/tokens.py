"""Stateless bearer tokens for auth — no server-side session or revocation list,
an intentional trade-off at this app's scale. `secret_key` is passed in rather than
read from settings here, so this stays unit-testable without settings/env plumbing.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=30)


class InvalidToken(Exception):
    pass


def create_access_token(user_id: uuid.UUID, secret_key: str) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, secret_key: str) -> uuid.UUID:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidToken("invalid or expired token") from exc
