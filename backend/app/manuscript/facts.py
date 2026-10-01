"""Fact sheet: the only source of numbers for the manuscript (PRD 1.3, FR-5.4).

The model writes numbers as placeholders such as ``{{A3.p_expr}}`` or
``{{V2.median}}``; the renderer substitutes values computed by the analysis
engine. IDs are stable across analysis runs: findings are keyed by their test
spec key, variables by name.
"""
import re

from ..analysis import engine
from ..analysis.findings import fmt, fmt_p
from ..storage import read_json


def _p_expr(label, p):
    if p is None:
        return None
    v = fmt_p(p)
    return f"{label} {v}" if v.startswith("<") else f"{label} = {v}"


def _en(terms, name):
    return (terms.get(name) or {}).get("en") or name


def _en_level(terms, name, level):
    return ((terms.get(name) or {}).get("levels") or {}).get(level) or level



def assign_ids(ids, analysis):
    """Stable short ids for accepted findings (A1…) and analysed variables (V1…)."""
    fmap, vmap = ids.setdefault("findings", {}), ids.setdefault("variables", {})
    for f in analysis.get("findings", []):
        if f["status"] == "accepted" and f["key"] not in fmap:
            fmap[f["key"]] = "A%d" % (len(fmap) + 1)
    for v in analysis.get("table1", []):
        if v["variable"] not in vmap:
            vmap[v["variable"]] = "V%d" % (len(vmap) + 1)
    return ids


def build(store, pid, ids, terms, lang="en"):
    """{"facts": {id: {value, desc, kind, ref}}, "findings": [...], "variables": [...]}.

    Values are in the engine's English notation (the renderer localises them); descriptions name variables
    the way the manuscript will: English terms, or the data's own Russian names in a Russian manuscript."""
    analysis = engine.latest(store, pid) or {}
    facts = {}
    if lang == "ru":
        name, level = (lambda n: n), (lambda n, lv: lv)
    else:
        name, level = (lambda n: _en(terms, n)), (lambda n, lv: _en_level(terms, n, lv))

    def add(fid, value, desc, kind="number", ref=None):
        if value is None:
            return
        facts[fid] = {"value": str(value), "desc": desc, "kind": kind, "ref": ref}

    summary = analysis.get("summary") or {}
    add("n", summary.get("n"), "number of cases in the series", ref={"type": "summary"})
    hyps = analysis.get("hypotheses", [])
    run = [h for h in hyps if h.get("p") is not None]
    add("tests_n", len(run) if analysis else None, "number of statistical tests performed (hypothesis log)",
        ref={"type": "log"})
    add("tests_sig_fdr", sum(1 for h in run if (h.get("q") or 1) < 0.05) if analysis else None,
        "number of associations significant after Benjamini–Hochberg correction (q < 0.05)", ref={"type": "log"})

    variables = []
    vmap = ids.get("variables", {})
    for v in analysis.get("table1", []):
        vid = vmap.get(v["variable"])
        if not vid:
            continue
        label = name(v["variable"])
        ref = {"type": "table1", "variable": v["variable"]}
        variables.append({"id": vid, "name": v["variable"], "label": label, "vtype": v["vtype"]})
        add(f"{vid}.n", v["n"], f"{label}: cases with data", ref=ref)
        add(f"{vid}.missing", v["missing"], f"{label}: missing values", ref=ref)
        if v["vtype"] == "quantitative" and v.get("median") is not None:
            for k in ("median", "q1", "q3", "min", "max", "mean", "sd"):
                if v.get(k) is not None:
                    add(f"{vid}.{k}", fmt(v[k]), f"{label}: {k}", ref=ref)
        for i, c in enumerate(v.get("counts") or [], 1):
            lvl = level(v["variable"], c["level"])
            add(f"{vid}.L{i}.count", c["count"], f"{label} = {lvl}: number of cases", ref=ref)
            if c.get("percent") is not None:
                add(f"{vid}.L{i}.pct", fmt(c["percent"], 0), f"{label} = {lvl}: percent of cases with data", ref=ref)

    findings = []
    fmap = ids.get("findings", {})
    for f in analysis.get("findings", []):
        if f["status"] != "accepted" or f["key"] not in fmap:
            continue
        aid = fmap[f["key"]]
        ref = {"type": "finding", "key": f["key"], "finding_id": f["id"]}
        findings.append({"id": aid, "key": f["key"], "finding_id": f["id"], "title": f["title"],
                         "evidence": f["evidence"], "kind": f["kind"], "statement": f["statement"],
                         "wording_en": f.get("wording_en"), "null_result": f.get("null_result"),
                         "figure": f.get("figure"), "literature": f.get("literature")})
        r = f.get("result")
        if not r:
            continue
        a, b = name(r["a"]), name(r["b"])
        add(f"{aid}.n", r["n"], f"{aid} ({a} × {b}): cases with complete data", ref=ref)
        add(f"{aid}.p_expr", _p_expr("p", r.get("p")), f"{aid}: renders 'p = …' or 'p < 0.001'", "expr", ref)
        add(f"{aid}.q_expr", _p_expr("q", r.get("q")), f"{aid}: renders 'q = …' (FDR-adjusted)", "expr", ref)
        e = r.get("effect") or {}
        if e:
            ename = {"OR": "OR", "Spearman ρ": "rho", "epsilon²": "epsilon squared", "Cramér's V": "Cramér's V"}.get(
                e["name"], "median difference" if e["name"].startswith("Hodges") else e["name"])
            add(f"{aid}.effect", fmt(e["value"]), f"{aid}: {ename} point estimate", ref=ref)
            add(f"{aid}.ci_low", fmt(e["ci_low"]), f"{aid}: {ename} lower 95% CI", ref=ref)
            add(f"{aid}.ci_high", fmt(e["ci_high"]), f"{aid}: {ename} upper 95% CI", ref=ref)
            add(f"{aid}.effect_expr", f"{ename} {fmt(e['value'])}, 95% CI {fmt(e['ci_low'])}–{fmt(e['ci_high'])}",
                f"{aid}: renders '{ename} x, 95% CI a–b'", "expr", ref)
        if r["kind"] == "cat_cat":
            la = [level(r["a"], x) for x in r["levels_a"]]
            lb = [level(r["b"], x) for x in r["levels_b"]]
            for i, row in enumerate(r["table"]):
                tot = sum(row)
                add(f"{aid}.row{i + 1}.total", tot, f"{aid}: cases with {a} = {la[i]}", ref=ref)
                for j, cell in enumerate(row):
                    add(f"{aid}.row{i + 1}.col{j + 1}", cell, f"{aid}: {a} = {la[i]} and {b} = {lb[j]}", ref=ref)
                    if tot:
                        add(f"{aid}.row{i + 1}.col{j + 1}.pct", fmt(100 * cell / tot, 0),
                            f"{aid}: percent with {b} = {lb[j]} among {a} = {la[i]}", ref=ref)
        elif r["kind"] == "group_numeric":
            for i, g in enumerate(r["groups"], 1):
                lvl = level(r["a"], g["level"])
                add(f"{aid}.g{i}.n", g["n"], f"{aid}: cases with {a} = {lvl}", ref=ref)
                for k in ("median", "q1", "q3", "min", "max"):
                    add(f"{aid}.g{i}.{k}", fmt(g[k]), f"{aid}: {b} {k} in {a} = {lvl}", ref=ref)
        elif r["kind"] == "spearman":
            add(f"{aid}.rho", fmt(r["effect"]["value"]), f"{aid}: Spearman rho", ref=ref)
    return {"facts": facts, "findings": findings, "variables": variables, "analysis": analysis}


