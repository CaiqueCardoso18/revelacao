"""Walks an event's folder, detects faces in new/changed photos, stores them.

Runs entirely against the local filesystem and the local SQLite database --
nothing here makes a network call, so a scan never uploads a photographer's
photos anywhere.
"""

from pathlib import Path

from . import db, face_engine
from .exporter import EXPORT_FOLDER_NAME

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}


def find_photos(folder_path):
    root = Path(folder_path)
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and EXPORT_FOLDER_NAME not in p.relative_to(root).parts
    )


def scan_event(event_id, folder_path, progress_cb=None):
    """Detects faces for every new/changed photo under folder_path.

    progress_cb(current, total) is called after each photo, so the frontend
    can show a live counter for large (5000+ photo) libraries.
    """
    conn = db.get_conn()
    try:
        photos = find_photos(folder_path)
        total = len(photos)
        db.update_scan_progress(conn, event_id, status="scanning", current=0, total=total)

        for i, photo_path in enumerate(photos, start=1):
            stat = photo_path.stat()
            photo_id, changed = db.upsert_photo(
                conn,
                event_id=event_id,
                path=str(photo_path),
                filename=photo_path.name,
                mtime=stat.st_mtime,
                size=stat.st_size,
            )

            if changed:
                faces = face_engine.detect_faces(photo_path)
                for face in faces:
                    db.insert_face(
                        conn,
                        photo_id=photo_id,
                        event_id=event_id,
                        bbox=face["bbox"],
                        det_score=face["det_score"],
                        embedding=face["embedding"],
                    )
                db.set_photo_face_count(conn, photo_id, len(faces))

            db.update_scan_progress(conn, event_id, current=i)
            if progress_cb:
                progress_cb(i, total)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        db.update_scan_progress(conn, event_id, status="error", error=str(exc))
        raise
    finally:
        conn.close()
