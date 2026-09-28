"""Google Gemini provider: settings, structured calls, free-tier rate limits, admin access."""
import json
from types import SimpleNamespace

import pytest
from conftest import make_client
from generate_sample import generate


class FakeGemini:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        outer = self

        class Interactions:
            @staticmethod
            def create(**kw):
                outer.calls.append(kw)
                r = outer.replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return SimpleNamespace(output_text=json.dumps(r))

        self.interactions = Interactions


def _quota(msg="limit: 10 per minute", delay="0s"):
    from google.genai import errors
    return errors.ClientError(429, {"error": {"code": 429, "message": msg, "status": "RESOURCE_EXHAUSTED",
                                              "details": [{"retryDelay": delay}]}})


@pytest.fixture()
def gemini(api, monkeypatch):
    from app import llm
    fake = FakeGemini([])
    monkeypatch.setattr(llm, "gemini_client", lambda key: fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    r = api.put("/api/admin/llm", json={"provider": "gemini", "gemini_api_key": "AIzaSy-test-key-1234"})
    assert r.status_code == 200, r.text
    return fake


def test_admin_settings_and_key_is_sealed(api, gemini, data_dir):
    v = api.get("/api/admin/llm").json()
    assert v["provider"] == "gemini" and v["gemini"]["configured"] and v["gemini"]["key_hint"] == "…1234"
    assert "AIzaSy-test-key" not in json.dumps(v)
    from app import db
    with db.engine().connect() as c:
        stored = {r[0]: r[1] for r in c.execute(db.app_settings.select().with_only_columns(
            db.app_settings.c.key, db.app_settings.c.value))}
    assert "AIzaSy" not in stored["llm.gemini_api_key"]  # sealed with the master key
    meta = api.get("/api/meta").json()["llm"]
    assert meta["provider"] == "gemini" and meta["available"] and meta["reasoning_model"] == "gemini-3.8-flash"
    other = make_client("user2@example.org")
    assert other.get("/api/admin/llm").status_code == 403
    assert other.put("/api/admin/llm", json={"provider": "anthropic"}).status_code == 403


def test_structured_call_and_thinking_levels(api, gemini):
    from app import llm
    gemini.replies = [{"a": "CD117", "b": "KIT мутация", "rationale": "ok"}]
    pid = api.post("/api/projects", json={"title": "t"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    r = api.post(f"/api/projects/{pid}/hypotheses/parse", json={"question": "KIT и CD117?"}).json()
    assert r["a"] == "CD117" and r["source"] == "llm"
    call = gemini.calls[-1]
    assert call["model"] == "gemini-3.8-flash" and call["response_format"]["mime_type"] == "application/json"
    assert call["response_format"]["schema"]["properties"]["a"]["enum"]
    assert call["generation_config"]["thinking_level"] == "low"  # extraction-type task
    assert llm.usage(pid)[-1]["model"] == "gemini-3.8-flash"
    gemini.replies = [{"answer": "stomach"}]
    t = api.post("/api/admin/llm/test").json()
    assert t["ok"] and gemini.calls[-1]["generation_config"]["thinking_level"] == "high"


class NextGenRateLimit(Exception):
    """Shape of google.genai._gaos RateLimitError (the Interactions API): status_code, no .code."""

    def __init__(self, msg):
        super().__init__(f"Error code: 429 - {{'error': {{'message': '{msg}'}}}}")
        self.status_code = 429


def test_rate_limit_retry_and_model_fallback(api, gemini):
    from app import llm
    project = {"id": "p", "anonymization": {"status": "confirmed"}}
    schema = {"type": "object"}
    gemini.replies = [_quota(delay="3s"), _quota(), {"ok": 1}]
    assert llm._call(project, "x", llm.MODEL_REASONING, "s", "u", schema, 100) == {"ok": 1}
    assert len(gemini.calls) == 3 and llm.used_model("?") == "gemini-3.8-flash"

    # the real error from the user's session: daily quota of the strongest model, new SDK error class
    daily = NextGenRateLimit("Rate limit exceeded for model gemini-3.8-flash (limit: 20 requests per day on Free Tier)")
    gemini.calls.clear()
    gemini.replies = [daily, {"ok": 2}]
    assert llm._call(project, "x", llm.MODEL_REASONING, "s", "u", schema, 100) == {"ok": 2}
    assert [c["model"] for c in gemini.calls] == ["gemini-3.8-flash", "gemini-3.7-flash"]
    assert llm.used_model("?") == "gemini-3.7-flash" and "gemini-3.8-flash" in llm.exhausted_models()
    assert llm.usage("p")[-1]["model"] == "gemini-3.7-flash"  # the log names the model that answered
    gemini.calls.clear()
    gemini.replies = [{"ok": 3}]  # exhausted model is skipped until the quota resets
    llm._call(project, "x", llm.MODEL_REASONING, "s", "u", schema, 100)
    assert gemini.calls[0]["model"] == "gemini-3.7-flash"
    assert "gemini-3.8-flash" in api.get("/api/meta").json()["llm"]["exhausted"]

    gemini.replies = [NextGenRateLimit("limit: 20 requests per day") for _ in llm.GEMINI_CHAIN]
    with pytest.raises(llm.LLMUnavailable, match="дневные лимиты"):
        llm._call(project, "x", llm.MODEL_REASONING, "s", "u", schema, 100)


def test_connection_and_unknown_errors_are_not_confused(api, gemini):
    import httpx

    from app import llm
    project = {"id": "p", "anonymization": {"status": "confirmed"}}
    gemini.replies = [httpx.ConnectError("boom")]
    with pytest.raises(llm.LLMUnavailable, match="нет соединения"):
        llm._call(project, "x", "m", "s", "u", {}, 10)
    gemini.replies = [ValueError("strange")]
    with pytest.raises(llm.LLMUnavailable, match="ValueError"):
        llm._call(project, "x", "m", "s", "u", {}, 10)


def test_bad_key_and_gate(api, gemini):
    from google.genai import errors

    from app import llm
    gemini.replies = [errors.ClientError(403, {"error": {"code": 403, "message": "API key not valid"}})]
    with pytest.raises(llm.LLMUnavailable, match="ключ"):
        llm._call({"id": "p", "anonymization": {"status": "confirmed"}}, "x", "m", "s", "u", {}, 10)
    with pytest.raises(llm.PrivacyGateError):
        llm.parse_question({"id": "x", "anonymization": {"status": "pending"}}, "q", [])


def test_schema_rejected_falls_back_to_prompted_json(api, gemini):
    from google.genai import errors

    from app import llm
    gemini.replies = [errors.ClientError(400, {"error": {"code": 400, "message": "response schema too complex"}}),
                      {"fields": []}]
    out = llm._call({"id": "p", "anonymization": {"status": "confirmed"}}, "x", "m", "s", "u",
                    {"type": "object", "properties": {"fields": {"type": "array"}}}, 10)
    assert out == {"fields": []}
    assert "schema" not in gemini.calls[-1]["response_format"] and "Return only a JSON" in gemini.calls[-1]["system_instruction"]


def test_ai_statement_names_gemini(api, gemini):
    from app import llm
    from app.manuscript import statements
    llm._log("pG", "write_section:results", "gemini-3.8-flash", "x")
    assert "Google Gemini; gemini-3.8-flash" in statements.ai_statement("pG")
