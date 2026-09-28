"""GROBID client (PRD 8.1): structured full text of PDFs as TEI.

Enabled with AI_ARTICLE_GROBID_URL (e.g. http://grobid:8070). Returns the same
blocks as parsing.pdf_blocks (page, section, text), with the reference list
excluded; on any failure the caller falls back to the built-in parser.
"""
import os
import re
import xml.etree.ElementTree as ET

import httpx

from .parsing import NOT_INDEXED, _heading

TEI = "{http://www.tei-c.org/ns/1.0}"


def url():
    return os.environ.get("AI_ARTICLE_GROBID_URL", "").rstrip("/")


def post(endpoint, content):
    """Single HTTP entry point (patched in tests)."""
    return httpx.post(endpoint, files={"input": ("paper.pdf", content, "application/pdf")},
                      data={"consolidateHeader": "0", "teiCoordinates": ["p", "head", "figure"]}, timeout=120)


def _page(el):
    coords = el.get("coords")
    if coords:
        m = re.match(r"(\d+),", coords.split(";")[0])
        if m:
            return int(m.group(1))
    return None


def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def parse_tei(xml: bytes):
    root = ET.fromstring(xml)
    doi = root.find(f".//{TEI}sourceDesc//{TEI}idno[@type='DOI']")
    title = root.find(f".//{TEI}titleStmt/{TEI}title")
    blocks = []
    for p in root.findall(f".//{TEI}profileDesc/{TEI}abstract//{TEI}p"):
        if _text(p):
            blocks.append({"page": _page(p) or 1, "section": "Abstract", "text": _text(p)})
    for div in root.findall(f".//{TEI}body/{TEI}div"):
        head = div.find(f"{TEI}head")
        name = _heading(_text(head)) if head is not None else None
        section = name or (_text(head)[:60] if head is not None else "Body")
        if section in NOT_INDEXED:
            continue
        for p in div.findall(f"{TEI}p"):
            if _text(p):
                blocks.append({"page": _page(p), "section": section, "text": _text(p)})
    for fig in root.findall(f".//{TEI}body//{TEI}figure"):
        desc = fig.find(f"{TEI}figDesc")
        label = fig.find(f"{TEI}head")
        text = " ".join(x for x in [_text(label) if label is not None else "", _text(desc) if desc is not None else ""] if x)
        if text:
            blocks.append({"page": _page(fig), "section": "Figures and tables", "text": text})
    return {"doi": doi.text.strip().lower() if doi is not None and doi.text else None,
            "title": _text(title) if title is not None else None, "blocks": blocks}


def process(content: bytes):
    """TEI-derived blocks, or None if GROBID is not configured or fails."""
    if not url():
        return None
    try:
        r = post(f"{url()}/api/processFulltextDocument", content)
        if r.status_code != 200:
            return None
        return parse_tei(r.content)
    except (httpx.HTTPError, ET.ParseError):
        return None
