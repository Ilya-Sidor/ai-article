"""Journal profile base (Module 4).

Layout of ``data/journals/<id>/``::

    profile.json          current profile (fields with value, quote, status)
    sources/<G-1>.txt     text snapshot of each guidelines source
    versions/v<N>.json    previous profile versions (FR-4.3)

A profile records where each value came from: a quote from the guidelines text
("grounded"), a value whose quote could not be found ("unverified"), an author's
manual entry ("manual"), or an explicit "not stated in the guidelines"
("missing"). A person confirms the whole profile against the official site;
the date and the source URL are stored (FR-4.1, acceptance criteria).
"""
import difflib
import hashlib
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config
from ..literature import metadata as md
from ..literature.citations import quote_in_text
from ..storage import now_iso, read_json, write_json
from .schema import FIELDS, parse_value

STALE_DAYS = 90
STARTER = Path(__file__).parent / "starter.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
CHALLENGE_MARKERS = ("client challenge", "just a moment", "enable javascript", "captcha", "access denied",
                     "are you a robot", "checking your browser", "cf-browser-verification")


class JournalError(ValueError):
    pass


def _root():
    d = config.DATA_DIR / "journals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dir(jid):
    if not _ID_RE.match(jid or ""):
        raise JournalError("журнал не найден")
    d = _root() / jid
    if not (d / "profile.json").exists():
        raise JournalError("журнал не найден")
    return d


def slugify(name):
    """Profile id: ASCII, Russian names transliterated ("Вопросы онкологии" → "voprosy-onkologii")."""
    from ..analysis.figures import latin
    s = re.sub(r"[^a-z0-9]+", "-", latin(name.lower())).strip("-")
    return s[:60] or "journal"


def seed():
    """Create empty profiles for the starter list (names and official URLs only)."""
    for j in json.loads(STARTER.read_text(encoding="utf-8")):
        if not (_root() / j["id"] / "profile.json").exists():
            _create(j["id"], j["name"], j["publisher"], j["guidelines_url"], custom=False,
                    suggestions={"citation.csl_id": j.get("csl_suggestion")})


def _create(jid, name, publisher, url, custom, suggestions=None):
    profile = {"id": jid, "name": name, "publisher": publisher, "guidelines_url": url, "custom": custom,
               "created_at": now_iso(), "updated_at": now_iso(), "version": 1, "fields": {}, "sources": [],
               "extraction": None, "verification": {"status": "none"}, "guidelines_changed": None,
               "suggestions": suggestions or {}, "history": []}
    write_json(_root() / jid / "profile.json", profile)
    return profile


def create(name, publisher="", url=""):
    name = name.strip()
    if not name:
        raise JournalError("укажите название журнала")
    jid = slugify(name)
    if (_root() / jid / "profile.json").exists():
        raise JournalError(f"журнал уже есть в базе: {jid}")
    return _create(jid, name, publisher.strip(), url.strip(), custom=True)


def delete(jid):
    import shutil
    profile = get(jid)
    if not profile["custom"]:
        raise JournalError("журналы стартовой базы не удаляются")
    shutil.rmtree(_dir(jid))


def get(jid):
    return read_json(_dir(jid) / "profile.json")


def _save(profile, actor, action, changed=None):
    d = _dir(profile["id"])
    old = read_json(d / "profile.json")
    write_json(d / "versions" / f"v{old['version']}.json", old)
    profile["version"] = old["version"] + 1
    profile["updated_at"] = now_iso()
    profile["history"] = (profile.get("history") or [])[-199:] + [
        {"version": profile["version"], "ts": profile["updated_at"], "actor": actor, "action": action,
         "changed": changed or []}]
    write_json(d / "profile.json", profile)
    return profile


def status(profile):
    """empty | draft | verified | stale | changed | outdated_verification."""
    if profile.get("guidelines_changed"):
        return "changed"
    v = profile.get("verification") or {}
    if v.get("status") == "verified":
        verified_at = datetime.fromisoformat(v["verified_at"])
        if datetime.now(timezone.utc) - verified_at > timedelta(days=STALE_DAYS):
            return "stale"
        return "verified"
    if v.get("status") == "outdated":
        return "outdated_verification"
    return "draft" if profile["fields"] else "empty"


def summary(profile):
    counts = {}
    for f in profile["fields"].values():
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return {k: profile[k] for k in ("id", "name", "publisher", "guidelines_url", "custom", "updated_at", "version")} | {
        "status": status(profile), "verification": profile["verification"], "field_counts": counts,
        "n_sources": len(profile["sources"]),
        "last_source_at": max((s["fetched_at"] for s in profile["sources"]), default=None)}


def list_profiles():
    out = []
    for d in sorted(_root().iterdir()):
        p = read_json(d / "profile.json")
        if p:
            out.append(summary(p))
    return sorted(out, key=lambda p: (p["custom"], p["name"].lower()))


