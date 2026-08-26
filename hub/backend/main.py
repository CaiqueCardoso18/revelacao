import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from . import auth
from .db import get_db, init_db
from .email import send_email
from .models import PasswordResetToken, User

RESET_TOKEN_TTL = timedelta(hours=1)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="revelação hub", lifespan=_lifespan)


@app.exception_handler(Exception)
async def _json_error_handler(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc) or exc.__class__.__name__})


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---- schemas ----


class SignupBody(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v):
        if len(v) < 8:
            raise ValueError("A senha precisa ter pelo menos 8 caracteres")
        return v


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class RequestResetBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v):
        if len(v) < 8:
            raise ValueError("A senha precisa ter pelo menos 8 caracteres")
        return v


def _set_session_cookie(response: Response, user_id: str):
    # Secure cookies are only ever sent back over HTTPS. There's no domain
    # (and therefore no TLS) yet, so this must stay off until one exists --
    # otherwise the browser sets the cookie but never sends it back, and
    # every request looks logged-out. Flip COOKIE_SECURE=true once HTTPS
    # is live.
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.make_session_token(user_id),
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
    )


# ---- routes ----


@app.post("/api/auth/signup")
def signup(body: SignupBody, response: Response, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(409, "Já existe uma conta com esse e-mail")

    user = User(email=body.email.lower(), password_hash=auth.hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    _set_session_cookie(response, user.id)
    return {"id": user.id, "email": user.email}


@app.post("/api/auth/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "E-mail ou senha incorretos")

    _set_session_cookie(response, user.id)
    return {"id": user.id, "email": user.email}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(auth.get_current_user)):
    return {"id": user.id, "email": user.email}


@app.post("/api/auth/request-password-reset")
def request_password_reset(body: RequestResetBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user:
        token = PasswordResetToken(
            user_id=user.id, expires_at=datetime.now(timezone.utc) + RESET_TOKEN_TTL
        )
        db.add(token)
        db.commit()
        send_email(
            user.email,
            "Redefinir senha - revelação",
            f"Use este link para redefinir sua senha (válido por 1 hora): "
            f"/reset-password?token={token.token}",
        )
    # Same response whether or not the email exists, so this can't be used to
    # probe which emails have an account.
    return {"ok": True}


@app.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordBody, db: Session = Depends(get_db)):
    token = db.get(PasswordResetToken, body.token)
    if (
        not token
        or token.used
        or token.expires_at < datetime.now(timezone.utc)
    ):
        raise HTTPException(400, "Link inválido ou expirado")

    user = db.get(User, token.user_id)
    user.password_hash = auth.hash_password(body.new_password)
    token.used = True
    db.commit()
    return {"ok": True}
