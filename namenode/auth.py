"""Autenticación básica: hashing bcrypt + tokens JWT."""
import time
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from common import config
from namenode import db

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    # bcrypt admite máximo 72 bytes.
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    pw = password.encode("utf-8")[:72]
    try:
        return bcrypt.checkpw(pw, password_hash.encode("utf-8"))
    except ValueError:
        return False


def seed_users():
    for pair in config.SEED_USERS.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        username, password = pair.split(":", 1)
        if not db.get_user(username):
            db.create_user(username, hash_password(password))


def authenticate(username: str, password: str) -> Optional[str]:
    user = db.get_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    payload = {
        "sub": username,
        "exp": int(time.time()) + config.JWT_EXPIRES_SECONDS,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token requerido"
        )
    try:
        payload = jwt.decode(
            creds.credentials, config.JWT_SECRET, algorithms=["HS256"]
        )
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
