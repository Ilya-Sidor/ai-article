"""Project literature base: sources, chunks, citations (Module 3).

Layout of ``data/projects/<id>/literature/``::

    sources.json          list of sources (metadata from Crossref / PubMed only)
    files/<S-001>.pdf     uploaded or open-access PDFs
    chunks/<S-001>.json   indexed fragments with page / section
    citations.json        claim → chunk + quote + verification + author decision
    comparisons.json      finding key → literature comparison (FR-2.6, FR-3.7)
    groundings.json       texts with automatically placed citations (FR-3.8)
    settings.json         citation style
"""
import re
import unicodedata

from ..storage import ProjectStore, now_iso, read_bytes, read_json, write_bytes, write_json
from . import metadata as md
from . import parsing
from .citations import DEFAULT_STYLE, available_styles, render
from .index import BM25, make_chunks

_index_cache = {}


class LiteratureError(ValueError):
    pass


def _dir(store: ProjectStore, pid: str):
    d = store.dir(pid) / "literature"
    d.mkdir(exist_ok=True)
    return d


def sources(store, pid):
    return read_json(_dir(store, pid) / "sources.json", [])


def _save_sources(store, pid, items):
    write_json(_dir(store, pid) / "sources.json", items)
    _index_cache.pop(pid, None)


def get_source(store, pid, sid):
    s = next((x for x in sources(store, pid) if x["id"] == sid), None)
    if s is None:
        raise LiteratureError("источник не найден")
    return s


def update_source(store, pid, sid, **changes):
    items = sources(store, pid)
    for s in items:
        if s["id"] == sid:
            s.update(changes)
            _save_sources(store, pid, items)
            return s
    raise LiteratureError("источник не найден")


def chunks(store, pid, sid=None):
    d = _dir(store, pid) / "chunks"
    if sid:
        return read_json(d / f"{sid}.json", [])
    out = []
    for s in sources(store, pid):
        out.extend(read_json(d / f"{s['id']}.json", []))
    return out


def chunks_by_id(store, pid):
    return {c["id"]: c for c in chunks(store, pid)}


def search(store, pid, query, top_k=8, source_ids=None):
    key = pid
    version = tuple((s["id"], s.get("n_chunks", 0)) for s in sources(store, pid))
    cached = _index_cache.get(key)
    if not cached or cached[0] != version:
        cached = (version, BM25(chunks(store, pid)))
        _index_cache[key] = cached
    return cached[1].search(query, top_k=top_k, source_ids=source_ids)


# ---------------------------------------------------------------------------
# Labels, dedupe
# ---------------------------------------------------------------------------

