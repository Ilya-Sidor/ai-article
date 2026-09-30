"""Features from free-text reports (FR-1.7, FR-1.9).

Runs only on the confirmed, anonymised case table (the privacy gate of llm._call enforces it). The model
returns, per case, feature values each with a verbatim quote from that case's report; the quote is checked
against the text, so every extracted value can be traced to its source. Values fill empty cells only: what
the author or a spreadsheet already gave is never overwritten, a disagreement is recorded as a conflict.
"""
import re

from . import llm
from .config import MODEL_EXTRACTION
from .ingest import build_dictionary
from .storage import now_iso, read_json, write_json

BATCH_CHARS = 30_000
GROUPS = ["clinical", "morphology", "ihc", "molecular", "followup", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "features": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "group": {"type": "string", "enum": GROUPS},
                           "unit": {"type": "string"}},
            "required": ["name", "group", "unit"], "additionalProperties": False}},
        "cases": {"type": "array", "items": {
            "type": "object",
            "properties": {"case_id": {"type": "string"}, "values": {"type": "array", "items": {
                "type": "object",
                "properties": {"feature": {"type": "string"}, "value": {"type": "string"},
                               "quote": {"type": "string"}},
                "required": ["feature", "value", "quote"], "additionalProperties": False}}},
            "required": ["case_id", "values"], "additionalProperties": False}},
    },
    "required": ["features", "cases"], "additionalProperties": False,
}

SYSTEM = """You turn anonymised clinical documents of a case series — pathology reports, discharge summaries
(выписной эпикриз), operation notes, clinical notes — into a table of cases × features for a case-series study.
One document (or several documents of one case) = one case. Extract what the study needs: demographics
(age, sex), clinical data (site, size, stage, symptoms), treatment (surgery type, resection margin, adjuvant
therapy), morphology, immunohistochemistry, molecular results, follow-up and outcome.

Rules:
- Use ONE consistent set of features for all cases; reuse the names of the existing columns and of the features
  already found (given below) whenever they mean the same thing. Feature names in Russian as in the reports,
  short, with the unit in the name when there is one (e.g. «Локализация», «Размер опухоли, мм»,
  «Митозы на 5 мм²», «Гистологический тип», «Некроз», «CD117», «DOG1», «Ki-67, %», «Мутация KIT, экзон»).
- One feature per marker or gene; for IHC give the result as in the report normalised to «положительный» /
  «отрицательный» (keep the percentage in a separate «…, %» feature when it is given).
- Values: numbers without units (decimal point), categories in the words of the report; omit a feature for a case
  when the report does not state it. Never infer, estimate or complete a value that is not written.
- quote: the exact fragment of THAT case's report the value comes from, copied character for character (short,
  but long enough to be unambiguous). No quote — no value.
- Dates were replaced by intervals such as «[+14.2 мес. от индексной даты]» (the index date is the first date
  of the document). Use them for time features: e.g. «Время до рецидива, мес» = the interval of the recurrence
  minus the interval of the surgery, one decimal; quote the fragments you used.
- Ignore placeholders such as [ФИО], [ДАТА РОЖДЕНИЯ] or [АДРЕС]; they are removed personal data."""


def text_columns(dictionary):
    return [v["name"] for v in dictionary if v.get("vtype") == "text"]


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("ё", "е").replace("Ё", "Е")).strip().lower()


def _quote_ok(quote, text):
    q = _norm(quote)
    return len(q) >= 2 and q in _norm(text)


def _batches(cases):
    batch, size = [], 0
    for cid, text in cases:
        if batch and size + len(text) > BATCH_CHARS:
            yield batch
            batch, size = [], 0
        batch.append((cid, text))
        size += len(text)
    if batch:
        yield batch


def extract(store, pid, columns=None):
    """Fill the case table from the text columns; returns a summary for the author."""
    project = store.get(pid)
    df = store.dataset(pid)
    d = store.dir(pid)
    dictionary = read_json(d / "dictionary.json", [])
    columns = [c for c in (columns or text_columns(dictionary)) if c in df.columns]
    if not columns:
        raise ValueError("в таблице нет текстовых столбцов (заключений) для извлечения")
    texts = {}
    for _, row in df.iterrows():
        parts = [f"{c}:\n{row[c]}" for c in columns if str(row[c]).strip()]
        if parts:
            texts[row["case_id"]] = "\n\n".join(parts)
    if not texts:
        raise ValueError("текстовые столбцы пусты")

    existing = [c for c in df.columns if c != "case_id" and c not in columns]
    features, found = {}, {}
    for batch in _batches(list(texts.items())):
        user = ("Existing columns of the table: " + (", ".join(existing) or "none") + "\n"
                + "Features found so far: " + (", ".join(features) or "none") + "\n\n"
                + "\n\n".join(f"=== case {cid} ===\n{text}" for cid, text in batch))
        out = llm._call(project, "extract_cases", MODEL_EXTRACTION, SYSTEM, user, SCHEMA, max_tokens=16000, gate=True)
        for f in out["features"]:
            features.setdefault(f["name"].strip(), f)
        for case in out["cases"]:
            if case["case_id"] in texts:
                found.setdefault(case["case_id"], []).extend(case["values"])
    model = llm.used_model(llm.effective_model(MODEL_EXTRACTION))

    extracted = read_json(d / "extracted.json", {})
    summary = {"cases": 0, "values": 0, "unverified": 0, "conflicts": [], "new_columns": [], "model": model}
    for cid, values in found.items():
        mask = df["case_id"] == cid
        filled = False
        for v in values:
            col, value = v["feature"].strip(), str(v["value"]).strip()
            if not col or not value or col == "case_id" or col in columns:
                continue
            if col not in df.columns:
                df[col] = ""
                summary["new_columns"].append(col)
            current = str(df.loc[mask, col].iloc[0]).strip()
            ok = _quote_ok(v["quote"], texts[cid])
            from_text_before = col in extracted.get(cid, {})  # a re-run may update its own earlier values
            if current and not from_text_before:
                if _norm(current) != _norm(value):  # the table or the author already gave a value: keep it
                    summary["conflicts"].append({"case_id": cid, "column": col, "table": current, "text": value,
                                                 "quote": v["quote"]})
                continue
            df.loc[mask, col] = value
            extracted.setdefault(cid, {})[col] = {"quote": v["quote"], "verified": ok, "model": model,
                                                  "at": now_iso()}
            summary["values"] += 1
            summary["unverified"] += not ok
            filled = True
        summary["cases"] += filled
    store.save_frame(d / "dataset.csv", df.fillna(""))
    write_json(d / "extracted.json", extracted)
    write_json(d / "extraction_conflicts.json", summary["conflicts"])
    old = {v["name"]: v for v in dictionary}
    fresh = build_dictionary(df)
    write_json(d / "dictionary.json", [old.get(v["name"], v) if old.get(v["name"], {}).get("edited") else v
                                       for v in fresh])
    store.audit(pid, "agent", "dataset.extracted_from_text",
                {k: (len(v) if isinstance(v, list) else v) for k, v in summary.items()})
    return summary
