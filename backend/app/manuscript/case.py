"""Case report mode (FR-1.10): one patient (up to a few), no statistics, structure per the CARE guidelines.

CARE — CAse REport guidelines (Gagnier JJ et al., 2013; checklist update Riley DS et al., J Clin Epidemiol
2017): title with "case report", 2–5 keywords, abstract, introduction, patient information, clinical findings,
timeline, diagnostic assessment, therapeutic interventions, follow-up and outcomes, discussion (strengths and
limitations, literature, rationale, take-away lessons), patient perspective, informed consent.

The case table plays the part of the analysis: every value of the case becomes a fact placeholder
({{V3.c1}} — variable V3 of case 1), so the text never contains a number the data does not give; the
anonymised documents of the case (reports, discharge summaries) are given to the model as its source.
"""
import re

from ..storage import read_json

MAX_CASES = 5  # CARE covers a single patient or a small series of up to a few patients
GROUP_LABELS = {
    "en": {"clinical": "Clinical data", "morphology": "Morphology", "ihc": "Immunohistochemistry",
           "molecular": "Molecular findings", "followup": "Treatment and follow-up", "other": "Other"},
    "ru": {"clinical": "Клинические данные", "morphology": "Морфология", "ihc": "Иммуногистохимия",
           "molecular": "Молекулярные исследования", "followup": "Лечение и наблюдение", "other": "Прочее"},
}
GROUP_ORDER = ["clinical", "morphology", "ihc", "molecular", "followup", "other"]

# CARE items checked in the manuscript: (key, label, where, patterns in en/ru)
CARE_ITEMS = [
    ("patient_information", "сведения о пациенте (CARE 5)", "case",
     r"patient information|patient history|history|анамнез|сведения о пациент|данные пациент"),
    ("clinical_findings", "клинические данные (CARE 6)", "case", r"clinical findings|examination|клиническ\w* (данн|картин|находк)|осмотр"),
    ("timeline", "хронология (CARE 7)", "case", r"timeline|chronolog|хронолог|временн\w* шкал|динамик"),
    ("diagnostic", "диагностика (CARE 8)", "case", r"diagnos|диагност|дифференциальн"),
    ("therapeutic", "лечение (CARE 9)", "case", r"therap|treatment|intervention|surgery|лечени|операци|терапи"),
    ("followup", "наблюдение и исход (CARE 10)", "case", r"follow-up|follow up|outcome|наблюдени|исход|катамнез"),
    ("strengths", "сильные стороны и ограничения в обсуждении (CARE 11)", "discussion",
     r"limitation|strength|ограничени|сильн\w* сторон"),
]


def is_case_report(project):
    return (project or {}).get("article_type") == "case_report"


def case_data(store, pid):
    """The case table in the shape the manuscript module expects from an analysis (or {} without data)."""
    df = store.dataset(pid)
    if df is None or not len(df):
        return {}
    dictionary = read_json(store.dir(pid) / "dictionary.json", [])
    df = df.head(MAX_CASES).fillna("")
    variables, documents = [], [[] for _ in range(len(df))]
    for v in dictionary:
        if v["name"] not in df.columns or v.get("vtype") == "identifier":
            continue
        values = [str(x).strip() for x in df[v["name"]]]
        if v.get("vtype") == "text":
            for i, x in enumerate(values):
                if x:
                    documents[i].append(f"{v['name']}:\n{x}")
            continue
        if any(values):
            variables.append({"name": v["name"], "group": v.get("group") or "other", "vtype": v.get("vtype"),
                              "unit": v.get("unit"), "values": values})
    return {"summary": {"n": int(len(df))}, "case_report": {
        "case_ids": list(df["case_id"]), "variables": variables,
        "documents": ["\n\n".join(d) for d in documents], "n_total": int(len(store.dataset(pid)))}}


def assign_ids(ids, data):
    vmap = ids.setdefault("variables", {})
    for v in (data.get("case_report") or {}).get("variables", []):
        if v["name"] not in vmap:
            vmap[v["name"]] = "V%d" % (len(vmap) + 1)
    return ids


def _labels(terms, lang):
    if lang == "ru":
        return (lambda n: n), (lambda n, x: x)
    from .facts import _en, _en_level
    return (lambda n: _en(terms, n)), (lambda n, x: _en_level(terms, n, x))


