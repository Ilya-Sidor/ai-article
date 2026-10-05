"""FastAPI application: REST API + static prototype UI."""
import io
import re
import zipfile
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import admin_api, auth, authors_api, chunks, cover_api, data_service, ethics_api, jobs, journals_api, literature_api, llm, manuscript_api
from .storage import encrypt_existing
from .analysis import engine, figures
from .literature import metadata as lit_metadata
from .journals.store import JournalError
from .manuscript.store import ManuscriptError
from .literature.service import LiteratureError
from .config import ARTICLE_TYPES, FRONTEND_DIR, MAX_UPLOAD_BYTES
from .ingest import UnsupportedFile
from .storage import NotFound, ProjectStore, read_bytes, read_json

from contextlib import asynccontextmanager  # noqa: E402

from starlette.concurrency import run_in_threadpool  # noqa: E402


@asynccontextmanager
async def lifespan(_app):
    from . import db
    db.engine()
    for p in store.list():  # migration: encrypt projects created before encryption at rest
        encrypt_existing(store.dir(p["id"]))
    jobs.start_inline(_app)
    yield


app = FastAPI(title="AI Article", version="0.2.0", lifespan=lifespan)
store = ProjectStore()

PUBLIC = ("/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/setup", "/api/health")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _deny(status, detail):
    return JSONResponse({"detail": detail}, status_code=status)


@app.middleware("http")
async def access_control(request: Request, call_next):
    """Default deny for the API: session (or job credential), CSRF header, project membership, async jobs."""
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if request.method in MUTATING and request.headers.get("x-aia") != "1":
        return _deny(403, "запрос отклонён (CSRF)")
    if path in PUBLIC or path.startswith("/api/auth/invite/"):
        return await call_next(request)
    user = await run_in_threadpool(auth.user_from_request, request)
    if not user:
        return _deny(401, "требуется вход")
    request.state.user = user
    project_id = None
    m = re.match(r"^/api/projects/([^/]+)(?:/|$)", path)
    if m:
        project_id = m.group(1)
        role = await run_in_threadpool(auth.role, user, project_id)
        if not role:
            return _deny(404, "проект не найден")
        request.state.role = role
        if request.method in MUTATING and role == "commenter" and not re.search(r"/comments(/|$)", path):
            return _deny(403, "роль «комментатор» не позволяет изменять проект")
    m = re.match(r"^/api/journals/([a-z0-9-]+)(?:/|$)", path)
    if m and m.group(1) != "schema":
        denied = await run_in_threadpool(journals_api.check_access, user, m.group(1), request.method)
        if denied:
            return _deny(*denied)
    label = jobs.long_label(request.method, path)
    if label and request.query_params.get("async") == "1":
        body = jobs.encode_body(await request.body())
        jid = await run_in_threadpool(jobs.enqueue, user, project_id, label, request.method, path, body,
                                      request.headers.get("content-type"))
        return JSONResponse({"job_id": jid, "label": label}, status_code=202)
    return await call_next(request)


# Registered after access_control, so it is the outermost layer and also covers its early 401/403/404 answers.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        if k == "Cache-Control" and not request.url.path.startswith("/api/"):
            # the interface itself: revalidate every time (ETag → 304), so an update reaches users at once
            v = "no-cache"
        response.headers.setdefault(k, v)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(auth.router)
app.include_router(admin_api.router)
app.include_router(authors_api.router)


@app.get("/api/health")
def health():
    from sqlalchemy import text

    from . import db
    with db.engine().connect() as c:
        c.execute(text("select 1"))
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    j = jobs.get(job_id, request.state.user)
    if not j:
        raise HTTPException(404, "задача не найдена")
    return j


@app.get("/api/jobs")
def list_jobs(request: Request, project: Optional[str] = None):
    return jobs.active(request.state.user, project)


@app.exception_handler(NotFound)
async def _not_found(_: Request, exc: NotFound):
    return JSONResponse({"detail": "проект не найден"}, status_code=404)


