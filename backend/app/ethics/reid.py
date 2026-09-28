"""Re-identification risk (FR-7.4).

Two checks:
* text — sentences that combine several quasi-identifiers about a patient
  (exact age, sex, date, place/institution, specific variant, occupation);
* data — k-anonymity of the supplementary case table over its
  quasi-identifier columns, with generalisations applied on export.
"""
import re

import pandas as pd

AGE = re.compile(r"\b(\d{1,3})[- ](year|yr|month)s?[- ]old\b|\baged? (of )?\d{1,3}\b|\bat (the )?age (of )?\d{1,3}\b",
                 re.I)
SEX = re.compile(r"\b(male|female|man|woman|boy|girl|gentleman|lady)\b", re.I)
DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                  r"(\s+\d{1,2},?)?\s+(19|20)\d{2}\b|\bin (19|20)\d{2}\b|\b\d{1,2}[./]\d{1,2}[./](19|20)?\d{2}\b")
PLACE = re.compile(r"\b(hospital|clinic|university|institute|centre|center|county|province|village|city of|"
                   r"region)\b", re.I)
VARIANT = re.compile(r"\bp\.[A-Z][a-z]{2}\d+|\bc\.\d+[ACGT_>+\-]|\bp\.\([A-Z]|\b[A-Z][0-9]+[A-Z]\b(?= mutation)")
JOB = re.compile(r"\b(farmer|miner|teacher|pilot|soldier|athlete|nurse|physician|firefighter|worker)\b", re.I)
CATEGORIES = [("age", AGE, "точный возраст"), ("sex", SEX, "пол"), ("date", DATE, "дата/год события"),
              ("place", PLACE, "место/учреждение"), ("variant", VARIANT, "конкретный вариант"),
              ("occupation", JOB, "профессия")]


def text_risks(sections, renderer, n_cases, case_report):
    """sections: [{key, heading, source}] → issues."""
    issues = []
    for sec in sections:
        text = renderer.text(sec["source"])
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text):
            found = [(k, label) for k, rx, label in CATEGORIES if rx.search(sent)]
            if len(found) >= 3 or (len(found) >= 2 and (case_report or n_cases <= 5)):
                sugg = []
                keys = {k for k, _ in found}
                if "age" in keys:
                    sugg.append("возраст диапазоном (например, 50–59 лет) или медианой")
                if "date" in keys:
                    sugg.append("интервалы вместо дат")
                if "place" in keys:
                    sugg.append("без названия учреждения/места")
                issues.append({"section": sec["key"], "heading": sec["heading"],
                               "severity": "warning", "code": "reid_text",
                               "message": "сочетание квазиидентификаторов: " + ", ".join(l for _, l in found),
                               "fragment": sent[:240], "suggestion": "; ".join(sugg) or "обобщите описание"})
    return issues


QI_PATTERNS = [("age", r"возраст|\bage\b"), ("sex", r"\bпол\b|\bsex\b|gender"),
               ("site", r"локализ|\bsite\b|location|орган")]


def qi_columns(dictionary):
    out = []
    for v in dictionary:
        if v["vtype"] in ("identifier", "text"):
            continue
        name = v["name"].lower()
        for kind, pat in QI_PATTERNS:
            if re.search(pat, name):
                out.append({"name": v["name"], "kind": kind})
                break
        else:
            if v["group"] == "molecular" and v["vtype"] == "categorical":
                out.append({"name": v["name"], "kind": "variant"})
    return out


def _age_band(x, width=10):
    try:
        a = float(str(x).replace(",", "."))
    except ValueError:
        return x
    if a >= 90:
        return "≥90"
    lo = int(a // width * width)
    return f"{lo}–{lo + width - 1}"


def supplementary_frame(df, dictionary, settings):
    """The case table as it will be exported, after the author's generalisations."""
    from ..anonymization import BIRTH_HEADER, DIRECT_HEADER, LINK_HEADERS, _any, _norm

    def identifier_like(name):  # defence in depth, independent of the data dictionary
        h = _norm(name)
        return (name.endswith("(внутр. ID)") or _any(DIRECT_HEADER, h) or _any(BIRTH_HEADER, h)
                or any(_any(p, h) for p in LINK_HEADERS.values()))

    keep = [v["name"] for v in dictionary if v.get("include", True) and v["vtype"] not in ("identifier", "text")
            and not identifier_like(v["name"])]
    if not settings.get("exclude_text", True):
        keep += [v["name"] for v in dictionary if v["vtype"] == "text"]
    out = df[["case_id"] + [c for c in keep if c in df.columns]].copy()
    for c in settings.get("drop_columns", []):
        if c in out.columns:
            out = out.drop(columns=[c])
    if settings.get("age_bands", True):
        for q in qi_columns(dictionary):
            if q["kind"] == "age" and q["name"] in out.columns:
                width = int(settings.get("age_band_width") or 10)
                out[q["name"]] = out[q["name"]].map(lambda x, w=width: _age_band(x, w))
    return out


def k_anonymity(frame, qi):
    cols = [q for q in qi if q in frame.columns]
    if not cols or frame.empty:
        return {"columns": cols, "k_min": None, "unique_cases": [], "classes": 0}
    key = frame[cols].astype(str).agg(" | ".join, axis=1)
    counts = key.map(key.value_counts())
    unique = frame.loc[counts == 1, "case_id"].tolist()
    return {"columns": cols, "k_min": int(counts.min()), "unique_cases": unique, "classes": int(key.nunique()),
            "n": int(len(frame))}


def data_risk(df, dictionary, settings):
    qi = [q["name"] for q in qi_columns(dictionary)]
    raw = supplementary_frame(df, dictionary, {"exclude_text": True, "age_bands": False})
    exported = supplementary_frame(df, dictionary, settings)
    return {"qi_columns": qi_columns(dictionary), "before": k_anonymity(raw, qi),
            "after": k_anonymity(exported, qi), "settings": settings,
            "exported_columns": [c for c in exported.columns]}


def csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")
