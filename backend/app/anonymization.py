"""Rule-based de-identification of uploaded case tables (FR-1.2 – FR-1.6).

Runs locally, before any data can reach an external LLM. Handles Russian and
English: direct identifiers are removed or replaced, record / block numbers are
replaced with internal IDs, exact dates become intervals from an index date.
Everything that was changed or looks like a quasi-identifier is listed in a
report that the author must confirm (FR-1.5).

This is a rules layer; a NER model (Presidio + ru/en NER, PRD 8.1) is planned
on top of it for the MVP.
"""
import re
from dataclasses import dataclass

import pandas as pd

from .documents import DOC_TEXT_COLUMN

MAX_REPORT_HITS = 2000
# words of clinical documents the NER model sometimes takes for names ("Выписной эпикриз")
NOT_NAMES = {"выписной", "выписка", "эпикриз", "заключение", "диагноз", "пациент", "пациентка", "больной", "больная",
             "анамнез", "операция", "протокол", "макроскопически", "микроскопически", "гистологическое",
             "иммуногистохимическое", "исследование", "материал", "препарат", "клинический", "рецидив", "метастаз"}


def _norm(header: str) -> str:
    return re.sub(r"\s+", " ", str(header).lower().replace("ё", "е")).strip()


def _any(patterns, text):
    return any(re.search(p, text) for p in patterns)


# ---------------------------------------------------------------------------
# Column-level classification by header
# ---------------------------------------------------------------------------

BIRTH_HEADER = [r"дата рождения", r"^д\.?\s?р\.?$", r"\bdob\b", r"birth"]
LINK_HEADERS = {
    "record": [r"истори[яи] болезни", r"\bи/б\b", r"^иб\b", r"номер иб", r"мед\w*\.? ?карт", r"амбулатор\w* карт",
               r"\bmrn\b", r"medical record", r"hospital (id|no|number)", r"patient id", r"case no",
               r"номер исследования", r"№ исследования",
               r"^(id|№|№ ?п/п|n|no\.?|nr|номер|case|case id|patient|код|код пациента)$"],
    "block": [r"\bблок", r"\bblock", r"стекл", r"\bslides?\b", r"гистолог\w* (номер|№)", r"(номер|№) гистолог",
              r"accession", r"histology (no|number|id)"],
}
DIRECT_HEADER = [
    r"\bфио\b", r"ф\.\s*и\.\s*о", r"фамили", r"^имя\b", r"\bимя пациент", r"отчеств", r"^пациент(ка)?$",
    r"^(patient|patient name|name|full name|surname|first name|last name)$",
    r"адрес", r"address", r"телефон", r"^тел\.?$", r"phone", r"e-?mail", r"почта", r"снилс", r"snils",
    r"полис", r"insurance", r"паспорт", r"passport", r"^инн$",
]
DATE_HEADER = [r"\bдата\b", r"\bdate\b"]
INDEX_DATE_HINT = [r"операц", r"surgery", r"резекц", r"resection", r"биопс", r"biops", r"диагноз", r"diagnos"]
AGE_HEADER = [r"возраст", r"\bage\b"]

# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

RU_MONTHS = {m: i + 1 for i, m in enumerate(
    ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"])}
EN_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"])}

DATE_NUMERIC = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)(?!\.\d)")
DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{2}:\d{2}(?::\d{2})?)?(?!\d)")
DATE_RU = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(RU_MONTHS) + r")\s+(\d{4})(?:\s*(?:года|г\.))?", re.I)
DATE_EN = re.compile(r"\b(?:(\d{1,2})\s+)?(" + "|".join(EN_MONTHS) + r")\s+(?:(\d{1,2}),?\s+)?(\d{4})\b", re.I)


def _mk_date(y, m, d):
    y = int(y)
    if y < 100:
        y += 2000 if y < 50 else 1900
    try:
        return pd.Timestamp(year=y, month=int(m), day=int(d))
    except (ValueError, TypeError):
        return None


def _match_to_date(kind, m):
    if kind == "numeric":
        return _mk_date(m.group(3), m.group(2), m.group(1))
    if kind == "iso":
        return _mk_date(m.group(1), m.group(2), m.group(3))
    if kind == "ru":
        return _mk_date(m.group(3), RU_MONTHS[m.group(2).lower()], m.group(1))
    day = m.group(1) or m.group(3) or 1
    return _mk_date(m.group(4), EN_MONTHS[m.group(2).lower()], day)


