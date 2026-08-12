"""Creates one folder per identified person and copies their photos into it.

A photo with more than one person in it is copied into every one of those
people's folders -- that's the whole point of clustering per-face instead of
per-photo, so nobody has to decide which single folder a group photo belongs in.
"""

import shutil
from pathlib import Path

from . import db

# The default export destination sits inside the event's own source folder
# (photographers want the result right next to the originals). scanner.py
# skips anything under a folder with this exact name so a later re-scan
# never re-ingests these copies as if they were new source photos.
EXPORT_FOLDER_NAME = "Organizado por pessoa"

SAFE_CHARS = "-_ áéíóúàâêôãõçÁÉÍÓÚÀÂÊÔÃÕÇ"


def safe_folder_name(name):
    cleaned = "".join(c for c in name if c.isalnum() or c in SAFE_CHARS).strip()
    return cleaned or "Pessoa"


def export_event(event_id, output_path):
    conn = db.get_conn()
    try:
        people = db.list_people(conn, event_id)
        output_root = Path(output_path)
        output_root.mkdir(parents=True, exist_ok=True)

        summary = []
        for person in people:
            if person["merge_suggestion_for"] is not None:
                continue
            photos = db.photos_for_person(conn, person["id"])
            if not photos:
                continue

            folder = output_root / safe_folder_name(person["name"])
            folder.mkdir(exist_ok=True)

            copied = 0
            for photo in photos:
                src = Path(photo["path"])
                dest = folder / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                    copied += 1

            summary.append({"person": person["name"], "photos": len(photos), "copied": copied})

        return {"output_path": str(output_root), "people": summary}
    finally:
        conn.close()