# ---------------------------------------------------------------------------
# Guidelines sources (URL, PDF, pasted text)
# ---------------------------------------------------------------------------

def html_to_text(html: bytes) -> str:
    import lxml.html
    doc = lxml.html.fromstring(html)
    for bad in doc.xpath("//script|//style|//noscript|//nav|//footer|//svg|//form"):
        bad.drop_tree()
    for el in doc.iter("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "table"):
        el.tail = "\n" + (el.tail or "")
    text = doc.text_content()
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _normalize(text):
    return "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip())


def _hash(text):
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:16]


def fetch_url(url):
    if not re.match(r"^https?://", url or ""):
        raise JournalError("нужен адрес страницы http(s)://…")
    try:
        r = md.fetch(url)
    except md.MetadataError as exc:
        raise JournalError(f"страница недоступна: {exc}") from exc
    ctype = r.headers.get("content-type", "") if hasattr(r, "headers") else ""
    if r.status_code != 200:
        raise JournalError(f"сайт ответил {r.status_code} — издатели часто закрывают guidelines от "
                           "автоматической загрузки. Откройте страницу в браузере и сохраните как PDF или "
                           "скопируйте текст.")
    if "pdf" in ctype or r.content[:4] == b"%PDF":
        return _pdf_text(r.content)
    text = html_to_text(r.content)
    if len(text) < 1500 or any(m in text[:3000].lower() for m in CHALLENGE_MARKERS):
        raise JournalError("сайт показал проверку «вы не робот» вместо guidelines. Откройте страницу в браузере "
                           "и сохраните как PDF или скопируйте текст.")
    return text


def _pdf_text(content):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:
        raise JournalError("не удалось прочитать PDF") from exc


def source_texts(profile):
    d = _dir(profile["id"])
    return {s["id"]: (d / "sources" / f"{s['id']}.txt").read_text(encoding="utf-8")
            for s in profile["sources"] if (d / "sources" / f"{s['id']}.txt").exists()}


def add_source(jid, kind, text=None, url=None, filename=None, content=None, replaces=None):
    """Store a guidelines snapshot. With ``replaces`` the new text is compared to the old one (FR-4.3)."""
    profile = get(jid)
    if kind == "url":
        text = fetch_url(url)
    elif kind == "pdf":
        text = _pdf_text(content)
    text = (text or "").strip()
    if len(text) < 300:
        raise JournalError("слишком мало текста — нужна полная страница Author Guidelines")
    new_hash = _hash(text)
    d = _dir(jid) / "sources"
    d.mkdir(exist_ok=True)
    old = next((s for s in profile["sources"] if s["id"] == replaces), None) if replaces else None
    change = None
    if old is not None:
        old_text = (d / f"{old['id']}.txt").read_text(encoding="utf-8")
        if old["hash"] == new_hash:
            old["checked_at"] = now_iso()
            _save(profile, "author", "guidelines.rechecked_unchanged")
            return {"changed": False, "source": old}
        diff = list(difflib.unified_diff(_normalize(old_text).splitlines(), _normalize(text).splitlines(),
                                         "было", "стало", n=1, lineterm=""))
        change = {"detected_at": now_iso(), "source_id": old["id"], "diff": diff[:400],
                  "truncated": len(diff) > 400}
    sid = old["id"] if old else "G-%d" % (max([int(s["id"][2:]) for s in profile["sources"]] + [0]) + 1)
    (d / f"{sid}.txt").write_text(text, encoding="utf-8")
    src = {"id": sid, "kind": kind, "url": url or (old or {}).get("url") or profile.get("guidelines_url"),
           "filename": filename, "fetched_at": now_iso(), "checked_at": now_iso(), "hash": new_hash,
           "chars": len(text)}
    profile["sources"] = [s for s in profile["sources"] if s["id"] != sid] + [src]
    if change:
        profile["guidelines_changed"] = change
    _save(profile, "author", "guidelines.source_added" if not old else "guidelines.source_updated")
    return {"changed": bool(change), "source": src, "diff": change}


def recheck(jid):
    """Re-download URL sources; report whether the guidelines text changed."""
    profile = get(jid)
    results = []
    for s in list(profile["sources"]):
        if s["kind"] != "url":
            results.append({"source": s["id"], "status": "manual",
                            "message": "источник загружен вручную — загрузите актуальную версию, чтобы сравнить"})
            continue
        try:
            r = add_source(jid, "url", url=s["url"], replaces=s["id"])
            results.append({"source": s["id"], "status": "changed" if r["changed"] else "unchanged"})
        except JournalError as exc:
            results.append({"source": s["id"], "status": "error", "message": str(exc)})
    return results


def delete_source(jid, sid):
    profile = get(jid)
    profile["sources"] = [s for s in profile["sources"] if s["id"] != sid]
    p = _dir(jid) / "sources" / f"{sid}.txt"
    if p.exists():
        p.unlink()
    return _save(profile, "author", "guidelines.source_deleted")


