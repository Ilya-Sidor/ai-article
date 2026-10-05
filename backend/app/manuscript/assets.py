"""Tables (FR-5.6) and figures (FR-5.7) generated from analysis results.

Cell values come straight from the analysis engine; the author edits captions
and decides what to include.
"""
from ..analysis.findings import fmt, fmt_p
from . import lang as L
from .facts import _en, _en_level


def _names(terms, lang):
    """Variable and level labels: the data's own (Russian) names in a Russian manuscript, English terms otherwise."""
    if lang == "ru":
        return (lambda name: name), (lambda name, level: level)
    return (lambda name: _en(terms, name)), (lambda name, level: _en_level(terms, name, level))


def sync(state, analysis):
    """Add slots for Table 1, accepted findings' tables/figures and overview figures (keeps author choices)."""
    fmap = state.get("ids", {}).get("findings", {})
    tables = {t["id"]: t for t in state.get("tables", [])}
    figures = {f["id"]: f for f in state.get("figures", [])}
    if analysis.get("case_report"):  # a case report: one table with the features of the case, no plots
        tables.setdefault("t1", {"id": "t1", "kind": "case_data", "include": True, "caption": None})
        state["tables"], state["figures"] = list(tables.values()), list(figures.values())
        return state
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


def default_caption(item, analysis, terms, kind, lang="en", state=None):
    ru = lang == "ru"
    if item["kind"] == "micro":
        from .micro import caption
        return caption(state or {}, item, lang, english=False)
    if item["kind"] == "table1":
        return "Клинико-морфологическая характеристика серии" if ru else "Clinicopathological characteristics of the series"
    if item["kind"] == "case_data":
        n = (analysis.get("summary") or {}).get("n", 1)
        if ru:
            return "Клинические, морфологические, иммуногистохимические и молекулярные данные " + (
                "наблюдения" if n == 1 else "наблюдений")
        return "Clinical, morphological, immunohistochemical and molecular features of the " + (
            "case" if n == 1 else "cases")
    if item["kind"] == "heatmap":
        return ("Иерархическая кластеризация случаев без учителя (расстояние Гауэра, средняя связь). Столбцы — "
                "признаки от низких (светлые) до высоких (тёмные) значений; серый — нет данных." if ru else
                "Unsupervised hierarchical clustering of the cases (Gower distance, average linkage). "
                "Columns show variables scaled from low (light) to high (dark); grey — missing.")
    if item["kind"] == "oncoprint":
        return ("Иммуногистохимический и молекулярный профиль отдельных случаев (тёмные — положительный "
                "результат/мутация выявлена)." if ru else
                "Immunohistochemical and molecular profile of individual cases (dark — positive/detected).")
    f = _finding(analysis, item.get("key"))
    if not f or not f.get("result"):
        return "[уточнить: подпись]"
    r = f["result"]
    name, _ = _names(terms, lang)
    a, b = name(r["a"]), name(r["b"])
    if ru:
        if kind == "table":
            return f"{b} в зависимости от признака «{a}»"
        return {"bar": f"Распределение признака «{b}» в зависимости от признака «{a}»; числа в столбцах — число случаев.",
                "box": f"{b} в зависимости от признака «{a}». Прямоугольники — медиана и межквартильный интервал; "
                       f"точки — отдельные случаи.",
                "scatter": f"Корреляция признаков «{a}» и «{b}»; каждая точка — один случай."}.get(
            f.get("figure"), f"{a} и {b}")
    if kind == "table":
        return f"{b} according to {a}"
    return {"bar": f"Distribution of {b} according to {a}; numbers in bars are case counts.",
            "box": f"{b} according to {a}. Boxes show the median and interquartile range; dots are individual cases.",
            "scatter": f"Correlation between {a} and {b}; each dot is one case."}.get(f.get("figure"), f"{a} and {b}")


