"""LLM integration with a privacy gate (FR-1.2, NFR-3, NFR-7).

Providers (chosen by the administrator in the UI, or ``AI_ARTICLE_LLM_PROVIDER``):
* ``anthropic`` (default) — Claude API;
* ``gemini`` — Google Gemini API (free tier available; on the free tier Google may use
  requests to improve its products, so only anonymised data may be sent — the privacy
  gate below guarantees that for patient-derived content);
* ``ollama`` — a local open-weight model served by Ollama (free, nothing leaves the computer).
Structured outputs use each provider's JSON-schema mode.

Rules enforced here:
* no request is sent for a project whose anonymization report is not confirmed;
* only variable names, types, levels and computed statistics are sent — never
  case-level rows;
* the outgoing-request log records metadata only (time, purpose, model, size),
  never content.
"""
import json
import os
import re
import threading

import anthropic
import httpx

from .config import MODEL_EXTRACTION, MODEL_REASONING
from .storage import now_iso


def provider():
    from . import settings
    return (settings.get("llm.provider", "AI_ARTICLE_LLM_PROVIDER", "anthropic") or "anthropic").lower()


GEMINI_MODELS = ["gemini-3.8-flash", "gemini-2.5-pro", "gemini-3.5-flash", "gemini-2.5-flash"]
GEMINI_DEFAULT = "gemini-3.8-flash"


def gemini_models():
    from . import settings
    return (settings.get("llm.gemini_model_reasoning", "AI_ARTICLE_GEMINI_MODEL_REASONING", GEMINI_DEFAULT),
            settings.get("llm.gemini_model_extraction", "AI_ARTICLE_GEMINI_MODEL_EXTRACTION", GEMINI_DEFAULT))


def gemini_key():
    from . import settings
    return settings.get_secret("llm.gemini_api_key", "GEMINI_API_KEY")


def openrouter_key():
    from . import settings
    return settings.get_secret("llm.openrouter_api_key", "OPENROUTER_API_KEY")


# Default free chains ("provider:model"), strongest first. The order is a judgement call and is
# editable by the administrator; extraction starts lower to save the strongest models' quota.
DEFAULT_CHAIN_REASONING = [
    "gemini:gemini-3.8-flash", "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free", "gemini:gemini-3.7-flash",
    "openrouter:nvidia/nemotron-3-super-120b-a12b:free", "gemini:gemini-3.6-flash", "openrouter:qwen/qwen3.8-27b:free",
    "gemini:gemini-3.5-flash", "gemini:gemini-2.5-pro", "openrouter:openrouter/free", "gemini:gemini-2.5-flash",
]
DEFAULT_CHAIN_EXTRACTION = [
    "openrouter:nvidia/nemotron-3-super-120b-a12b:free", "gemini:gemini-3.5-flash", "openrouter:qwen/qwen3.8-27b:free",
    "gemini:gemini-2.5-flash", "openrouter:dots-studio/dots-3-note-preview:free", "gemini:gemini-3.5-flash-lite",
    "openrouter:openrouter/free", "gemini:gemini-3.1-flash-lite", "gemini:gemini-2.5-flash-lite",
]


def chains():
    from . import settings

    def parse(key, default):
        raw = settings.get(key)
        items = [x.strip() for x in raw.splitlines() if x.strip()] if raw else list(default)
        return [x for x in items if x.split(":", 1)[0] in ("gemini", "openrouter")]
    return parse("llm.chain_reasoning", DEFAULT_CHAIN_REASONING), parse("llm.chain_extraction", DEFAULT_CHAIN_EXTRACTION)


def _chain_for(model):
    """Ordered entries for a request; depends on the provider mode."""
    p = provider()
    if p == "gemini":
        return ["gemini:" + m for m in _gemini_chain(model)]
    reasoning, extraction = chains()
    entries = reasoning if model == MODEL_REASONING else extraction
    if p == "openrouter":
        entries = [e for e in entries if e.startswith("openrouter:")]
    have = {"gemini": bool(gemini_key()), "openrouter": bool(openrouter_key())}
    return [e for e in entries if have[e.split(":", 1)[0]]]


