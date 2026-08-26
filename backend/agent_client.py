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
import webbrowser
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

DEVICE_POLL_INTERVAL = 3  # seconds
DEVICE_POLL_TIMEOUT = 600  # seconds -- matches the hub's DEVICE_CODE_TTL


def _http_base(ws_url):
    return ws_url.replace("wss://", "https://").replace("ws://", "http://").split("/agent/connect")[0]


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


async def start_device_pairing(hub_url=None):
    """Zero-typing pairing: opens a browser tab, waits for one click there.

    Never blocks the caller for long and never raises -- if the hub is
    unreachable (no internet, hub down) this just logs and returns None so
    the app keeps working fully offline, exactly as it always could.
    """
    http_base = _http_base(hub_url or HUB_URL)

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(http_base + "/api/device/start")
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.info("agent: hub indisponível agora -- seguindo só localmente")
            return None

        device_code = data["device_code"]
        verification_url = data["verification_url"]

        print()
        print("Pra acessar suas fotos de qualquer navegador (opcional), confirme na")
        print("aba que vai abrir agora. Pode fechar essa aba pra usar só localmente.")
        print(f"Se não abrir sozinho: {verification_url}")
        print()
        try:
            webbrowser.open(verification_url)
        except Exception:
            pass

        deadline = asyncio.get_event_loop().time() + DEVICE_POLL_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(DEVICE_POLL_INTERVAL)
            try:
                resp = await client.get(http_base + "/api/device/poll", params={"code": device_code})
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            result = resp.json()
            if result.get("status") == "approved":
                token = result["agent_token"]
                save_pairing_token(token)
                print("Conectado! Suas fotos já podem ser vistas de qualquer navegador.\n")
                return token

    logger.info("agent: pareamento não confirmado a tempo -- seguindo só localmente")
    return None


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


async def bootstrap_and_run(hub_url=None):
    """The one call run.command/run.bat make: use a saved token, or try the
    zero-typing pairing flow once, then stay connected if either worked --
    otherwise do nothing further (local-only use is always valid)."""
    token = read_pairing_token()
    if not token:
        token = await start_device_pairing(hub_url=hub_url)
    if token:
        await run_agent(token=token, hub_url=hub_url)


def _json(obj):
    import json

    return json.dumps(obj)


def _loads(raw):
    import json

    return json.loads(raw)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(bootstrap_and_run())
