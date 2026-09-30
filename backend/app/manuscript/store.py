"""Manuscript state: plan, author inputs, sections with version history (FR-0.3, FR-5.1, FR-5.2).

``data/projects/<id>/manuscript/state.json`` holds everything; each section keeps
all its versions (who, when, why, source text, input fingerprint), so any
version can be compared or restored.
"""
import difflib
import hashlib
import json
import re

from ..storage import now_iso, read_json, write_json

KINDS = {
    "introduction": [r"introduc", r"background", r"введени"],
    "methods": [r"method", r"materials", r"patients and", r"материал", r"метод"],
    "case_presentation": [r"case (presentation|report|description|history)", r"clinical (summary|history)",
                          r"описание (случая|наблюдени)", r"клиническ\w* (случа|наблюдени)"],
    "results": [r"result", r"findings", r"результат"],
    "discussion": [r"discuss", r"обсуждени"],
    "conclusion": [r"conclu", r"заключени", r"вывод"],
}
DEFAULT_SECTIONS = {
    "case_report": ["Introduction", "Case presentation", "Discussion", "Conclusion"],
    "_default": ["Introduction", "Materials and methods", "Results", "Discussion", "Conclusion"],
}
DEFAULT_SECTIONS_RU = {
    "case_report": ["Введение", "Описание наблюдения", "Обсуждение", "Заключение"],
    "_default": ["Введение", "Материал и методы", "Результаты", "Обсуждение", "Заключение"],
}
NARRATIVE = ["introduction", "methods", "case_presentation", "results", "discussion", "conclusion", "other"]


class ManuscriptError(ValueError):
    pass


def kind_of(heading: str) -> str:
    h = heading.lower()
    for kind, pats in KINDS.items():
        if any(re.search(p, h) for p in pats):
            return kind
    if h.startswith(("abstract", "резюме", "аннотаци")):
        return "abstract"
    return "other"


def slug(heading):
    """Section key: ASCII, so that Russian headings get distinct keys too ("Результаты" → "rezultaty")."""
    from ..analysis.figures import latin
    return re.sub(r"[^a-z0-9]+", "_", latin(heading.lower())).strip("_")[:40] or "section"


def path(store, pid):
    return store.dir(pid) / "manuscript" / "state.json"


def load(store, pid):
    return read_json(path(store, pid), {
        "ids": {}, "terms": {}, "inputs": {}, "plan": None, "sections": {}, "front": {},
        "tables": [], "figures": [], "created_at": now_iso()})


def save(store, pid, state):
    state["updated_at"] = now_iso()
    write_json(path(store, pid), state)
    return state


def fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Sections and versions
# ---------------------------------------------------------------------------

def ensure_sections(state, headings, lang="en"):
    """Create section slots from the approved plan: abstract, main sections, statements.

    A Russian manuscript also gets an English abstract (Russian journals indexed in international databases
    require the abstract in both languages)."""
    if lang == "ru":
        order = [("abstract", "Резюме", "abstract"), ("abstract_en", "Abstract", "abstract_en")]
    else:
        order = [("abstract", "Abstract", "abstract")]
    for h in headings:
        order.append((slug(h), h, kind_of(h)))
    order.append(("statements", "Дополнительная информация" if lang == "ru" else "Statements", "statements"))
    for i, (key, heading, kind) in enumerate(order):
        sec = state["sections"].setdefault(key, {"key": key, "versions": [], "comments": [], "status": "empty",
                                                 "number_whitelist": []})
        sec.update({"heading": heading, "kind": kind, "order": i})
    keep = {k for k, _, _ in order}
    for k in list(state["sections"]):
        if k not in keep and not state["sections"][k]["versions"]:
            del state["sections"][k]
    return state


def ordered(state):
    return sorted(state["sections"].values(), key=lambda s: s.get("order", 99))


def current(section):
    return section["versions"][-1] if section["versions"] else None


def current_source(section):
    v = current(section)
    return v["source"] if v else ""


def add_version(state, key, source, actor, note, fp=None, extra=None):
    sec = state["sections"].get(key)
    if sec is None:
        raise ManuscriptError("раздел не найден")
    cur = current(sec)
    if cur and cur["source"] == source and actor == "author":
        return cur
    v = {"n": len(sec["versions"]) + 1, "ts": now_iso(), "actor": actor, "note": note, "source": source,
         "fingerprint": fp if fp is not None else (cur or {}).get("fingerprint")}
    if extra:
        v.update(extra)
    sec["versions"].append(v)
    sec["status"] = "draft"
    return v


def rollback(state, key, n):
    sec = state["sections"][key]
    old = next((v for v in sec["versions"] if v["n"] == n), None)
    if old is None:
        raise ManuscriptError("версия не найдена")
    return add_version(state, key, old["source"], "author", f"откат к версии {n}", old.get("fingerprint"))


def diff(section, a, b):
    va = next((v for v in section["versions"] if v["n"] == a), None)
    vb = next((v for v in section["versions"] if v["n"] == b), None)
    if not va or not vb:
        raise ManuscriptError("версия не найдена")
    sa = re.split(r"(?<=[.!?])\s+", va["source"])
    sb = re.split(r"(?<=[.!?])\s+", vb["source"])
    return list(difflib.unified_diff(sa, sb, f"v{a}", f"v{b}", n=1, lineterm=""))


def accept(state, key):
    sec = state["sections"][key]
    if not sec["versions"]:
        raise ManuscriptError("раздел ещё не написан")
    sec["status"] = "accepted"
    sec["accepted_version"] = current(sec)["n"]
    sec["accepted_at"] = now_iso()


def add_comment(state, key, quote, text):
    sec = state["sections"][key]
    c = {"id": "c%d" % (len(sec["comments"]) + 1), "ts": now_iso(), "quote": quote, "text": text, "resolved": False}
    sec["comments"].append(c)
    return c