def ollama_url():
    return os.environ.get("AI_ARTICLE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_model():
    return os.environ.get("AI_ARTICLE_OLLAMA_MODEL", "qwen3:8b")


def effective_model(model):
    p = provider()
    if p == "ollama":
        return ollama_model()
    if p == "gemini":
        reasoning, extraction = gemini_models()
        return reasoning if model == MODEL_REASONING else extraction
    if p in ("free", "openrouter"):
        entries = [e for e in _chain_for(model) if e not in exhausted_models()] or _chain_for(model)
        return entries[0].split(":", 1)[1] if entries else model
    return model


def context_budget_chars():
    """How much source text (papers, guidelines) one request may carry."""
    if provider() == "ollama":
        return int(os.environ.get("AI_ARTICLE_OLLAMA_MAX_CHARS", "60000"))
    if provider() in ("free", "openrouter"):
        return 240_000  # chain may fall back to 128–262k-token models
    return 400_000  # Claude and Gemini: 1M-token context


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
    if provider() in ("free", "openrouter"):
        r, e = effective_model(MODEL_REASONING), effective_model(MODEL_EXTRACTION)
        base = {"provider": provider(), "reasoning_model": r, "extraction_model": e, "local": False,
                "keys": {"gemini": bool(gemini_key()), "openrouter": bool(openrouter_key())}}
        if not (_chain_for(MODEL_REASONING) or _chain_for(MODEL_EXTRACTION)):
            return {**base, "available": False,
                    "reason": "не заданы ключи Gemini/OpenRouter — администратор вводит их в «Аккаунт → ИИ-модели»"}
        return {**base, "available": True, "exhausted": exhausted_models()}
    if provider() == "gemini":
        reasoning, extraction = gemini_models()
        base = {"provider": "gemini", "reasoning_model": reasoning, "extraction_model": extraction, "local": False}
        if not gemini_key():
            return {**base, "available": False,
                    "reason": "не задан ключ Google Gemini API — администратор вводит его в разделе «Аккаунт → ИИ-модели»"}
        return {**base, "available": True, "exhausted": exhausted_models()}
    if provider() == "ollama":
        m = ollama_model()
        base = {"provider": "ollama", "reasoning_model": m, "extraction_model": m, "local": True}
        try:
            tags = _http_get(f"{ollama_url()}/api/tags").json().get("models", [])
        except Exception:
            return {**base, "available": False,
                    "reason": f"локальная модель недоступна: Ollama не запущен ({ollama_url()})"}
        if not any(t.get("name") == m or t.get("model") == m for t in tags):
            return {**base, "available": False, "reason": f"модель {m} не загружена в Ollama"}
        return {**base, "available": True}
    try:
        client()
        return {"provider": "anthropic", "available": True, "reasoning_model": MODEL_REASONING,
                "extraction_model": MODEL_EXTRACTION, "local": False}
    except LLMUnavailable as exc:
        return {"provider": "anthropic", "available": False, "reason": str(exc), "reasoning_model": MODEL_REASONING,
                "extraction_model": MODEL_EXTRACTION, "local": False}


def _http_get(url):
    return httpx.get(url, timeout=10)


def _http_post(url, payload, timeout):
    """Single HTTP entry point for the local model (patched in tests)."""
    return httpx.post(url, json=payload, timeout=timeout)


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
    return _clean_strings(json.loads(text))


def _ollama_call(project, purpose, model, system, user, schema, max_tokens, gate=True):
    """Structured request to the local model; the JSON schema is enforced by Ollama."""
    if gate:
        _gate(project)
    m = ollama_model()
    _log(project["id"], purpose, m, system + user)
    think = os.environ.get("AI_ARTICLE_OLLAMA_THINK", "auto")
    payload = {
        "model": m, "stream": False, "format": schema,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "think": (model == MODEL_REASONING) if think == "auto" else think == "1",
        "options": {"temperature": 0.2, "num_ctx": int(os.environ.get("AI_ARTICLE_OLLAMA_NUM_CTX", "24576")),
                    "num_predict": min(max_tokens, 8192)},
    }
    try:
        r = _http_post(f"{ollama_url()}/api/chat", payload, timeout=900)
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"локальная модель недоступна: запустите Ollama ({ollama_url()})") from exc
    if r.status_code != 200:
        detail = r.text[:300]
        raise LLMUnavailable(f"локальная модель вернула ошибку {r.status_code}: {detail}")
    data = r.json()
    if data.get("done_reason") == "length":
        raise LLMUnavailable("ответ локальной модели обрезан по длине — сократите запрос")
    try:
        return _clean_strings(json.loads(data["message"]["content"]))
    except (KeyError, ValueError) as exc:
        raise LLMUnavailable("локальная модель вернула некорректный JSON") from exc


