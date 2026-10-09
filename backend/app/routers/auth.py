from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..errors import DomainError
from ..models import User
from ..security import SESSION_COOKIE, Actor, change_pin, client_ip, create_user, login, logout, require_user, verify_pin

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class LoginIn(BaseModel):
    name: str = Field(max_length=80)
    pin: str = Field(max_length=64)


@public.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


@public.post("/auth/login")
def do_login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    res = login(db, client_ip(request), body.name, body.pin)
    response.set_cookie(
        SESSION_COOKIE, res.token, httponly=True, secure=settings.cookie_secure, samesite="strict",
        max_age=settings.session_hours * 3600, path="/",
    )
    return {"user": {"id": res.user.id, "name": res.user.name}, "csrf_token": res.csrf_token}


@public.post("/auth/logout")
def do_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    logout(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(actor: Actor = Depends(require_user)):
    return {"user": {"id": actor.user_id, "name": actor.name}, "csrf_token": actor.csrf_token}


class ChangePinIn(BaseModel):
    current_pin: str = Field(max_length=64)
    new_pin: str = Field(max_length=64)


@router.post("/auth/change-pin")
def do_change_pin(body: ChangePinIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    user = db.get(User, actor.user_id)
    if not verify_pin(user.pin_hash, body.current_pin):
        raise DomainError("invalid_pin", "Current PIN is incorrect", status=403)
    change_pin(db, user, body.new_pin)
    audit.record(db, actor.name, "user.change_pin", "user", user.id)
    db.commit()
    return {"ok": True, "message": "PIN changed. Other devices have been logged out."}


@router.get("/users")
def users(db: Session = Depends(get_db)):
    return [{"id": u.id, "name": u.name, "is_active": u.is_active,
             "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}
            for u in db.scalars(select(User).order_by(User.id))]


class NewUserIn(BaseModel):
    name: str = Field(max_length=80)
    pin: str = Field(max_length=64)
    your_pin: str = Field(max_length=64)


@router.post("/users")
def add_user(body: NewUserIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    me_ = db.get(User, actor.user_id)
    if not verify_pin(me_.pin_hash, body.your_pin):
        raise DomainError("invalid_pin", "Your PIN is incorrect", status=403)
    if db.query(User).filter(User.is_active).count() >= 5:
        raise DomainError("too_many_users", "This app is meant for a few family members only")
    u = create_user(db, body.name, body.pin)
    audit.record(db, actor.name, "user.create", "user", u.id, None, {"name": u.name})
    db.commit()
    return {"id": u.id, "name": u.name}