DATE_PATTERNS = [("iso", DATE_ISO), ("numeric", DATE_NUMERIC), ("ru", DATE_RU), ("en", DATE_EN)]


def parse_date(value):
    s = str(value).strip()
    if not s:
        return None
    for kind, rx in DATE_PATTERNS:
        m = rx.fullmatch(s) or (rx.match(s) if kind == "iso" else None)
        if m:
            return _match_to_date(kind, m)
    return None


def months_between(start, end):
    return round((end - start).days / 30.4375, 1)


# ---------------------------------------------------------------------------
# Free-text rules (order matters)
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    kind: str
    label: str
    regex: re.Pattern
    token: str
    group: int = 0


def _rx(p, flags=0):
    return re.compile(p, flags)


CAP_RU = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
BIRTH_RULES = [  # before the date rules: a birth date must not become an interval (it would reveal the age)
    Rule("birth_date", "Дата рождения",
         _rx(r"(?:дат\w*\s+рождени\w*|д\.\s*р\.)\s*[:—–-]?\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", re.I),
         "[ДАТА РОЖДЕНИЯ]"),
    Rule("birth_date", "Год рождения",
         _rx(r"(?<!\d)(?:19|20)\d{2}\s*г\.?\s*р\.?(?![а-яё])|год\w*\s+рождени\w*\s*[:—–-]?\s*(?:19|20)\d{2}", re.I),
         "[ГОД РОЖДЕНИЯ]"),
]

