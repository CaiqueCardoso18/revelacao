"""Pairs a user's local agent to their account and relays API requests to it.

The hub never stores photos, faces, or events -- every request the browser
makes for that kind of data is forwarded live, over a WebSocket the AGENT
opened outbound (so nothing needs an inbound port on the user's machine), to
whichever machine is currently connected for that user, and the response is
streamed straight back. If nothing is connected, the caller gets a clear
503 instead of stale or fabricated data.
"""

import asyncio
import base64
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import auth
from .db import get_db
from .models import AgentToken, User

router = APIRouter()

RELAY_TIMEOUT = 45  # seconds -- generous enough for a slower local operation (e.g. export)

# Single active agent connection per user, in-memory (fine for one hub
# instance; would need a shared store to run more than one hub replica).
_connections: dict[str, WebSocket] = {}
_pending: dict[str, "asyncio.Future"] = {}


@router.post("/api/pairing-tokens")
def create_pairing_token(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    token = AgentToken(user_id=user.id)
    db.add(token)
    db.commit()
    return {"token": token.token}


@router.get("/api/agent-status")
def agent_status(user: User = Depends(auth.get_current_user)):
    return {"connected": user.id in _connections}


@router.websocket("/agent/connect")
async def agent_connect(ws: WebSocket):
    await ws.accept()

    try:
        first = await ws.receive_json()
    except Exception:
        await ws.close(code=4000)
        return

    token_str = first.get("token")
    db: Session = next(get_db())
    try:
        token = db.get(AgentToken, token_str) if token_str else None
        if not token:
            await ws.send_json({"type": "error", "message": "token inválido"})
            await ws.close(code=4001)
            return

        user_id = token.user_id
        token.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    _connections[user_id] = ws
    await ws.send_json({"type": "connected"})

    try:
        while True:
            msg = await ws.receive_json()
            request_id = msg.get("request_id")
            fut = _pending.pop(request_id, None)
            if fut and not fut.done():
                fut.set_result(msg)
    except WebSocketDisconnect:
        pass
    finally:
        if _connections.get(user_id) is ws:
            del _connections[user_id]


@router.api_route("/api/{path:path}", methods=["GET", "POST"])
async def relay(path: str, request: Request, user: User = Depends(auth.get_current_user)):
    ws = _connections.get(user.id)
    if ws is None:
        raise HTTPException(503, "Seu computador não está conectado agora")

    body = await request.body()
    request_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _pending[request_id] = fut

    await ws.send_json(
        {
            "request_id": request_id,
            "method": request.method,
            "path": "/api/" + path,
            "query": request.url.query,
            "body_b64": base64.b64encode(body).decode() if body else None,
        }
    )

    try:
        result = await asyncio.wait_for(fut, timeout=RELAY_TIMEOUT)
    except asyncio.TimeoutError:
        _pending.pop(request_id, None)
        raise HTTPException(504, "Sem resposta do seu computador (tempo esgotado)")

    content = base64.b64decode(result["body_b64"]) if result.get("body_b64") else b""
    return Response(
        content=content,
        status_code=result.get("status", 200),
        media_type=result.get("content_type") or "application/octet-stream",
    )
