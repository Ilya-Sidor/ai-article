"""REST API of the cover letter (Module 6)."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .journals import store as journals
from .literature_api import store
from .manuscript import cover as cv, store as ms
from .manuscript_api import _renderer, context

router = APIRouter(prefix="/api")

SETTINGS = ("tone", "editor", "salutation", "corresponding_name", "corresponding_affiliation", "corresponding_email",
            "suggested_reviewers", "excluded_reviewers", "previous_submission")


def _load(pid):
    ctx, state = context(pid)
    cover = state.setdefault("cover", cv.empty())
    if not cover["settings"].get("editor") and ctx.get("profile"):
        cover["settings"]["editor"] = journals.value(ctx["profile"], "cover_letter.editor") or ""
    return ctx, state, cover


def _abstract_and_statements(state, renderer):
    ab = state["sections"].get("abstract")
    st = state["sections"].get("statements")
    return (renderer.text(ms.current_source(ab)) if ab else "", ms.current_source(st) if st else "")


def cover_status(pid, ctx=None, state=None):
    """Issues of the cover letter (used by the view and by the export)."""
    if ctx is None:
        ctx, state, _ = _load(pid)
    cover = state.get("cover") or cv.empty()
    renderer = _renderer(pid, ctx, state)
    cur = cv.current(cover)
    if not cur:
        return [{"section": "cover_letter", "heading": "Cover letter", "severity": "blocking", "code": "cover_missing",
                 "message": "cover letter не подготовлен", "fragment": "", "suggestion": ""}], renderer
    issues = cv.check(cover, renderer.text(cur["body"]), cur["body"], state.get("front"), ctx["template"], ctx)
    if cover["status"] != "accepted":
        issues.append({"section": "cover_letter", "heading": "Cover letter", "severity": "blocking",
                       "code": "cover_not_accepted", "message": "cover letter не принят автором", "fragment": "",
                       "suggestion": ""})
    return issues, renderer


def _view(pid):
    ctx, state, cover = _load(pid)
    ms.save(store, pid, state)
    issues, renderer = cover_status(pid, ctx, state)
    cur = cv.current(cover)
    tpl = ctx["template"] or {}
    prereq = []
    if not (state.get("front") or {}).get("title"):
        prereq.append("выберите название статьи (шаг 7 → «Название и ключевые слова»)")
    ab = state["sections"].get("abstract")
    if not ab or ab["status"] != "accepted":
        prereq.append("примите Abstract (шаг 7)")
    not_acc = [s["heading"] for s in ms.ordered(state) if s["status"] != "accepted"]
    letter = cv.assemble(cover, renderer.text(cur["body"]), ctx.get("journal"), ctx.get("lang", "en")) if cur else []
    return {
        "settings": cover["settings"], "status": cover["status"], "confirmed": cover.get("confirmed", {}),
        "body": cur["body"] if cur else "", "segments": renderer.segments(cur["body"]) if cur else [],
        "letter": [{"kind": k, "text": t} for k, t in letter],
        "versions": [{k: v.get(k) for k in ("n", "ts", "actor", "note")} for v in cover["versions"]],
        "coverage": (cur or {}).get("coverage", []), "questions": (cur or {}).get("questions", []),
        "requirements": (tpl.get("cover_letter") or {}).get("requirements") or [],
        "suggested_reviewers_requirement": (tpl.get("cover_letter") or {}).get("suggested_reviewers"),
        "journal": ctx.get("journal"), "has_journal": bool(ctx.get("profile")),
        "prerequisites": prereq, "sections_not_accepted": not_acc,
        "issues": issues, "blocking": sum(1 for i in issues if i["severity"] == "blocking"),
        "facts": [{"id": k, "desc": v["desc"], "value": v["value"]} for k, v in ctx["facts"].items()],
        "tones": cv.TONES,
    }


@router.get("/projects/{pid}/cover")
def get_cover(pid: str):
    return _view(pid)


@router.put("/projects/{pid}/cover/settings")
def put_settings(pid: str, settings: dict):
    ctx, state, cover = _load(pid)
    for k in SETTINGS:
        if k in settings:
            cover["settings"][k] = (settings[k] or "").strip() if isinstance(settings[k], str) else settings[k]
    if cover["settings"].get("tone") not in cv.TONES:
        cover["settings"]["tone"] = "formal"
    ms.save(store, pid, state)
    store.audit(pid, "author", "cover.settings_saved", {"fields": sorted(k for k in settings if k in SETTINGS)})
    return _view(pid)


@router.post("/projects/{pid}/cover/generate")
def generate(pid: str):
    ctx, state, cover = _load(pid)
    v = _view(pid)
    if v["prerequisites"]:
        raise HTTPException(400, "cover letter пишется после финализации статьи (FR-6.1): " + "; ".join(v["prerequisites"]))
    if not v["has_journal"]:
        raise HTTPException(400, "выберите целевой журнал на шаге «Проект»")
    renderer = _renderer(pid, ctx, state)
    abstract, statements = _abstract_and_statements(state, renderer)
    out = cv.generate(ctx["project"], ctx, cover, state.get("front") or {}, abstract, statements)
    cv.add_version(cover, out["body"], "agent", "черновик агента",
                   {"coverage": out["requirements_coverage"], "questions": out["questions"]})
    ms.save(store, pid, state)
    store.audit(pid, "agent", "cover.generated", {})
    return _view(pid)


class BodyIn(BaseModel):
    body: str = Field(max_length=50_000)


@router.put("/projects/{pid}/cover/body")
def put_body(pid: str, body: BodyIn):
    ctx, state, cover = _load(pid)
    cv.add_version(cover, body.body, "author", "правка автора")
    ms.save(store, pid, state)
    store.audit(pid, "author", "cover.edited", {})
    return _view(pid)


class ReviseIn(BaseModel):
    instruction: str = Field(min_length=3, max_length=4000)
    selection: Optional[str] = None


@router.post("/projects/{pid}/cover/revise")
def revise(pid: str, body: ReviseIn):
    ctx, state, cover = _load(pid)
    cur = cv.current(cover)
    if not cur:
        raise HTTPException(400, "письмо ещё не написано")
    renderer = _renderer(pid, ctx, state)
    abstract, statements = _abstract_and_statements(state, renderer)
    out = cv.revise(ctx["project"], ctx, cover, cur["body"], body.instruction, body.selection,
                    state.get("front") or {}, abstract, statements)
    cv.add_version(cover, out["body"], "agent", f"по инструкции: {body.instruction[:120]}",
                   {"coverage": out["requirements_coverage"], "questions": out["questions"]})
    ms.save(store, pid, state)
    store.audit(pid, "agent", "cover.revised", {})
    return _view(pid)


@router.post("/projects/{pid}/cover/accept")
def accept(pid: str):
    ctx, state, cover = _load(pid)
    if not cover["versions"]:
        raise HTTPException(400, "письмо ещё не написано")
    cover["status"] = "accepted"
    cover["accepted_version"] = cv.current(cover)["n"]
    ms.save(store, pid, state)
    store.audit(pid, "author", "cover.accepted", {"version": cover["accepted_version"]})
    return _view(pid)


@router.post("/projects/{pid}/cover/reopen")
def reopen(pid: str):
    ctx, state, cover = _load(pid)
    cover["status"] = "draft" if cover["versions"] else "empty"
    ms.save(store, pid, state)
    return _view(pid)


class RollbackIn(BaseModel):
    n: int


@router.post("/projects/{pid}/cover/rollback")
def rollback(pid: str, body: RollbackIn):
    ctx, state, cover = _load(pid)
    old = next((v for v in cover["versions"] if v["n"] == body.n), None)
    if old is None:
        raise HTTPException(404, "версия не найдена")
    cv.add_version(cover, old["body"], "author", f"откат к версии {body.n}",
                   {"coverage": old.get("coverage", []), "questions": old.get("questions", [])})
    ms.save(store, pid, state)
    return _view(pid)


@router.get("/projects/{pid}/cover/diff")
def diff(pid: str, a: int, b: int):
    ctx, state, cover = _load(pid)
    try:
        return {"diff": cv.diff(cover, a, b)}
    except StopIteration:
        raise HTTPException(404, "версия не найдена")


class ConfirmIn(BaseModel):
    requirement: str
    done: bool


@router.post("/projects/{pid}/cover/confirm")
def confirm(pid: str, body: ConfirmIn):
    ctx, state, cover = _load(pid)
    conf = cover.setdefault("confirmed", {})
    if body.done:
        conf[body.requirement] = True
    else:
        conf.pop(body.requirement, None)
    ms.save(store, pid, state)
    return _view(pid)


def letter_docx(pid, draft=True):
    ctx, state, cover = _load(pid)
    cur = cv.current(cover)
    if not cur:
        return None
    renderer = _renderer(pid, ctx, state)
    return cv.docx_bytes(cv.assemble(cover, renderer.text(cur["body"]), ctx.get("journal"), ctx.get("lang", "en")), draft)


@router.get("/projects/{pid}/cover/letter.docx")
def download(pid: str, draft: bool = True):
    data = letter_docx(pid, draft)
    if data is None:
        raise HTTPException(404, "письмо ещё не написано")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="cover_letter.docx"'})
