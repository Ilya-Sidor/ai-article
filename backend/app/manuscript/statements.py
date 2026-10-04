"""Required statements (FR-5.10) and the AI-use disclosure (FR-7.1, FR-7.2).

Generated deterministically from the author's inputs and from the project's
own log of LLM requests, so the disclosure lists what was actually used.
Missing information becomes an ``[уточнить: …]`` marker that blocks export.
"""
from importlib import metadata

CREDIT_ROLES = ["Conceptualization", "Data curation", "Formal analysis", "Funding acquisition", "Investigation",
                "Methodology", "Project administration", "Resources", "Software", "Supervision", "Validation",
                "Visualization", "Writing – original draft", "Writing – review & editing"]

TASKS = {
    "parse_question": "mapping the authors' research questions to study variables",
    "interpret_finding": "suggesting alternative explanations for exploratory findings",
    "extract_source": "structured extraction of data from the cited articles",
    "compare_finding": "retrieving and comparing published data with the study findings",
    "verify_citation": "checking that cited passages support the corresponding statements",
    "ground_text": "suggesting references for draft text",
    "plan": "proposing the manuscript outline",
    "section": "drafting manuscript text",
    "revise": "language editing and revision of text at the authors' request",
    "front": "suggesting title and keyword options",
    "terms": "translating variable names into English terminology",
}


def ai_usage(pid):
    from ..llm import usage
    used = {}
    for r in usage(pid):
        purpose = r["purpose"].split(":")[0].replace("write_section", "section").replace("revise_section", "revise")
        used.setdefault(purpose, set()).add(r["model"])
    return used


def ai_statement(pid):
    used = ai_usage(pid)
    models = sorted({m for ms in used.values() for m in ms})
    tasks = [TASKS[p] for p in TASKS if p in used]
    try:
        scipy_v = metadata.version("scipy")
    except metadata.PackageNotFoundError:
        scipy_v = "?"
    if not used:
        tool = ("No generative AI was used to write this manuscript text. Statistical analyses were performed with "
                f"reproducible Python code (SciPy {scipy_v}).")
        return tool
    if all("claude" in m for m in models):
        engine = f"large language models (Anthropic Claude; {', '.join(models)})"
    elif all("gemini" in m for m in models):
        engine = f"large language models (Google Gemini; {', '.join(models)})"
    elif all("deepseek" in m for m in models):
        engine = f"large language models (DeepSeek; {', '.join(models)})"
    elif not any("claude" in m or "gemini" in m or "deepseek" in m or ":free" in m or "/" in m for m in models):
        engine = f"an open-weight large language model run locally ({', '.join(models)}, via Ollama)"
    else:
        engine = f"large language models ({', '.join(models)})"
    return (
        f"During the preparation of this work the authors used AI Article, a manuscript preparation tool based on "
        f"{engine}, for " + "; ".join(tasks) + ". "
        "Statistical analyses were not performed by the language model: all reported numbers were computed by "
        f"deterministic, reproducible Python code (SciPy {scipy_v}), and the analysis script is available. "
        "Each literature citation was checked against the text of the cited source. The authors reviewed and "
        "edited all content, take full responsibility for the content of the publication, and no AI tool is "
        "listed as an author.")


# CRediT roles → the contribution categories Russian journals ask for («Участие авторов»)
CREDIT_RU = [("Концепция и дизайн исследования", ("Conceptualization", "Methodology")),
             ("Сбор и обработка материала", ("Data curation", "Investigation", "Resources")),
             ("Статистическая обработка данных", ("Formal analysis", "Software", "Validation")),
             ("Написание текста", ("Writing – original draft", "Visualization")),
             ("Редактирование", ("Writing – review & editing", "Supervision", "Project administration"))]


def ai_statement_ru(pid):
    """«Использование инструментов ИИ» по четырём пунктам, которые требуют российские журналы."""
    used = ai_usage(pid)
    models = sorted({m for ms in used.values() for m in ms})
    try:
        scipy_v = metadata.version("scipy")
    except metadata.PackageNotFoundError:
        scipy_v = "?"
    tool = f"приложения AI Article на основе больших языковых моделей ({', '.join(models)})" if models else ""
    text_used = [p for p in ("plan", "section", "revise", "front") if p in used]
    lit_used = [p for p in ("compare_finding", "verify_citation", "ground_text", "extract_source") if p in used]
    return " ".join([
        "При статистической обработке данных ИИ не использовался: все расчёты выполнены воспроизводимым кодом "
        f"Python (SciPy {scipy_v}), скрипт анализа прилагается.",
        (f"При написании текста статьи использовались языковые модели {tool} (план, черновики разделов и правка "
         "текста по указаниям авторов); авторы проверили и отредактировали весь текст и несут за него полную "
         "ответственность." if text_used else "При написании текста статьи ИИ не использовался."),
        "Для создания таблиц и графиков ИИ не использовался: они построены программно из результатов анализа.",
        (f"При формировании списка литературы языковые модели ({', '.join(models)}) использовались для поиска "
         "фрагментов источников и проверки соответствия цитат их тексту; оформление списка выполнено программно "
         "(стиль CSL)." if lit_used else "Для формирования и форматирования списка литературы ИИ не использовался."),
    ])


def _todo(what):
    return f"[уточнить: {what}]"


