"""Catalogue of journal profile fields (PRD 4.2).

Only field definitions live in code. Values come from the journal's current
Author Guidelines (extracted with a supporting quote and verified by a person)
and are stored as data, never hard-coded (PRD 4.4).
"""

ARTICLE_TYPES = {
    "original_article": "Original article",
    "case_series": "Case series",
    "brief_report": "Brief report / short communication",
    "case_report": "Case report",
    "letter": "Letter / correspondence",
    "review": "Review",
}

REQ = ["required", "recommended", "optional", "not_accepted"]
REQ_LABELS = {"required": "обязательно", "recommended": "рекомендуется", "optional": "по желанию",
              "not_accepted": "не принимается"}

# (path, type, label, group, hint for the extractor)
GLOBAL_FIELDS = [
    ("scope", "text", "Scope и аудитория", "Общее", "journal aims and scope in one or two sentences"),
    ("language_variant", "enum:UK|US", "Вариант английского", "Общее", "British or American spelling if specified"),
    ("tone", "text", "Тон / профиль журнала", "Общее",
     "clinico-diagnostic vs molecular-mechanistic focus, as stated or evident from scope"),
    ("title.max_chars", "int", "Название: макс. символов", "Заголовок и ключевые слова", "maximum title length"),
    ("title.max_words", "int", "Название: макс. слов", "Заголовок и ключевые слова", ""),
    ("running_title.max_chars", "int", "Running title: макс. символов", "Заголовок и ключевые слова", ""),
    ("keywords.min", "int", "Ключевые слова: мин.", "Заголовок и ключевые слова", ""),
    ("keywords.max", "int", "Ключевые слова: макс.", "Заголовок и ключевые слова", ""),
    ("keywords.mesh", "bool", "Ключевые слова из MeSH", "Заголовок и ключевые слова", ""),
    ("citation.style_name", "text", "Стиль цитирования (как в guidelines)", "Цитирование",
     "e.g. Vancouver, numbered, Harvard"),
    ("citation.in_text", "enum:numeric|author-year", "Ссылки в тексте", "Цитирование", ""),
    ("citation.csl_id", "text", "CSL-стиль (id в репозитории CSL)", "Цитирование",
     "leave empty; set by the author from the CSL repository"),
    ("figures.formats", "list", "Форматы файлов рисунков", "Таблицы и рисунки", "e.g. TIFF, EPS"),
    ("figures.dpi_photo", "int", "Разрешение микрофото, dpi", "Таблицы и рисунки", "halftone / photographs"),
    ("figures.dpi_line", "int", "Разрешение графиков, dpi", "Таблицы и рисунки", "line art / combination"),
    ("figures.color_mode", "text", "Цветовой режим", "Таблицы и рисунки", "RGB / CMYK"),
    ("figures.scale_bars", "bool", "Масштабный отрезок на микрофото обязателен", "Таблицы и рисунки", ""),
    ("figures.caption_rules", "text", "Требования к подписям", "Таблицы и рисунки",
     "e.g. stain and magnification in legends"),
    ("figures.placement", "enum:in_text|separate_files", "Размещение рисунков", "Таблицы и рисунки", ""),
    ("tables.rules", "text", "Требования к таблицам", "Таблицы и рисунки", ""),
    ("statements.ethics", "enum:" + "|".join(REQ), "Одобрение этического комитета", "Обязательные заявления", ""),
    ("statements.consent", "enum:" + "|".join(REQ), "Информированное согласие", "Обязательные заявления", ""),
    ("statements.coi", "enum:" + "|".join(REQ), "Конфликт интересов", "Обязательные заявления", ""),
    ("statements.funding", "enum:" + "|".join(REQ), "Финансирование", "Обязательные заявления", ""),
    ("statements.data_availability", "enum:" + "|".join(REQ), "Доступность данных", "Обязательные заявления", ""),
    ("statements.author_contributions", "enum:" + "|".join(REQ), "Вклад авторов (CRediT)",
     "Обязательные заявления", ""),
    ("statements.acknowledgements", "enum:" + "|".join(REQ), "Благодарности", "Обязательные заявления", ""),
    ("statements.ai_disclosure", "enum:" + "|".join(REQ), "Заявление об использовании ИИ",
     "Обязательные заявления", "disclosure of AI-assisted technologies"),
    ("ai_policy", "text", "Политика журнала по ИИ", "Обязательные заявления",
     "what must be disclosed, where, whether AI may be listed as author"),
    ("reporting_guidelines", "list", "Чек-листы отчётности", "Отчётность", "e.g. CARE, REMARK, STROBE, STARD"),
    ("extras.highlights", "enum:" + "|".join(REQ), "Highlights / Key points", "Дополнительные элементы", ""),
    ("extras.highlights_count", "text", "Highlights: число и длина", "Дополнительные элементы", ""),
    ("extras.what_is_new", "enum:" + "|".join(REQ), "«What is new» / «Key findings»", "Дополнительные элементы", ""),
    ("extras.graphical_abstract", "enum:" + "|".join(REQ), "Graphical abstract", "Дополнительные элементы", ""),
    ("extras.running_title", "enum:" + "|".join(REQ), "Running title", "Дополнительные элементы", ""),
    ("cover_letter.requirements", "list", "Требования к cover letter", "Cover letter",
     "items the cover letter must contain"),
    ("cover_letter.suggested_reviewers", "enum:" + "|".join(REQ), "Предложенные рецензенты", "Cover letter", ""),
    ("cover_letter.editor", "text", "Главный редактор", "Cover letter", "Editor-in-Chief name if given"),
]

