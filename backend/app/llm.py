"""Claude integration with a privacy gate (FR-1.2, NFR-3, NFR-7).

Rules enforced here:
* no request is sent for a project whose anonymization report is not confirmed;
* only variable names, types, levels and computed statistics are sent — never
  case-level rows;
* the outgoing-request log records metadata only (time, purpose, model, size),
  never content.
"""
import json
import re

import anthropic

from .config import MODEL_EXTRACTION, MODEL_REASONING
from .storage import now_iso


class LLMUnavailable(RuntimeError):
    pass


class PrivacyGateError(PermissionError):
    pass


_client = None


def client():
    global _client
    if _client is None:
        try:
            _client = anthropic.Anthropic()
        except Exception as exc:  # no credentials configured
            raise LLMUnavailable("Claude API не настроен: задайте ANTHROPIC_API_KEY или выполните `ant auth login`") from exc
    return _client


def status():
    try:
        client()
        return {"available": True, "reasoning_model": MODEL_REASONING, "extraction_model": MODEL_EXTRACTION}
    except LLMUnavailable as exc:
        return {"available": False, "reason": str(exc), "reasoning_model": MODEL_REASONING,
                "extraction_model": MODEL_EXTRACTION}


def _gate(project: dict):
    if project["anonymization"]["status"] != "confirmed":
        raise PrivacyGateError("запрос к внешней LLM заблокирован: отчёт об анонимизации не подтверждён")


def _log(project_id: str, purpose: str, model: str, payload: str):
    """Metadata only (NFR-7): time, project, purpose, model, size — never content."""
    from . import db
    with db.engine().begin() as conn:
        conn.execute(db.llm_requests.insert().values(project_id=project_id, purpose=purpose, model=model,
                                                     chars=len(payload)))


def usage(project_id):
    from . import db
    with db.engine().connect() as conn:
        rows = conn.execute(db.llm_requests.select().where(db.llm_requests.c.project_id == project_id)
                            .order_by(db.llm_requests.c.id)).mappings().all()
    return [{"ts": r["ts"].isoformat() if r["ts"] else None, "project": r["project_id"], "purpose": r["purpose"],
             "model": r["model"], "chars": r["chars"]} for r in rows]


def request(project, purpose, model, gate=True, **kwargs):
    """One Messages API call with the privacy gate, metadata-only logging and error mapping.

    ``gate`` is False only for calls that carry no patient-derived data (e.g. reading
    published papers)."""
    if gate:
        _gate(project)
    _log(project["id"], purpose, model, json.dumps([kwargs.get("system"), kwargs.get("messages")], default=str))
    try:
        response = client().beta.messages.create(model=model, **kwargs)
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable("Claude API: ошибка аутентификации — проверьте ключ") from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable("Claude API: превышен лимит запросов, повторите позже") from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailable(f"Claude API вернул ошибку {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable("нет соединения с Claude API") from exc
    if response.stop_reason == "refusal":
        raise LLMUnavailable("модель отклонила запрос")
    if response.stop_reason == "max_tokens":
        raise LLMUnavailable("ответ модели обрезан по лимиту токенов")
    return response


def final_json(response):
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _call(project, purpose, model, system, user, schema, max_tokens, gate=True, **extra):
    response = request(
        project, purpose, model, gate=gate, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}, **extra.pop("output_config", {})},
        **extra,
    )
    return final_json(response)


# ---------------------------------------------------------------------------
# FR-2.12: author's question in natural language → pair of variables
# ---------------------------------------------------------------------------

def _variables_brief(dictionary):
    return [{"name": v["name"], "type": v["vtype"], "group": v["group"], "levels": v.get("levels", [])[:12]}
            for v in dictionary if v.get("include") and v["vtype"] not in ("identifier", "text")]


