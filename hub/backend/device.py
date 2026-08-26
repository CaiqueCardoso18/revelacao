"""Zero-typing pairing: the agent opens a browser tab, the user clicks one
button, the agent picks up the result on its own. No code to copy anywhere.

Same shape as the device-authorization flow CLIs like `gh auth login` use:
the agent asks the hub for a one-time code, opens `verification_url` in the
default browser, and polls until either the user approves it there or it
expires. Codes are single-use and short-lived, and live only in memory --
losing them on a hub restart just means an in-flight pairing has to be
retried, which is harmless.
"""

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import auth
from .db import get_db
from .models import AgentToken, User

router = APIRouter()

DEVICE_CODE_TTL = 600  # seconds

# code -> {"created_at": float, "approved": bool, "agent_token": str | None}
_device_codes: dict[str, dict] = {}


def _prune_expired():
    now = time.time()
    expired = [c for c, entry in _device_codes.items() if now - entry["created_at"] > DEVICE_CODE_TTL]
    for c in expired:
        del _device_codes[c]


@router.post("/api/device/start")
def device_start(request: Request):
    _prune_expired()
    code = secrets.token_urlsafe(24)
    _device_codes[code] = {"created_at": time.time(), "approved": False, "agent_token": None}
    verification_url = str(request.base_url) + "pair.html?code=" + code
    return {"device_code": code, "verification_url": verification_url}


@router.get("/api/device/poll")
def device_poll(code: str):
    entry = _device_codes.get(code)
    if not entry or time.time() - entry["created_at"] > DEVICE_CODE_TTL:
        raise HTTPException(404, "Código expirado ou inválido")
    if not entry["approved"]:
        return {"status": "pending"}
    token = entry["agent_token"]
    del _device_codes[code]  # single-use: the agent only ever gets it once
    return {"status": "approved", "agent_token": token}


class DeviceApproveBody(BaseModel):
    code: str


@router.get("/api/device/info")
def device_info(code: str):
    entry = _device_codes.get(code)
    if not entry or time.time() - entry["created_at"] > DEVICE_CODE_TTL:
        raise HTTPException(404, "Código expirado ou inválido")
    return {"pending": not entry["approved"]}


@router.post("/api/device/approve")
def device_approve(
    body: DeviceApproveBody, user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)
):
    entry = _device_codes.get(body.code)
    if not entry or time.time() - entry["created_at"] > DEVICE_CODE_TTL:
        raise HTTPException(404, "Código expirado ou inválido")

    token = AgentToken(user_id=user.id, label="Pareado automaticamente")
    db.add(token)
    db.commit()

    entry["approved"] = True
    entry["agent_token"] = token.token
    return {"ok": True}
