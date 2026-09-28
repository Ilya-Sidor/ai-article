"""Tables (FR-5.6) and figures (FR-5.7) generated from analysis results.

Cell values come straight from the analysis engine; the author edits captions
and decides what to include.
"""
from ..analysis.findings import fmt, fmt_p
from .facts import _en, _en_level


def sync(state, analysis):
    """Add slots for Table 1, accepted findings' tables/figures and overview figures (keeps author choices)."""
    fmap = state.get("ids", {}).get("findings", {})
    tables = {t["id"]: t for t in state.get("tables", [])}
    figures = {f["id"]: f for f in state.get("figures", [])}
    if analysis.get("table1"):
        tables.setdefault("t1", {"id": "t1", "kind": "table1", "include": True, "caption": None})
    for f in analysis.get("findings", []):
        aid = fmap.get(f["key"])
        if not aid or f["status"] != "accepted" or not f.get("result"):
            continue
        if f["kind"] in ("cat_cat", "group_numeric"):
            tables.setdefault(f"t_{aid}", {"id": f"t_{aid}", "kind": "finding", "key": f["key"], "include": False,
                                           "caption": None})
        if f.get("figure") in ("bar", "box", "scatter"):
            figures.setdefault(f"f_{aid}", {"id": f"f_{aid}", "kind": "finding", "key": f["key"], "include": True,
                                            "caption": None})
    if (analysis.get("clustering") or {}).get("status") == "ok":
        figures.setdefault("f_heatmap", {"id": "f_heatmap", "kind": "heatmap", "include": False, "caption": None})
    figures.setdefault("f_oncoprint", {"id": "f_oncoprint", "kind": "oncoprint", "include": False, "caption": None})
    state["tables"] = list(tables.values())
    state["figures"] = list(figures.values())
    return state


def _finding(analysis, key):
    return next((f for f in analysis.get("findings", []) if f["key"] == key), None)


def default_caption(item, analysis, terms, kind):
    if item["kind"] == "table1":
        return "Clinicopathological characteristics of the series"
    if item["kind"] == "heatmap":
        return ("Unsupervised hierarchical clustering of the cases (Gower distance, average linkage). "
                "Columns show variables scaled from low (light) to high (dark); grey — missing.")
    if item["kind"] == "oncoprint":
        return "Immunohistochemical and molecular profile of individual cases (dark — positive/detected)."
    f = _finding(analysis, item.get("key"))
    if not f or not f.get("result"):
        return "[уточнить: подпись]"
    r = f["result"]
    a, b = _en(terms, r["a"]), _en(terms, r["b"])
    if kind == "table":
        return f"{b} according to {a}"
    return {"bar": f"Distribution of {b} according to {a}; numbers in bars are case counts.",
            "box": f"{b} according to {a}. Boxes show the median and interquartile range; dots are individual cases.",
            "scatter": f"Correlation between {a} and {b}; each dot is one case."}.get(f.get("figure"), f"{a} and {b}")


def table_data(item, analysis, terms):
    n = (analysis.get("summary") or {}).get("n")
    caption = item.get("caption") or default_caption(item, analysis, terms, "table")
    if item["kind"] == "table1":
        rows = []
        for v in analysis.get("table1", []):
            label = _en(terms, v["variable"])
            if v["vtype"] == "quantitative":
                if v.get("median") is None:
                    continue
                rows.append([f"{label}, median (IQR); range",
                             f"{fmt(v['median'])} ({fmt(v['q1'])}–{fmt(v['q3'])}); {fmt(v['min'])}–{fmt(v['max'])}"])
            else:
                rows.append([label, ""])
                for c in v.get("counts") or []:
                    rows.append([f"    {_en_level(terms, v['variable'], c['level'])}",
                                 f"{c['count']} ({fmt(c['percent'], 0) if c.get('percent') is not None else '—'}%)"])
            if v.get("missing"):
                rows.append(["    missing", str(v["missing"])])
        return {"caption": caption, "columns": ["Characteristic", f"All cases (n = {n})"], "rows": rows,
                "footnote": "IQR, interquartile range. Percentages are calculated among cases with available data."}
    f = _finding(analysis, item["key"])
    r = f["result"]
    a, b = _en(terms, r["a"]), _en(terms, r["b"])
    e = r.get("effect") or {}
    foot = (f"{r['test']}; p {fmt_p(r['p']) if fmt_p(r['p']).startswith('<') else '= ' + fmt_p(r['p'])}; "
            f"FDR-adjusted q = {fmt_p(r.get('q'))}; {e.get('name', '')} {fmt(e.get('value'))} "
            f"(95% CI {fmt(e.get('ci_low'))}–{fmt(e.get('ci_high'))}). Evidence level: {f['evidence']}.")
    if r["kind"] == "cat_cat":
        lb = [_en_level(terms, r["b"], x) for x in r["levels_b"]]
        rows = []
        for i, row in enumerate(r["table"]):
            tot = sum(row)
            rows.append([_en_level(terms, r["a"], r["levels_a"][i]), str(tot)]
                        + [f"{c} ({fmt(100 * c / tot, 0) if tot else '—'}%)" for c in row])
        return {"caption": caption, "columns": [a, "n"] + [f"{b}: {x}" for x in lb], "rows": rows, "footnote": foot}
    rows = [[_en_level(terms, r["a"], g["level"]), str(g["n"]), f"{fmt(g['median'])} ({fmt(g['q1'])}–{fmt(g['q3'])})",
             f"{fmt(g['min'])}–{fmt(g['max'])}"] for g in r["groups"]]
    return {"caption": caption, "columns": [a, "n", f"{b}, median (IQR)", "Range"], "rows": rows, "footnote": foot}


def figure_data(item, analysis, terms):
    f = _finding(analysis, item.get("key")) if item["kind"] == "finding" else None
    return {"id": item["id"], "kind": item["kind"], "finding": f,
            "caption": item.get("caption") or default_caption(item, analysis, terms, "figure")}
