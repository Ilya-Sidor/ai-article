"""Profile applied to a project: manuscript template (FR-4.4), compliance indicator
(FR-4.5) and pre-submission checklist (FR-4.6).

Items that depend on the manuscript text are reported as "pending" until the
manuscript module exists; nothing is shown as passed without an actual check.
"""
from .schema import ARTICLE_TYPES, FIELDS, REQ_LABELS
from .store import status as profile_status, value

STATEMENTS = ["ethics", "consent", "coi", "funding", "data_availability", "author_contributions",
              "acknowledgements", "ai_disclosure"]
EXTRAS = ["highlights", "what_is_new", "graphical_abstract", "running_title"]


def _t(profile, article_type, field, default=None):
    return value(profile, f"types.{article_type}.{field}", default)


def template(profile, article_type):
    """Structure the manuscript module will follow for this journal and article type."""
    missing = []

    def need(path, v):
        if v is None:
            missing.append(FIELDS[path][1] + (f" ({ARTICLE_TYPES.get(article_type)})" if path.startswith("types.")
                                             else ""))
        return v

    tp = f"types.{article_type}."
    accepted = _t(profile, article_type, "accepted")
    statements = [{"key": s, "label": FIELDS[f"statements.{s}"][1],
                   "requirement": value(profile, f"statements.{s}")} for s in STATEMENTS]
    extras = [{"key": e, "label": FIELDS[f"extras.{e}"][1], "requirement": value(profile, f"extras.{e}")}
              for e in EXTRAS]
    return {
        "journal": profile["name"], "journal_id": profile["id"], "profile_status": profile_status(profile),
        "article_type": article_type, "article_type_label": ARTICLE_TYPES.get(article_type),
        "accepted": accepted, "type_name": _t(profile, article_type, "name"),
        "sections": need(tp + "sections", _t(profile, article_type, "sections")) or [],
        "abstract": {"structured": need(tp + "abstract_structured", _t(profile, article_type, "abstract_structured")),
                     "headings": _t(profile, article_type, "abstract_headings") or [],
                     "words": need(tp + "abstract_words", _t(profile, article_type, "abstract_words"))},
        "limits": {
            "words_total": need(tp + "words_total", _t(profile, article_type, "words_total")),
            "words_counted": _t(profile, article_type, "words_counted"),
            "tables": _t(profile, article_type, "max_tables"), "figures": _t(profile, article_type, "max_figures"),
            "tables_figures": _t(profile, article_type, "max_tables_figures"),
            "references": _t(profile, article_type, "max_references"),
            "authors": _t(profile, article_type, "max_authors"),
        },
        "title": {"max_chars": value(profile, "title.max_chars"), "max_words": value(profile, "title.max_words")},
        "keywords": {"min": value(profile, "keywords.min"), "max": value(profile, "keywords.max"),
                     "mesh": value(profile, "keywords.mesh")},
        "language_variant": need("language_variant", value(profile, "language_variant")),
        "tone": value(profile, "tone"),
        "citation": {"style_name": value(profile, "citation.style_name"), "in_text": value(profile, "citation.in_text"),
                     "csl_id": value(profile, "citation.csl_id")},
        "statements": statements, "extras": extras,
        "reporting_guidelines": value(profile, "reporting_guidelines") or [],
        "figures": {k: value(profile, f"figures.{k}") for k in
                    ("formats", "dpi_photo", "dpi_line", "color_mode", "scale_bars", "caption_rules", "placement")},
        "cover_letter": {"requirements": value(profile, "cover_letter.requirements") or [],
                         "suggested_reviewers": value(profile, "cover_letter.suggested_reviewers"),
                         "editor": value(profile, "cover_letter.editor")},
        "ai_policy": value(profile, "ai_policy"),
        "missing": missing,
    }


def compliance(tpl, counts):
    """FR-4.5: current values vs limits. ``counts`` holds what can be measured now (None = not yet)."""
    rows = [
        ("words_total", "Слов в основном тексте", tpl["limits"]["words_total"], counts.get("words")),
        ("abstract_words", "Слов в abstract", tpl["abstract"]["words"], counts.get("abstract_words")),
        ("title_chars", "Символов в названии", tpl["title"]["max_chars"], counts.get("title_chars")),
        ("tables", "Таблиц", tpl["limits"]["tables"], counts.get("tables")),
        ("figures", "Рисунков", tpl["limits"]["figures"], counts.get("figures")),
        ("references", "Ссылок", tpl["limits"]["references"], counts.get("references")),
        ("keywords", "Ключевых слов", tpl["keywords"]["max"], counts.get("keywords")),
    ]
    out = []
    for key, label, limit, current in rows:
        if current is None:
            st = "pending"
        elif limit is None:
            st = "no_limit"
        else:
            st = "over" if current > limit else "near" if current > 0.9 * limit else "ok"
        out.append({"key": key, "label": label, "limit": limit, "current": current, "status": st})
    return out


