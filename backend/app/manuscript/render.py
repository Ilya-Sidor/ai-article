"""Source text → rendered manuscript.

Markup of the source text (what the model and the author edit):

* ``{{A3.p_expr}}`` — a value from the fact sheet (numbers are never typed);
* ``{{TAB:t1}}`` / ``{{FIG:f2}}`` — "Table 1" / "Figure 2", numbered by inclusion;
* ``[[CIT-0003]]`` or ``[[CIT-0003, CIT-0007]]`` — citations (module 3 records);
* ``[уточнить: …]`` — information the author still has to provide;
* ``### Heading`` — subheading; blank line — paragraph break.
"""
import re

from ..literature.citations import render_clusters

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TEX_SYMBOLS = {"geq": "≥", "ge": "≥", "leq": "≤", "le": "≤", "neq": "≠", "ne": "≠", "pm": "±", "times": "×",
                "approx": "≈", "sim": "~", "cdot": "·", "chi": "χ", "alpha": "α", "beta": "β", "kappa": "κ",
                "mu": "μ", "rho": "ρ", "sigma": "σ", "tau": "τ", "lambda": "λ", "Delta": "Δ", "infty": "∞",
                "rightarrow": "→", "to": "→", "%": "%", "_": "_", "&": "&", ",": " ", ";": " ", " ": " "}
_SUPERSCRIPTS = {"2": "²", "3": "³", "-1": "⁻¹"}
LATEX_LEFTOVER = re.compile(r"\\[A-Za-z]+|\$[^$\n]{1,120}\$|`[^`\n]{1,60}`")
_LOOSE_ID = re.compile(r"`\s*\{{0,2}\s*([A-Za-z0-9_.:-]+)\s*\}{0,2}\s*`|(?<!\{)\{\s*([A-Za-z0-9_.:-]+)\s*\}(?!\})")


def normalize_placeholders(text, known):
    """Models sometimes mark fact ids as Markdown code (`V2.median`) or single braces ({V2.median}) instead of
    {{V2.median}}. Ids that exist in the fact list (and table/figure refs) become real placeholders; anything
    else is left alone and shows up in the checks."""
    if not text or ("`" not in text and "{" not in text):
        return text

    def fix(m):
        key = m.group(1) or m.group(2)
        ok = key in known or key.upper().startswith(("TAB:", "FIG:"))
        return "{{" + key + "}}" if ok else m.group(0)
    return _LOOSE_ID.sub(fix, text)


def _tex_math(expr):
    expr = re.sub(r"\\(?:text|mathrm|mathit|textit|operatorname)\{([^{}]*)\}", r"\1", expr)
    expr = re.sub(r"\^\{?(-1|2|3)\}?", lambda m: _SUPERSCRIPTS[m.group(1)], expr)
    expr = re.sub(r"\\([A-Za-z]+|[%_&,; ])", lambda m: _TEX_SYMBOLS.get(m.group(1), m.group(0)), expr)
    return re.sub(r"\s*([≥≤≠±×≈])\s*", r" \1 ", expr).replace("  ", " ")


def plain_text(text):
    """Text as it goes to the reader and to .docx: models sometimes write LaTeX math ("$p \\geq 0.05$"), and a
    decoded JSON "\\b"/"\\f" leaves control characters that Word XML rejects. Common math becomes Unicode;
    what cannot be converted stays visible and is flagged by the checks."""
    if not text:
        return text
    text = _CTRL.sub("", text.replace("\x08", "\\b").replace("\x0c", "\\f"))
    if "$" in text or "\\" in text:
        text = re.sub(r"(?<![\w$])\$([^$\n]{1,120})\$(?![\w$])", lambda m: _tex_math(m.group(1)), text)
        text = re.sub(r"\\(geq?|leq?|neq?|pm|times|approx|chi|alpha|beta|kappa|%)(?![A-Za-z])",
                      lambda m: _TEX_SYMBOLS[m.group(1)], text)
    return text


TOKEN = re.compile(r"\{\{\s*([A-Za-z0-9_.:-]+)\s*\}\}|\[\[\s*(CIT-\d{4}(?:\s*,\s*CIT-\d{4})*)\s*\]\]|"
                   r"\[(?:уточнить|TODO|to confirm)\s*:\s*([^\]]+)\]", re.I)


