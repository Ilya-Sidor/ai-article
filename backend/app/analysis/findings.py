"""Finding cards (PRD 2.3) built deterministically from sandbox results.

Statements, limitations and suggested wording are templated from the computed
numbers; wording strength follows the evidence level (PRD 2.2).
"""
import inspect
import math

from . import sandbox_lib
from .guardrails import evidence_level

EVIDENCE_ORDER = {"significant": 0, "exploratory": 1, "descriptive": 2}


def fmt(x, nd=None):
    if x is None:
        return "—"
    x = float(x)
    if nd is None:
        ax = abs(x)
        if 0 < ax < 0.0001:
            return "<0.0001" if x > 0 else "-<0.0001"
        if 0 < ax < 0.1:
            return f"{x:.2g}"  # 2 significant digits: 0.0071, 0.00041
        nd = 0 if ax >= 100 else 1 if ax >= 10 else 2
    return f"{x:.{nd}f}"


def fmt_p(p):
    if p is None:
        return "—"
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def p_eq(label, p):
    """'p = 0.012' or 'p < 0.001'."""
    v = fmt_p(p)
    return f"{label} {v}" if v.startswith("<") else f"{label} = {v}"


def fmt_effect(e):
    if not e:
        return ""
    return f"{e['name']} {fmt(e['value'])} (95% ДИ {fmt(e['ci_low'])}–{fmt(e['ci_high'])})"


def fmt_effect_en(e):
    if not e:
        return ""
    name = e["name"].replace("ρ", "rho")
    return f"{name} {fmt(e['value'])}, 95% CI {fmt(e['ci_low'])}–{fmt(e['ci_high'])}"


def _ordinal_label(code, levels):
    if code is None or not levels:
        return fmt(code)
    lo, hi = math.floor(code), math.ceil(code)
    lo, hi = max(0, min(lo, len(levels) - 1)), max(0, min(hi, len(levels) - 1))
    return levels[lo] if lo == hi else f"{levels[lo]}–{levels[hi]}"


def _group_value(g, vspec):
    if vspec.get("vtype") == "ordinal":
        return _ordinal_label(g["median"], vspec.get("levels"))
    return f"{fmt(g['median'])} [IQR {fmt(g['q1'])}–{fmt(g['q3'])}]"


def code_snippet(test_spec):
    args = ", ".join(f"{k}={v!r}" for k, v in test_spec["args"].items())
    call = f"result = {test_spec['kind']}(df, {args})"
    source = inspect.getsource(sandbox_lib.TESTS[test_spec["kind"]])
    return f"# Вызов (analysis.py, {test_spec['id']}):\n{call}\n\n# Реализация:\n{source}"


def _statement(r, variables):
    a, b, n = r["a"], r["b"], r["n"]
    if r["kind"] == "cat_cat":
        la, lb, t = r["levels_a"], r["levels_b"], r["table"]
        if len(la) == 2 and len(lb) == 2:
            rows = [sum(row) for row in t]
            pct = r.get("row_percent") or [None, None]
            return (f"«{b}» = {lb[1]}: {t[1][1]} из {rows[1]} ({fmt(pct[1], 0)}%) при «{a}» = {la[1]} "
                    f"против {t[0][1]} из {rows[0]} ({fmt(pct[0], 0)}%) при «{a}» = {la[0]} (n = {n}).")
        return f"Распределение «{b}» различается между категориями «{a}» (n = {n}); см. таблицу сопряжённости."
    if r["kind"] == "group_numeric":
        vspec = variables.get(b, {})
        parts = [f"{g['level']}: {_group_value(g, vspec)} (n = {g['n']})" for g in r["groups"]]
        return f"«{b}» по группам «{a}» — " + "; ".join(parts) + "."
    rho = r["effect"]["value"]
    direction = "положительная" if rho > 0 else "отрицательная"
    return f"«{a}» и «{b}»: {direction} ранговая корреляция, ρ = {fmt(rho)} (n = {n})."