def checklist(project, profile, tpl, lit_completeness, counts, manual):
    """FR-4.6. status: pass | fail | warn | pending | manual."""
    items = []

    def add(key, group, text, st, detail="", blocking=False):
        if st == "manual" and manual.get(key):
            st, detail = "pass", f"отмечено автором {manual[key]}"
        items.append({"key": key, "group": group, "text": text, "status": st, "detail": detail,
                      "blocking": blocking and st in ("fail", "pending", "manual")})

    ps = tpl["profile_status"]
    add("profile_verified", "Профиль журнала", "Профиль проверен человеком по официальному сайту",
        "pass" if ps == "verified" else "fail",
        {"verified": "", "stale": "проверка старше 90 дней — перепроверьте guidelines",
         "changed": "guidelines изменились после проверки", "outdated_verification": "профиль изменён после проверки",
         "draft": "черновик — не проверен", "empty": "профиль не заполнен"}.get(ps, ps), blocking=True)
    if tpl["missing"]:
        add("profile_complete", "Профиль журнала", "Ключевые параметры профиля заполнены", "warn",
            "не заполнено: " + ", ".join(tpl["missing"]))
    add("type_accepted", "Профиль журнала", f"Тип статьи «{tpl['article_type_label']}» принимается журналом",
        "fail" if tpl["accepted"] is False else "pass" if tpl["accepted"] else "warn",
        "" if tpl["accepted"] else "в профиле не указано", blocking=tpl["accepted"] is False)

    csl = tpl["citation"]["csl_id"]
    add("citation_style", "Литература", "Стиль цитирования соответствует журналу",
        "pass" if csl and counts.get("style") == csl else "fail" if csl else "warn",
        f"в проекте: {counts.get('style')}; по профилю: {csl or 'не задан'}")
    for c in compliance(tpl, counts):
        if c["key"] == "references":
            add("references_limit", "Литература", "Число ссылок в пределах лимита",
                {"ok": "pass", "near": "pass", "over": "fail", "no_limit": "pass", "pending": "pending"}[c["status"]],
                f"{c['current']} из {c['limit'] if c['limit'] is not None else '—'}", blocking=True)
    add("citations_verified", "Литература", "Все принятые цитаты подтверждены проверкой, отозванных статей нет",
        "fail" if lit_completeness["blocking"] else "pass", "; ".join(lit_completeness["blocking"][:3]), blocking=True)

    for c in compliance(tpl, counts):
        if c["key"] in ("words_total", "abstract_words", "title_chars", "tables", "figures", "keywords"):
            add(f"limit_{c['key']}", "Лимиты", f"{c['label']}: в пределах лимита",
                "pending" if c["status"] == "pending" else "fail" if c["status"] == "over" else "pass",
                "появится после генерации разделов (модуль 5)" if c["status"] == "pending"
                else f"{c['current']} из {c['limit']}", blocking=True)
    if tpl["abstract"]["structured"]:
        add("abstract_structure", "Структура", "Abstract структурирован по заголовкам журнала", "pending",
            ", ".join(tpl["abstract"]["headings"]) or "заголовки не указаны", blocking=True)
    if tpl["sections"]:
        add("sections", "Структура", "Разделы рукописи соответствуют журналу", "pending", " → ".join(tpl["sections"]),
            blocking=True)

    for s in tpl["statements"]:
        if s["requirement"] in ("required", "recommended"):
            add(f"statement_{s['key']}", "Заявления", f"{s['label']} ({REQ_LABELS[s['requirement']]})", "pending",
                "генерируется в модуле 5 (FR-5.10)", blocking=s["requirement"] == "required")
    add("ai_not_author", "Этика", "ИИ не указан автором; авторы несут ответственность за содержание (FR-7.2)",
        "manual", "подтвердите вручную", blocking=True)
    add("ethics_approval", "Этика", "Есть одобрение этического комитета или waiver (FR-7.3)", "manual",
        "номер/дата одобрения понадобятся для Methods", blocking=True)
    add("consent", "Этика", "Получено согласие на публикацию (редкие случаи, клинические фото)", "manual")
    for e in tpl["extras"]:
        if e["requirement"] == "required":
            add(f"extra_{e['key']}", "Дополнительные элементы", e["label"], "pending", "будет подготовлено в модуле 5",
                blocking=True)
    for g in tpl["reporting_guidelines"]:
        add(f"reporting_{g}", "Отчётность", f"Заполнен чек-лист {g}", "pending", "FR-5.11 (P1)")
    fig = tpl["figures"]
    if fig.get("dpi_photo") or fig.get("scale_bars"):
        add("figures_specs", "Рисунки", "Рисунки: разрешение, формат, масштабные отрезки", "manual",
            f"dpi ≥ {fig.get('dpi_photo') or '—'}; форматы: {', '.join(fig.get('formats') or []) or '—'}; "
            f"масштабный отрезок: {'обязателен' if fig.get('scale_bars') else 'не указано'}")
    if tpl["cover_letter"]["requirements"]:
        add("cover_letter", "Cover letter", "Cover letter содержит требуемые пункты", "pending",
            "; ".join(tpl["cover_letter"]["requirements"]), blocking=True)
    return items