@app.exception_handler(data_service.DataError)
@app.exception_handler(LiteratureError)
@app.exception_handler(JournalError)
@app.exception_handler(ManuscriptError)
@app.exception_handler(UnsupportedFile)
@app.exception_handler(engine.AnalysisError)
async def _bad_request(_: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(llm.PrivacyGateError)
async def _gate(_: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(lit_metadata.MetadataError)
async def _upstream(_: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.exception_handler(llm.LLMUnavailable)
async def _llm(_: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=503)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@app.get("/api/meta")
def meta():
    from .journals import store as journals
    journals.seed()  # the suggestions come from the journal base, so new and custom journals appear too
    return {"journals": [{"name": j["name"], "publisher": j["publisher"]} for j in journals.list_profiles()],
            "article_types": ARTICLE_TYPES, "llm": llm.status()}


# ---------------------------------------------------------------------------
# Projects (FR-0.1)
# ---------------------------------------------------------------------------

class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    focus: str = Field(default="", max_length=4000)
    journal: str = Field(default="", max_length=300)
    article_type: str = "case_series"


class ProjectPatch(BaseModel):
    title: Optional[str] = None
    focus: Optional[str] = None
    journal: Optional[str] = None
    article_type: Optional[str] = None


@app.get("/api/projects")
def list_projects(request: Request):
    mine = auth.project_ids(request.state.user)
    return [dict(p, role=None) for p in store.list() if p["id"] in mine]


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn, request: Request):
    if body.article_type not in ARTICLE_TYPES:
        raise HTTPException(400, "неизвестный тип статьи")
    p = store.create(body.model_dump())
    auth.add_member(p["id"], request.state.user["id"], "owner")
    return p


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return store.get(pid)


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch):
    changes = body.model_dump(exclude_none=True)
    if "article_type" in changes and changes["article_type"] not in ARTICLE_TYPES:
        raise HTTPException(400, "неизвестный тип статьи")
    p = store.update(pid, **changes)
    store.audit(pid, "author", "project.updated", {"fields": sorted(changes)})
    return p


@app.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: str, request: Request):
    if request.state.role != "owner":
        raise HTTPException(403, "удалить проект может только владелец")
    store.delete(pid)  # crypto-shredding: the project key is destroyed first
    auth.remove_project(pid)


@app.get("/api/projects/{pid}/audit")
def audit(pid: str):
    return list(reversed(store.audit_log(pid)))


# ---------------------------------------------------------------------------
# Data: upload → anonymization report → confirm (Module 1)
# ---------------------------------------------------------------------------

@app.post("/api/chunks")
async def upload_chunk(request: Request, upload_id: str, index: int, total: int, name: str = "upload"):
    """One part of a large file (see chunks.py); the file itself is then sent as a ref."""
    return chunks.put(request.state.user["id"], upload_id, index, total, name[:200], await request.body())


@app.post("/api/projects/{pid}/uploads")
async def upload(pid: str, request: Request, files: List[UploadFile] = File(default=[]), refs: str = Form("")):
    store.get(pid)
    reports = []
    for name, content in await chunks.collect(request, files, refs, MAX_UPLOAD_BYTES):
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"файл {name} больше 20 МБ")
        # parsing, anonymisation and NER are CPU-bound: off the event loop, so other users are not blocked
        reports.append(await run_in_threadpool(data_service.upload, store, pid, name, content))
    if not reports:
        raise HTTPException(400, "нет файлов")
    return reports


@app.get("/api/projects/{pid}/anonymization")
def anonymization(pid: str):
    return {"project": store.get(pid), "pending": data_service.pending_reports(store, pid)}


@app.get("/api/projects/{pid}/anonymization/preview/{upload_id}")
def anonymization_preview(pid: str, upload_id: str):
    path = store.dir(pid) / "pending" / f"{upload_id}.csv"
    if not upload_id.isalnum() or not path.exists():
        raise HTTPException(404, "загрузка не найдена")
    df = store.load_frame(path)
    return {"columns": list(df.columns), "rows": df.head(50).to_dict(orient="records")}


@app.post("/api/projects/{pid}/anonymization/confirm")
def confirm(pid: str):
    return data_service.confirm(store, pid)


@app.post("/api/projects/{pid}/anonymization/discard")
def discard(pid: str, upload_id: Optional[str] = None):
    data_service.discard(store, pid, upload_id)
    return {"ok": True}


@app.get("/api/projects/{pid}/dataset")
def dataset(pid: str):
    return data_service.dataset_view(store, pid)


class CellEdit(BaseModel):
    case_id: str
    column: str
    value: str


@app.patch("/api/projects/{pid}/dataset")
def edit_cell(pid: str, body: CellEdit):
    data_service.edit_cell(store, pid, body.case_id, body.column, body.value)
    return {"ok": True}


class ExtractIn(BaseModel):
    columns: List[str] = []


@app.post("/api/projects/{pid}/dataset/extract")
def extract_from_text(pid: str, body: ExtractIn):
    """FR-1.7: features from the (anonymised) report texts; each value keeps its quote."""
    from . import case_extraction
    if store.dataset(pid) is None:
        raise HTTPException(400, "сначала загрузите данные и подтвердите анонимизацию")
    try:
        summary = case_extraction.extract(store, pid, body.columns or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"summary": summary, **data_service.dataset_view(store, pid)}