def _title(r, variables):
    a, b = r["a"], r["b"]
    if r["kind"] == "cat_cat" and r.get("effect", {}).get("name") == "OR":
        ga, gb = variables.get(a, {}).get("group"), variables.get(b, {}).get("group")
        if ga == gb == "molecular":
            return (f"Взаимоисключение: {a} / {b}" if r["effect"]["value"] < 1 else f"Ко-встречаемость: {a} / {b}")
    if r["kind"] == "spearman":
        return f"Корреляция: {a} × {b}"
    return f"Ассоциация: {a} × {b}"


def _limitations(r, tier, n_total, all_tests):
    lim = []
    n = r["n"]
    if tier.code == "minimal":
        lim.append("n < 10 — только разведочный результат (guardrail)")
    elif n < 30:
        lim.append(f"малое n ({n})")
    if n < n_total:
        lim.append(f"пропуски: анализ по {n} из {n_total} случаев")
    e = r.get("effect") or {}
    lo, hi = e.get("ci_low"), e.get("ci_high")
    if e.get("name") == "OR" and lo is not None and hi is not None:
        if lo <= 1 <= hi:
            lim.append("95% ДИ для OR включает 1")
        if lo > 0 and hi / lo > 10:
            lim.append("широкий ДИ")
    elif lo is not None and hi is not None and e.get("name") != "Cramér's V" and not e.get("name", "").startswith("eps"):
        if lo <= 0 <= hi:
            lim.append("95% ДИ включает 0")
    p, q = r.get("p"), r.get("q")
    if p is not None and p < 0.05 and (q is None or q >= 0.05):
        lim.append(f"не значимо после поправки на множественные сравнения ({p_eq('q', q)})")
    # possible confounding: a third variable clearly associated with both a and b
    sig = {}
    for t in all_tests:
        if t.get("status") == "ok" and t.get("q") is not None and t["q"] < 0.1:
            sig.setdefault(t["a"], set()).add(t["b"])
            sig.setdefault(t["b"], set()).add(t["a"])
    shared = sorted((sig.get(r["a"], set()) & sig.get(r["b"], set())) - {r["a"], r["b"]})
    for c in shared[:2]:
        lim.append(f"возможный вклад «{c}» (связан с обоими признаками)")
    return lim


def _wording_en(r, level):
    a, b = r["a"], r["b"]
    eff = fmt_effect_en(r.get("effect"))
    p, q = p_eq("p", r.get("p")), p_eq("q", r.get("q"))
    if r.get("p") is not None and r["p"] >= 0.05:
        return f"No association between {a} and {b} was detected in this series ({eff}; {p})."
    if level == "significant":
        return f"{b} was associated with {a} ({eff}; {p}, FDR-adjusted {q})."
    return (f"In this series, an association between {a} and {b} was observed ({eff}; {p}, {q}). "
            f"Given the exploratory nature of this analysis, this finding warrants validation in an independent cohort.")


