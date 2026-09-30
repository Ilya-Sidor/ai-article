"""Uploads in parts (the public tunnel drops request bodies larger than ~1 MB).

The browser sends a large file as numbered parts to POST /api/chunks, then calls the ordinary upload endpoint
with the part set's id in the form field ``refs`` instead of the file. Parts live only in the server's memory,
bound to the user who sent them (the original of a patient file never touches the disk, as with a direct
upload), and are dropped once used or after an hour.
"""
import json
import threading
import time

from fastapi import HTTPException

CHUNK_MAX = 2 * 1024 * 1024
FILE_MAX = 45 * 1024 * 1024
TTL_S = 3600

_store = {}
_lock = threading.Lock()


def _expire():
    now = time.time()
    for key in [k for k, v in _store.items() if now - v["ts"] > TTL_S]:
        del _store[key]


def put(user_id, upload_id, index, total, name, data: bytes):
    if not (0 < len(upload_id) <= 64 and upload_id.replace("-", "").isalnum()):
        raise HTTPException(400, "некорректный идентификатор загрузки")
    if not (0 <= index < total <= FILE_MAX // 1024) or len(data) > CHUNK_MAX:
        raise HTTPException(400, "некорректная часть файла")
    with _lock:
        _expire()
        entry = _store.setdefault((user_id, upload_id), {"name": name, "total": total, "parts": {}, "ts": time.time()})
        if entry["total"] != total:
            raise HTTPException(400, "некорректная часть файла")
        entry["parts"][index] = data  # a repeated part (retry after a dropped connection) replaces the first
        entry["ts"] = time.time()
        if sum(len(p) for p in entry["parts"].values()) > FILE_MAX:
            del _store[(user_id, upload_id)]
            raise HTTPException(413, "файл больше 45 МБ")
        return {"received": len(entry["parts"]), "total": total}


def take(user_id, upload_id):
    with _lock:
        entry = _store.pop((user_id, upload_id), None)
    if entry is None:
        raise HTTPException(400, "части файла не найдены (прошло больше часа?) — загрузите файл снова")
    if len(entry["parts"]) != entry["total"]:
        raise HTTPException(400, f"файл «{entry['name']}» передан не полностью — загрузите снова")
    return entry["name"], b"".join(entry["parts"][i] for i in range(entry["total"]))


async def collect(request, files, refs, limit):
    """(filename, content) of directly attached files and of files assembled from parts."""
    out = []
    for f in files or []:
        content = await f.read(limit + 1)
        out.append((f.filename or "upload", content))
    for upload_id in json.loads(refs) if refs else []:
        out.append(take(request.state.user["id"], str(upload_id)))
    return out