@app.get("/api/projects/{pid}/dataset/export.csv")
def export_dataset(pid: str):
    path = store.dir(pid) / "dataset.csv"
    if not path.exists():
        raise HTTPException(404, "данных нет")
    return Response(read_bytes(path), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="cases_anonymized.csv"'})


@app.put("/api/projects/{pid}/dictionary")
def put_dictionary(pid: str, items: List[dict]):
    return data_service.save_dictionary(store, pid, items)


# ---------------------------------------------------------------------------
# Analysis (Module 2)
# ---------------------------------------------------------------------------

@app.get("/api/projects/{pid}/analysis")
def get_analysis(pid: str):
    store.get(pid)
    result = engine.latest(store, pid) or {}
    comps = read_json(store.dir(pid) / "literature" / "comparisons.json", {})
    for f in result.get("findings", []):
        c = comps.get(f["key"])
        f["literature"] = c and {
            "status": c["literature_status"], "label": literature_api.STATUS_LABELS.get(c["literature_status"]),
            "n_sources_with_evidence": c["n_sources_with_evidence"], "n_sources_in_base": c["n_sources_in_base"],
            "novelty": c["novelty"] if c.get("novelty_confirmed") else None}
    return result


@app.post("/api/projects/{pid}/analysis/run")
def run_analysis(pid: str):
    return engine.run_analysis(store, pid)


class DecisionIn(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(proposed|accepted|rejected)$")
    comment: Optional[str] = Field(default=None, max_length=4000)


def _finding(pid, fid):
    result = engine.latest(store, pid)
    f = next((x for x in (result or {}).get("findings", []) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404, "находка не найдена")
    return result, f


@app.post("/api/projects/{pid}/findings/{fid}")
def decide(pid: str, fid: str, body: DecisionIn):
    _, f = _finding(pid, fid)
    return engine.set_decision(store, pid, f["key"], body.status, body.comment)


@app.post("/api/projects/{pid}/findings/{fid}/interpret")
def interpret(pid: str, fid: str):
    result, f = _finding(pid, fid)
    project = store.get(pid)
    context = {"n_cases": result["summary"]["n"], "guardrail_tier": result["guardrails"]["label"],
               "article_focus": project.get("focus", ""), "n_tests_in_log": result["summary"]["n_tests"]}
    interpretation = llm.interpret_finding(project, f, context)
    engine.set_decision(store, pid, f["key"], interpretation=interpretation)
    return interpretation


@app.get("/api/projects/{pid}/findings/{fid}/figure.png")
def finding_figure(pid: str, fid: str):
    _, f = _finding(pid, fid)
    run_dir = engine.run_dir_latest(store, pid)
    path = figures.render_finding(run_dir, f, read_json(run_dir / "spec.json"))
    if not path:
        raise HTTPException(404, "для этой находки нет графика")
    return Response(read_bytes(path), media_type="image/png")


@app.get("/api/projects/{pid}/analysis/figures/{name}.png")
def overview_figure(pid: str, name: str):
    if name not in ("heatmap", "oncoprint"):
        raise HTTPException(404, "нет такого графика")
    run_dir = engine.run_dir_latest(store, pid)
    path = figures.render_overview(run_dir, name, read_json(run_dir / "spec.json"),
                                   read_json(run_dir / "results.json", {}).get("clustering"))
    if not path:
        raise HTTPException(404, "график недоступен при текущих данных")
    return Response(read_bytes(path), media_type="image/png")


class QuestionIn(BaseModel):
    question: Optional[str] = Field(default=None, max_length=2000)
    a: Optional[str] = None
    b: Optional[str] = None


@app.post("/api/projects/{pid}/hypotheses/parse")
def parse_hypothesis(pid: str, body: QuestionIn):
    project = store.get(pid)
    dictionary = read_json(store.dir(pid) / "dictionary.json", [])
    if not body.question:
        raise HTTPException(400, "пустой вопрос")
    try:
        parsed = llm.parse_question(project, body.question, dictionary)
        parsed["source"] = "llm"
    except llm.LLMUnavailable:
        parsed = llm.match_question_locally(body.question, dictionary)
        parsed["source"] = "local"
    return parsed


@app.post("/api/projects/{pid}/hypotheses")
def add_hypothesis(pid: str, body: QuestionIn):
    if not body.a or not body.b:
        raise HTTPException(400, "укажите два признака")
    return engine.add_user_hypothesis(store, pid, body.a, body.b, body.question)


@app.get("/api/projects/{pid}/analysis/export.zip")
def export_analysis(pid: str):
    run_dir = engine.run_dir_latest(store, pid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("analysis.py", "analysis_data.csv", "environment.json", "results.json", "hypotheses.json",
                     "spec.json"):
            z.writestr(name, read_bytes(run_dir / name))
        z.writestr("README.txt", (
            "Воспроизведение анализа\n=======================\n"
            "pip install numpy pandas scipy  (версии — в environment.json)\n"
            "python analysis.py analysis_data.csv > results_check.json\n"
            "Результат должен совпасть с results.json.\n"))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="analysis_{run_dir.name}.zip"'})


app.include_router(literature_api.router)
app.include_router(journals_api.router)
app.include_router(manuscript_api.router)
app.include_router(cover_api.router)
app.include_router(ethics_api.router)

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

def _asset_version():
    import hashlib
    h = hashlib.sha256()
    for f in sorted(FRONTEND_DIR.glob("*.*")):
        if f.suffix in (".js", ".css"):
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    """The page links its scripts and styles with a content version (app.js?v=…), so after an update every
    browser loads the new interface instead of a cached one."""
    from fastapi.responses import HTMLResponse
    v = _asset_version()
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    html = re.sub(r'((?:src|href)=")([\w-]+\.(?:js|css))"', lambda m: f'{m.group(1)}{m.group(2)}?v={v}"', html)
    return HTMLResponse(html)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
