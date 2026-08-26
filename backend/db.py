import sqlite3
import time
import numpy as np
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "revelacao.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    folder_path TEXT NOT NULL UNIQUE,
    output_path TEXT,
    cluster_eps REAL NOT NULL DEFAULT 0.42,
    min_cluster_size INTEGER NOT NULL DEFAULT 3,
    scan_status TEXT NOT NULL DEFAULT 'idle',
    scan_current INTEGER NOT NULL DEFAULT 0,
    scan_total INTEGER NOT NULL DEFAULT 0,
    scan_started_at REAL,
    scan_finished_at REAL,
    scan_error TEXT,
    next_person_number INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id),
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    faces_detected INTEGER NOT NULL DEFAULT 0,
    UNIQUE(event_id, path)
);
CREATE INDEX IF NOT EXISTS idx_photos_event ON photos(event_id);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id),
    name TEXT NOT NULL,
    cover_face_id INTEGER,
    merge_suggestion_for INTEGER REFERENCES people(id),
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_people_event ON people(event_id);

CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL REFERENCES photos(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    bbox_x REAL NOT NULL,
    bbox_y REAL NOT NULL,
    bbox_w REAL NOT NULL,
    bbox_h REAL NOT NULL,
    det_score REAL NOT NULL,
    embedding BLOB NOT NULL,
    person_id INTEGER REFERENCES people(id)
);
CREATE INDEX IF NOT EXISTS idx_faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS idx_faces_event ON faces(event_id);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
"""


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate(conn)
    finally:
        conn.close()


def _migrate(conn):
    """Adds columns introduced after an event/database already existed on disk."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "next_person_number" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN next_person_number INTEGER NOT NULL DEFAULT 1")
        conn.commit()


def embedding_to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_embedding(blob):
    return np.frombuffer(blob, dtype=np.float32)


# ---- events ----

def create_event(conn, event_id, label, folder_path, output_path=None):
    conn.execute(
        "INSERT INTO events (id, label, folder_path, output_path, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id, label, folder_path, output_path, time.time()),
    )
    conn.commit()


def list_events(conn):
    return conn.execute("SELECT * FROM events ORDER BY created_at DESC").fetchall()


