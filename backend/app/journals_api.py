"""REST API of the journal profiles module (Module 4)."""
from typing import Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .config import ARTICLE_TYPES as PROJECT_ARTICLE_TYPES
from .journals import checks, extract, store as journals
from .journals.schema import ARTICLE_TYPES, FIELDS, REQ_LABELS
from .literature import service as lit, styles as csl_styles
from .literature.metadata import MetadataError
from .literature_api import store  # the same store proxy as the literature API
from .storage import now_iso, read_json, write_json

router = APIRouter(prefix="/api")


@router.get("/journals/schema")
def schema():
    groups = {}
    for path, (ftype, label, group, hint) in FIELDS.items():
        groups.setdefault(group, []).append({"path": path, "type": ftype, "label": label, "hint": hint})
    return {"groups": groups, "article_types": ARTICLE_TYPES, "requirement_labels": REQ_LABELS,
            "stale_days": journals.STALE_DAYS}


def check_access(user, jid, method):
    """(status, message) if the user may not read/change this profile, else None (FR-4.7)."""
    try:
        p = journals.get(jid)
    except journals.JournalError:
        return None  # 404 comes from the route
    if not _visible(p, user):
        return (404, "журнал не найден")
    if method in ("GET", "HEAD"):
        return None
    if not p.get("custom"):
        return None if user["is_admin"] else (403, "профили стартовой базы изменяет администратор")
    if p.get("owner_id") not in (None, user["id"]) and not user["is_admin"]:
        return (404, "журнал не найден")
    return None


def _visible(p, user):
    return not p.get("custom") or p.get("owner_id") in (None, user["id"]) or user["is_admin"]


@router.get("/journals")
def list_journals(request: Request):
    journals.seed()
    user = request.state.user
    return [s for s in journals.list_profiles() if _visible(journals.get(s["id"]), user)]


class JournalIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    publisher: str = ""
    guidelines_url: str = ""


@router.post("/journals", status_code=201)
def create_journal(body: JournalIn, request: Request):
    p = journals.create(body.name, body.publisher, body.guidelines_url)
    p["owner_id"] = request.state.user["id"]
    journals._save(p, "author", "profile.created")
    return p


@router.get("/journals/{jid}")
def get_journal(jid: str, request: Request):
    p = journals.get(jid)
    if not _visible(p, request.state.user):
        raise HTTPException(404, "журнал не найден")
    return dict(p, summary=journals.summary(p))


@router.delete("/journals/{jid}", status_code=204)
def delete_journal(jid: str):
    journals.delete(jid)


class JournalMeta(BaseModel):
    name: Optional[str] = None
    publisher: Optional[str] = None
    guidelines_url: Optional[str] = None


@router.patch("/journals/{jid}")
def patch_journal(jid: str, body: JournalMeta):
    return journals.update_meta(jid, **body.model_dump())


class UrlIn(BaseModel):
    url: str
    replaces: Optional[str] = None


@router.post("/journals/{jid}/sources/url")
def source_url(jid: str, body: UrlIn):
    return journals.add_source(jid, "url", url=body.url.strip(), replaces=body.replaces)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    url: Optional[str] = None
    replaces: Optional[str] = None


@router.post("/journals/{jid}/sources/text")
def source_text(jid: str, body: TextIn):
    return journals.add_source(jid, "text", text=body.text, url=body.url, replaces=body.replaces)


@router.post("/journals/{jid}/sources/pdf")
async def source_pdf(jid: str, file: UploadFile = File(...), replaces: Optional[str] = Form(None)):
    content = await file.read(30 * 1024 * 1024)
    return journals.add_source(jid, "pdf", content=content, filename=file.filename, replaces=replaces or None)


@router.get("/journals/{jid}/sources/{sid}")
def source_get(jid: str, sid: str):
    texts = journals.source_texts(journals.get(jid))
    if sid not in texts:
        raise HTTPException(404, "источник не найден")
    return {"id": sid, "text": texts[sid]}


@router.delete("/journals/{jid}/sources/{sid}")
def source_delete(jid: str, sid: str):
    return journals.delete_source(jid, sid)


@router.post("/journals/{jid}/recheck")
def recheck(jid: str):
    return {"results": journals.recheck(jid), "profile": journals.summary(journals.get(jid))}


@router.post("/journals/{jid}/acknowledge-change")
def acknowledge(jid: str):
    return journals.acknowledge_change(jid)


