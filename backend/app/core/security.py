import hashlib
import os
from datetime import datetime, timedelta, timezone

import jwt

from app.core.errors import ApiError


def jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "nascon-jwt-dev")


def hash_password(password: str) -> str:
    salt = b"nascon-password-salt"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 1000)
    return digest.hex()


def verify_password(password: str, stored: str) -> bool:
    return hash_password(password) == stored


def issue_token(claims: dict, hours: int = 2) -> str:
    now = datetime.now(timezone.utc)
    payload = dict(claims)
    payload["iat"] = int(now.timestamp())
    payload["exp"] = int((now + timedelta(hours=hours)).timestamp())
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def read_token(header: str | None) -> dict:
    if not header or not header.startswith("Bearer "):
        raise ApiError(401, "UNAUTHENTICATED", "Missing bearer token.")
    raw = header.split(" ", 1)[1].strip()
    if not raw:
        raise ApiError(401, "UNAUTHENTICATED", "Missing bearer token.")
    try:
        header_data = jwt.get_unverified_header(raw)
    except jwt.PyJWTError as exc:
        raise ApiError(401, "UNAUTHENTICATED", "Malformed token.") from exc
    if header_data.get("alg") == "none" or header_data.get("alg") != "HS256":
        raise ApiError(401, "UNAUTHENTICATED", "Token algorithm is not allowed.")
    try:
        return jwt.decode(raw, jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(401, "UNAUTHENTICATED", "Token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise ApiError(401, "UNAUTHENTICATED", "Token signature is invalid.") from exc
