"""REST API of the literature module (Module 3)."""
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import llm
from .analysis import engine
from .literature import agent, metadata as md, service as lit
from .literature import styles as csl_styles
from .storage import ProjectStore, now_iso, read_bytes, read_json, write_json

router = APIRouter(prefix="/api")


class _StoreProxy:
    """Delegates to ``main.store`` so tests can swap the store in one place."""

    def __getattr__(self, name):
        from . import main
        return getattr(main.store, name)


store: ProjectStore = _StoreProxy()

STATUS_LABELS = {
    "described_consistent": "описано, согласуется",
    "described_contradicting": "описано, есть противоречия",
    "described_small_series": "описано только на малых сериях",
    "not_described": "в базе не описано",
    "insufficient_sources": "в базе недостаточно источников",
}


def _search_fn(pid):
    labels = {s["id"]: s["label"] for s in lit.sources(store, pid)}

    def search(query, top_k=8):
        return [dict(h, source_label=labels.get(h["source_id"], h["source_id"]))
                for h in lit.search(store, pid, query, top_k=top_k)]
    return search


def _comparisons(pid):
    return read_json(store.dir(pid) / "literature" / "comparisons.json", {})


def _save_comparisons(pid, data):
    write_json(store.dir(pid) / "literature" / "comparisons.json", data)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

@router.get("/literature/styles")
def styles():
    return csl_styles.available()


class StyleIn(BaseModel):
    id: str


