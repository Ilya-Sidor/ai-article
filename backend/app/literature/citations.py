"""Citations as verifiable fragments (PRD 1.3, FR-3.8 – FR-3.11).

A citation links a claim to a chunk of a source in the project base plus a
verbatim quote. The quote is checked mechanically against the chunk text
before anything else; a chunk id that does not exist or a quote that is not
in the chunk is rejected, which rules out invented sources by construction.
"""
import re
import unicodedata
import warnings

from citeproc import Citation, CitationItem, CitationStylesBibliography, CitationStylesStyle, formatter
from citeproc.source.json import CiteProcJSON

from .styles import DEFAULT_STYLE, available as available_styles, path as style_path  # noqa: F401


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()  # also expands PDF ligatures (ﬁ → fi)
    t = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", t)
    t = re.sub(r"[\u2018\u2019\u201c\u201d\"'`]", "", t)
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", t)  # hyphenation at line breaks
    return re.sub(r"\s+", " ", t).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """The quote must occur verbatim in the fragment.

    Only typography is normalized (whitespace, line-break hyphenation, quote marks,
    dashes, ligatures); an ellipsis may skip text between parts that occur in order.
    Deliberately no fuzzy matching: a changed number or a dropped "not" must fail.
    """
    parts = [p.strip() for p in re.split(r"\.\.\.|…|\[\.\.\.\]", _norm(quote)) if p.strip()]
    if not parts or sum(len(p) for p in parts) < 12:
        return False
    hay, pos = _norm(text), 0
    for p in parts:
        i = hay.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def check_evidence(items, chunks_by_id):
    """Split model-proposed evidence into grounded and rejected items."""
    ok, rejected = [], []
    for e in items:
        chunk = chunks_by_id.get(e.get("chunk_id"))
        if chunk is None:
            rejected.append(dict(e, reason="фрагмента нет в базе проекта"))
        elif not quote_in_text(e.get("quote", ""), chunk["text"]):
            rejected.append(dict(e, reason="цитата не найдена во фрагменте"))
        else:
            ok.append(dict(e, source_id=chunk["source_id"], page=chunk.get("page"), section=chunk.get("section")))
    return ok, rejected


# ---------------------------------------------------------------------------
# Bibliography (FR-3.10)
# ---------------------------------------------------------------------------

def _csl_items(sources):
    items = []
    for s in sources:
        csl = dict(s.get("csl") or {})
        if not csl:
            csl = {"type": "article-journal", "title": s.get("title") or s["id"]}
        csl["id"] = s["id"]
        csl.pop("PMCID", None)
        # citeproc-py joins month/day without delimiters ("2023Mar4"); journal styles only need the year
        parts = (csl.get("issued") or {}).get("date-parts")
        if parts and parts[0]:
            csl["issued"] = {"date-parts": [[parts[0][0]]]}
        items.append(csl)
    return items


def render(sources, cited_order, style=DEFAULT_STYLE, fmt="plain"):
    """In-text markers and the reference list.

    ``cited_order`` — source ids in order of first citation. Numeric styles are
    numbered in that order; author–date styles are sorted by the style itself.
    """
    csl_file = style_path(style)
    by_id = {s["id"]: s for s in sources}
    order = [sid for sid in cited_order if sid in by_id]
    if not order:
        return {"style": style, "markers": {}, "entries": []}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        csl_style = CitationStylesStyle(str(csl_file), validate=False)
        bib = CitationStylesBibliography(csl_style, CiteProcJSON(_csl_items([by_id[s] for s in order])),
                                         formatter.html if fmt == "html" else formatter.plain)
        cits = {}
        for sid in order:
            c = Citation([CitationItem(sid)])
            bib.register(c)
            cits[sid] = c
        bib.sort()
        markers = {sid: str(bib.cite(c, lambda item: None)) for sid, c in cits.items()}
        canon = {sid.lower(): sid for sid in order}  # citeproc lower-cases keys
        entries = [{"source_id": canon.get(str(key).lower(), str(key)),
                    "text": re.sub(r"^(\[?\d+[.\]])(?=\S)", r"\1 ", str(entry))}
                   for key, entry in zip(bib.keys, bib.bibliography())]
    return {"style": style, "markers": markers, "entries": entries}


def render_clusters(sources, clusters, style=DEFAULT_STYLE):
    """Markers for citation clusters in order of appearance and the matching reference list.

    ``clusters`` — list of lists of source ids, in the order they appear in the manuscript.
    """
    by_id = {s["id"]: s for s in sources}
    clusters = [[sid for sid in c if sid in by_id] for c in clusters]
    order = []
    for c in clusters:
        for sid in c:
            if sid not in order:
                order.append(sid)
    if not order:
        return {"markers": [""] * len(clusters), "entries": []}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        csl_style = CitationStylesStyle(str(style_path(style)), validate=False)
        bib = CitationStylesBibliography(csl_style, CiteProcJSON(_csl_items([by_id[s] for s in order])),
                                         formatter.plain)
        cits = []
        for c in clusters:
            cit = Citation([CitationItem(sid) for sid in c]) if c else None
            if cit:
                bib.register(cit)
            cits.append(cit)
        bib.sort()
        markers = [str(bib.cite(c, lambda item: None)) if c else "" for c in cits]
        canon = {sid.lower(): sid for sid in order}
        entries = [{"source_id": canon.get(str(key).lower(), str(key)),
                    "text": re.sub(r"^(\[?\d+[.\]])(?=\S)", r"\1 ", str(entry))}
                   for key, entry in zip(bib.keys, bib.bibliography())]
    return {"markers": markers, "entries": entries}
