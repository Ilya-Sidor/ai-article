"""Bibliographic metadata and open-access full text (FR-3.2, FR-3.3, FR-3.12, FR-3.13).

Metadata for the reference list comes only from Crossref and PubMed, never
from the LLM. Requests carry identifiers or the author's search query only.
"""
import os
import re
import xml.etree.ElementTree as ET

import httpx

TIMEOUT = 20.0
USER_AGENT = "AI-Article/0.1 (pathology manuscript assistant)"
CONTACT_EMAIL = os.environ.get("AI_ARTICLE_CONTACT_EMAIL", "")
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MAX_PDF_BYTES = 40 * 1024 * 1024

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)


class MetadataError(RuntimeError):
    pass


def fetch(url: str, params: dict = None, accept: str = None) -> httpx.Response:
    """Single HTTP entry point (patched in tests)."""
    headers = {"User-Agent": USER_AGENT + (f" mailto:{CONTACT_EMAIL}" if CONTACT_EMAIL else "")}
    if accept:
        headers["Accept"] = accept
    try:
        return httpx.get(url, params=params, headers=headers, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise MetadataError(f"нет ответа от {httpx.URL(url).host}") from exc


def normalize_doi(value: str) -> str:
    v = value.strip()
    v = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", v, flags=re.I)
    v = re.sub(r"^doi:\s*", "", v, flags=re.I)
    return v.rstrip(".,;)]").lower()


def _eutils_params(**kw):
    params = dict(kw, tool="ai-article")
    if CONTACT_EMAIL:
        params["email"] = CONTACT_EMAIL
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    return params


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------

def crossref_work(doi: str):
    r = fetch(f"https://api.crossref.org/works/{doi}",
              params={"mailto": CONTACT_EMAIL} if CONTACT_EMAIL else None)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise MetadataError(f"Crossref ответил {r.status_code}")
    return r.json()["message"]


def crossref_search(bibliographic: str, rows: int = 3):
    params = {"query.bibliographic": bibliographic[:500], "rows": rows}
    if CONTACT_EMAIL:
        params["mailto"] = CONTACT_EMAIL
    r = fetch("https://api.crossref.org/works", params=params)
    if r.status_code != 200:
        raise MetadataError(f"Crossref ответил {r.status_code}")
    return r.json()["message"]["items"]


def _first(v):
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)


def crossref_to_csl(msg: dict) -> dict:
    date = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (msg.get(key) or {}).get("date-parts")
        if parts and parts[0] and parts[0][0]:
            date = {"date-parts": [parts[0]]}
            break
    csl_type = {"journal-article": "article-journal", "book-chapter": "chapter", "book": "book",
                "posted-content": "article", "proceedings-article": "paper-conference"}.get(msg.get("type"), "article")
    csl = {
        "type": csl_type,
        "title": _first(msg.get("title")) or "",
        "author": [{k: a[k] for k in ("family", "given") if k in a} for a in msg.get("author", []) if "family" in a]
        or [{"literal": a["name"]} for a in msg.get("author", []) if "name" in a],
        "container-title": _first(msg.get("container-title")) or "",
        "container-title-short": _first(msg.get("short-container-title")) or "",
        "volume": msg.get("volume"), "issue": msg.get("issue"), "page": msg.get("page"),
        "DOI": msg.get("DOI", "").lower(), "ISSN": _first(msg.get("ISSN")), "publisher": msg.get("publisher"),
    }
    if date:
        csl["issued"] = date
    return {k: v for k, v in csl.items() if v}


def crossref_retraction(msg: dict):
    for upd in msg.get("updated-by", []) or []:
        if upd.get("type") in ("retraction", "withdrawal", "removal"):
            return {"status": "retracted", "notice_doi": upd.get("DOI"), "source": "Crossref"}
    for upd in msg.get("update-to", []) or []:
        if upd.get("type") in ("retraction", "withdrawal"):
            return {"status": "retraction_notice", "notice_doi": msg.get("DOI"), "source": "Crossref"}
    return {"status": "ok", "source": "Crossref"}


# ---------------------------------------------------------------------------
# PubMed / PMC
# ---------------------------------------------------------------------------

def pubmed_summary(pmids):
    ids = ",".join(str(p) for p in pmids)
    r = fetch(f"{EUTILS}/esummary.fcgi", params=_eutils_params(db="pubmed", id=ids, retmode="json"))
    if r.status_code != 200:
        raise MetadataError(f"PubMed ответил {r.status_code}")
    result = r.json().get("result", {})
    return [result[u] for u in result.get("uids", []) if "error" not in result[u]]


def pubmed_search(term: str, retmax: int = 10):
    r = fetch(f"{EUTILS}/esearch.fcgi", params=_eutils_params(db="pubmed", term=term, retmode="json",
                                                                 retmax=retmax, sort="relevance"))
    if r.status_code != 200:
        raise MetadataError(f"PubMed ответил {r.status_code}")
    return r.json().get("esearchresult", {}).get("idlist", [])


def pmid_for_doi(doi: str):
    ids = pubmed_search(f"{doi}[doi]", retmax=2)
    return ids[0] if len(ids) == 1 else None


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}