def get_event(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def next_person_number(conn, event_id):
    """Hands out a running, never-reused number for a fresh "Pessoa NN" name.

    Stored per event instead of derived from the current person count so
    numbers stay stable across re-clusters -- otherwise a person who
    temporarily drops out (e.g. their cluster shrank below the threshold)
    would free up their number for someone else to reuse next pass.
    """
    row = conn.execute(
        "SELECT next_person_number FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    n = row["next_person_number"]
    conn.execute(
        "UPDATE events SET next_person_number = ? WHERE id = ?", (n + 1, event_id)
    )
    conn.commit()
    return n


def update_scan_progress(conn, event_id, status=None, current=None, total=None, error=None):
    fields, values = [], []
    if status is not None:
        fields.append("scan_status = ?")
        values.append(status)
        if status == "scanning" and current == 0:
            fields.append("scan_started_at = ?")
            values.append(time.time())
        if status in ("done", "error"):
            fields.append("scan_finished_at = ?")
            values.append(time.time())
    if current is not None:
        fields.append("scan_current = ?")
        values.append(current)
    if total is not None:
        fields.append("scan_total = ?")
        values.append(total)
    if error is not None:
        fields.append("scan_error = ?")
        values.append(error)
    if not fields:
        return
    values.append(event_id)
    conn.execute(f"UPDATE events SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()


# ---- photos ----

def upsert_photo(conn, event_id, path, filename, mtime, size):
    cur = conn.execute(
        "SELECT id, mtime, size FROM photos WHERE event_id = ? AND path = ?",
        (event_id, path),
    ).fetchone()
    if cur and cur["mtime"] == mtime and cur["size"] == size:
        return cur["id"], False
    if cur:
        conn.execute("DELETE FROM faces WHERE photo_id = ?", (cur["id"],))
        conn.execute(
            "UPDATE photos SET mtime = ?, size = ?, faces_detected = 0 WHERE id = ?",
            (mtime, size, cur["id"]),
        )
        conn.commit()
        return cur["id"], True
    cur = conn.execute(
        "INSERT INTO photos (event_id, path, filename, mtime, size) VALUES (?, ?, ?, ?, ?)",
        (event_id, path, filename, mtime, size),
    )
    conn.commit()
    return cur.lastrowid, True


def set_photo_face_count(conn, photo_id, count):
    conn.execute("UPDATE photos SET faces_detected = ? WHERE id = ?", (count, photo_id))
    conn.commit()


def list_photos(conn, event_id):
    return conn.execute(
        "SELECT * FROM photos WHERE event_id = ? ORDER BY filename", (event_id,)
    ).fetchall()


# ---- faces ----

def insert_face(conn, photo_id, event_id, bbox, det_score, embedding):
    x, y, w, h = bbox
    cur = conn.execute(
        "INSERT INTO faces (photo_id, event_id, bbox_x, bbox_y, bbox_w, bbox_h, det_score, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (photo_id, event_id, x, y, w, h, det_score, embedding_to_blob(embedding)),
    )
    conn.commit()
    return cur.lastrowid


def list_faces(conn, event_id):
    return conn.execute("SELECT * FROM faces WHERE event_id = ?", (event_id,)).fetchall()


def clear_person_assignments(conn, event_id):
    conn.execute("UPDATE faces SET person_id = NULL WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM people WHERE event_id = ?", (event_id,))
    conn.commit()


def assign_face_person(conn, face_id, person_id):
    conn.execute("UPDATE faces SET person_id = ? WHERE id = ?", (person_id, face_id))


# ---- people ----

def create_person(conn, event_id, name, cover_face_id=None, merge_suggestion_for=None):
    cur = conn.execute(
        "INSERT INTO people (event_id, name, cover_face_id, merge_suggestion_for, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_id, name, cover_face_id, merge_suggestion_for, time.time()),
    )
    return cur.lastrowid


def list_people(conn, event_id):
    return conn.execute(
        """
        SELECT p.*, COUNT(f.id) AS photo_count
        FROM people p
        LEFT JOIN faces f ON f.person_id = p.id
        WHERE p.event_id = ?
        GROUP BY p.id
        ORDER BY photo_count DESC
        """,
        (event_id,),
    ).fetchall()


def rename_person(conn, person_id, name):
    conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, person_id))
    conn.commit()


def merge_people(conn, source_id, target_id):
    conn.execute(
        "UPDATE faces SET person_id = ? WHERE person_id = ?", (target_id, source_id)
    )
    # Some other, even-smaller fragment may already be suggested to merge
    # into `source_id` (a chain: X -> source_id -> target_id). Re-point it at
    # the new home instead of leaving a dangling reference, which would
    # otherwise fail the foreign-key check on the delete below.
    conn.execute(
        "UPDATE people SET merge_suggestion_for = ? WHERE merge_suggestion_for = ?",
        (target_id, source_id),
    )
    conn.execute("DELETE FROM people WHERE id = ?", (source_id,))
    conn.commit()


def photos_for_person(conn, person_id):
    return conn.execute(
        """
        SELECT DISTINCT ph.*
        FROM faces f JOIN photos ph ON ph.id = f.photo_id
        WHERE f.person_id = ?
        ORDER BY ph.filename
        """,
        (person_id,),
    ).fetchall()


def count_photos(conn, event_id):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM photos WHERE event_id = ?", (event_id,)
    ).fetchone()["n"]


def count_faces(conn, event_id):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE event_id = ?", (event_id,)
    ).fetchone()["n"]


def unidentified_summary(conn, event_id):
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS faces,
            COUNT(DISTINCT photo_id) AS photos
        FROM faces
        WHERE event_id = ? AND person_id IS NULL
        """,
        (event_id,),
    ).fetchone()
    return {"rostos": row["faces"], "fotos": row["photos"]}


def review_pairs(conn, event_id):
    return conn.execute(
        """
        SELECT p.id, p.name, COUNT(f.id) AS photo_count,
               t.id AS target_id, t.name AS target_name
        FROM people p
        JOIN people t ON t.id = p.merge_suggestion_for
        LEFT JOIN faces f ON f.person_id = p.id
        WHERE p.event_id = ? AND p.merge_suggestion_for IS NOT NULL
        GROUP BY p.id
        """,
        (event_id,),
    ).fetchall()


def confirmed_people(conn, event_id):
    return conn.execute(
        """
        SELECT p.*, COUNT(f.id) AS photo_count
        FROM people p
        LEFT JOIN faces f ON f.person_id = p.id
        WHERE p.event_id = ? AND p.merge_suggestion_for IS NULL
        GROUP BY p.id
        ORDER BY photo_count DESC
        """,
        (event_id,),
    ).fetchall()


def get_person(conn, person_id):
    return conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()


def get_photo(conn, photo_id):
    return conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()


def get_face(conn, face_id):
    return conn.execute("SELECT * FROM faces WHERE id = ?", (face_id,)).fetchone()


def co_occurring_people(conn, person_id, photo_id):
    return conn.execute(
        """
        SELECT DISTINCT p.id, p.name
        FROM faces f JOIN people p ON p.id = f.person_id
        WHERE f.photo_id = ? AND f.person_id != ?
        """,
        (photo_id, person_id),
    ).fetchall()
