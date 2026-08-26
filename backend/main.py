import io
import subprocess
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import clustering, db, exporter, scanner


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="revelação", lifespan=_lifespan)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_active_scans = set()


@app.exception_handler(Exception)
async def _json_error_handler(request, exc):
    # Starlette's default handler for an unhandled exception returns plain
    # text ("Internal Server Error"), which the frontend can't res.json() --
    # in Safari that surfaces as a cryptic "The string did not match the
    # expected pattern" instead of any useful message. Always return JSON.
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=500, content={"detail": str(exc) or exc.__class__.__name__})


# ---- schemas ----


class NewEvent(BaseModel):
    label: str
    folder_path: str


class RenamePerson(BaseModel):
    name: str


class MergePeople(BaseModel):
    source_id: int
    target_id: int


class ExportRequest(BaseModel):
    output_path: str


# ---- scan orchestration ----

RECLUSTER_EVERY = 150  # photos, during a scan -- cheap enough to not slow the scan down


def _run_scan_and_cluster(event_id, folder_path):
    def on_progress(current, total):
        if current < total and current % RECLUSTER_EVERY == 0:
            try:
                clustering.cluster_event(event_id)
            except Exception:
                # Best-effort live preview -- a hiccup here shouldn't abort the
                # scan. The final pass below still runs and surfaces real errors.
                pass

    try:
        scanner.scan_event(event_id, folder_path, progress_cb=on_progress)

        conn = db.get_conn()
        db.update_scan_progress(conn, event_id, status="clustering")
        conn.close()

        clustering.cluster_event(event_id)

        conn = db.get_conn()
        db.update_scan_progress(conn, event_id, status="done")
        conn.close()
    except Exception as exc:
        conn = db.get_conn()
        db.update_scan_progress(conn, event_id, status="error", error=str(exc))
        conn.close()
    finally:
        _active_scans.discard(event_id)


def _start_scan(event_id, folder_path):
    if event_id in _active_scans:
        return False
    _active_scans.add(event_id)
    thread = threading.Thread(
        target=_run_scan_and_cluster, args=(event_id, folder_path), daemon=True
    )
    thread.start()
    return True


# ---- native folder picker (macOS) ----


@app.post("/api/pick-folder")
def api_pick_folder(prompt: str = "Selecione a pasta do evento"):
    script = f'POSIX path of (choose folder with prompt "{prompt}")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=120
        )
    except FileNotFoundError:
        return {"path": None, "error": "not_macos"}

    if result.returncode != 0:
        return {"path": None}
    return {"path": result.stdout.strip()}


# ---- events ----


@app.get("/api/events")
def api_list_events():
    conn = db.get_conn()
    try:
        events = db.list_events(conn)
        return [_event_summary(conn, e) for e in events]
    finally:
        conn.close()


@app.post("/api/events")
def api_create_event(body: NewEvent):
    folder = Path(body.folder_path)
    if not folder.is_dir():
        raise HTTPException(400, f"Pasta não encontrada: {body.folder_path}")

    event_id = uuid.uuid4().hex[:12]
    conn = db.get_conn()
    try:
        db.create_event(conn, event_id, body.label, str(folder))
    finally:
        conn.close()

    _start_scan(event_id, str(folder))
    return {"id": event_id}


@app.get("/api/events/{event_id}")
def api_get_event(event_id: str):
    conn = db.get_conn()
    try:
        event = db.get_event(conn, event_id)
        if not event:
            raise HTTPException(404, "Evento não encontrado")
        return _event_detail(conn, event)
    finally:
        conn.close()


@app.post("/api/events/{event_id}/rescan")
def api_rescan_event(event_id: str):
    conn = db.get_conn()
    try:
        event = db.get_event(conn, event_id)
        if not event:
            raise HTTPException(404, "Evento não encontrado")
    finally:
        conn.close()
    started = _start_scan(event_id, event["folder_path"])
    return {"started": started}


@app.post("/api/events/{event_id}/export")
def api_export_event(event_id: str, body: ExportRequest):
    conn = db.get_conn()
    try:
        event = db.get_event(conn, event_id)
        if not event:
            raise HTTPException(404, "Evento não encontrado")
    finally:
        conn.close()
    return exporter.export_event(event_id, body.output_path)


# ---- people ----


