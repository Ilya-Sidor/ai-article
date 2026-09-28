"""CSL style registry: bundled styles plus any style from the CSL repository (FR-3.10).

Journal-specific styles in the repository are usually "dependent" (they only point
to a parent). citeproc-py needs the independent parent, so installing a dependent
style stores the parent file and an alias.
"""
import re
from pathlib import Path

from .. import config
from . import metadata as md

BUNDLED_DIR = Path(__file__).parent / "csl"
BUNDLED_TITLES = {
    "nlm-citation-sequence": "Vancouver / NLM (числовой)",
    "elsevier-vancouver": "Elsevier Vancouver",
    "american-medical-association": "AMA",
    "springer-basic-brackets": "Springer basic (числовой)",
    "nature": "Nature",
    "apa": "APA 7",
    "harvard-cite-them-right": "Harvard",
}
DEFAULT_STYLE = "nlm-citation-sequence"
REPO = "https://raw.githubusercontent.com/citation-style-language/styles/master"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,120}$")


def _dir():
    d = config.DATA_DIR / "csl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _aliases():
    from ..storage import read_json
    return read_json(_dir() / "aliases.json", {})


def available():
    """id → title for every style that can be rendered."""
    out = dict(BUNDLED_TITLES)
    from ..storage import read_json
    installed = read_json(_dir() / "installed.json", {})
    out.update(installed)
    return out


def path(style_id: str) -> Path:
    style_id = _aliases().get(style_id, style_id)
    for d in (BUNDLED_DIR, _dir()):
        p = d / f"{style_id}.csl"
        if p.exists():
            return p
    raise ValueError("неизвестный стиль")


def _fetch(style_id):
    for sub in ("", "dependent/"):
        r = md.fetch(f"{REPO}/{sub}{style_id}.csl")
        if r.status_code == 200 and b"<style" in r.content:
            return r.content.decode("utf-8")
    return None


def install(style_id: str) -> dict:
    """Download a style (and its parent if dependent) from the CSL repository."""
    from ..storage import write_json
    style_id = style_id.strip().lower()
    if not _ID_RE.match(style_id):
        raise ValueError("некорректный id стиля CSL")
    if style_id in available():
        return {"id": style_id, "title": available()[style_id]}
    text = _fetch(style_id)
    if text is None:
        raise ValueError(f"стиль «{style_id}» не найден в репозитории CSL")
    title = re.search(r"<title>(.*?)</title>", text, re.S)
    title = title.group(1).strip() if title else style_id
    parent = re.search(r'href="https?://www\.zotero\.org/styles/([a-z0-9-]+)"\s+rel="independent-parent"', text) \
        or re.search(r'rel="independent-parent"\s+href="https?://www\.zotero\.org/styles/([a-z0-9-]+)"', text)
    if parent:
        parent_id = parent.group(1)
        if not (BUNDLED_DIR / f"{parent_id}.csl").exists() and not (_dir() / f"{parent_id}.csl").exists():
            ptext = _fetch(parent_id)
            if ptext is None:
                raise ValueError(f"родительский стиль «{parent_id}» не найден")
            (_dir() / f"{parent_id}.csl").write_text(ptext, encoding="utf-8")
        aliases = _aliases()
        aliases[style_id] = parent_id
        write_json(_dir() / "aliases.json", aliases)
    else:
        (_dir() / f"{style_id}.csl").write_text(text, encoding="utf-8")
    from ..storage import read_json
    installed = read_json(_dir() / "installed.json", {})
    installed[style_id] = title + (f" (→ {parent.group(1)})" if parent else "")
    write_json(_dir() / "installed.json", installed)
    return {"id": style_id, "title": installed[style_id]}
