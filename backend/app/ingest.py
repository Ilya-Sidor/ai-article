"""Reading uploaded tables and building the data dictionary (FR-1.1, FR-1.8, FR-1.11)."""
import io
import json
import re

import numpy as np
import pandas as pd

TABULAR_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".json"}


class UnsupportedFile(ValueError):
    pass


def read_table(filename: str, content: bytes) -> pd.DataFrame:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext in (".csv", ".tsv"):
        text = None
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise UnsupportedFile("не удалось определить кодировку файла")
        sep = "\t" if ext == ".tsv" else None
        return pd.read_csv(io.StringIO(text), sep=sep, engine="python", dtype=str, keep_default_na=False)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    if ext == ".json":
        data = json.loads(content.decode("utf-8"))
        if isinstance(data, dict):
            data = data.get("cases") or data.get("data") or [data]
        return pd.json_normalize(data).astype(str)
    from .documents import DOCUMENT_EXT, DocumentError, read_document
    if ext in DOCUMENT_EXT:
        try:
            return read_document(ext, content)
        except DocumentError as exc:
            raise UnsupportedFile(str(exc)) from exc
    raise UnsupportedFile(
        f"формат {ext or '(без расширения)'} не поддерживается; поддерживаются таблицы (.xlsx, .xls, .csv, .tsv, "
        ".json) и документы (.docx, .doc, .pdf, .rtf, .txt)")


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

MISSING = {"", "na", "n/a", "nd", "н/д", "нд", "nan", "none", "null", "нет данных", "не известно", "неизвестно",
           "unknown", "not done", "не проводилось", "?"}
POSITIVE = {"+", "pos", "positive", "положительный", "положительная", "положит", "polozh", "да", "yes", "y", "true",
            "1", "есть", "present", "присутствует", "mut", "mutated", "mutation", "мутация", "mutant",
            "обнаружена", "выявлена", "detected", "amplified", "амплификация"}
NEGATIVE = {"-", "−", "–", "neg", "negative", "отрицательный", "отрицательная", "отрицат", "нет", "no", "n", "false",
            "0", "wt", "wild type", "wild-type", "дикий тип", "не обнаружена", "не выявлена", "not detected",
            "absent", "отсутствует"}
BINARY_LABELS = {"ihc": ("negative", "positive"), "molecular": ("not detected", "detected")}

ORDINAL_FAMILIES = [
    (re.compile(r"^g\s?([1-4])$"), lambda m: int(m.group(1))),
    (re.compile(r"^grade\s?([1-4])$"), lambda m: int(m.group(1))),
    (re.compile(r"^([0-3])\+?$"), lambda m: int(m.group(1))),
    (re.compile(r"^p?t([0-4])([a-d]?)$"), lambda m: int(m.group(1)) * 10 + (ord(m.group(2)) - 96 if m.group(2) else 0)),
    (re.compile(r"^(?:stage\s)?(i{1,3}|iv)([a-c]?)$"),
     lambda m: {"i": 1, "ii": 2, "iii": 3, "iv": 4}[m.group(1)] * 10 + (ord(m.group(2)) - 96 if m.group(2) else 0)),
]
ORDINAL_WORDS = {
    "очень низкий": 0, "very low": 0, "низкий": 1, "low": 1, "умеренный": 2, "промежуточный": 2,
    "intermediate": 2, "moderate": 2, "высокий": 3, "high": 3,
    "слабая": 1, "weak": 1, "умеренная": 2, "сильная": 3, "strong": 3,
}

GROUP_PATTERNS = [
    ("followup", [r"follow", r"наблюд", r"исход", r"outcome", r"рецидив", r"relapse", r"recurr", r"смерт", r"death",
                  r"прогресс", r"progress", r"метастаз", r"мес\. от", r"survival", r"выживаем", r"status"]),
    ("molecular", [r"мутац", r"mutat", r"\bexon", r"экзон", r"\bfish\b", r"fusion", r"слия", r"транслок", r"amplif",
                   r"амплиф", r"\bmsi\b", r"\btmb\b", r"\bvaf\b", r"\bngs\b", r"\bpcr\b", r"пцр", r"cnv"]),
    ("ihc", [r"\bигх\b", r"\bihc\b", r"ki-?67", r"\bcd\d+", r"dog1", r"\bs-?100", r"\bsma\b", r"desmin", r"десмин",
             r"\bck\b", r"cytokeratin", r"цитокерат", r"\ber\b", r"\bpr\b", r"her2", r"\bp53\b", r"\bp16\b", r"sdhb",
             r"vimentin", r"виментин", r"synaptophysin", r"синаптофизин", r"chromogranin", r"хромогранин", r"h-?score",
             r"экспресс", r"express", r"\bpd-?l1\b", r"\bpan-?ck\b"]),
    ("morphology", [r"гистотип", r"histotype", r"histolog", r"гистолог", r"grade", r"степень", r"митоз", r"mitos",
                    r"mitot", r"некроз", r"necros", r"архитект", r"architect", r"клеточн", r"cellular", r"атипи",
                    r"atypi", r"инвази", r"invasi", r"вариант", r"variant", r"subtype", r"подтип", r"морфолог",
                    r"morpholog", r"риск", r"risk", r"pleomorph", r"плеоморф"]),
]