_gemini = {}
_local = threading.local()
# Free-tier quotas are per model on Gemini and per account on OpenRouter; when one is exhausted
# the next entry of the chain answers. Gemini-only mode walks this list from the configured model.
GEMINI_CHAIN = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-pro",
                "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
_exhausted = {}  # "provider:model" or "openrouter:*" -> (until_utc, message)
OPENROUTER_URL = "https://openrouter.ai/api/v1"
_or_caps = {"ts": 0, "caps": {}}


class _Skip(Exception):
    """This model cannot answer now; try the next entry of the chain."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def gemini_client(key):
    if key not in _gemini:
        from google import genai
        _gemini.clear()
        _gemini[key] = genai.Client(api_key=key)
    return _gemini[key]


def used_model(default):
    """The model that actually answered the last request in this thread (fallbacks included)."""
    return getattr(_local, "model", None) or default


def _next_quota_reset():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    return (pt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(ZoneInfo("UTC"))


def _block(entry, message, minutes=None):
    from datetime import datetime, timedelta, timezone
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes else _next_quota_reset()
    _exhausted[entry] = (until, message[:300])


def exhausted_models():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for m in [m for m, (until, _) in _exhausted.items() if until <= now]:
        _exhausted.pop(m, None)
    out = {}
    for m, (until, msg) in _exhausted.items():
        out[m.split(":", 1)[1] if m.startswith("gemini:") else m] = {"until": until.isoformat(timespec="minutes"),
                                                                     "message": msg}
        out[m] = out[m.split(":", 1)[1] if m.startswith("gemini:") else m]
    return out


def _is_blocked(entry):
    ex = exhausted_models()
    return entry in ex or (entry.startswith("openrouter:") and "openrouter:*" in ex)


def _classify(exc):
    """(kind, status, message) for errors of both google-genai API generations, by duck typing."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    msg = str(exc)
    if isinstance(status, int):
        if status == 429:
            return ("daily" if re.search(r"per ?day|daily|RequestsPerDay", msg, re.I) else "rate"), status, msg
        if status in (401, 403):
            return "auth", status, msg
        if status == 400 and "schema" in msg.lower():
            return "schema", status, msg
        if status == 404:
            return "model", status, msg
        return "status", status, msg
    name = type(exc).__name__.lower()
    if "connection" in name or "timeout" in name or isinstance(exc, (httpx.HTTPError, OSError)):
        return "connection", None, msg
    return "unknown", None, f"{type(exc).__name__}: {msg}"


def _retry_delay(exc, attempt):
    body = getattr(exc, "details", None) or getattr(exc, "body", None) or {}
    try:
        for d in (body.get("error", {}) if isinstance(body, dict) else {}).get("details", []) or []:
            if "retryDelay" in d:
                return min(float(str(d["retryDelay"]).rstrip("s")), 90.0)
    except (AttributeError, TypeError, ValueError):
        pass
    return min(10.0 * 2 ** attempt, 60.0)


def _gemini_chain(model):
    reasoning, extraction = gemini_models()
    first = reasoning if model == MODEL_REASONING else extraction
    tail = [m for m in GEMINI_CHAIN if m != first]
    if first in GEMINI_CHAIN:
        tail = GEMINI_CHAIN[GEMINI_CHAIN.index(first) + 1:]
    return [first] + tail