TYPE_FIELDS = [
    ("accepted", "bool", "Тип принимается журналом", "whether the journal publishes this article type"),
    ("name", "text", "Название типа в журнале", "how the journal names this article type"),
    ("sections", "list", "Разделы по порядку", "required main-text sections in order"),
    ("words_total", "int", "Лимит слов", "main text word limit"),
    ("words_counted", "text", "Что входит в подсчёт слов", "e.g. excludes abstract, references, legends"),
    ("abstract_words", "int", "Abstract: лимит слов", ""),
    ("abstract_structured", "bool", "Abstract структурированный", ""),
    ("abstract_headings", "list", "Заголовки abstract", "e.g. Aims, Methods and results, Conclusions"),
    ("max_tables", "int", "Макс. таблиц", ""),
    ("max_figures", "int", "Макс. рисунков", ""),
    ("max_tables_figures", "int", "Макс. таблиц + рисунков вместе", ""),
    ("max_references", "int", "Макс. ссылок", ""),
    ("max_authors", "int", "Макс. авторов", ""),
]


def all_fields():
    """path → (type, label, group, hint) for every field including per-article-type ones."""
    out = {p: (t, label, group, hint) for p, t, label, group, hint in GLOBAL_FIELDS}
    for key, tname in ARTICLE_TYPES.items():
        for f, t, label, hint in TYPE_FIELDS:
            out[f"types.{key}.{f}"] = (t, label, f"Тип: {tname}", hint)
    return out


FIELDS = all_fields()


def parse_value(ftype: str, raw):
    """Typed value from a string/JSON value; raises ValueError on mismatch."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if ftype == "int":
        if isinstance(raw, bool):
            raise ValueError("ожидается число")
        if isinstance(raw, (int, float)):
            return int(raw)
        import re
        m = re.search(r"\d[\d,\s]*", str(raw))
        if not m:
            raise ValueError("ожидается число")
        return int(re.sub(r"[,\s]", "", m.group(0)))
    if ftype == "bool":
        if isinstance(raw, bool):
            return raw
        v = str(raw).strip().lower()
        if v in ("true", "yes", "да", "1", "required", "mandatory"):
            return True
        if v in ("false", "no", "нет", "0", "not required"):
            return False
        raise ValueError("ожидается да/нет")
    if ftype == "list":
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        import re
        return [x.strip() for x in re.split(r"[;\n]|,(?![^()]*\))", str(raw)) if x.strip()]
    if ftype.startswith("enum:"):
        options = ftype[5:].split("|")
        v = str(raw).strip()
        for o in options:
            if v.lower() == o.lower():
                return o
        raise ValueError("допустимо: " + ", ".join(options))
    return str(raw).strip()