def numbering(state):
    tables = [t for t in state.get("tables", []) if t.get("include")]
    figures = [f for f in state.get("figures", []) if f.get("include")]
    return ({t["id"]: i for i, t in enumerate(tables, 1)}, {f["id"]: i for i, f in enumerate(figures, 1)})


def collect_clusters(sections):
    """Citation clusters (lists of citation ids) in manuscript order."""
    out = []
    for sec in sections:
        for m in TOKEN.finditer(sec["source"]):
            if m.group(2):
                out.append([c.strip() for c in m.group(2).split(",")])
    return out


class Renderer:
    def __init__(self, facts, state, citations, sources, style, sections):
        self.facts = facts
        self.tab_no, self.fig_no = numbering(state)
        self.cit = {c["id"]: c for c in citations}
        clusters = collect_clusters(sections)
        src_clusters = []
        for cl in clusters:
            ids = []
            for cid in cl:
                c = self.cit.get(cid)
                if c and c["decision"] != "rejected" and c["source_id"] not in ids:
                    ids.append(c["source_id"])
            src_clusters.append(ids)
        r = render_clusters(sources, src_clusters, style) if clusters else {"markers": [], "entries": []}
        self.cluster_markers = {tuple(cl): m for cl, m in zip(clusters, r["markers"])}
        self.bibliography = r["entries"]

    def segments(self, source):
        """Paragraph list; each paragraph is a list of typed segments for the UI and the exporter."""
        paragraphs = []
        source = normalize_placeholders(source, self.facts)
        # a "### " subheading is always its own block, even when the model puts its paragraph on the next line
        source = re.sub(r"^[ \t]*(#{2,4} [^\n]*)$", lambda m: "\n" + "### " + m.group(1).lstrip("#").strip() + "\n",
                        source, flags=re.M)
        for block in re.split(r"\n\s*\n", source.strip()):
            block = block.strip()
            if not block:
                continue
            if block.startswith("### "):
                paragraphs.append({"type": "heading", "segments": [{"t": "text", "v": plain_text(block[4:].strip())}]})
                continue
            segs, pos = [], 0
            for m in TOKEN.finditer(block):
                if m.start() > pos:
                    segs.append({"t": "text", "v": plain_text(block[pos:m.start()])})
                segs.append(self._token(m))
                pos = m.end()
            if pos < len(block):
                segs.append({"t": "text", "v": plain_text(block[pos:])})
            paragraphs.append({"type": "p", "segments": segs})
        return paragraphs

    def _token(self, m):
        raw = m.group(0)
        if m.group(1):
            key = m.group(1)
            if key.upper().startswith("TAB:"):
                n = self.tab_no.get(key[4:])
                return {"t": "ref", "v": f"Table {n}", "raw": raw} if n else {"t": "unknown", "v": raw, "raw": raw}
            if key.upper().startswith("FIG:"):
                n = self.fig_no.get(key[4:])
                return {"t": "ref", "v": f"Figure {n}", "raw": raw} if n else {"t": "unknown", "v": raw, "raw": raw}
            f = self.facts.get(key)
            if f is None:
                return {"t": "unknown", "v": raw, "raw": raw}
            return {"t": "fact", "id": key, "v": f["value"], "desc": f["desc"], "ref": f.get("ref"), "raw": raw}
        if m.group(2):
            ids = [c.strip() for c in m.group(2).split(",")]
            bad = [c for c in ids if c not in self.cit or self.cit[c]["decision"] == "rejected"]
            return {"t": "cite", "ids": ids, "v": self.cluster_markers.get(tuple(ids), "") or raw,
                    "bad": bad, "raw": raw}
        return {"t": "todo", "v": m.group(3).strip(), "raw": raw}

    def text(self, source):
        """Plain rendered text (for word counts, checks and export)."""
        out = []
        for p in self.segments(source):
            s = "".join(seg["v"] if seg["t"] != "todo" else f"[{seg['v']}]" for seg in p["segments"])
            out.append(s if p["type"] == "p" else s.upper())
        return "\n\n".join(out)


def word_count(text):
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9][\w'’\-./%]*", text))
