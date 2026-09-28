"""Free model chain: Gemini + OpenRouter, fallbacks across providers and quotas."""
import json
from types import SimpleNamespace

import httpx
import pytest

CAPS = {"data": [
    {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "supported_parameters": ["reasoning", "tools"]},
    {"id": "nvidia/nemotron-3-super-120b-a12b:free", "supported_parameters": ["structured_outputs", "response_format",
                                                                              "reasoning"]},
    {"id": "qwen/qwen3.8-27b:free", "supported_parameters": ["structured_outputs"]},
    {"id": "openrouter/free", "supported_parameters": ["structured_outputs"]},
]}


class FakeOR:
    def __init__(self):
        self.replies = []
        self.calls = []

    def post(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": body, "headers": headers})
        status, payload = self.replies.pop(0)
        if isinstance(payload, dict):
            return httpx.Response(status, json=payload, request=httpx.Request("POST", url))
        return httpx.Response(status, text=payload, request=httpx.Request("POST", url))


class FakeGemini:
    def __init__(self):
        self.replies = []
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


class DailyLimit(Exception):
    def __init__(self):
        super().__init__("Rate limit exceeded for model (limit: 20 requests per day on Free Tier)")
        self.status_code = 429


def ok(obj):
    return 200, {"choices": [{"message": {"content": json.dumps(obj)}}]}


@pytest.fixture()
def chain(api, monkeypatch):
    from app import llm
    fo, fg = FakeOR(), FakeGemini()
    monkeypatch.setattr(llm, "_or_post", fo.post)
    monkeypatch.setattr(llm, "_http_get", lambda url: httpx.Response(200, json=CAPS, request=httpx.Request("GET", url)))
    monkeypatch.setattr(llm, "gemini_client", lambda key: fg)
    monkeypatch.setattr("time.sleep", lambda s: None)
    llm._or_caps.update(ts=0, caps={})
    r = api.put("/api/admin/llm", json={"provider": "free", "gemini_api_key": "AIza-test", "openrouter_api_key": "sk-or-test"})
    assert r.status_code == 200, r.text
    return SimpleNamespace(o=fo, g=fg, llm=llm, api=api)


P = {"id": "p", "anonymization": {"status": "confirmed"}}
SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"], "additionalProperties": False}


def test_chain_falls_back_from_gemini_to_openrouter(chain):
    llm = chain.llm
    chain.g.replies = [DailyLimit()]              # gemini-3.8-flash: daily quota exhausted
    chain.o.replies = [ok({"x": 1})]              # nemotron ultra answers (prompted JSON, no schema support)
    assert llm._call(P, "t", llm.MODEL_REASONING, "sys", "user", SCHEMA, 100) == {"x": 1}
    assert llm.used_model("?") == "nvidia/nemotron-3-ultra-550b-a55b:free"
    body = chain.o.calls[0]["body"]
    assert "response_format" not in body and "JSON schema" in body["messages"][0]["content"]
    assert body["reasoning"] == {"effort": "high"} and chain.o.calls[0]["headers"]["Authorization"] == "Bearer sk-or-test"
    assert llm.usage("p")[-1]["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"

    chain.o.replies = [ok({"x": 2})]              # extraction chain starts with nemotron super (schema support)
    assert llm._call(P, "t", llm.MODEL_EXTRACTION, "sys", "user", SCHEMA, 100) == {"x": 2}
    body = chain.o.calls[-1]["body"]
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA and body["provider"] == {"require_parameters": True}
    assert body["reasoning"] == {"effort": "low"}


def test_openrouter_daily_limit_blocks_all_free_models(chain):
    llm = chain.llm
    chain.o.replies = [(429, "Rate limit exceeded: free-models-per-day")]
    chain.g.replies = [{"x": 3}]                  # next entry: gemini-3.5-flash
    assert llm._call(P, "t", llm.MODEL_EXTRACTION, "s", "u", SCHEMA, 100) == {"x": 3}
    assert chain.g.calls[-1]["model"] == "gemini-3.5-flash"
    assert "openrouter:*" in llm.exhausted_models()
    n = len(chain.o.calls)
    chain.g.replies = [{"x": 4}]
    llm._call(P, "t", llm.MODEL_EXTRACTION, "s", "u", SCHEMA, 100)
    assert len(chain.o.calls) == n                # no more OpenRouter calls today


def test_code_fenced_json_and_404_privacy_hint(chain):
    llm = chain.llm
    chain.g.replies = [DailyLimit(), DailyLimit()]   # gemini-3.8 and 3.7 exhausted; super answers after ultra 404
    chain.o.replies = [(404, {"error": {"message": "No endpoints found matching your data policy"}}),
                       (200, {"choices": [{"message": {"content": "```json\n{\"x\": 5}\n```"}}]})]
    assert llm._call(P, "t", llm.MODEL_REASONING, "s", "u", SCHEMA, 100) == {"x": 5}
    blocked = llm.exhausted_models()["openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"]
    assert "Privacy" in blocked["message"] or "приватности" in blocked["message"]


def test_missing_key_skips_provider_and_ui_view(chain):
    llm = chain.llm
    chain.api.put("/api/admin/llm", json={"openrouter_api_key": ""})
    assert all(e.startswith("gemini:") for e in llm._chain_for(llm.MODEL_REASONING))
    v = chain.api.get("/api/admin/llm").json()
    assert v["provider"] == "free" and not v["openrouter"]["configured"]
    assert any(m["id"] == "qwen/qwen3.8-27b:free" and m["structured"] for m in v["openrouter"]["free_models"])
    bad = chain.api.put("/api/admin/llm", json={"chain_reasoning": "claude:opus"})
    assert bad.status_code == 400


def test_all_exhausted_message(chain):
    llm = chain.llm
    chain.g.replies = [DailyLimit() for _ in range(20)]
    chain.o.replies = [(429, "free-models-per-day")]
    with pytest.raises(llm.LLMUnavailable, match="дневные лимиты"):
        llm._call(P, "t", llm.MODEL_REASONING, "s", "u", SCHEMA, 100)
