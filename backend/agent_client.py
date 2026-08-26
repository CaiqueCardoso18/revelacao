"""Connects this machine to the hub as an "agent" so the app is reachable
from a normal browser URL, without ever uploading a single photo.

This opens an OUTBOUND WebSocket to the hub (no inbound port needed on this
machine -- works through NAT/home routers with zero configuration) and, for
each request the hub relays over it, dispatches it into the SAME FastAPI
`app` used for local access (backend.main.app) via an in-process ASGI call --
not a second real HTTP server. Every route (scan, cluster, thumbnails,
export, ...) is reused completely unchanged; only the transport differs.

Runs alongside the existing local server (run.command/run.bat start both),
so http://127.0.0.1:8420 keeps working exactly as before regardless of
whether the hub connection is up.
"""

import asyncio
import base64
import logging
import os
from pathlib import Path

import httpx
import websockets

from .main import app

logger = logging.getLogger("revelacao.agent")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOKEN_FILE = DATA_DIR / "agent_token"

HUB_URL = os.environ.get("HUB_URL", "ws://95.216.170.49/agent/connect")

_RECONNECT_MIN = 2
_RECONNECT_MAX = 30


def read_pairing_token():
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip() or None
    return None


def save_pairing_token(token):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip())


async def _dispatch(msg, client: httpx.AsyncClient):
    method = msg.get("method", "GET")
    path = msg.get("path", "/")
    query = msg.get("query") or ""
    url = path + ("?" + query if query else "")
    body_b64 = msg.get("body_b64")
    content = base64.b64decode(body_b64) if body_b64 else None

    try:
        response = await client.request(method, url, content=content)
        return {
            "request_id": msg["request_id"],
            "status": response.status_code,
            "content_type": response.headers.get("content-type", "application/json"),
            "body_b64": base64.b64encode(response.content).decode(),
        }
    except Exception as exc:  # noqa: BLE001 -- must always answer, even on a bug
        logger.exception("agent: error handling relayed request")
        body = f'{{"detail": {str(exc)!r}}}'.encode()
        return {
            "request_id": msg["request_id"],
            "status": 500,
            "content_type": "application/json",
            "body_b64": base64.b64encode(body).decode(),
        }


async def run_agent(token=None, hub_url=None):
    """Keeps a connection to the hub alive, reconnecting with backoff."""
    token = token or read_pairing_token()
    if not token:
        logger.warning("agent: no pairing token configured, not connecting to the hub")
        return

    hub_url = hub_url or HUB_URL
    delay = _RECONNECT_MIN

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        while True:
            try:
                async with websockets.connect(hub_url, ping_interval=20) as ws:
                    await ws.send(_json({"token": token}))
                    hello = _loads(await ws.recv())
                    if hello.get("type") == "error":
                        logger.error("agent: hub rejected pairing token: %s", hello.get("message"))
                        return
                    logger.info("agent: connected to hub")
                    delay = _RECONNECT_MIN

                    async for raw in ws:
                        msg = _loads(raw)
                        reply = await _dispatch(msg, client)
                        await ws.send(_json(reply))
            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                logger.warning("agent: disconnected from hub (%s), retrying in %ss", exc, delay)
            except Exception:
                logger.exception("agent: unexpected error, retrying in %ss", delay)

            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)


def _json(obj):
    import json

    return json.dumps(obj)


def _loads(raw):
    import json

    return json.loads(raw)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_agent())
