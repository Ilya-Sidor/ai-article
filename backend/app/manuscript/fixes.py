"""Where to fix an issue and how (one click where possible).

Every issue of the manuscript, the checks and the export status gets ``fix``: a readable place, a target to open
({"step", "tab", "section"}) and actions the interface can run in place — fill an [уточнить: …] gap, confirm a
number, remove a dead reference, ask the model to repair a passage, translate terms, decide a citation, set
the consent. The author no longer has to work out where a problem lives.
"""

SECTION_ACTIONS = {
    "limitations": ("revise", "Добавь в конец обсуждения абзац об ограничениях исследования (малая серия, "
                              "ретроспективный дизайн, отсутствие валидационной когорты; для case report — одно "
                              "наблюдение). Остальной текст не меняй."),
    "evidence_wording": ("revise", "Смягчи эту формулировку до уровня доказательности находки: «наблюдалось в данной "
                                   "серии», «может указывать», «требует подтверждения». Не меняй числа и ссылки."),
    "cyrillic": ("revise", "Переведи русские слова в этом фрагменте на английский, используя термины из "
                           "терминологии. Остальное не меняй."),
    "cliche": ("revise", "Перефразируй этот фрагмент без клише, сохранив смысл, числа и ссылки."),
    "markup": ("revise", "Перепиши формулу в этом фрагменте обычным текстом с символами Unicode (≥, ≤, ±, χ²)."),
    "abbreviation": ("revise", "Расшифруй аббревиатуру при первом упоминании. Остальное не меняй."),
    "budget": ("revise", "Сократи раздел до бюджета слов, сохранив все числа, ссылки и ключевые утверждения."),
    "ai_repeat": ("revise", "Начни обсуждение с главного результата этой работы, а не с повторения определения из "
                            "введения. Остальной текст не меняй."),
    "novelty_claim": ("revise", "Смягчи утверждение о новизне: похожие случаи уже опубликованы. Напиши, чем этот "
                                "случай отличается от описанных, без слов «впервые»/«first»."),
    "overlap": ("revise", "Перефразируй этот фрагмент своими словами (он совпадает с текстом источника), сохранив "
                          "смысл и ссылку на источник."),
}


def _section_target(issue, sections):
    s = sections.get(issue.get("section"))
    if not s:
        return None, None
    return f"Раздел «{s['heading']}»", {"step": "draft", "tab": "sections", "section": s["key"]}


def annotate(issue, sections):
    """sections: {key: section state}. Returns the issue with ``fix`` added."""
    code, frag = issue.get("code", ""), issue.get("fragment") or ""
    where, goto = _section_target(issue, sections)
    actions = []
    kind = (sections.get(issue.get("section")) or {}).get("kind")

    if code == "todo" and issue.get("section") not in (None, "cover_letter"):
        if kind == "statements":  # gaps of the statements come from the author's data
            actions.append({"type": "goto", "label": "Заполнить в «Данные автора»",
                            "goto": {"step": "draft", "tab": "inputs"}})
        elif frag:
            actions.append({"type": "fill", "find": frag, "label": "Вставить в текст",
                            "placeholder": "впишите: " + issue["message"].split(": ", 1)[-1]})
    elif code == "number_unmatched":
        num = issue["message"].split("число ", 1)[-1].split(" ", 1)[0]
        actions.append({"type": "whitelist", "number": num, "label": "Число верное"})
    elif code in ("unknown_ref", "citation_bad") and issue.get("section") != "cover_letter" and frag:
        actions.append({"type": "remove", "find": frag, "label": "Удалить ссылку из текста"})
    elif code in ("consistency", "abstract_unsupported") and goto:
        actions.append({"type": "revise", "section": issue["section"], "label": "Исправить с помощью ИИ",
                        "instruction": issue.get("suggestion") or "", "selection": frag[:300] if code == "consistency" else None})
        if issue.get("remark"):
            actions.append({"type": "remark", "id": issue["remark"], "status": "dismissed", "label": "Не согласен"})
    elif code in ("ai_style", "ai_rhythm") and goto:
        actions.append({"type": "humanize", "section": issue["section"], "label": "Убрать ИИ-стиль (ИИ)"})
    elif code in SECTION_ACTIONS and goto:
        actions.append({"type": "revise", "section": issue["section"], "label": "Исправить с помощью ИИ",
                        "instruction": SECTION_ACTIONS[code][1],
                        "selection": frag[:300] if code not in ("limitations", "budget") else None})
    elif code == "not_accepted":
        actions.append({"type": "accept", "label": "Принять раздел"})
    elif code == "citation_pending" and issue.get("citation"):
        actions += [{"type": "citation", "id": issue["citation"], "decision": "accepted", "label": "Принять цитату"},
                    {"type": "citation", "id": issue["citation"], "decision": "rejected", "label": "Отклонить"}]
    elif code == "citation_unverified" and issue.get("citation"):
        actions.append({"type": "verify", "id": issue["citation"], "label": "Перепроверить цитату"})

    if code in ("untranslated", "cyrillic_table", "cyrillic_figure"):
        where, goto = "Терминология", {"step": "draft", "tab": "terms"}
        actions.append({"type": "auto_terms", "label": "Перевести автоматически"})
    elif code in ("title", "care_title", "care_keywords"):
        where, goto = "Название и ключевые слова", {"step": "draft", "tab": "front"}
    elif code == "care_consent":
        where, goto = "Данные автора → согласие пациента", {"step": "draft", "tab": "inputs"}
        actions.append({"type": "consent", "label": "Письменное согласие получено"})
    elif code.startswith("care_"):
        kinds = ("discussion",) if code == "care_strengths" else ("case_presentation", "results")
        target = next((s for s in sections.values() if s["kind"] in kinds), None)
        if target:
            where, goto = f"Раздел «{target['heading']}»", {"step": "draft", "tab": "sections", "section": target["key"]}
            actions.append({"type": "revise", "section": target["key"], "label": "Дописать с помощью ИИ",
                            "instruction": f"Добавь недостающий пункт CARE: {issue['message'].split(': ', 1)[-1]}. "
                                           "Используй только данные случая; чего нет в данных — [уточнить: …]."})
    elif code.startswith("titlepage_"):
        where, goto = "Данные автора → авторы и учреждения", {"step": "draft", "tab": "inputs"}
    elif code.startswith("micro_"):
        where, goto = "Таблицы и рисунки → микрофотографии", {"step": "draft", "tab": "assets"}
    elif code == "no_journal":
        where, goto = "Проект → целевой журнал", {"step": "project"}
    elif code.startswith("journal_"):
        where, goto = "Чек-лист журнала", {"step": "outline"}
    elif issue.get("section") == "cover_letter" or code.startswith("cover_"):
        where, goto = "Сопроводительное письмо", {"step": "cover"}
    elif code in ("citation_pending", "citation_unverified"):
        where = where or "Литература"
        goto = goto or {"step": "literature"}
    elif code in ("overlap", "ai_author", "reid_text", "reid_data") and not goto:
        where, goto = "Проверки (этика и заимствования)", {"step": "checks"}
    elif code == "terminology":
        where = "Вся рукопись — выберите одно написание"

    issue["fix"] = {"where": where or issue.get("heading") or "", "goto": goto, "actions": actions}
    return issue


def annotate_all(issues, state):
    sections = state.get("sections") or {}
    return [annotate(i, sections) for i in issues]