def pubmed_to_csl(s: dict) -> dict:
    ids = {a["idtype"]: a["value"] for a in s.get("articleids", [])}
    date = None
    m = re.match(r"(\d{4})(?:\s+(\w{3}))?(?:\s+(\d{1,2}))?", s.get("pubdate", ""))
    if m:
        parts = [int(m.group(1))]
        if m.group(2) and m.group(2).lower() in MONTHS:
            parts.append(MONTHS[m.group(2).lower()])
            if m.group(3):
                parts.append(int(m.group(3)))
        date = {"date-parts": [parts]}
    authors = []
    for a in s.get("authors", []):
        if a.get("authtype", "Author") != "Author":
            continue
        name = a.get("name", "")
        mm = re.match(r"(.+?)\s+([A-Z]{1,4})$", name)
        # PubMed initials "AB" → "A. B." so that CSL styles re-initialize them correctly
        authors.append({"family": mm.group(1), "given": " ".join(f"{c}." for c in mm.group(2))} if mm
                       else {"literal": name})
    csl = {
        "type": "article-journal", "title": s.get("title", "").rstrip("."), "author": authors,
        "container-title": s.get("fulljournalname", ""), "container-title-short": s.get("source", ""),
        "volume": s.get("volume"), "issue": s.get("issue"), "page": s.get("pages"),
        "DOI": ids.get("doi", "").lower(), "PMID": s.get("uid"), "PMCID": ids.get("pmc"),
    }
    if date:
        csl["issued"] = date
    return {k: v for k, v in csl.items() if v}


def pubmed_retraction(s: dict):
    types = [t.lower() for t in s.get("pubtype", [])]
    if "retracted publication" in types:
        return {"status": "retracted", "source": "PubMed"}
    if "retraction of publication" in types:
        return {"status": "retraction_notice", "source": "PubMed"}
    return {"status": "ok", "source": "PubMed"}


def pubmed_abstract(pmid: str):
    r = fetch(f"{EUTILS}/efetch.fcgi", params=_eutils_params(db="pubmed", id=pmid, retmode="xml"))
    if r.status_code != 200:
        return []
    root = ET.fromstring(r.content)
    blocks = []
    for node in root.iter("AbstractText"):
        text = "".join(node.itertext()).strip()
        if text:
            label = node.get("Label")
            blocks.append({"page": None, "section": "Abstract" + (f" — {label.title()}" if label else ""),
                           "text": text})
    return blocks


def pmc_fulltext_xml(pmcid: str):
    """JATS XML for open-access PMC articles, or None."""
    num = pmcid.upper().replace("PMC", "")
    r = fetch(f"{EUTILS}/efetch.fcgi", params=_eutils_params(db="pmc", id=num, retmode="xml"))
    if r.status_code != 200 or b"<body" not in r.content:
        return None
    return r.content


# ---------------------------------------------------------------------------
# Unpaywall
# ---------------------------------------------------------------------------

def unpaywall_pdf(doi: str):
    """Download an OA PDF located by Unpaywall (requires AI_ARTICLE_CONTACT_EMAIL)."""
    if not CONTACT_EMAIL:
        return None
    r = fetch(f"https://api.unpaywall.org/v2/{doi}", params={"email": CONTACT_EMAIL})
    if r.status_code != 200:
        return None
    loc = (r.json() or {}).get("best_oa_location") or {}
    url = loc.get("url_for_pdf")
    if not url:
        return None
    pdf = fetch(url, accept="application/pdf")
    if pdf.status_code != 200 or not pdf.content.startswith(b"%PDF") or len(pdf.content) > MAX_PDF_BYTES:
        return None
    return {"content": pdf.content, "url": url, "license": loc.get("license")}


# ---------------------------------------------------------------------------
# Reference manager import (FR-3.14)
# ---------------------------------------------------------------------------

def parse_reference_file(text: str):
    """Records from RIS or BibTeX: [{doi, pmid, title}]."""
    records = []
    if re.search(r"^TY  - ", text, re.M):
        for block in re.split(r"^ER  -.*$", text, flags=re.M):
            rec = {"doi": None, "pmid": None, "title": None}
            for line in block.splitlines():
                m = re.match(r"^([A-Z][A-Z0-9])  - (.*)$", line)
                if not m:
                    continue
                tag, val = m.group(1), m.group(2).strip()
                if tag == "DO":
                    rec["doi"] = normalize_doi(val)
                elif tag in ("AN", "M1", "C7") and re.fullmatch(r"\d{6,9}", val):
                    rec["pmid"] = rec["pmid"] or val
                elif tag in ("TI", "T1") and not rec["title"]:
                    rec["title"] = val
                elif tag == "UR" and not rec["doi"]:
                    dm = DOI_RE.search(val)
                    if dm:
                        rec["doi"] = normalize_doi(dm.group(1))
            if any(rec.values()):
                records.append(rec)
        return records
    for entry in re.split(r"(?=@\w+\s*\{)", text):
        if not entry.strip().startswith("@"):
            continue
        def field(name):
            m = re.search(name + r"\s*=\s*[{\"](.+?)[}\"]\s*,?\s*$", entry, re.I | re.M)
            return m.group(1).strip() if m else None
        rec = {"doi": normalize_doi(field("doi")) if field("doi") else None, "pmid": field("pmid"),
               "title": (field("title") or "").strip("{}") or None}
        if any(rec.values()):
            records.append(rec)
    return records