@app.post("/api/people/{person_id}/rename")
def api_rename_person(person_id: int, body: RenamePerson):
    conn = db.get_conn()
    try:
        db.rename_person(conn, person_id, body.name)
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/people/merge")
def api_merge_people(body: MergePeople):
    conn = db.get_conn()
    try:
        db.merge_people(conn, body.source_id, body.target_id)
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/people/{person_id}/cover")
def api_person_cover(person_id: int, size: int = 200):
    conn = db.get_conn()
    try:
        person = db.get_person(conn, person_id)
        if not person or person["cover_face_id"] is None:
            raise HTTPException(404, "Sem foto de capa")
        face = db.get_face(conn, person["cover_face_id"])
        photo = db.get_photo(conn, face["photo_id"])
    finally:
        conn.close()
    return _face_crop_response(photo["path"], face, size)


@app.get("/api/faces/{face_id}/crop")
def api_face_crop(face_id: int, size: int = 200):
    conn = db.get_conn()
    try:
        face = db.get_face(conn, face_id)
        if not face:
            raise HTTPException(404, "Rosto não encontrado")
        photo = db.get_photo(conn, face["photo_id"])
    finally:
        conn.close()
    return _face_crop_response(photo["path"], face, size)


@app.get("/api/photos/{photo_id}/thumbnail")
def api_photo_thumbnail(photo_id: int, size: int = 360):
    conn = db.get_conn()
    try:
        photo = db.get_photo(conn, photo_id)
    finally:
        conn.close()
    if not photo:
        raise HTTPException(404, "Foto não encontrada")

    img = Image.open(photo["path"]).convert("RGB")
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")


def _face_crop_response(photo_path, face, size, margin=0.4):
    img = Image.open(photo_path).convert("RGB")
    x, y, w, h = face["bbox_x"], face["bbox_y"], face["bbox_w"], face["bbox_h"]
    cx, cy = x + w / 2, y + h / 2
    half = max(w, h) * (0.5 + margin)
    left, top = max(0, cx - half), max(0, cy - half)
    right, bottom = min(img.width, cx + half), min(img.height, cy + half)
    crop = img.crop((int(left), int(top), int(right), int(bottom)))
    crop.thumbnail((size, size))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")


@app.get("/api/people/{person_id}/photos")
def api_person_photos(person_id: int):
    conn = db.get_conn()
    try:
        person = db.get_person(conn, person_id)
        if not person:
            raise HTTPException(404, "Pessoa não encontrada")
        photos = db.photos_for_person(conn, person_id)
        result = []
        for photo in photos:
            others = db.co_occurring_people(conn, person_id, photo["id"])
            result.append(
                {
                    "id": photo["id"],
                    "filename": photo["filename"],
                    "with": [{"id": o["id"], "name": o["name"]} for o in others],
                }
            )
        return {"person": person["name"], "photos": result}
    finally:
        conn.close()


# ---- serializers ----


def _event_summary(conn, event):
    pessoas = len(db.confirmed_people(conn, event["id"]))
    return {
        "id": event["id"],
        "label": event["label"],
        "path": event["folder_path"],
        "status": event["scan_status"],
        "current": event["scan_current"],
        "total": event["scan_total"],
        "pessoas": pessoas,
        "review_count": len(db.review_pairs(conn, event["id"])),
    }


def _event_detail(conn, event):
    people = db.confirmed_people(conn, event["id"])
    review = db.review_pairs(conn, event["id"])
    unidentified = db.unidentified_summary(conn, event["id"])

    return {
        "id": event["id"],
        "label": event["label"],
        "path": event["folder_path"],
        "status": event["scan_status"],
        "current": event["scan_current"],
        "total": event["scan_total"],
        "error": event["scan_error"],
        "stats": {
            "fotos": db.count_photos(conn, event["id"]),
            "rostos": db.count_faces(conn, event["id"]),
        },
        "people": [
            {
                "id": p["id"],
                "name": p["name"],
                "count": p["photo_count"],
            }
            for p in people
        ],
        "review": [
            {
                "id": r["id"],
                "name": r["name"],
                "count": r["photo_count"],
                "mergeWithId": r["target_id"],
                "mergeWith": r["target_name"],
            }
            for r in review
        ],
        "unidentified": unidentified,
    }


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8420)