@router.post("/literature/styles")
def install_style(body: StyleIn):
    try:
        return csl_styles.install(body.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/projects/{pid}/literature")
def overview(pid: str):
    store.get(pid)
    return {"sources": lit.sources(store, pid), "settings": lit.settings(store, pid),
            "completeness": lit.completeness(store, pid),
            "contact_email_configured": bool(md.CONTACT_EMAIL)}


@router.post("/projects/{pid}/literature/pdf")
async def upload_pdf(pid: str, files: List[UploadFile] = File(...)):
    store.get(pid)
    added, errors = [], []
    for f in files:
        content = await f.read(md.MAX_PDF_BYTES + 1)
        if len(content) > md.MAX_PDF_BYTES:
            errors.append({"file": f.filename, "error": "файл больше 40 МБ"})
            continue
        try:
            added.append(lit.add_pdf(store, pid, f.filename or "paper.pdf", content))
        except lit.LiteratureError as exc:
            errors.append({"file": f.filename, "error": str(exc)})
    return {"added": added, "errors": errors}


class IdentifiersIn(BaseModel):
    values: List[str] = Field(min_length=1, max_length=50)


@router.post("/projects/{pid}/literature/identifiers")
def add_identifiers(pid: str, body: IdentifiersIn):
    store.get(pid)
    added, errors = [], []
    for v in body.values:
        if not v.strip():
            continue
        try:
            added.append(lit.add_identifier(store, pid, v))
        except (lit.LiteratureError, md.MetadataError) as exc:
            errors.append({"value": v, "error": str(exc)})
    return {"added": added, "errors": errors}


@router.post("/projects/{pid}/literature/import")
async def import_refs(pid: str, file: UploadFile = File(...)):
    store.get(pid)
    content = (await file.read(5 * 1024 * 1024)).decode("utf-8", errors="replace")
    return lit.import_references(store, pid, content)


@router.get("/projects/{pid}/literature/pubmed")
def pubmed(pid: str, q: str):
    store.get(pid)
    if not q.strip():
        raise HTTPException(400, "пустой запрос")
    try:
        return lit.pubmed_suggestions(store, pid, q.strip())
    except md.MetadataError as exc:
        raise HTTPException(502, str(exc))


class IdentifierIn(BaseModel):
    identifier: str


@router.patch("/projects/{pid}/literature/sources/{sid}")
def set_identifier(pid: str, sid: str, body: IdentifierIn):
    try:
        return lit.set_identifier(store, pid, sid, body.identifier)
    except md.MetadataError as exc:
        raise HTTPException(502, str(exc))


@router.delete("/projects/{pid}/literature/sources/{sid}", status_code=204)
def delete_source(pid: str, sid: str):
    lit.get_source(store, pid, sid)
    lit.delete_source(store, pid, sid)


@router.get("/projects/{pid}/literature/sources/{sid}/file.pdf")
def source_pdf(pid: str, sid: str):
    lit.get_source(store, pid, sid)
    path = store.dir(pid) / "literature" / "files" / f"{sid}.pdf"
    if not path.exists():
        raise HTTPException(404, "PDF нет")
    return Response(read_bytes(path), media_type="application/pdf")


@router.get("/projects/{pid}/literature/sources/{sid}/chunks")
def source_chunks(pid: str, sid: str):
    lit.get_source(store, pid, sid)
    return lit.chunks(store, pid, sid)


@router.post("/projects/{pid}/literature/sources/{sid}/extract")
def extract(pid: str, sid: str):
    src = lit.get_source(store, pid, sid)
    chunks = lit.chunks(store, pid, sid)
    if not chunks:
        raise HTTPException(400, "у источника нет текста — загрузите PDF")
    result = agent.extract_source(store.get(pid), src, chunks)
    store.audit(pid, "agent", "literature.extracted", {"source": sid})
    return lit.update_source(store, pid, sid, extraction=result)


@router.post("/projects/{pid}/literature/retractions")
def retractions(pid: str):
    store.get(pid)
    return {"changed": lit.recheck_retractions(store, pid)}


@router.get("/projects/{pid}/literature/search")
def search(pid: str, q: str, k: int = 10):
    store.get(pid)
    return _search_fn(pid)(q, top_k=max(1, min(k, 30)))


@router.get("/projects/{pid}/literature/matrix")
def matrix(pid: str):
    store.get(pid)
    own = None
    res = engine.latest(store, pid)
    if res:
        groups = {v["name"]: v for v in read_json(store.dir(pid) / "dictionary.json", [])}
        own = {"source_id": None, "label": "Собственная серия", "design": "серия случаев", "n": res["summary"]["n"],
               "entity": store.get(pid).get("focus") or "", "histotypes": [], "ihc": {}, "molecular": {}}
        for v in res["table1"]:
            spec = groups.get(v["variable"], {})
            if spec.get("vtype") != "binary" or spec.get("group") not in ("ihc", "molecular"):
                continue
            k = lit.marker_key(v["variable"])
            pos = next((c for c in v.get("counts", []) if c["level"] == spec["levels"][-1]), None)
            if k and pos:
                own[spec["group"]][k] = {"n_pos": pos["count"], "n_total": v["n"], "text": None, "chunk_id": None}
    return lit.matrix(store, pid, own)


# ---------------------------------------------------------------------------
# Findings × literature (FR-2.6, FR-3.7)
# ---------------------------------------------------------------------------

def _findings(pid):
    res = engine.latest(store, pid)
    return [f for f in (res or {}).get("findings", []) if f["status"] != "rejected"]


def _citations_view(pid, cids):
    by_id = {c["id"]: c for c in lit.citations(store, pid)}
    labels = {s["id"]: s["label"] for s in lit.sources(store, pid)}
    return [dict(by_id[c], source_label=labels.get(by_id[c]["source_id"])) for c in cids if c in by_id]


@router.get("/projects/{pid}/literature/findings")
def findings(pid: str):
    comps = _comparisons(pid)
    out = []
    for f in _findings(pid):
        comp = comps.get(f["key"])
        if comp:
            comp = dict(comp, status_label=STATUS_LABELS.get(comp["literature_status"]),
                        citations=_citations_view(pid, comp["citation_ids"]))
        out.append({"id": f["id"], "key": f["key"], "title": f["title"], "evidence": f["evidence"],
                    "status": f["status"], "statement": f["statement"], "comparison": comp})
    return out


@router.post("/projects/{pid}/literature/findings/{fid}/compare")
def compare(pid: str, fid: str):
    project = store.get(pid)
    f = next((x for x in _findings(pid) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404, "находка не найдена или отклонена")
    if not lit.chunks(store, pid):
        raise HTTPException(400, "база литературы пуста — добавьте источники")
    chunks = lit.chunks_by_id(store, pid)
    result = agent.compare_finding(project, f, _search_fn(pid), chunks)

    # replace this finding's previous agent citations that the author has not decided on
    kept = [c for c in lit.citations(store, pid)
            if not (c["context"] == {"type": "finding", "key": f["key"]} and c["origin"] == "agent"
                    and c["decision"] == "pending")]
    lit.save_citations(store, pid, kept)
    cids = []
    checks = agent.verify_citations(project, [(e["note"], chunks[e["chunk_id"]], e["quote"])
                                              for e in result["evidence"]])
    for e, verification in zip(result["evidence"], checks):
        cit = lit.add_citation(store, pid, e["note"], {"type": "finding", "key": f["key"]}, chunks[e["chunk_id"]],
                               e["quote"], e["relation"], "agent", verification)
        cids.append(cit["id"])
    sources_hit = {chunks[e["chunk_id"]]["source_id"] for e in result["evidence"] if e["relation"] != "context"}
    comps = _comparisons(pid)
    prev = comps.get(f["key"], {})
    comps[f["key"]] = {
        "finding_id": fid, "literature_status": result["literature_status"], "summary": result["summary"],
        "novelty": result["novelty"], "novelty_confirmed": prev.get("novelty_confirmed"),
        "citation_ids": cids, "rejected": result["rejected"], "queries": result["queries"],
        "n_sources_in_base": len(lit.sources(store, pid)), "n_sources_with_evidence": len(sources_hit),
        "model": result["model"], "created_at": result["created_at"],
    }
    _save_comparisons(pid, comps)
    store.audit(pid, "agent", "literature.finding_compared",
                {"finding": f["key"], "status": result["literature_status"], "citations": len(cids),
                 "rejected": len(result["rejected"])})
    return findings(pid)


class NoveltyIn(BaseModel):
    text: str = Field(max_length=2000)
    confirmed: bool


@router.post("/projects/{pid}/literature/findings/{fid}/novelty")
def novelty(pid: str, fid: str, body: NoveltyIn):
    f = next((x for x in _findings(pid) if x["id"] == fid), None)
    comps = _comparisons(pid)
    if f is None or f["key"] not in comps:
        raise HTTPException(404, "сначала сопоставьте находку с литературой")
    comps[f["key"]]["novelty"] = body.text
    comps[f["key"]]["novelty_confirmed"] = now_iso() if body.confirmed else None
    _save_comparisons(pid, comps)
    store.audit(pid, "author", "literature.novelty", {"finding": f["key"], "confirmed": body.confirmed})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

@router.get("/projects/{pid}/literature/citations")
def list_citations(pid: str):
    store.get(pid)
    return _citations_view(pid, [c["id"] for c in lit.citations(store, pid)])


class CitationIn(BaseModel):
    claim: str = Field(min_length=3, max_length=2000)
    chunk_id: str
    quote: Optional[str] = None
    finding_key: Optional[str] = None
    relation: str = Field(default="supports", pattern="^(supports|contradicts|context)$")


@router.post("/projects/{pid}/literature/citations")
def add_citation(pid: str, body: CitationIn):
    chunk = lit.chunks_by_id(store, pid).get(body.chunk_id)
    if chunk is None:
        raise HTTPException(400, "фрагмента нет в базе проекта")
    quote = body.quote or chunk["text"]
    from .literature.citations import quote_in_text
    if not quote_in_text(quote, chunk["text"]):
        raise HTTPException(400, "цитата не найдена во фрагменте")
    context = {"type": "finding", "key": body.finding_key} if body.finding_key else {"type": "manual"}
    cit = lit.add_citation(store, pid, body.claim, context, chunk, quote, body.relation, "author")
    if body.finding_key:
        comps = _comparisons(pid)
        if body.finding_key in comps:
            comps[body.finding_key]["citation_ids"].append(cit["id"])
            _save_comparisons(pid, comps)
    store.audit(pid, "author", "citation.added", {"citation": cit["id"], "source": cit["source_id"]})
    return cit


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(pending|accepted|rejected)$")


@router.post("/projects/{pid}/literature/citations/{cid}")
def decide(pid: str, cid: str, body: DecisionIn):
    return lit.update_citation(store, pid, cid, decision=body.decision)


@router.post("/projects/{pid}/literature/citations/{cid}/verify")
def verify(pid: str, cid: str):
    cit = next((c for c in lit.citations(store, pid) if c["id"] == cid), None)
    if cit is None:
        raise HTTPException(404, "цитата не найдена")
    chunk = lit.chunks_by_id(store, pid).get(cit["chunk_id"])
    if chunk is None:
        raise HTTPException(400, "фрагмент удалён из базы")
    verification = agent.verify_citation(store.get(pid), cit["claim"], chunk, cit["quote"])
    return lit.update_citation(store, pid, cid, verification=verification)


# ---------------------------------------------------------------------------
# Grounding a text (FR-3.8) and bibliography (FR-3.10)
# ---------------------------------------------------------------------------

def _groundings(pid):
    return read_json(store.dir(pid) / "literature" / "groundings.json", [])


def _render_grounding(pid, doc):
    cits = {c["id"]: c for c in lit.citations(store, pid)}
    order = []
    for s in doc["sentences"]:
        for cid in s["citation_ids"]:
            c = cits.get(cid)
            if c and c["decision"] != "rejected" and c["source_id"] not in order:
                order.append(c["source_id"])
    rendered = lit.render(lit.sources(store, pid), order, lit.settings(store, pid)["style"])
    labels = {s["id"]: s["label"] for s in lit.sources(store, pid)}
    sentences = []
    for s in doc["sentences"]:
        items = [dict(cits[c], source_label=labels.get(cits[c]["source_id"])) for c in s["citation_ids"] if c in cits]
        markers = []
        for c in items:
            m = rendered["markers"].get(c["source_id"])
            if c["decision"] != "rejected" and m and m not in markers:
                markers.append(m)
        sentences.append(dict(s, citations=items, markers=markers))
    return dict(doc, sentences=sentences, bibliography=rendered["entries"])


@router.get("/projects/{pid}/literature/groundings")
def groundings(pid: str):
    store.get(pid)
    return [_render_grounding(pid, d) for d in reversed(_groundings(pid))]


class GroundIn(BaseModel):
    text: str = Field(min_length=10, max_length=20000)


@router.post("/projects/{pid}/literature/ground")
def ground(pid: str, body: GroundIn):
    project = store.get(pid)
    if not lit.chunks(store, pid):
        raise HTTPException(400, "база литературы пуста — добавьте источники")
    chunks = lit.chunks_by_id(store, pid)
    result = agent.ground_text(project, body.text, _search_fn(pid), chunks)
    docs = _groundings(pid)
    gid = "T-%03d" % (len(docs) + 1)
    sentences = []
    pairs = [(s, e) for s in result["sentences"] for e in s["evidence"]]
    checks = iter(agent.verify_citations(project, [(s["text"], chunks[e["chunk_id"]], e["quote"]) for s, e in pairs]))
    for s in result["sentences"]:
        cids = []
        for e in s["evidence"]:
            cit = lit.add_citation(store, pid, s["text"], {"type": "text", "grounding_id": gid},
                                   chunks[e["chunk_id"]], e["quote"], "supports", "agent", next(checks))
            cids.append(cit["id"])
        sentences.append({"index": s["index"], "text": s["text"], "status": s["status"], "citation_ids": cids,
                          "rejected": s["rejected"]})
    doc = {"id": gid, "text": body.text, "sentences": sentences, "queries": result["queries"],
           "model": result["model"], "created_at": result["created_at"]}
    docs.append(doc)
    write_json(store.dir(pid) / "literature" / "groundings.json", docs)
    store.audit(pid, "agent", "literature.text_grounded", {"grounding": gid, "sentences": len(sentences)})
    return _render_grounding(pid, doc)


@router.get("/projects/{pid}/literature/bibliography")
def bibliography(pid: str, style: Optional[str] = None, scope: str = "cited"):
    store.get(pid)
    if style and style not in csl_styles.available():
        raise HTTPException(400, "неизвестный стиль")
    return lit.bibliography(store, pid, style, "all" if scope == "all" else "cited")


class SettingsIn(BaseModel):
    style: str


@router.put("/projects/{pid}/literature/settings")
def put_settings(pid: str, body: SettingsIn):
    store.get(pid)
    lit.set_style(store, pid, body.style)
    return lit.settings(store, pid)