@router.post("/journals/{jid}/extract")
def extract_profile(jid: str):
    profile = journals.get(jid)
    texts = journals.source_texts(profile)
    if not texts:
        raise HTTPException(400, "сначала добавьте текст guidelines")
    items, meta = extract.extract_profile(profile, texts)
    return journals.apply_extraction(jid, items, meta)


class FieldsIn(BaseModel):
    updates: Dict[str, dict]


@router.put("/journals/{jid}/fields")
def put_fields(jid: str, body: FieldsIn):
    return journals.set_fields(jid, body.updates)


class VerifyIn(BaseModel):
    verified_by: str = Field(min_length=2, max_length=200)
    note: str = ""


@router.post("/journals/{jid}/verify")
def verify(jid: str, body: VerifyIn):
    return journals.verify(jid, body.verified_by, body.note)


# ---------------------------------------------------------------------------
# Project ↔ journal
# ---------------------------------------------------------------------------

class ProjectJournalIn(BaseModel):
    journal_id: str
    article_type: Optional[str] = None


def _apply_style(pid, profile):
    """Set the literature citation style from the profile (installs the CSL style if needed)."""
    csl = journals.value(profile, "citation.csl_id")
    if not csl:
        return {"style": None, "message": "в профиле не указан CSL-стиль — стиль проекта не изменён"}
    try:
        csl_styles.install(csl)
        lit.set_style(store, pid, csl)
        return {"style": csl, "message": f"стиль цитирования проекта: {csl}"}
    except (ValueError, lit.LiteratureError, MetadataError) as exc:
        return {"style": None, "message": f"не удалось установить стиль {csl}: {exc}"}


@router.put("/projects/{pid}/journal")
def set_project_journal(pid: str, body: ProjectJournalIn):
    project = store.get(pid)
    profile = journals.get(body.journal_id)
    changes = {"journal_id": profile["id"], "journal": profile["name"]}
    if body.article_type:
        if body.article_type not in PROJECT_ARTICLE_TYPES:
            raise HTTPException(400, "неизвестный тип статьи")
        changes["article_type"] = body.article_type
    previous = project.get("journal_id")
    project = store.update(pid, **changes)
    applied = _apply_style(pid, profile)
    store.audit(pid, "author", "journal.selected",
                {"journal": profile["id"], "previous": previous, "style": applied["style"]})
    return {"project": project, "applied": applied}


def _counts(pid):
    from .manuscript_api import manuscript_counts
    comp = lit.completeness(store, pid)
    counts = {"references": comp["n_cited"], "style": lit.settings(store, pid)["style"], "words": None,
              "abstract_words": None, "title_chars": None, "tables": None, "figures": None, "keywords": None}
    mc = manuscript_counts(pid)
    for k in ("words", "abstract_words", "title_chars", "tables", "figures", "keywords"):
        if mc.get(k) is not None:
            counts[k] = mc[k]
    if mc.get("references"):
        counts["references"] = mc["references"]
    return counts


@router.get("/projects/{pid}/journal")
def project_journal(pid: str):
    project = store.get(pid)
    jid = project.get("journal_id")
    if not jid:
        return {"profile": None}
    profile = journals.get(jid)
    tpl = checks.template(profile, project["article_type"])
    counts = _counts(pid)
    manual = read_json(store.dir(pid) / "journal_checklist.json", {})
    items = checks.checklist(project, profile, tpl, lit.completeness(store, pid), counts, manual)
    from .manuscript_api import checklist_updates
    updates = checklist_updates(pid, tpl)
    for i in items:
        if i["key"] in updates:
            i["status"], i["detail"] = updates[i["key"]]
            i["blocking"] = i["blocking"] and i["status"] != "pass"
    return {"profile": journals.summary(profile), "template": tpl, "compliance": checks.compliance(tpl, counts),
            "checklist": items, "blocking": sum(1 for i in items if i["blocking"])}


class ChecklistIn(BaseModel):
    key: str
    done: bool


@router.post("/projects/{pid}/journal/checklist")
def tick(pid: str, body: ChecklistIn):
    store.get(pid)
    path = store.dir(pid) / "journal_checklist.json"
    state = read_json(path, {})
    if body.done:
        state[body.key] = now_iso()[:10]
    else:
        state.pop(body.key, None)
    write_json(path, state)
    store.audit(pid, "author", "checklist.ticked", {"item": body.key, "done": body.done})
    return project_journal(pid)