TEXT_RULES = BIRTH_RULES + [
    Rule("email", "E-mail", _rx(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    Rule("snils", "СНИЛС", _rx(r"(?<!\d)\d{3}-\d{3}-\d{3}[\s-]\d{2}(?!\d)"), "[СНИЛС]"),
    Rule("passport", "Паспорт", _rx(r"паспорт\w*\s*[:№]?\s*\d{2}\s?\d{2}\s?№?\s?\d{6}", re.I), "[ПАСПОРТ]"),
    Rule("policy", "Полис", _rx(r"(?:полис\w*(?:\s+омс)?|policy)\s*[:№#]?\s*\d[\d\s]{8,20}\d", re.I), "[ПОЛИС]"),
    Rule("policy", "Полис", _rx(r"(?<!\d)\d{16}(?!\d)"), "[ПОЛИС]"),
    Rule("record", "Номер истории болезни / карты",
         _rx(r"(?:и/б|истори[яи]\s+болезни|мед\.?\s*карт[аы]|амб\.?\s*карт[аы]|MRN|medical\s+record(?:\s+number)?|"
             r"hospital\s+(?:no|number|id))\s*[:№#.]?\s*№?\s*[A-Za-zА-Яа-я]{0,3}[-/]?\d[\w/-]*", re.I), "[НОМЕР_ИБ]"),
    Rule("block", "Номер гистологического блока",
         _rx(r"(?:блок(?:а|и|ов)?|стекл(?:о|а)|гистологи\w*\s*(?:№|номер)|block|slide|accession)\s*[:№#]?\s*№?\s*"
             r"[A-Za-zА-Яа-я]{0,3}[-/]?\d{1,7}(?:[-/]\d{1,7}){0,2}(?:[-/][A-Za-zА-Яа-я0-9]{1,3})?", re.I), "[БЛОК]"),
    Rule("block", "Номер гистологического блока", _rx(r"\b[SHГБ]\d{2}[-/]\d{3,7}\b"), "[БЛОК]"),
    Rule("date", "Дата", DATE_ISO, "[ДАТА]"),
    Rule("date", "Дата", DATE_NUMERIC, "[ДАТА]"),
    Rule("date", "Дата", DATE_RU, "[ДАТА]"),
    Rule("date", "Дата", DATE_EN, "[ДАТА]"),
    Rule("block", "Номер исследования", _rx(r"№\s*\d{2,7}[/-]\d{2,4}"), "[БЛОК]"),
    Rule("phone", "Телефон", _rx(r"(?<!\d)(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"), "[ТЕЛЕФОН]"),
    Rule("phone", "Телефон", _rx(r"(?<![\w+])\+\d{1,3}[\s\-(]*\d{2,4}[\s\-)]*\d{3}[\s-]*\d{2,4}(?:[\s-]*\d{2,4})?(?!\d)"),
         "[ТЕЛЕФОН]"),
    Rule("address", "Адрес",
         _rx(r"(?:ул\.|улица|пр-т|просп\.|проспект|пер\.|переулок|бульвар|б-р|шоссе|наб\.)\s*[А-ЯЁ0-9][^,;\n]*"
             r"(?:,\s*(?:д\.|дом)\s*\d+\w*)?(?:,\s*(?:корп\.|к\.)\s*\d+)?(?:,\s*(?:кв\.|квартира)\s*\d+)?"), "[АДРЕС]"),
    Rule("address", "Адрес", _rx(r"(?<![А-Яа-яЁё])(?:г\.|город|пос\.|пгт|дер\.)\s*[А-ЯЁ][а-яё\-]+"), "[АДРЕС]"),
    Rule("address", "Адрес",
         _rx(r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Lane|Blvd\.?)"),
         "[АДРЕС]"),
    Rule("name", "ФИО", _rx(CAP_RU + r"\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:вич(?:а|у|ем|е)?|вн(?:а|ы|е|у|ой)|"
                              r"ичн(?:а|ы|е|у|ой)|инична|оглы|кызы)\b"), "[ФИО]"),
    Rule("name", "ФИО", _rx(CAP_RU + r"\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.?"), "[ФИО]"),
    Rule("name", "ФИО", _rx(r"(?<![А-Яа-яЁё])[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?" + CAP_RU), "[ФИО]"),
    Rule("name", "ФИО",
         _rx(r"(?:[Пп]ациент(?:ка|а|у|ом|ки)?|[Бб]ольн(?:ой|ая|ого|ую)|ФИО|[Вв]рач|[Дд]октор)\s*[:\-]?\s*("
             + CAP_RU + r"(?:\s+" + CAP_RU + r"){0,2})"), "[ФИО]", group=1),
    Rule("name", "ФИО", _rx(r"(?:проф\.|профессор\w*|доц\.|доцент\w*|акад\.|академик\w*|д-р|докт\.)\s*("
                          + CAP_RU + r"(?:\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.?)?)"), "[ФИО]", group=1),
    Rule("name", "Name", _rx(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"), "[NAME]"),
    Rule("name", "Name", _rx(r"(?:[Pp]atient|[Nn]ame)\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"), "[NAME]",
         group=1),
]
LONG_NUMBER = re.compile(r"(?<![\d.,])\d{6,}(?![\d.,])")


def mask(value: str) -> str:
    s = str(value)
    if len(s) <= 1:
        return "•"
    return s[0] + re.sub(r"\w", "•", s[1:])


class Anonymizer:
    """De-identifies one table. ``link_map`` is shared across uploads of a project
    so that the same record number always maps to the same internal ID."""

    def __init__(self, filename: str, link_map: dict, use_ner=None):
        from . import ner
        self.use_ner = ner.enabled() if use_ner is None else use_ner
        self.filename = filename
        self.link_map = link_map
        self.hits = []
        self.counts = {}
        self.columns = []
        self.quasi = []
        self.notes = []

    # ---- helpers --------------------------------------------------------
    def _hit(self, kind, label, column, row, original, replacement, category="direct"):
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if len(self.hits) < MAX_REPORT_HITS:
            self.hits.append({"kind": kind, "label": label, "category": category, "column": column,
                              "row": row, "preview": mask(original), "replacement": replacement})

    def _internal_id(self, kind, original):
        prefix = {"record": "PT", "block": "BLK"}[kind]
        table = self.link_map.setdefault(kind, {})
        key = re.sub(r"\s+", "", str(original)).upper()
        if key not in table:
            table[key] = "%s-%04d" % (prefix, len(table) + 1)
        return table[key]

    @staticmethod
    def first_date(text):
        """Index date of a free-text document (a discharge summary, a report): its first date that is not a
        birth date. The other dates then become intervals, so the timeline (surgery → recurrence) survives."""
        for rule in BIRTH_RULES:
            text = rule.regex.sub(" ", text)
        found = []
        for kind, rx in (("iso", DATE_ISO), ("numeric", DATE_NUMERIC), ("ru", DATE_RU), ("en", DATE_EN)):
            for m in rx.finditer(text):
                d = _match_to_date(kind, m)
                if d is not None:
                    found.append((m.start(), d))
        return min(found)[1] if found else None

    def scrub_text(self, text: str, column: str, row: int, index_date=None) -> str:
        out = str(text)
        for rule in TEXT_RULES:
            def repl(m, rule=rule):
                token = rule.token
                if rule.kind == "date" and index_date is not None:
                    kind = {DATE_ISO: "iso", DATE_NUMERIC: "numeric", DATE_RU: "ru", DATE_EN: "en"}[rule.regex]
                    d = _match_to_date(kind, m)
                    if d is not None:
                        token = "[%+.1f мес. от индексной даты]" % months_between(index_date, d)
                if rule.group:
                    s, e = m.span(rule.group)
                    self._hit(rule.kind, rule.label, column, row, m.group(rule.group), token)
                    return m.group(0)[: s - m.start()] + token + m.group(0)[e - m.start():]
                self._hit(rule.kind, rule.label, column, row, m.group(0), token)
                return token
            out = rule.regex.sub(repl, out)
        out = self._ner(out, column, row)
        for m in LONG_NUMBER.finditer(out):
            self.quasi.append({"column": column, "row": row, "reason": "длинный числовой код — возможно, идентификатор",
                               "preview": mask(m.group(0))})
        return out

    def _ner(self, text, column, row):
        """Second layer: named entities the rules missed (names in any case form, bare first names)."""
        from . import ner
        if not self.use_ner:
            return text
        spans = ner.entities(text)
        for start, end, etype, value in sorted(spans, reverse=True):
            if etype == "PER" and all(w in NOT_NAMES for w in re.findall(r"[а-яё]+", value.lower())):
                continue
            if etype == "PER":
                self._hit("name_ner", "ФИО (NER)", column, row, value, "[ФИО]")
                text = text[:start] + "[ФИО]" + text[end:]
            else:
                self.quasi.append({"column": column, "row": row, "preview": value,
                                   "reason": ("место" if etype == "LOC" else "организация")
                                   + " (NER) — проверьте, не идентифицирует ли пациента"})
        return text

    # ---- main -----------------------------------------------------------
    def run(self, df: pd.DataFrame):
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        df = df.fillna("").astype(str).apply(lambda s: s.str.strip())
        rows = [i + 2 for i in range(len(df))]  # spreadsheet row numbers (header is row 1)

        roles = {}
        for col in df.columns:
            h = _norm(col)
            values = [v for v in df[col] if v]
            date_share = (sum(parse_date(v) is not None for v in values) / len(values)) if values else 0
            if _any(BIRTH_HEADER, h):
                roles[col] = ("birth", None)
            elif any(_any(p, h) for p in LINK_HEADERS.values()):
                kind = next(k for k, p in LINK_HEADERS.items() if _any(p, h))
                roles[col] = ("link", kind)
            elif _any(DIRECT_HEADER, h):
                roles[col] = ("direct", None)
            elif _any(DATE_HEADER, h) or (values and date_share >= 0.8):
                roles[col] = ("date", None)
            else:
                roles[col] = ("keep", None)

        date_cols = [c for c, (r, _) in roles.items() if r == "date"]
        index_col = next((c for c in date_cols if _any(INDEX_DATE_HINT, _norm(c))), date_cols[0] if date_cols else None)
        index_dates = [parse_date(v) for v in df[index_col]] if index_col else [None] * len(df)
        has_age = any(_any(AGE_HEADER, _norm(c)) for c in df.columns)

        out = pd.DataFrame(index=df.index)
        link_col = None
        for col in df.columns:
            role, kind = roles[col]
            non_empty = int((df[col] != "").sum())
            if role == "direct":
                self.columns.append({"column": col, "action": "удалён", "category": "direct",
                                     "detail": f"прямой идентификатор, значений: {non_empty}"})
                self.counts["column_removed"] = self.counts.get("column_removed", 0) + 1
                continue
            if role == "link":
                new_col = f"{col} (внутр. ID)"
                out[new_col] = [self._internal_id(kind, v) if v else "" for v in df[col]]
                if kind == "record" and link_col is None:
                    link_col = new_col
                self.columns.append({"column": col, "action": "заменён внутренним ID", "category": "direct",
                                     "detail": f"{non_empty} значений → {new_col}"})
                self.counts[kind] = self.counts.get(kind, 0) + non_empty
                continue
            if role == "birth":
                if not has_age and index_col:
                    ages = []
                    for v, idx in zip(df[col], index_dates):
                        b = parse_date(v)
                        ages.append(str(int((idx - b).days // 365.25)) if b is not None and idx is not None else "")
                    out["Возраст на индексную дату, лет"] = ages
                    detail = "преобразована в возраст на индексную дату"
                else:
                    detail = "удалена (возраст уже есть в данных)" if has_age else "удалена (нет индексной даты)"
                self.columns.append({"column": col, "action": "удалена", "category": "direct", "detail": detail})
                self.counts["birth_date"] = self.counts.get("birth_date", 0) + non_empty
                continue
            if role == "date":
                if col == index_col:
                    self.columns.append({"column": col, "action": "индексная дата", "category": "direct",
                                         "detail": "используется как точка отсчёта и удалена"})
                    self.counts["date"] = self.counts.get("date", 0) + non_empty
                    continue
                new_col = f"{col} (мес. от индексной даты)"
                vals = []
                for v, idx in zip(df[col], index_dates):
                    d = parse_date(v)
                    vals.append(str(months_between(idx, d)) if d is not None and idx is not None else "")
                out[new_col] = vals
                self.columns.append({"column": col, "action": "преобразована в интервал", "category": "direct",
                                     "detail": f"→ «{new_col}»" + ("" if index_col else " (индексная дата не найдена)")})
                self.counts["date"] = self.counts.get("date", 0) + non_empty
                continue
            if col == DOC_TEXT_COLUMN and not index_col:  # a document: its own first date is the index date
                doc_dates = [self.first_date(v) for v in df[col]]
                if any(doc_dates):
                    self.notes.append("Индексная дата документа — первая дата в тексте (кроме даты рождения); "
                                      "остальные даты заменены интервалами в месяцах от неё (FR-1.4).")
                out[col] = [self.scrub_text(v, col, r, idx) if v else "" for v, r, idx in zip(df[col], rows, doc_dates)]
                continue
            out[col] = [self.scrub_text(v, col, r, idx) if v else "" for v, r, idx in zip(df[col], rows, index_dates)]

        if index_col:
            self.notes.append(f"Индексная дата: «{index_col}». Все точные даты заменены интервалами в месяцах (FR-1.4).")
        elif date_cols:
            self.notes.append("Индексная дата не определена — даты удалены.")
        self.notes.append("Исходный файл не сохраняется; метаданные документа (FR-1.6) отброшены при разборе.")

        for col in out.columns:
            if _any(AGE_HEADER, _norm(col)):
                ages = pd.to_numeric(out[col], errors="coerce")
                self.quasi.append({"column": col, "row": None, "preview": "",
                                   "reason": "точный возраст — квазиидентификатор; для редких диагнозов "
                                             "рассмотрите диапазоны (FR-7.4)"})
                for r, a in zip(rows, ages):
                    if pd.notna(a) and a >= 90:
                        self.quasi.append({"column": col, "row": r, "preview": "≥90",
                                           "reason": "возраст ≥ 90 лет — высокий риск реидентификации"})

        report = {
            "file": self.filename, "n_rows": int(len(df)), "n_columns_in": int(len(df.columns)),
            "n_columns_out": int(len(out.columns)), "index_date_column": index_col,
            "columns": self.columns, "hits": self.hits, "hits_truncated": len(self.hits) >= MAX_REPORT_HITS,
            "counts": self.counts, "quasi": self.quasi, "notes": self.notes, "link_column": link_col,
        }
        out.insert(0, "_source_row", rows)
        return out, report


def anonymize_frame(df: pd.DataFrame, filename: str, link_map: dict, use_ner=None):
    return Anonymizer(filename, link_map, use_ner).run(df)
