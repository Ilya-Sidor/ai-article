"""Full-text structure: PDF pages and PMC JATS → blocks with page and section (FR-3.4, FR-3.5).

The reference list of a paper is detected and excluded from the index so that
a citation can never be grounded in someone else's bibliography entry.
GROBID (PRD 8.1) can replace the heuristic segmenter behind the same interface.
"""
import io
import re
import xml.etree.ElementTree as ET

from pypdf import PdfReader

from .metadata import DOI_RE, normalize_doi

HEADINGS = [
    ("Abstract", r"abstract|summary"),
    ("Introduction", r"introduction|background"),
    ("Methods", r"(?:patients?,?\s+)?materials?\s+and\s+methods|methods|patients\s+and\s+methods|study\s+design"),
    ("Results", r"results|findings"),
    ("Discussion", r"discussion"),
    ("Conclusion", r"conclusions?|concluding\s+remarks"),
    ("References", r"references|bibliography|literature\s+cited"),
    ("Back matter", r"acknowledg(?:e)?ments?|funding|conflicts?\s+of\s+interest|disclosures?|author\s+contributions"),
]
HEADING_RE = re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2})*\.?\s+)?(" + "|".join(f"(?P<g{i}>{p})" for i, (_, p) in enumerate(HEADINGS))
    + r")\s*[:.]?\s*$", re.I)
NOT_INDEXED = {"References", "Back matter"}


def _heading(line: str):
    if len(line) > 45:
        return None
    m = HEADING_RE.match(line)
    if not m:
        return None
    for i, (name, _) in enumerate(HEADINGS):
        if m.group(f"g{i}"):
            return name
    return None


def clean_text(text: str) -> str:
    text = text.replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
    text = re.sub(r"[ \t]+", " ", text)
    return text


def pdf_pages(content: bytes):
    reader = PdfReader(io.BytesIO(content))
    return [clean_text(p.extract_text() or "") for p in reader.pages]


def find_doi(text: str):
    m = DOI_RE.search(text)
    return normalize_doi(m.group(1)) if m else None


def pdf_blocks(pages):
    """Split pages into paragraphs tagged with page number and current section."""
    blocks = []
    section = "Front matter"
    for pno, page in enumerate(pages, 1):
        buf = []

        def flush():
            text = " ".join(buf).strip()
            if text:
                blocks.append({"page": pno, "section": section, "text": text})
            buf.clear()

        for line in page.splitlines():
            line = line.strip()
            h = _heading(line)
            if h:
                flush()
                section = h
                continue
            if not line:
                flush()
                continue
            buf.append(line)
            if line.endswith((".", ":")) and len(" ".join(buf)) > 600:
                flush()
        flush()
    return blocks


def jats_blocks(xml: bytes):
    """Sections, paragraphs, figure and table captions from a PMC JATS article."""
    root = ET.fromstring(xml)
    article = root.find(".//article") if root.tag != "article" else root
    if article is None:
        return []
    blocks = []

    def text_of(node):
        return re.sub(r"\s+", " ", "".join(node.itertext())).strip()

    abstract = article.find(".//front//abstract")
    if abstract is not None:
        for p in abstract.iter("p"):
            t = text_of(p)
            if t:
                blocks.append({"page": None, "section": "Abstract", "text": t})

    def canonical(title):
        h = _heading(title)
        return h or title[:60]

    def walk(sec, name):
        title = sec.find("title")
        sec_name = canonical(text_of(title)) if title is not None and text_of(title) else name
        if sec_name in NOT_INDEXED:
            return
        for child in sec:
            if child.tag == "p":
                t = text_of(child)
                if t:
                    blocks.append({"page": None, "section": sec_name, "text": t})
            elif child.tag == "sec":
                walk(child, sec_name)
            elif child.tag in ("table-wrap", "fig"):
                cap = child.find(".//caption")
                label = child.find("label")
                t = " ".join(filter(None, [text_of(label) if label is not None else "",
                                            text_of(cap) if cap is not None else ""]))
                if child.tag == "table-wrap":
                    rows = [" | ".join(text_of(c) for c in tr) for tr in child.iter("tr")]
                    t = (t + " " + " ; ".join(rows)).strip()
                if t:
                    blocks.append({"page": None, "section": sec_name, "text": t})

    body = article.find(".//body")
    if body is not None:
        walk(body, "Body")
    return blocks


def first_pages_text(pages, n=2):
    return "\n".join(pages[:n])