def build_findings(results, spec, tier, n_total, decisions, origins):
    variables = spec["variables"]
    tests_by_id = {t["id"]: t for t in spec["tests"]}
    all_tests = results["tests"]
    findings = []
    for r in all_tests:
        if r.get("status") != "ok":
            continue
        origin = origins.get(r["spec_key"], "auto")
        if r["p"] >= 0.05 and origin != "user":
            continue
        level = evidence_level(r, tier)
        findings.append({
            "key": r["spec_key"], "kind": r["kind"], "origin": origin, "evidence": level,
            "null_result": r["p"] >= 0.05, "title": _title(r, variables),
            "statement": ("Ассоциация не выявлена. " if r["p"] >= 0.05 else "") + _statement(r, variables),
            "statistics": f"{r['test']}: {p_eq('p', r['p'])}; {p_eq('q (FDR)', r.get('q'))}; {fmt_effect(r['effect'])}",
            "limitations": _limitations(r, tier, n_total, all_tests),
            "wording_en": _wording_en(r, level),
            "figure": {"cat_cat": "bar", "group_numeric": "box", "spearman": "scatter"}[r["kind"]],
            "code": code_snippet(tests_by_id[r["id"]]), "test_id": r["id"], "result": r,
            "explanation": {
                "data": f"«{r['a']}» и «{r['b']}», {r['n']} случаев с полными данными",
                "method": r["test"],
                "why": _why(r, variables),
            },
        })

    for o in results.get("outliers", []):
        cases = ", ".join(f"{c['case_id']} ({fmt(c['value'])})" for c in o["cases"])
        findings.append(_descriptive(
            f"outlier|{o['variable']}", f"Выбросы: {o['variable']}",
            f"«{o['variable']}»: значения за пределами 1.5×IQR у случаев {cases}; медиана {fmt(o['median'])} "
            f"[IQR {fmt(o['q1'])}–{fmt(o['q3'])}], n = {o['n']}.",
            ["проверьте корректность значений в исходных данных", "описательное наблюдение"], "box_single",
            {"variable": o["variable"]}))
    for a in results.get("atypical", []):
        dev = "; ".join(f"{d['variable']}: {d['value']} (в серии преобладает {d['typical']})" for d in a["deviations"])
        findings.append(_descriptive(
            f"atypical|{a['case_id']}", f"Нетипичный профиль: {a['case_id']}",
            f"Случай {a['case_id']} отличается от преобладающего профиля серии: {dev}.",
            ["описательное наблюдение", "рассмотрите альтернативный диагноз / технический артефакт окраски"], None,
            {"case_id": a["case_id"]}))
    shared = results.get("shared")
    if shared:
        if shared["shared"]:
            txt = "; ".join(f"{s['variable']}: {s['value']} (n = {s['n']})" for s in shared["shared"])
            findings.append(_descriptive("shared", "Общие признаки всех случаев", f"У всех случаев совпадают: {txt}.",
                                         ["описательное сравнение случаев"], None, {}))
        if shared["discordant"]:
            txt = "; ".join(f"{s['variable']}: {', '.join(s['values'])}" for s in shared["discordant"])
            findings.append(_descriptive("discordant", "Различия между случаями", f"Случаи различаются по: {txt}.",
                                         ["описательное сравнение случаев"], None, {}))

    findings.sort(key=lambda f: (EVIDENCE_ORDER[f["evidence"]], f["result"].get("p", 1) if f.get("result") else 1))
    for i, f in enumerate(findings, 1):
        f["id"] = "F-%02d" % i
        dec = decisions.get(f["key"], {})
        f["status"] = dec.get("status", "proposed")
        f["comments"] = dec.get("comments", [])
        f["interpretation"] = dec.get("interpretation")
    return findings


def _why(r, variables):
    ta, tb = variables[r["a"]]["vtype"], variables[r["b"]]["vtype"]
    if r["kind"] == "cat_cat":
        if r["test"].startswith("Pearson"):
            return f"оба признака категориальные ({ta}, {tb}), n ≥ 30 и все ожидаемые частоты ≥ 5 → χ²"
        return f"оба признака категориальные ({ta}, {tb}); точный тест корректен при малых частотах"
    if r["kind"] == "group_numeric":
        k = len(r["groups"])
        return (f"сравнение {'двух' if k == 2 else str(k)} групп по {tb}-признаку без предположения о нормальности "
                f"→ {'Mann–Whitney' if k == 2 else 'Kruskal–Wallis'}")
    return f"оба признака упорядоченные ({ta}, {tb}) → ранговая корреляция Спирмена"


def _descriptive(key, title, statement, limitations, figure, extra):
    return {"key": key, "kind": "descriptive", "origin": "auto", "evidence": "descriptive", "null_result": False,
            "title": title, "statement": statement, "statistics": "описательная статистика, тесты не применялись",
            "limitations": limitations, "wording_en": None, "figure": figure, "figure_args": extra, "code": None,
            "test_id": None, "result": None, "explanation": None}