def infer_group(name: str) -> str:
    h = name.lower().replace("ё", "е")
    for group, pats in GROUP_PATTERNS:
        if any(re.search(p, h) for p in pats):
            return group
    if re.fullmatch(r"[A-Z][A-Z0-9]{1,6}", name.strip()):
        return "molecular"
    return "clinical"


def parse_number(value):
    s = str(value).strip().replace(",", ".").replace("−", "-")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(%|мм|mm|см|cm|лет|years?|y|мес\.?|months?)?", s, re.I)
    return float(m.group(1)) if m else None


def _canonical(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw).strip())


def _ordinal_rank(value: str):
    v = value.lower().strip()
    if v in ORDINAL_WORDS:
        return ("words", ORDINAL_WORDS[v])
    for i, (rx, fn) in enumerate(ORDINAL_FAMILIES):
        m = rx.match(v)
        if m:
            return (i, fn(m))
    return None


def infer_variable(name: str, series: pd.Series) -> dict:
    values = [_canonical(v) for v in series.fillna("")]
    present = [v for v in values if v.lower() not in MISSING]
    group = infer_group(name)
    spec = {"name": name, "label": name, "group": group, "vtype": "categorical", "levels": [], "value_map": {},
            "unit": None, "include": True, "n_present": len(present), "n_missing": len(values) - len(present)}
    if name == "case_id" or name.endswith("(внутр. ID)"):
        spec.update(vtype="identifier", include=False)
        return spec
    if not present:
        spec.update(include=False)
        return spec

    uniq = sorted(set(present))
    lowered = {v: v.lower() for v in uniq}
    # whole reports (even one or two of them) are free text; the text of an uploaded document always is
    if name == "Текст документа" or np.mean([len(v) for v in present]) > 80:
        spec.update(vtype="text", include=False)
        return spec

    # binary: all values map to a positive/negative token
    if all(lowered[v] in POSITIVE | NEGATIVE for v in uniq) and {lowered[v] in POSITIVE for v in uniq} == {True, False}:
        neg, pos = BINARY_LABELS.get(group, ("no", "yes"))
        spec.update(vtype="binary", levels=[neg, pos],
                    value_map={v: (pos if lowered[v] in POSITIVE else neg) for v in uniq})
        return spec

    numbers = [parse_number(v) for v in present]
    parsed = [x for x in numbers if x is not None]
    if len(parsed) >= 0.9 * len(present) and len(set(parsed)) > 2:
        unit = None
        m = re.search(r"(%|мм|mm|см|cm|лет|years|мес)", " ".join(present) + " " + name.lower())
        if m:
            unit = m.group(1)
        spec.update(vtype="quantitative", unit=unit)
        return spec

    ranks = {v: _ordinal_rank(v) for v in uniq}
    families = {r[0] for r in ranks.values() if r is not None}
    if len(uniq) >= 3 and all(r is not None for r in ranks.values()) and len(families) == 1:
        spec.update(vtype="ordinal", levels=sorted(uniq, key=lambda v: ranks[v][1]))
        return spec

    if len(uniq) == 2:
        spec.update(vtype="binary", levels=uniq)
        return spec

    avg_len = np.mean([len(v) for v in present])
    if len(uniq) > max(10, 0.6 * len(present)) and avg_len > 25:
        spec.update(vtype="text", include=False)
        return spec
    if len(uniq) == 1:
        spec.update(vtype="categorical", levels=uniq)
        return spec
    spec.update(vtype="categorical", levels=sorted(uniq, key=lambda v: (-present.count(v), v)))
    return spec


def build_dictionary(df: pd.DataFrame) -> list:
    return [infer_variable(c, df[c]) for c in df.columns if c != "case_id"]


def prepare_analysis_frame(df: pd.DataFrame, dictionary: list):
    """Typed, normalized table for the sandbox plus a list of unparsed cells."""
    out = pd.DataFrame({"case_id": df["case_id"]})
    problems = []
    for spec in dictionary:
        col = spec["name"]
        if col not in df.columns or not spec.get("include") or spec["vtype"] in ("identifier", "text"):
            continue
        vals = []
        for cid, raw in zip(df["case_id"], df[col]):
            v = _canonical(raw)
            if v.lower() in MISSING:
                vals.append("")
                continue
            if spec["vtype"] == "quantitative":
                x = parse_number(v)
                if x is None:
                    problems.append({"case_id": cid, "column": col, "value": v, "issue": "не распознано как число"})
                    vals.append("")
                else:
                    vals.append(repr(x))
            else:
                v = spec.get("value_map", {}).get(v, v)
                if spec["levels"] and v not in spec["levels"]:
                    problems.append({"case_id": cid, "column": col, "value": v, "issue": "значение вне словаря"})
                    vals.append("")
                else:
                    vals.append(v)
        out[col] = vals
    return out, problems
