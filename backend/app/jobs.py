"""Background jobs for long operations (NFR-14).

A long request sent with ``?async=1`` is stored as a job (method, path, body,
user) and answered with 202 + job id. A worker replays it through the
application itself with an internal credential bound to the job's user, so
authentication, project access checks and the audit log apply unchanged. The
queue lives in the database: in-process worker threads for development
(``AI_ARTICLE_WORKER=inline``, default), ``python -m app.worker`` in
production (``AI_ARTICLE_WORKER=external``).
"""
import json
import os
import re
import threading
import time
import uuid

from sqlalchemy import select

from . import db
from .db import utcnow

LONG_OPERATIONS = [
    (r"/analysis/run$", "Статистический анализ"),
    (r"/literature/pdf$", "Загрузка PDF статей"),
    (r"/literature/discover$", "Поиск похожих публикаций и журналов"),
    (r"/manuscript/consistency$", "Проверка согласованности"),
    (r"/manuscript/review$", "Рецензирование (ИИ)"),
    (r"/dataset/extract$", "Извлечение признаков из заключений"),
    (r"/hypotheses$", "Проверка гипотезы"),
    (r"/findings/[^/]+/interpret$", "Интерпретация находки"),
    (r"/literature/identifiers$", "Загрузка источников"),
    (r"/literature/sources/[^/]+/extract$", "Извлечение тезисов"),
    (r"/literature/findings/[^/]+/compare$", "Сопоставление с литературой"),
    (r"/literature/ground$", "Расстановка цитат"),
    (r"/literature/retractions$", "Проверка отзывов статей"),
    (r"/manuscript/terms/auto$", "Перевод терминов"),
    (r"/manuscript/plan$", "План статьи"),
    (r"/manuscript/sections/[^/]+/generate$", "Генерация раздела"),
    (r"/manuscript/sections/[^/]+/revise$", "Правка раздела"),
    (r"/manuscript/front/generate$", "Варианты названия"),
    (r"/cover/generate$", "Cover letter"),
    (r"/cover/revise$", "Правка cover letter"),
    (r"^/api/journals/[^/]+/extract$", "Извлечение профиля журнала"),
    (r"^/api/journals/[^/]+/recheck$", "Перепроверка guidelines"),
]
WORKERS = int(os.environ.get("AI_ARTICLE_WORKERS", "2"))
_started = False
_app = None


def long_label(method, path):
    if method != "POST":
        return None
    for pattern, label in LONG_OPERATIONS:
        if re.search(pattern, path):
            return label
    return None


def encode_body(raw: bytes) -> str:
    """Request bodies are stored as text; file uploads (multipart, binary) go base64-encoded."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import base64
        return "b64:" + base64.b64encode(raw).decode("ascii")


def _decode_body(body):
    if body and body.startswith("b64:"):
        import base64
        return base64.b64decode(body[4:])
    return (body or "").encode("utf-8")


def enqueue(user, project_id, label, method, path, body, content_type):
    jid = uuid.uuid4().hex
    with db.engine().begin() as c:
        c.execute(db.jobs.insert().values(id=jid, user_id=user["id"], project_id=project_id, label=label, method=method,
                                          path=path, body=body, content_type=content_type, status="queued",
                                          created_at=utcnow()))
    return jid


def _public(j):
    out = {k: j[k] for k in ("id", "project_id", "label", "status", "status_code", "error")}
    for k in ("created_at", "started_at", "finished_at"):
        out[k] = j[k].isoformat() if j[k] else None
    if j["status"] == "done" and j["result"]:
        try:
            out["result"] = json.loads(j["result"])
        except ValueError:
            out["result"] = None
    return out


def get(job_id, user):
    with db.engine().connect() as c:
        j = c.execute(select(db.jobs).where(db.jobs.c.id == job_id, db.jobs.c.user_id == user["id"])).mappings().first()
    return _public(j) if j else None


def active(user, project_id=None):
    q = select(db.jobs).where(db.jobs.c.user_id == user["id"], db.jobs.c.status.in_(["queued", "running"]))
    if project_id:
        q = q.where(db.jobs.c.project_id == project_id)
    with db.engine().connect() as c:
        return [_public(j) for j in c.execute(q.order_by(db.jobs.c.created_at)).mappings()]


def _claim():
    with db.engine().begin() as c:
        cand = c.execute(select(db.jobs.c.id).where(db.jobs.c.status == "queued")
                         .order_by(db.jobs.c.created_at).limit(1)).first()
        if not cand:
            return None
        n = c.execute(db.jobs.update().where(db.jobs.c.id == cand[0], db.jobs.c.status == "queued")
                      .values(status="running", started_at=utcnow())).rowcount
        if n != 1:
            return None
        return c.execute(select(db.jobs).where(db.jobs.c.id == cand[0])).mappings().first()


def run_one(app):
    """Claim and execute one queued job; returns True if a job was processed."""
    from fastapi.testclient import TestClient

    from .auth import job_token
    j = _claim()
    if not j:
        return False
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"x-aia-job": job_token(j["id"]), "x-aia": "1"}
    if j["content_type"]:
        headers["content-type"] = j["content_type"]
    try:
        r = client.request(j["method"], j["path"], content=_decode_body(j["body"]), headers=headers)
        ok = r.status_code < 400
        detail = None
        if not ok:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:500]
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
        values = {"status": "done" if ok else "error", "status_code": r.status_code, "finished_at": utcnow(),
                  "result": r.text if ok else None, "error": None if ok else str(detail)}
    except Exception as exc:  # the job must always finish
        values = {"status": "error", "status_code": 500, "finished_at": utcnow(), "error": f"внутренняя ошибка: {exc}"}
    values["body"] = None  # the request (possibly uploaded files) is not kept once the job is done
    with db.engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == j["id"]).values(**values))
    return True


def recover():
    """Jobs left 'running' by a stopped process are reported as interrupted (LLM calls are not replayed)."""
    with db.engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.status == "running").values(
            status="error", error="прервано перезапуском сервера — запустите снова", finished_at=utcnow()))


def _loop(app, stop):
    while not stop.is_set():
        try:
            if not run_one(app):
                stop.wait(0.5)
        except Exception:
            time.sleep(1)


def start_inline(app):
    global _started
    if _started or os.environ.get("AI_ARTICLE_WORKER", "inline") != "inline":
        return
    _started = True
    recover()
    stop = threading.Event()
    for i in range(WORKERS):
        threading.Thread(target=_loop, args=(app, stop), name=f"aia-worker-{i}", daemon=True).start()