def _json_prompt(system, schema):
    return system + "\n\nReturn only a JSON object (no prose, no code fences) matching this JSON schema:\n" + \
        json.dumps(schema, ensure_ascii=False)


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_strings(obj):
    """Models writing LaTeX inside JSON strings ("\\geq", "\\beta", "\\frac") produce control characters when
    the JSON is decoded ("\\b" is backspace, "\\f" form feed). Restore those backslashes and drop other control
    characters: they are invalid in .docx XML and never intended."""
    if isinstance(obj, str):
        if not _CTRL.search(obj):
            return obj
        return _CTRL.sub("", obj.replace("\x08", "\\b").replace("\x0c", "\\f"))
    if isinstance(obj, list):
        return [_clean_strings(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _clean_strings(v) for k, v in obj.items()}
    return obj


def _parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return _clean_strings(json.loads(t))
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            return _clean_strings(json.loads(m.group(0)))
        raise


def _gemini_once(entry, model_id, system, user, schema, max_tokens, reasoning):
    import time
    key = gemini_key()
    response_format = {"type": "text", "mime_type": "application/json", "schema": schema}
    body = {"model": model_id, "system_instruction": system, "input": user, "response_format": response_format,
            "generation_config": {"max_output_tokens": min(max_tokens, 65536),
                                  "thinking_level": "high" if reasoning else "low"}}
    attempt = 0
    while True:
        try:
            interaction = gemini_client(key).interactions.create(**body, timeout=900)
            break
        except Exception as exc:
            kind, status, msg = _classify(exc)
            if kind == "rate" and attempt < 3:
                time.sleep(_retry_delay(exc, attempt))
                attempt += 1
                continue
            if kind == "daily":
                _block(entry, msg)
            if kind == "schema" and "schema" in body["response_format"]:
                body.update(response_format={"type": "text", "mime_type": "application/json"},
                            system_instruction=_json_prompt(system, schema))
                continue
            if kind == "auth":
                _block("gemini:" + model_id, "ключ Gemini отклонён", minutes=10)
                raise _Skip("auth", "Gemini API отклонил ключ") from exc
            if kind == "connection":
                raise _Skip("connection", "нет соединения с Gemini API — проверьте интернет/VPN") from exc
            if kind in ("daily", "rate", "model", "status"):
                raise _Skip(kind, msg) from exc
            raise _Skip("unknown", f"ошибка Gemini API: {msg[:200]}") from exc
    try:
        return _parse_json(getattr(interaction, "output_text", None) or "")
    except ValueError as exc:
        raise _Skip("invalid", "Gemini вернул некорректный JSON") from exc


def _openrouter_caps():
    """model id -> set of supported parameters, from the public catalogue (cached for a day)."""
    import time
    if time.time() - _or_caps["ts"] > 86400 or not _or_caps["caps"]:
        try:
            data = _http_get(f"{OPENROUTER_URL}/models").json().get("data", [])
            _or_caps["caps"] = {m["id"]: set(m.get("supported_parameters") or []) for m in data}
            _or_caps["ts"] = time.time()
        except Exception:
            _or_caps["ts"] = time.time() - 86400 + 300  # retry in 5 minutes
    return _or_caps["caps"]


def _or_post(url, body, headers, timeout):
    """Single HTTP entry point for OpenRouter (patched in tests)."""
    return httpx.post(url, json=body, headers=headers, timeout=timeout)


def _openrouter_once(entry, model_id, system, user, schema, max_tokens, reasoning):
    import time
    caps = _openrouter_caps().get(model_id, set())
    structured = "structured_outputs" in caps
    body = {"model": model_id, "max_tokens": min(max_tokens, 32000),
            "messages": [{"role": "system", "content": system if structured else _json_prompt(system, schema)},
                         {"role": "user", "content": user}]}
    if structured:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "result", "strict": True, "schema": schema}}
        body["provider"] = {"require_parameters": True}
    if "reasoning" in caps:
        body["reasoning"] = {"effort": "high" if reasoning else "low"}
    headers = {"Authorization": f"Bearer {openrouter_key()}", "X-Title": "AI Article",
               "HTTP-Referer": "https://github.com/Ilya-Sidor/ai-article"}
    for attempt in range(3):
        try:
            r = _or_post(f"{OPENROUTER_URL}/chat/completions", body, headers, timeout=600)
        except httpx.HTTPError as exc:
            raise _Skip("connection", "нет соединения с OpenRouter") from exc
        text = r.text[:400]
        if r.status_code == 429:
            if re.search(r"per.?day|free-models-per-day|daily", text, re.I):
                _block("openrouter:*", "исчерпан дневной лимит бесплатных моделей OpenRouter")
                raise _Skip("daily", text)
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise _Skip("rate", text)
        if r.status_code in (401, 403):
            _block("openrouter:*", "ключ OpenRouter отклонён", minutes=10)
            raise _Skip("auth", "OpenRouter отклонил ключ")
        if r.status_code == 404:
            _block(entry, "модель недоступна: проверьте в настройках приватности OpenRouter, что разрешены "
                          "бесплатные эндпоинты (free endpoints)")
            raise _Skip("model", text)
        if r.status_code != 200:
            raise _Skip("status", f"OpenRouter {r.status_code}: {text}")
        data = r.json()
        if data.get("error"):
            raise _Skip("status", str(data["error"])[:300])
        try:
            content = data["choices"][0]["message"]["content"]
            return _parse_json(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise _Skip("invalid", "модель вернула некорректный JSON") from exc
    raise _Skip("rate", "OpenRouter: лимит запросов в минуту")


def _chain_call(project, purpose, model, system, user, schema, max_tokens, gate=True):
    """Walk the chain: the first model that can answer does; the answering model is logged."""
    if gate:
        _gate(project)
    entries = _chain_for(model)
    if not entries:
        raise LLMUnavailable("не заданы ключи ИИ-провайдеров — «Аккаунт → ИИ-модели»")
    reasons = []
    down = set()  # providers that failed as a whole in this request (key rejected, no connection)
    for entry in entries:
        prov, model_id = entry.split(":", 1)
        if _is_blocked(entry) or prov in down:
            continue
        once = _gemini_once if prov == "gemini" else _openrouter_once
        try:
            out = once(entry, model_id, system, user, schema, max_tokens, model == MODEL_REASONING)
        except _Skip as sk:
            if sk.kind == "unknown":  # most likely a bug, not a quota: do not hide it behind fallbacks
                raise LLMUnavailable(str(sk)) from sk
            if sk.kind in ("auth", "connection"):
                down.add(prov)
            reasons.append((entry, sk.kind, str(sk)))
            continue
        if not isinstance(out, dict):
            reasons.append((entry, "invalid", "ответ не является JSON-объектом"))
            continue
        _log(project["id"], purpose, model_id, system + user)
        _local.model = model_id
        return out
    kinds = {k for _, k, _ in reasons}
    if kinds & {"auth"} and not kinds - {"auth"}:
        raise LLMUnavailable("ключ API отклонён — проверьте его в «Аккаунт → ИИ-модели»")
    if kinds and kinds <= {"connection"}:
        raise LLMUnavailable("нет соединения с ИИ-провайдером — проверьте интернет/VPN")
    if not reasons or "daily" in kinds:
        raise LLMUnavailable("исчерпаны дневные лимиты бесплатных моделей — Gemini обновится в 10:00 МСК, "
                             "OpenRouter — в 03:00 МСК; можно добавить модели в цепочку в «Аккаунт → ИИ-модели»")
    detail = "; ".join(f"{e.split(':', 1)[1]}: {msg[:80]}" for e, _, msg in reasons[-3:])
    raise LLMUnavailable(f"ни одна модель цепочки не ответила ({detail})")


def _gemini_call(project, purpose, model, system, user, schema, max_tokens, gate=True):
    return _chain_call(project, purpose, model, system, user, schema, max_tokens, gate)


def _call(project, purpose, model, system, user, schema, max_tokens, gate=True, **extra):
    _local.model = None
    if provider() in ("gemini", "free", "openrouter"):
        return _chain_call(project, purpose, model, system, user, schema, max_tokens, gate)
    if provider() == "ollama":
        out = _ollama_call(project, purpose, model, system, user, schema, max_tokens, gate)
        _local.model = ollama_model()
        return out
    response = request(
        project, purpose, model, gate=gate, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}, **extra.pop("output_config", {})},
        **extra,
    )
    _local.model = model
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
    result["model"] = used_model(effective_model(MODEL_REASONING))
    result["created_at"] = now_iso()
    return result


_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def unverified_numbers(result: dict, source: str) -> list:
    """Numbers in the LLM text that do not occur in the card it was given."""
    allowed = {m.group(0).replace(",", ".") for m in _NUM.finditer(source)}
    text = " ".join([result["interpretation"], *result["alternative_explanations"], *result["limitations"],
                     *result["next_checks"]])
    return sorted({m.group(0) for m in _NUM.finditer(text) if m.group(0).replace(",", ".") not in allowed})