def terms_needed(store, pid):
    """Variable names and levels that need English labels."""
    from . import case
    if case.is_case_report(store.get(pid)):
        return case.terms_needed(case.case_data(store, pid))
    analysis = engine.latest(store, pid) or {}
    dictionary = {v["name"]: v for v in read_json(store.dir(pid) / "dictionary.json", [])}
    out = []
    for v in analysis.get("table1", []):
        spec = dictionary.get(v["variable"], {})
        out.append({"name": v["variable"], "group": spec.get("group"), "vtype": v["vtype"], "unit": spec.get("unit"),
                    "levels": [c["level"] for c in v.get("counts") or []]})
    return out


_CYRILLIC = re.compile("[А-Яа-яЁё]")


def untranslated(terms, needed):
    """Entries of ``needed`` whose English label is missing or still Cyrillic; ``levels`` narrowed to those
    values. Values without Cyrillic (KIT+, 0, pT2) need no translation."""
    out = []
    for n in needed:
        t = terms.get(n["name"]) or {}
        en, lv = t.get("en") or "", t.get("levels") or {}
        name_missing = bool(_CYRILLIC.search(n["name"])) and (not en or bool(_CYRILLIC.search(en)))
        levels = [x for x in n["levels"] if _CYRILLIC.search(str(x)) and
                  (not lv.get(x) or _CYRILLIC.search(lv[x]))]
        if name_missing or levels:
            out.append(dict(n, levels=levels, name_missing=name_missing))
    return out


def merge_terms(terms, translated, missing):
    """Fill only what was missing: an author's own English label is never overwritten."""
    wanted = {m["name"]: m for m in missing}
    for t in translated:
        m = wanted.get(t["name"])
        if not m:
            continue
        cur = terms.setdefault(t["name"], {"en": "", "levels": {}, "source": "agent"})
        if m["name_missing"] and t["en"].strip() and not _CYRILLIC.search(t["en"]):
            cur["en"] = t["en"].strip()
        levels = cur.setdefault("levels", {})
        for x in t["levels"]:
            if x["value"] in m["levels"] and x["en"].strip() and not _CYRILLIC.search(x["en"]):
                levels[x["value"]] = x["en"].strip()
    return terms