def _norm_title(t):
    t = unicodedata.normalize("NFKD", t or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def label(csl):
    authors = csl.get("author") or []
    first = (authors[0].get("family") or authors[0].get("literal") or "?") if authors else "?"
    year = ((csl.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    return f"{first}{' et al.' if len(authors) > 2 else ''} {year or 'n.d.'}"


def find_duplicate(items, doi=None, pmid=None, title=None, exclude=None):
    nt = _norm_title(title) if title else None
    for s in items:
        if s["id"] == exclude:
            continue
        if doi and s.get("doi") == doi:
            return s
        if pmid and s.get("pmid") == str(pmid):
            return s
        if nt and len(nt) > 20 and _norm_title(s.get("title")) == nt:
            return s
    return None


def _next_id(items):
    n = max([int(s["id"].split("-")[1]) for s in items] + [0])
    return "S-%03d" % (n + 1)


# ---------------------------------------------------------------------------
# Metadata resolution (FR-3.3)
# ---------------------------------------------------------------------------

def resolve(doi=None, pmid=None):
    """Canonical metadata: PubMed record when a PMID exists, else Crossref."""
    out = {"doi": doi, "pmid": str(pmid) if pmid else None, "pmcid": None, "csl": None, "metadata_source": None,
           "retraction": {"status": "unknown"}}
    cr = None
    if doi:
        cr = md.crossref_work(doi)
        if cr:
            out["csl"] = md.crossref_to_csl(cr)
            out["metadata_source"] = "Crossref"
            out["retraction"] = md.crossref_retraction(cr)
        if not pmid:
            try:
                out["pmid"] = md.pmid_for_doi(doi)
            except md.MetadataError:
                pass
    if out["pmid"]:
        summ = md.pubmed_summary([out["pmid"]])
        if summ:
            pub = md.pubmed_to_csl(summ[0])
            out["pmcid"] = pub.get("PMCID")
            out["doi"] = out["doi"] or pub.get("DOI")
            if out["csl"] is None or not doi:
                out["csl"] = pub
                out["metadata_source"] = "PubMed"
            else:  # keep Crossref record, add PubMed identifiers and journal abbreviation
                out["csl"]["PMID"] = pub.get("PMID")
                if pub.get("container-title-short"):
                    out["csl"]["container-title-short"] = pub["container-title-short"]
            pr = md.pubmed_retraction(summ[0])
            if pr["status"] != "ok" or out["retraction"]["status"] == "unknown":
                out["retraction"] = pr
            if out["doi"] and cr is None and doi is None:
                try:
                    cr = md.crossref_work(out["doi"])
                    if cr and md.crossref_retraction(cr)["status"] != "ok":
                        out["retraction"] = md.crossref_retraction(cr)
                except md.MetadataError:
                    pass
        elif not out["csl"]:
            raise LiteratureError(f"PMID {out['pmid']} не найден в PubMed")
    if not out["csl"]:
        raise LiteratureError(f"DOI {doi} не найден в Crossref")
    return out


def _title_in_text(title, text):
    words = [w for w in _norm_title(title).split() if len(w) > 2]
    if not words:
        return False
    hay = " " + _norm_title(text) + " "
    return sum(1 for w in words if f" {w} " in hay) / len(words) >= 0.85


# ---------------------------------------------------------------------------
# Adding sources
# ---------------------------------------------------------------------------

def _year(csl):
    return ((csl.get("issued") or {}).get("date-parts") or [[None]])[0][0]


def _new_source(sid, kind, meta, fulltext, verification):
    csl = meta.get("csl") or {}
    return {
        "id": sid, "kind": kind, "title": csl.get("title", ""), "label": label(csl) if csl else sid,
        "journal": csl.get("container-title-short") or csl.get("container-title"),
        "year": _year(csl),
        "doi": meta.get("doi"), "pmid": meta.get("pmid"), "pmcid": meta.get("pmcid"), "csl": csl or None,
        "metadata_source": meta.get("metadata_source"), "verification": verification,
        "retraction": meta.get("retraction", {"status": "unknown"}), "fulltext": fulltext,
        "n_chunks": 0, "extraction": None, "added_at": now_iso(),
    }


def _index(store, pid, sid, blocks):
    ch = make_chunks(sid, blocks)
    write_json(_dir(store, pid) / "chunks" / f"{sid}.json", ch)
    return len(ch)


def add_pdf(store: ProjectStore, pid: str, filename: str, content: bytes):
    if not content.startswith(b"%PDF"):
        raise LiteratureError("файл не похож на PDF")
    try:
        pages = parsing.pdf_pages(content)
    except Exception as exc:
        raise LiteratureError("не удалось прочитать PDF") from exc
    if not any(p.strip() for p in pages):
        raise LiteratureError("в PDF нет текстового слоя (скан); нужен OCR — появится в MVP")
    head = parsing.first_pages_text(pages)
    doi = parsing.find_doi(head)
    items = sources(store, pid)

    meta, verification = {"csl": None}, {"status": "not_found", "details": "DOI в PDF не найден"}
    try:
        if doi:
            meta = resolve(doi=doi)
        else:
            first_lines = " ".join(l.strip() for l in pages[0].splitlines()[:12] if l.strip())
            for cand in md.crossref_search(first_lines, rows=3):
                title = md._first(cand.get("title"))
                if title and _title_in_text(title, head):
                    meta = resolve(doi=cand["DOI"].lower())
                    break
        if meta.get("csl"):
            if _title_in_text(meta["csl"].get("title", ""), head):
                verification = {"status": "verified",
                                "details": f"название из {meta['metadata_source']} найдено на первых страницах PDF"}
            else:
                verification = {"status": "mismatch",
                                "details": f"название из {meta['metadata_source']} не найдено в PDF — проверьте DOI"}
    except (md.MetadataError, LiteratureError) as exc:
        verification = {"status": "not_found", "details": str(exc)}

    dup = find_duplicate(items, meta.get("doi"), meta.get("pmid"), (meta.get("csl") or {}).get("title"))
    if dup:
        raise LiteratureError(f"этот источник уже в базе: {dup['id']} ({dup['label']})")
    sid = _next_id(items)
    fulltext = {"status": "pdf", "origin": "загружен автором", "pages": len(pages), "filename": filename}
    src = _new_source(sid, "pdf", meta, fulltext, verification)
    if not src["title"]:
        src["title"] = filename
        src["label"] = filename
    write_bytes(_dir(store, pid) / "files" / f"{sid}.pdf", content)
    from . import grobid
    tei = grobid.process(content)
    if tei and tei["blocks"]:
        src["fulltext"]["parser"] = "GROBID"
        blocks = tei["blocks"]
    else:
        src["fulltext"]["parser"] = "pypdf"
        blocks = parsing.pdf_blocks(pages)
    src["n_chunks"] = _index(store, pid, sid, blocks)
    items.append(src)
    _save_sources(store, pid, items)
    store.audit(pid, "author", "literature.pdf_added", {"source": sid, "verification": verification["status"]})
    return src


def _fetch_fulltext(store, pid, sid, meta):
    """PMC JATS → Unpaywall PDF → PubMed abstract."""
    if meta.get("pmcid"):
        try:
            xml = md.pmc_fulltext_xml(meta["pmcid"])
            if xml:
                blocks = parsing.jats_blocks(xml)
                if blocks:
                    return {"status": "pmc", "origin": f"PubMed Central {meta['pmcid']}"}, blocks
        except Exception:  # network errors or malformed XML must not block adding the source
            pass
    if meta.get("doi"):
        try:
            oa = md.unpaywall_pdf(meta["doi"])
        except md.MetadataError:
            oa = None
        if oa:
            pages = parsing.pdf_pages(oa["content"])
            write_bytes(_dir(store, pid) / "files" / f"{sid}.pdf", oa["content"])
            return ({"status": "pdf", "origin": f"Unpaywall ({oa.get('license') or 'OA'})", "pages": len(pages),
                     "url": oa["url"]}, parsing.pdf_blocks(pages))
    if meta.get("pmid"):
        try:
            blocks = md.pubmed_abstract(meta["pmid"])
        except md.MetadataError:
            blocks = []
        if blocks:
            return {"status": "abstract_only", "origin": "PubMed abstract"}, blocks
    return {"status": "none", "origin": "полный текст недоступен — загрузите PDF"}, []


def add_identifier(store: ProjectStore, pid: str, value: str):
    value = value.strip()
    if re.fullmatch(r"\d{5,9}", value):
        doi, pmid = None, value
    elif md.DOI_RE.search(value):
        doi, pmid = md.normalize_doi(md.DOI_RE.search(value).group(1)), None
    else:
        raise LiteratureError("укажите DOI (10.xxxx/…) или PMID (число)")
    items = sources(store, pid)
    dup = find_duplicate(items, doi, pmid)
    if dup:
        raise LiteratureError(f"этот источник уже в базе: {dup['id']} ({dup['label']})")
    try:
        meta = resolve(doi=doi, pmid=pmid)
    except md.MetadataError as exc:
        raise LiteratureError(str(exc)) from exc
    dup = find_duplicate(items, meta.get("doi"), meta.get("pmid"), meta["csl"].get("title"))
    if dup:
        raise LiteratureError(f"этот источник уже в базе: {dup['id']} ({dup['label']})")
    sid = _next_id(items)
    fulltext, blocks = _fetch_fulltext(store, pid, sid, meta)
    src = _new_source(sid, "identifier", meta, fulltext,
                      {"status": "verified", "details": f"метаданные получены из {meta['metadata_source']}"})
    src["n_chunks"] = _index(store, pid, sid, blocks) if blocks else 0
    items.append(src)
    _save_sources(store, pid, items)
    store.audit(pid, "author", "literature.identifier_added", {"source": sid, "fulltext": fulltext["status"]})
    return src


def set_identifier(store, pid, sid, value):
    """Author corrects the DOI/PMID of an uploaded PDF; metadata are re-fetched and re-verified."""
    src = get_source(store, pid, sid)
    value = value.strip()
    doi = md.normalize_doi(value) if md.DOI_RE.search(value) else None
    pmid = value if re.fullmatch(r"\d{5,9}", value) else None
    if not doi and not pmid:
        raise LiteratureError("укажите DOI или PMID")
    meta = resolve(doi=doi, pmid=pmid)
    dup = find_duplicate(sources(store, pid), meta.get("doi"), meta.get("pmid"), exclude=sid)
    if dup:
        raise LiteratureError(f"этот источник уже в базе: {dup['id']}")
    verification = {"status": "verified", "details": f"метаданные из {meta['metadata_source']}"}
    if src["kind"] == "pdf":
        pdf = _dir(store, pid) / "files" / f"{sid}.pdf"
        head = parsing.first_pages_text(parsing.pdf_pages(read_bytes(pdf)))
        if not _title_in_text(meta["csl"].get("title", ""), head):
            verification = {"status": "mismatch", "details": "название по указанному DOI/PMID не найдено в PDF"}
    csl = meta["csl"]
    return update_source(store, pid, sid, doi=meta["doi"], pmid=meta["pmid"], pmcid=meta["pmcid"], csl=csl,
                         title=csl.get("title", ""), label=label(csl), year=_year(csl),
                         journal=csl.get("container-title-short") or csl.get("container-title"),
                         metadata_source=meta["metadata_source"], verification=verification,
                         retraction=meta["retraction"])


def import_references(store, pid, text):
    report = {"added": [], "skipped": []}
    for rec in md.parse_reference_file(text):
        ident = rec["doi"] or rec["pmid"]
        if not ident:
            report["skipped"].append({"title": rec["title"], "reason": "нет DOI/PMID — добавьте PDF или идентификатор"})
            continue
        try:
            src = add_identifier(store, pid, ident)
            report["added"].append({"id": src["id"], "label": src["label"]})
        except (LiteratureError, md.MetadataError) as exc:
            report["skipped"].append({"title": rec["title"] or ident, "reason": str(exc)})
    return report


def delete_source(store, pid, sid):
    items = [s for s in sources(store, pid) if s["id"] != sid]
    _save_sources(store, pid, items)
    for p in (_dir(store, pid) / "chunks" / f"{sid}.json", _dir(store, pid) / "files" / f"{sid}.pdf"):
        if p.exists():
            p.unlink()
    cits = [c for c in citations(store, pid) if c["source_id"] != sid]
    write_json(_dir(store, pid) / "citations.json", cits)
    store.audit(pid, "author", "literature.source_deleted", {"source": sid})


def recheck_retractions(store, pid):
    changed = []
    for s in sources(store, pid):
        if not (s.get("doi") or s.get("pmid")):
            continue
        try:
            meta = resolve(doi=s.get("doi"), pmid=s.get("pmid"))
        except (md.MetadataError, LiteratureError):
            continue
        if meta["retraction"] != s.get("retraction"):
            update_source(store, pid, s["id"], retraction=meta["retraction"])
            changed.append(s["id"])
    return changed


def pubmed_suggestions(store, pid, query, retmax=10):
    """FR-3.12: candidates are only shown; they enter the base after the author adds them."""
    ids = md.pubmed_search(query, retmax=retmax)
    if not ids:
        return []
    have = {s.get("pmid") for s in sources(store, pid)}
    out = []
    for s in md.pubmed_summary(ids):
        csl = md.pubmed_to_csl(s)
        out.append({"pmid": s["uid"], "label": label(csl), "title": csl.get("title"),
                    "journal": csl.get("container-title-short"), "in_base": s["uid"] in have,
                    "retraction": md.pubmed_retraction(s)["status"]})
    return out


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

def citations(store, pid):
    return read_json(_dir(store, pid) / "citations.json", [])


def save_citations(store, pid, items):
    write_json(_dir(store, pid) / "citations.json", items)


def add_citation(store, pid, claim, context, chunk, quote, relation="supports", origin="agent", verification=None):
    items = citations(store, pid)
    n = max([int(c["id"].split("-")[1]) for c in items] + [0]) + 1
    cit = {"id": "CIT-%04d" % n, "claim": claim, "context": context, "source_id": chunk["source_id"],
           "chunk_id": chunk["id"], "page": chunk.get("page"), "section": chunk.get("section"), "quote": quote,
           "relation": relation, "origin": origin,
           "verification": verification or {"status": "unverified", "rationale": "независимая проверка не выполнялась"},
           "decision": "pending", "created_at": now_iso()}
    items.append(cit)
    save_citations(store, pid, items)
    return cit


def update_citation(store, pid, cid, **changes):
    items = citations(store, pid)
    for c in items:
        if c["id"] == cid:
            c.update(changes)
            save_citations(store, pid, items)
            store.audit(pid, "author" if "decision" in changes else "agent", "citation.updated",
                        {"citation": cid, **{k: v for k, v in changes.items() if k == "decision"}})
            return c
    raise LiteratureError("цитата не найдена")


def active_citations(store, pid):
    return [c for c in citations(store, pid) if c["decision"] != "rejected"]


def settings(store, pid):
    return read_json(_dir(store, pid) / "settings.json", {"style": DEFAULT_STYLE})


def set_style(store, pid, style):
    if style not in available_styles():
        raise LiteratureError("неизвестный стиль")
    write_json(_dir(store, pid) / "settings.json", {"style": style})


def bibliography(store, pid, style=None, scope="cited"):
    style = style or settings(store, pid)["style"]
    srcs = sources(store, pid)
    if scope == "all":
        order = [s["id"] for s in srcs]
    else:
        order = []
        for c in sorted(citations(store, pid), key=lambda c: c["created_at"]):
            if c["decision"] == "accepted" and c["source_id"] not in order:
                order.append(c["source_id"])
    return render(srcs, order, style)


def completeness(store, pid):
    """FR-3.11 plus blocking issues for export (FR-3.9, FR-3.13)."""
    srcs = sources(store, pid)
    ids = {s["id"] for s in srcs}
    cits = citations(store, pid)
    used = {c["source_id"] for c in cits if c["decision"] == "accepted"}
    dups = []
    for i, s in enumerate(srcs):
        d = find_duplicate(srcs[:i], s.get("doi"), s.get("pmid"), s.get("title"))
        if d:
            dups.append({"source": s["id"], "duplicate_of": d["id"]})
    blocking, warnings = [], []
    for c in cits:
        if c["source_id"] not in ids:
            blocking.append(f"{c['id']}: ссылка на источник вне базы")
        elif c["decision"] == "accepted" and c["verification"]["status"] not in ("supported",):
            blocking.append(f"{c['id']}: принятая цитата не подтверждена проверкой "
                            f"({c['verification']['status']}) — решите вручную")
    pending = [c["id"] for c in cits if c["decision"] == "pending"]
    if pending:
        warnings.append(f"цитат ожидают решения автора: {len(pending)}")
    for s in srcs:
        if s["retraction"].get("status") == "retracted" and s["id"] in used:
            blocking.append(f"{s['id']} ({s['label']}): статья отозвана, но процитирована")
        elif s["retraction"].get("status") == "retracted":
            warnings.append(f"{s['id']} ({s['label']}): статья отозвана")
        if s["verification"]["status"] != "verified":
            warnings.append(f"{s['id']} ({s['label']}): метаданные не подтверждены — {s['verification']['details']}")
        if s["fulltext"]["status"] in ("none", "abstract_only"):
            warnings.append(f"{s['id']} ({s['label']}): доступен только "
                            f"{'abstract' if s['fulltext']['status'] == 'abstract_only' else 'библиографическая запись'}")
    return {"n_sources": len(srcs), "n_cited": len(used), "unused": [s["id"] for s in srcs if s["id"] not in used],
            "duplicates": dups, "blocking": blocking, "warnings": warnings}


# ---------------------------------------------------------------------------
# Literature matrix (FR-3.6)
# ---------------------------------------------------------------------------

_MARKER_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")


def marker_key(name: str):
    m = _MARKER_RE.search(name or "")
    if not m:
        return None
    k = m.group(0).upper()
    return {"KI67": "KI-67", "MIB-1": "KI-67", "C-KIT": "CD117"}.get(k, k)


def matrix(store, pid, own_series=None):
    cols = {"ihc": {}, "molecular": {}}
    rows = []
    for s in sources(store, pid):
        ex = s.get("extraction")
        if not ex:
            continue
        row = {"source_id": s["id"], "label": s["label"], "design": ex.get("design"), "n": ex.get("n_cases"),
               "entity": ex.get("entity"), "histotypes": ex.get("histotypes", []), "ihc": {}, "molecular": {}}
        for group, key_name in (("ihc", "markers"), ("molecular", "molecular")):
            for item in ex.get(key_name, []):
                k = marker_key(item.get("name") or item.get("gene"))
                if not k:
                    continue
                cols[group].setdefault(k, 0)
                cols[group][k] += 1
                row[group][k] = {"n_pos": item.get("n_positive"), "n_total": item.get("n_total"),
                                 "text": item.get("summary"), "chunk_id": item.get("chunk_id")}
        rows.append(row)
    if own_series:
        rows.insert(0, own_series)
        for group in ("ihc", "molecular"):
            for k in own_series[group]:
                cols[group].setdefault(k, 0)
    return {"columns": {g: sorted(c, key=lambda k: (-c[k], k)) for g, c in cols.items()}, "rows": rows}