def parse_question(project: dict, question: str, dictionary: list) -> dict:
    variables = _variables_brief(dictionary)
    names = [v["name"] for v in variables]
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "enum": names + [""]},
            "b": {"type": "string", "enum": names + [""]},
            "rationale": {"type": "string"},
        },
        "required": ["a", "b", "rationale"], "additionalProperties": False,
    }
    system = ("You map a pathologist's research question to exactly two variables of their data dictionary. "
              "Choose the two variables whose association answers the question. If the question cannot be "
              "expressed as an association between two listed variables, return empty strings for a and b and "
              "explain why in rationale. Write rationale in the language of the question.")
    user = json.dumps({"question": question, "variables": variables}, ensure_ascii=False)
    return _call(project, "parse_question", MODEL_EXTRACTION, system, user, schema, max_tokens=2000,
                 output_config={"effort": "low"})


def match_question_locally(question: str, dictionary: list) -> dict:
    """Fallback without an LLM: pick variables whose names occur in the question."""
    q = question.lower()
    hits = []
    for v in _variables_brief(dictionary):
        tokens = [t for t in re.split(r"[\s,;:()/]+", v["name"].lower()) if len(t) >= 2]
        score = sum(1 for t in tokens if t in q)
        if score:
            hits.append((score, q.find(tokens[0]) if tokens else 0, v["name"]))
    hits.sort(key=lambda x: (-x[0], x[1]))
    if len(hits) < 2:
        return {"a": "", "b": "", "rationale": "Не удалось однозначно определить два признака — выберите их вручную."}
    return {"a": hits[0][2], "b": hits[1][2], "rationale": "Сопоставлено по названиям признаков (без LLM)."}


# ---------------------------------------------------------------------------
# FR-2.9: interpretation of a finding. Numbers come only from the card.
# ---------------------------------------------------------------------------

INTERPRETATION_SCHEMA = {
    "type": "object",
    "properties": {
        "interpretation": {"type": "string"},
        "alternative_explanations": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "next_checks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["interpretation", "alternative_explanations", "limitations", "next_checks"],
    "additionalProperties": False,
}

INTERPRETATION_SYSTEM = """You assist a practising pathologist who is analysing their own case series.
You receive one finding card whose numbers were computed by a statistics engine. Your job is interpretation only.

Rules:
- Never compute, round, re-derive or invent numbers. If you mention a number, copy it exactly from the card.
- Match the strength of your language to the evidence level: "descriptive" and "exploratory" findings must be
  described as observations in this series that need validation, never as established associations.
- Do not cite literature or claim what "is known"; literature comparison happens in a separate step with the
  author's own sources.
- Alternative explanations should be concrete for surgical pathology: confounding by histotype or site,
  selection/referral bias, pre-analytical and staining artefacts, antibody clone differences, scoring thresholds,
  small numbers.
- Write in Russian, concisely, in the register of a pathology colleague."""


def interpret_finding(project: dict, finding: dict, context: dict) -> dict:
    card = {k: finding.get(k) for k in ("title", "evidence", "statement", "statistics", "limitations")}
    payload = json.dumps({"finding": card, "series": context}, ensure_ascii=False)
    result = _call(project, "interpret_finding", MODEL_REASONING, INTERPRETATION_SYSTEM, payload,
                   INTERPRETATION_SCHEMA, max_tokens=16000, output_config={"effort": "medium"},
                   betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    result["unverified_numbers"] = unverified_numbers(result, payload)
    result["model"] = MODEL_REASONING
    result["created_at"] = now_iso()
    return result


_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def unverified_numbers(result: dict, source: str) -> list:
    """Numbers in the LLM text that do not occur in the card it was given."""
    allowed = {m.group(0).replace(",", ".") for m in _NUM.finditer(source)}
    text = " ".join([result["interpretation"], *result["alternative_explanations"], *result["limitations"],
                     *result["next_checks"]])
    return sorted({m.group(0) for m in _NUM.finditer(text) if m.group(0).replace(",", ".") not in allowed})