def acknowledge_change(jid):
    profile = get(jid)
    profile["guidelines_changed"] = None
    if profile["verification"].get("status") == "verified":
        profile["verification"] = dict(profile["verification"], status="outdated")
    return _save(profile, "author", "guidelines.change_acknowledged")


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

def set_fields(jid, updates, actor="author"):
    """updates: {path: {"value": raw, "missing": bool}}. Author edits become status "manual"."""
    profile = get(jid)
    texts = "\n".join(source_texts(profile).values())
    changed = []
    for path, upd in updates.items():
        if path not in FIELDS:
            raise JournalError(f"неизвестное поле: {path}")
        ftype = FIELDS[path][0]
        if upd.get("missing"):
            new = {"value": None, "quote": None, "status": "missing", "updated_at": now_iso()}
        else:
            try:
                value = parse_value(ftype, upd.get("value"))
            except ValueError as exc:
                raise JournalError(f"{FIELDS[path][1]}: {exc}") from exc
            if value is None:
                profile["fields"].pop(path, None)
                changed.append(path)
                continue
            quote = (upd.get("quote") or "").strip() or None
            grounded = bool(quote and texts and quote_in_text(quote, texts))
            new = {"value": value, "quote": quote, "status": "grounded" if grounded else "manual",
                   "updated_at": now_iso()}
        old = profile["fields"].get(path)
        if not old or old.get("value") != new["value"] or old.get("status") != new["status"]:
            profile["fields"][path] = new
            changed.append(path)
    if changed and profile["verification"].get("status") == "verified":
        profile["verification"] = dict(profile["verification"], status="outdated")
    return _save(profile, actor, "fields.updated", changed)


def apply_extraction(jid, items, meta):
    """Merge LLM-extracted fields. Manual entries of the author are never overwritten."""
    profile = get(jid)
    texts = "\n".join(source_texts(profile).values())
    changed, skipped_manual, invalid = [], [], []
    returned = set()
    for it in items:
        path = it["path"]
        if path not in FIELDS:
            continue
        returned.add(path)
        existing = profile["fields"].get(path)
        if existing and existing["status"] == "manual":
            skipped_manual.append(path)
            continue
        try:
            value = parse_value(FIELDS[path][0], it["value"])
        except ValueError as exc:
            invalid.append({"path": path, "value": it["value"], "error": str(exc)})
            continue
        if value is None:
            continue
        grounded = quote_in_text(it.get("quote") or "", texts)
        profile["fields"][path] = {"value": value, "quote": it.get("quote"),
                                   "status": "grounded" if grounded else "unverified", "updated_at": now_iso()}
        changed.append(path)
    for path in FIELDS:  # explicitly mark what the guidelines do not state (acceptance criteria)
        if path not in returned and path not in profile["fields"] and not path.endswith("csl_id"):
            profile["fields"][path] = {"value": None, "quote": None, "status": "missing", "updated_at": now_iso()}
    profile["extraction"] = dict(meta, n_changed=len(changed), skipped_manual=skipped_manual, invalid=invalid)
    if profile["verification"].get("status") == "verified":
        profile["verification"] = dict(profile["verification"], status="outdated")
    return _save(profile, "agent", "fields.extracted", changed)


def verify(jid, verified_by, note=""):
    profile = get(jid)
    if not verified_by.strip():
        raise JournalError("укажите, кто проверил профиль")
    if not profile["sources"]:
        raise JournalError("добавьте текст guidelines (URL, PDF или вставленный текст) — проверка фиксирует источник")
    if not profile.get("guidelines_url"):
        raise JournalError("укажите URL официальной страницы guidelines")
    if profile.get("guidelines_changed"):
        raise JournalError("guidelines изменились — сначала просмотрите изменения")
    pending = [p for p, f in profile["fields"].items() if f["status"] == "unverified"]
    if pending:
        raise JournalError(f"есть поля без подтверждающей цитаты ({len(pending)}) — исправьте или подтвердите вручную")
    profile["verification"] = {"status": "verified", "verified_by": verified_by.strip(), "verified_at": now_iso(),
                               "note": note.strip(), "guidelines_url": profile["guidelines_url"],
                               "sources": [{"id": s["id"], "hash": s["hash"], "fetched_at": s["fetched_at"]}
                                           for s in profile["sources"]]}
    return _save(profile, "author", "profile.verified")


def update_meta(jid, **changes):
    profile = get(jid)
    for k in ("name", "publisher", "guidelines_url"):
        if k in changes and changes[k] is not None:
            profile[k] = changes[k].strip()
    return _save(profile, "author", "profile.meta_updated")


def value(profile, path, default=None):
    f = (profile or {}).get("fields", {}).get(path)
    return f["value"] if f and f.get("value") is not None else default
