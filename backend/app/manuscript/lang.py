"""Language of the manuscript: English (default) or Russian, set by the journal profile field ``language``.

Facts are computed once, in the English notation of the analysis engine ("OR 0.0074, 95% CI 0.0003–0.21",
"p < 0.001"); a Russian manuscript renders them in Russian notation (decimal comma, "ОШ", "95% ДИ"). The
same fact may therefore appear in both languages of a bilingual journal (the English abstract of a Russian
article keeps the English notation).
"""
import re

LANGUAGES = ("en", "ru")

# effect names as written by facts.build → Russian
_EFFECTS = [("median difference", "разница медиан"), ("epsilon squared", "ε²"), ("Cramér's V", "V Крамера"),
            ("rho", "ρ"), ("OR", "ОШ"), ("HR", "ОР")]
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")

LABELS = {
    "en": {"table": "Table", "figure": "Figure", "table_ref": "Table", "figure_ref": "Figure",
           "keywords": "Keywords", "references": "References", "legends": "Figure legends",
           "running_title": "Running title", "title_todo": "[уточнить: название статьи]",
           "declarations": "Declarations", "abstract": "Abstract", "statements": "Statements"},
    "ru": {"table": "Таблица", "figure": "Рис.", "table_ref": "табл.", "figure_ref": "рис.",
           "keywords": "Ключевые слова", "references": "Литература/References", "legends": "Подписи к рисункам",
           "running_title": "Краткое название", "title_todo": "[уточнить: название статьи]",
           "declarations": "Дополнительная информация", "abstract": "Резюме",
           "statements": "Дополнительная информация"},
}

TESTS_RU = [("Fisher", "точный критерий Фишера"), ("Mann", "критерий Манна–Уитни"),
            ("Kruskal", "критерий Краскела–Уоллиса"), ("Spearman", "ранговая корреляция Спирмена"),
            ("chi", "критерий χ²"), ("χ²", "критерий χ²")]
EVIDENCE_RU = {"significant": "значимая после поправки FDR", "exploratory": "поисковая",
               "descriptive": "описательная"}


def of(template):
    """Manuscript language for a journal template (None → English)."""
    return "ru" if (template or {}).get("language") == "ru" else "en"


def number(text, lang):
    """Decimal comma for Russian: '0.003' → '0,003' (only between digits)."""
    return _DECIMAL.sub(",", text) if lang == "ru" and text else text


def fact(value, lang):
    """A fact value in the notation of the manuscript language."""
    if lang != "ru" or not value:
        return value
    v = value
    for en, ru in _EFFECTS:
        v = re.sub(r"(?<![\w'])" + re.escape(en) + r"(?![\w'])", ru, v)
    v = v.replace("95% CI", "95% ДИ")
    v = number(v, lang)
    return v.replace(", 95% ДИ", "; 95% ДИ")  # the comma is the decimal separator now


def test_name(name, lang):
    if lang != "ru" or not name:
        return name
    return next((ru for key, ru in TESTS_RU if key.lower() in name.lower()), name)


def label(key, lang):
    return LABELS["ru" if lang == "ru" else "en"][key]