def build(data, ids, terms, lang="en"):
    """Facts {{V3.c1}} for every value of every case."""
    cr = data.get("case_report") or {}
    name, level = _labels(terms, lang)
    facts, variables = {}, []
    n = len(cr.get("case_ids", []))
    if n:
        facts["n"] = {"value": str(n), "desc": "number of patients described", "kind": "number",
                      "ref": {"type": "summary"}}
    vmap = ids.get("variables", {})
    for v in cr.get("variables", []):
        vid = vmap.get(v["name"])
        if not vid:
            continue
        label = name(v["name"])
        variables.append({"id": vid, "name": v["name"], "label": label, "vtype": v["vtype"]})
        for i, x in enumerate(v["values"], 1):
            if not x:
                continue
            numeric = re.fullmatch(r"-?\d+(?:[.,]\d+)?", x) is not None
            value = x.replace(",", ".") if numeric else level(v["name"], x)
            facts[f"{vid}.c{i}"] = {"value": value, "kind": "number" if numeric else "text",
                                    "desc": f"case {i}: {label}" + (f", {v['unit']}" if v.get("unit") else ""),
                                    "ref": {"type": "case", "variable": v["name"], "case": i}}
    return {"facts": facts, "findings": [], "variables": variables, "analysis": data}


def terms_needed(data):
    out = []
    for v in (data.get("case_report") or {}).get("variables", []):
        levels = sorted({x for x in v["values"] if x and not re.fullmatch(r"-?\d+(?:[.,]\d+)?", x)})
        out.append({"name": v["name"], "group": v["group"], "vtype": v["vtype"], "unit": v.get("unit"),
                    "levels": levels})
    return out


def table(data, terms, lang="en"):
    """Table 1 of a case report: the features of the case(s), grouped as the CARE sections."""
    cr = data["case_report"]
    name, level = _labels(terms, lang)
    ru = lang == "ru"
    n = len(cr["case_ids"])
    rows = []
    for g in GROUP_ORDER:
        vs = [v for v in cr["variables"] if (v["group"] if v["group"] in GROUP_ORDER else "other") == g]
        if not vs:
            continue
        rows.append([GROUP_LABELS["ru" if ru else "en"][g]] + [""] * n)
        for v in vs:
            label = name(v["name"]) + (f", {v['unit']}" if v.get("unit") and v["unit"] not in name(v["name"]) else "")
            rows.append([f"    {label}"] + [level(v["name"], x) if x else "—" for x in v["values"]])
    if n == 1:
        cols = ["Признак", "Значение"] if ru else ["Feature", "Value"]
    else:
        cols = ["Признак"] + [f"Случай {i}" for i in range(1, n + 1)] if ru else \
            ["Feature"] + [f"Case {i}" for i in range(1, n + 1)]
    return cols, rows


def care_issues(sections, renderer, front, inputs, template):
    """CARE checklist items that can be checked in the text (warnings) and the consent (blocking)."""
    issues = []

    def add(sev, key, message, suggestion=""):
        issues.append({"section": None, "heading": "CARE (case report)", "severity": sev, "code": "care_" + key,
                       "message": message, "fragment": "", "suggestion": suggestion})

    by_kind = {}
    for s in sections:
        by_kind.setdefault(s["kind"], []).append(renderer.text(s["source"], s["kind"]) if s["source"] else "")
    case_text = " ".join(by_kind.get("case_presentation", []) + by_kind.get("results", []) + by_kind.get("methods", []))
    disc = " ".join(by_kind.get("discussion", []))
    if case_text.strip():
        for key, label, where, rx in CARE_ITEMS:
            text = disc if where == "discussion" else case_text
            if where == "discussion" and not disc.strip():
                continue
            if not re.search(rx, text, re.I):
                add("warning", key, f"не найдено: {label}", "добавьте подраздел или абзац")
    title = (front or {}).get("title") or ""
    if title and not re.search(r"case report|case series|клиническ\w* (наблюдени|случа)|случа\w* из практики|описани\w* случа",
                               title, re.I):
        add("warning", "title", "в названии нет слов «case report» / «клиническое наблюдение» (CARE 1)")
    kw = (front or {}).get("keywords") or []
    if kw and not 2 <= len(kw) <= 5:
        add("warning", "keywords", f"ключевых слов {len(kw)} — CARE рекомендует 2–5, включая «case report» (CARE 2)")
    consent = (inputs or {}).get("consent") or {}
    if consent.get("status") == "waived" and (consent.get("details") or "").strip():
        add("warning", "consent", "согласие пациента не получено (обоснование указано) — проверьте, принимает ли журнал "
                                  "case report без письменного согласия (CARE 13)")
    elif consent.get("status") != "written":
        add("blocking", "consent", "для case report нужно письменное информированное согласие пациента на публикацию "
                                   "(CARE 13) или обоснование его отсутствия", "«2. Данные автора» → согласие")
    return issues
