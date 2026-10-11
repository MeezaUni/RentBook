"""PIN authentication, server-side sessions, CSRF and login rate limiting."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import re
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .errors import DomainError
from .models import LoginAttempt, User, UserSession, utcnow

SESSION_COOKIE = "rb_session"
CSRF_HEADER = "X-CSRF-Token"

# Argon2id (the argon2-cffi default type) with OWASP-recommended parameters.
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
# Used to equalise timing when the user name does not exist.
_DUMMY_HASH = _hasher.hash("not-a-real-pin-" + secrets.token_hex(8))

PIN_RE = re.compile(r"^\d{6,12}$")


class AuthError(DomainError):
    status_code = 401


def validate_pin(pin: str) -> None:
    if not isinstance(pin, str) or not PIN_RE.match(pin):
        raise DomainError("weak_pin", "PIN must be 6 to 12 digits.")
    if len(set(pin)) == 1 or pin in "01234567890123" or pin in "98765432109876":
        raise DomainError("weak_pin", "PIN is too easy to guess (repeated or sequential digits).")


def hash_pin(pin: str) -> str:
    validate_pin(pin)
    return _hasher.hash(pin)


def verify_pin(pin_hash: str, pin: str) -> bool:
    try:
        return _hasher.verify(pin_hash, pin)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    # The direct peer, or the forwarded client address when RENTBOOK_FORWARDED_ALLOW_IPS
    # names the proxy (uvicorn rewrites request.client; see cli.serve).
    return request.client.host if request.client else "unknown"


def rate_limit_key(ip: str) -> str:
    """The address login limits are counted against.

    One IPv6 subscriber (a phone, a home line) is normally given a whole /64 and can pick any address
    in it, so the /64 counts as one visitor. Otherwise each new address would get fresh guesses.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip[:64]
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((addr, 64), strict=False))
    return str(addr)


def _check_rate_limit(db: Session, ip: str) -> None:
    window_start = utcnow() - dt.timedelta(minutes=settings.login_window_minutes)
    failures = db.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.ip == ip,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.attempted_at >= window_start,
        )
    )
    if failures and failures >= settings.login_max_failures:
        raise DomainError(
            "too_many_attempts",
            f"Too many failed attempts. Try again in {settings.login_window_minutes} minutes.",
            status=429,
        )


@dataclass
class LoginResult:
    user: User
    token: str
    csrf_token: str
    expires_at: dt.datetime


def login(db: Session, ip: str, name: str, pin: str) -> LoginResult:
    ip = rate_limit_key(ip)
    _check_rate_limit(db, ip)
    user = db.scalar(
        select(User).where(func.lower(User.name) == (name or "").strip().lower(), User.is_active)
    )
    ok = verify_pin(user.pin_hash if user else _DUMMY_HASH, pin or "")
    db.add(LoginAttempt(ip=ip, succeeded=bool(ok and user)))
    if not (ok and user):
        db.commit()
        raise AuthError("invalid_login", "Name or PIN is incorrect.")
    if _hasher.check_needs_rehash(user.pin_hash):
        user.pin_hash = _hasher.hash(pin)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = utcnow() + dt.timedelta(hours=settings.session_hours)
    db.add(UserSession(token_hash=_token_hash(token), csrf_token=csrf, user_id=user.id, expires_at=expires))
    user.last_login_at = utcnow()
    # Opportunistic cleanup of stale rows.
    db.query(LoginAttempt).filter(LoginAttempt.attempted_at < utcnow() - dt.timedelta(days=30)).delete()
    db.commit()
    return LoginResult(user=user, token=token, csrf_token=csrf, expires_at=expires)


def logout(db: Session, token: str | None) -> None:
    if not token:
        return
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if sess and not sess.revoked_at:
        sess.revoked_at = utcnow()
        db.commit()


@dataclass
class Actor:
    user_id: int
    name: str
    session_id: int
    csrf_token: str


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_user(request: Request, db: Session = Depends(get_db)) -> Actor:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AuthError("not_authenticated", "Please log in.")
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if not sess or sess.revoked_at or sess.expires_at < utcnow() or not sess.user.is_active:
        raise AuthError("not_authenticated", "Your session has expired. Please log in again.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), sess.csrf_token.encode()):
            raise DomainError("csrf_failed", "Security token missing or invalid. Reload the page.", status=403)
    return Actor(user_id=sess.user_id, name=sess.user.name, session_id=sess.id, csrf_token=sess.csrf_token)


def create_user(db: Session, name: str, pin: str) -> User:
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise DomainError("invalid_name", "Name must be 1-80 characters.")
    if db.scalar(select(User).where(func.lower(User.name) == name.lower())):
        raise DomainError("user_exists", f"A user named '{name}' already exists.")
    user = User(name=name, pin_hash=hash_pin(pin))
    db.add(user)
    db.flush()
    return user


def change_pin(db: Session, user: User, new_pin: str) -> None:
    user.pin_hash = hash_pin(new_pin)
    # Revoke all other sessions of that user.
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))):
        s.revoked_at = utcnow()