def table_data(item, analysis, terms, lang="en"):
    """Cells, headers and footnote in the manuscript language; ``caption_en`` for bilingual journals."""
    ru = lang == "ru"
    num = lambda x, nd=None: L.number(fmt(x, nd), lang)  # noqa: E731
    nump = lambda p: L.number(fmt_p(p), lang)  # noqa: E731
    name, level = _names(terms, lang)
    n = (analysis.get("summary") or {}).get("n")
    caption = item.get("caption") or default_caption(item, analysis, terms, "table", lang)
    caption_en = item.get("caption_en") or default_caption(item, analysis, terms, "table", "en")
    if item["kind"] == "case_data":
        from .case import table
        cols, rows = table(analysis, terms, lang)
        rows = [[L.number(c, lang) for c in r] for r in rows]
        return {"caption": caption, "caption_en": caption_en, "columns": cols, "rows": rows,
                "footnote": "Данные обезличены." if ru else "Data are de-identified."}
    if item["kind"] == "table1":
        rows = []
        for v in analysis.get("table1", []):
            label = name(v["variable"])
            if v["vtype"] == "quantitative":
                if v.get("median") is None:
                    continue
                rows.append([f"{label}, {'медиана (МКИ); размах' if ru else 'median (IQR); range'}",
                             f"{num(v['median'])} ({num(v['q1'])}–{num(v['q3'])}); {num(v['min'])}–{num(v['max'])}"])
            else:
                rows.append([label, ""])
                for c in v.get("counts") or []:
                    rows.append([f"    {level(v['variable'], c['level'])}",
                                 f"{c['count']} ({num(c['percent'], 0) if c.get('percent') is not None else '—'}%)"])
            if v.get("missing"):
                rows.append([f"    {'нет данных' if ru else 'missing'}", str(v["missing"])])
        if ru:
            return {"caption": caption, "caption_en": caption_en, "rows": rows,
                    "columns": ["Признак", f"Все случаи (n = {n})"],
                    "footnote": "МКИ — межквартильный интервал. Проценты рассчитаны от числа случаев с известными данными."}
        return {"caption": caption, "caption_en": caption_en, "columns": ["Characteristic", f"All cases (n = {n})"],
                "rows": rows,
                "footnote": "IQR, interquartile range. Percentages are calculated among cases with available data."}
    f = _finding(analysis, item["key"])
    r = f["result"]
    a, b = name(r["a"]), name(r["b"])
    e = r.get("effect") or {}
    p_txt = nump(r["p"]) if nump(r["p"]).startswith("<") else "= " + nump(r["p"])
    if ru:
        foot = (f"{L.test_name(r['test'], lang)}; p {p_txt}; q (поправка FDR) = {nump(r.get('q'))}; "
                f"{L.fact(e.get('name', ''), lang)} {num(e.get('value'))} "
                f"(95% ДИ {num(e.get('ci_low'))}–{num(e.get('ci_high'))}). "
                f"Уровень доказательности: {L.EVIDENCE_RU.get(f['evidence'], f['evidence'])}.")
    else:
        foot = (f"{r['test']}; p {p_txt}; FDR-adjusted q = {nump(r.get('q'))}; {e.get('name', '')} "
                f"{num(e.get('value'))} (95% CI {num(e.get('ci_low'))}–{num(e.get('ci_high'))}). "
                f"Evidence level: {f['evidence']}.")
    if r["kind"] == "cat_cat":
        lb = [level(r["b"], x) for x in r["levels_b"]]
        rows = []
        for i, row in enumerate(r["table"]):
            tot = sum(row)
            rows.append([level(r["a"], r["levels_a"][i]), str(tot)]
                        + [f"{c} ({num(100 * c / tot, 0) if tot else '—'}%)" for c in row])
        return {"caption": caption, "caption_en": caption_en, "columns": [a, "n"] + [f"{b}: {x}" for x in lb],
                "rows": rows, "footnote": foot}
    rows = [[level(r["a"], g["level"]), str(g["n"]), f"{num(g['median'])} ({num(g['q1'])}–{num(g['q3'])})",
             f"{num(g['min'])}–{num(g['max'])}"] for g in r["groups"]]
    cols = [a, "n", f"{b}, медиана (МКИ)", "Размах"] if ru else [a, "n", f"{b}, median (IQR)", "Range"]
    return {"caption": caption, "caption_en": caption_en, "columns": cols, "rows": rows, "footnote": foot}


def figure_data(item, analysis, terms, lang="en", state=None):
    if item["kind"] == "micro":  # a figure of micrographs: the legend comes from its panels
        from .micro import caption
        return {"id": item["id"], "kind": "micro", "finding": None, "item": item,
                "caption": item.get("caption") or caption(state or {}, item, lang),
                "caption_en": item.get("caption_en") or caption(state or {}, item, lang, english=True)}
    f = _finding(analysis, item.get("key")) if item["kind"] == "finding" else None
    return {"id": item["id"], "kind": item["kind"], "finding": f,
            "caption": item.get("caption") or default_caption(item, analysis, terms, "figure", lang),
            "caption_en": item.get("caption_en") or default_caption(item, analysis, terms, "figure", "en")}
