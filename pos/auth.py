"""POS auth — JWT tokens for cashier/owner logins, stored in kirana_app_users."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, status
from jose import JWTError, jwt

from config import get_settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    s = get_settings()
    to_encode = {**data}
    to_encode["exp"] = datetime.utcnow() + (
        expires_delta or timedelta(minutes=s.pos_token_expire_minutes)
    )
    return jwt.encode(to_encode, s.pos_secret_key, algorithm=s.pos_algorithm)


def decode_token(token: str) -> dict:
    s = get_settings()
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, s.pos_secret_key, algorithms=[s.pos_algorithm])
        username: str = payload.get("sub")
        if not username:
            raise exc
        return payload
    except JWTError:
        raise exc
