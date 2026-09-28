"""AI-use transparency and ethics reminders (FR-7.1 – FR-7.3)."""
import re

from ..manuscript.statements import TASKS, ai_statement

AI_NAMES = re.compile(r"\b(claude|chatgpt|gpt-?\d*|gemini|copilot|bard|llama|anthropic|openai|ai article|"
                      r"large language model|llm|artificial intelligence)\b", re.I)


def usage_log(pid):
    from ..llm import usage
    rows = usage(pid)
    summary = {}
    for r in rows:
        key = r["purpose"].split(":")[0]
        s = summary.setdefault(key, {"purpose": key, "calls": 0, "models": set(), "chars": 0})
        s["calls"] += 1
        s["models"].add(r["model"])
        s["chars"] += r.get("chars", 0)
    labels = {"write_section": TASKS["section"], "revise_section": TASKS["revise"],
              "cover_letter": "drafting the cover letter", "revise_cover_letter": "revising the cover letter", **TASKS}
    out = [dict(s, models=sorted(s["models"]), task=labels.get(s["purpose"], s["purpose"])) for s in summary.values()]
    return {"entries": rows[-200:], "summary": sorted(out, key=lambda s: -s["calls"]), "total_calls": len(rows)}


def authorship_issues(inputs):
    issues = []
    for a in (inputs or {}).get("authors") or []:
        if AI_NAMES.search(a.get("name", "")):
            issues.append({"section": None, "heading": "Авторы", "severity": "blocking", "code": "ai_author",
                           "message": f"«{a['name']}» — ИИ не может быть автором (ICMJE, FR-7.2)", "fragment": "",
                           "suggestion": "укажите использование ИИ в заявлении, а не в списке авторов"})
    return issues


def reminders(project, inputs, n_cases, has_figures, template):
    """FR-7.3: what must be in place before submission, given this study."""
    inputs = inputs or {}
    e = inputs.get("ethics") or {}
    c = (inputs.get("consent") or {}).get("status")
    small = project["article_type"] == "case_report" or (n_cases or 0) <= 5
    items = [
        {"key": "ethics", "text": "Одобрение этического комитета или официальный waiver",
         "status": "ok" if (e.get("committee") and (e.get("approval_number") or e.get("waiver"))) else "missing",
         "detail": e.get("committee") or "не указано на шаге 7 → «Данные автора»"},
        {"key": "consent", "text": "Информированное согласие на публикацию",
         "status": "ok" if c == "written" else ("attention" if small else ("ok" if c else "missing")),
         "detail": ("для редких случаев и серий ≤ 5 журналы обычно требуют письменное согласие пациента на публикацию"
                    if small and c != "written" else {"waived": "не требовалось (ретроспективно, анонимизировано)",
                                                      "not_applicable": "не применимо", "written": "получено"}.get(c, "не указано"))},
        {"key": "photos", "text": "Согласие на клинические фотографии",
         "status": "attention" if has_figures.get("clinical") else "na",
         "detail": "клинических фото в рукописи нет" if not has_figures.get("clinical") else "нужно отдельное согласие"},
        {"key": "anonymised", "text": "Данные обезличены: отчёт анонимизации подтверждён",
         "status": "ok" if project["anonymization"]["status"] == "confirmed" else "missing",
         "detail": project["anonymization"].get("confirmed_at") or ""},
        {"key": "ai_policy", "text": "Использование ИИ раскрыто по политике журнала",
         "status": "ok" if (template or {}).get("ai_policy") else "attention",
         "detail": (template or {}).get("ai_policy") or "политика журнала по ИИ не заполнена в профиле — проверьте guidelines"},
    ]
    return items


def statement_preview(pid):
    return ai_statement(pid)