def build(pid, inputs, template, lang="en"):
    """Source text of the Statements section (### headings)."""
    if lang == "ru":
        return build_ru(pid, inputs, template)
    req = {s["key"]: s["requirement"] for s in (template or {}).get("statements", [])}

    def wanted(key):
        return req.get(key) in (None, "required", "recommended", "optional")

    parts = []
    e = inputs.get("ethics") or {}
    if wanted("ethics"):
        if e.get("waiver"):
            body = f"The requirement for ethical approval was waived by {e.get('committee') or _todo('комитет')}"
            body += f" ({e['approval_number']})." if e.get("approval_number") else "."
        elif e.get("committee"):
            body = (f"The study was approved by {e['committee']} (approval No. "
                    f"{e.get('approval_number') or _todo('номер одобрения')}"
                    + (f", {e['approval_date']}" if e.get("approval_date") else "") + ").")
        else:
            body = _todo("этический комитет и номер одобрения или waiver")
        parts.append(("Ethics approval", body))
    c = inputs.get("consent") or {}
    if wanted("consent"):
        body = {"written": "Written informed consent for publication was obtained from all patients.",
                "waived": "The requirement for informed consent was waived because of the retrospective design and "
                          "the use of anonymised archival material.",
                "not_applicable": "Not applicable."}.get(c.get("status"), _todo("информированное согласие"))
        if c.get("details"):
            body += " " + c["details"]
        parts.append(("Consent", body))
    if wanted("coi"):
        parts.append(("Conflict of interest", inputs.get("coi") or _todo("конфликт интересов")))
    if wanted("funding"):
        parts.append(("Funding", inputs.get("funding") or _todo("финансирование (или «This research received no "
                                                               "specific funding»)")))
    if wanted("data_availability"):
        parts.append(("Data availability", inputs.get("data_availability") or
                      "The anonymised dataset and the analysis script are available from the corresponding author "
                      "on reasonable request."))
    if wanted("author_contributions"):
        authors = inputs.get("authors") or []
        if authors:
            body = " ".join(f"{a['name']}: {', '.join(a.get('roles') or []) or _todo('роли CRediT')}."
                            for a in authors)
        else:
            body = _todo("авторы и их роли CRediT")
        parts.append(("Author contributions (CRediT)", body))
    if wanted("acknowledgements") and inputs.get("acknowledgements"):
        parts.append(("Acknowledgements", inputs["acknowledgements"]))
    if inputs.get("patient_perspective"):  # CARE 12 (case reports)
        parts.append(("Patient perspective", inputs["patient_perspective"]))
    if wanted("ai_disclosure"):
        parts.append(("Declaration of generative AI use", ai_statement(pid)))
    return "\n\n".join(f"### {h}\n\n{b}" for h, b in parts)


def build_ru(pid, inputs, template):
    """«Дополнительная информация» русскоязычной статьи."""
    req = {s["key"]: s["requirement"] for s in (template or {}).get("statements", [])}

    def wanted(key):
        return req.get(key) in (None, "required", "recommended", "optional")

    parts = []
    e = inputs.get("ethics") or {}
    if wanted("ethics"):
        if e.get("waiver"):
            body = f"Необходимость одобрения этическим комитетом отменена: {e.get('committee') or _todo('комитет')}"
            body += f" ({e['approval_number']})." if e.get("approval_number") else "."
        elif e.get("committee"):
            body = (f"Исследование одобрено: {e['committee']} (протокол № "
                    f"{e.get('approval_number') or _todo('номер одобрения')}"
                    + (f" от {e['approval_date']}" if e.get("approval_date") else "") + "). Исследование выполнено "
                    "в соответствии с Хельсинкской декларацией.")
        else:
            body = _todo("этический комитет и номер одобрения или отмена одобрения")
        parts.append(("Соответствие принципам этики", body))
    c = inputs.get("consent") or {}
    if wanted("consent"):
        body = {"written": "Получено письменное информированное согласие пациентов на публикацию.",
                "waived": "Информированное согласие не требовалось: ретроспективное исследование обезличенного "
                          "архивного материала.",
                "not_applicable": "Не применимо."}.get(c.get("status"), _todo("информированное согласие"))
        if c.get("details"):
            body += " " + c["details"]
        parts.append(("Информированное согласие", body))
    if wanted("coi"):
        parts.append(("Конфликт интересов", inputs.get("coi") or _todo("конфликт интересов (или «Авторы заявляют "
                                                                         "об отсутствии конфликта интересов»)")))
    if wanted("funding"):
        parts.append(("Финансирование", inputs.get("funding") or _todo("финансирование (или «Исследование не "
                                                                      "имело спонсорской поддержки»)")))
    if wanted("data_availability"):
        parts.append(("Доступность данных", inputs.get("data_availability") or
                      "Обезличенные данные и скрипт анализа доступны у автора, ответственного за переписку, "
                      "по обоснованному запросу."))
    if wanted("author_contributions"):
        authors = inputs.get("authors") or []
        if authors:
            lines = []
            for label, roles in CREDIT_RU:
                names = [a["name"] for a in authors if set(a.get("roles") or []) & set(roles)]
                if names:
                    lines.append(f"{label} — {', '.join(names)}.")
            body = "\n".join(lines) or _todo("роли авторов")
        else:
            body = _todo("авторы и их участие")
        parts.append(("Участие авторов", body))
    if wanted("acknowledgements") and inputs.get("acknowledgements"):
        parts.append(("Благодарности", inputs["acknowledgements"]))
    if inputs.get("patient_perspective"):  # CARE 12 (case reports)
        parts.append(("Мнение пациента", inputs["patient_perspective"]))
    if wanted("ai_disclosure"):
        parts.append(("Использование инструментов ИИ", ai_statement_ru(pid)))
    return "\n\n".join(f"### {h}\n\n{b}" for h, b in parts)
