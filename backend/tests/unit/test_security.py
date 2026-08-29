import uuid

import pytest

from app.security.passwords import hash_password, verify_password
from app.security.tokens import InvalidToken, create_access_token, decode_access_token


def test_verify_password_accepts_correct_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong", hashed) is False


def test_hash_password_salts_each_call_differently() -> None:
    assert hash_password("same password") != hash_password("same password")


def test_access_token_round_trips_to_the_same_user_id() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "test-secret")
    assert decode_access_token(token, "test-secret") == user_id


def test_decode_access_token_rejects_wrong_secret() -> None:
    token = create_access_token(uuid.uuid4(), "test-secret")
    with pytest.raises(InvalidToken):
        decode_access_token(token, "wrong-secret")


def test_decode_access_token_rejects_garbage() -> None:
    with pytest.raises(InvalidToken):
        decode_access_token("not-a-token", "test-secret")
