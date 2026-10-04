"""DeepSeek provider: key from a file, JSON Output, thinking for writing tasks, fallback, balance errors."""
import json

import httpx
import pytest


class FakeDS:
    def __init__(self):
        self.replies, self.calls = [], []

    def post(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": body, "headers": headers})
        status, payload = self.replies.pop(0)
        req = httpx.Request("POST", url)
        return httpx.Response(status, json=payload, request=req) if isinstance(payload, dict) else \
            httpx.Response(status, text=payload, request=req)


def ok(obj):
    return 200, {"choices": [{"message": {"content": json.dumps(obj), "reasoning_content": "…"}}]}


@pytest.fixture()
def ds(api, monkeypatch, data_dir):
    from app import llm
    fake = FakeDS()
    monkeypatch.setattr(llm, "_or_post", fake.post)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert api.put("/api/admin/llm", json={"provider": "deepseek"}).status_code == 200
    return fake


P = {"id": "p", "anonymization": {"status": "confirmed"}}
SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def test_key_from_file_and_request_shape(api, ds, data_dir):
    from app import llm
    assert api.get("/api/meta").json()["llm"]["available"] is False  # no key yet
    (data_dir / "deepseek_api_key.txt").write_text("sk-test-file-key\n")
    meta = api.get("/api/meta").json()["llm"]
    assert meta["available"] and meta["reasoning_model"] == "deepseek-v4-pro"
    v = api.get("/api/admin/llm").json()["deepseek"]
    assert v["key_in_file"] and v["key_hint"] == "…-key" and "sk-test" not in json.dumps(v)

    ds.replies = [ok({"x": 1})]
    assert llm._call(P, "t", llm.MODEL_REASONING, "sys", "user", SCHEMA, 100) == {"x": 1}
    c = ds.calls[-1]
    assert c["url"] == "https://api.deepseek.com/chat/completions"
    assert c["headers"]["Authorization"] == "Bearer sk-test-file-key"
    b = c["body"]
    assert b["model"] == "deepseek-v4-pro" and b["thinking"] == {"type": "enabled"} and b["reasoning_effort"] == "high"
    assert b["response_format"] == {"type": "json_object"} and "JSON" in b["messages"][0]["content"]
    assert llm.usage("p")[-1]["model"] == "deepseek-v4-pro"

    ds.replies = [ok({"x": 2})]
    llm._call(P, "t", llm.MODEL_EXTRACTION, "sys", "user", SCHEMA, 100)
    b = ds.calls[-1]["body"]
    assert b["model"] == "deepseek-flash" and b["thinking"] == {"type": "disabled"} and "reasoning_effort" not in b


def test_retries_fallback_and_balance(api, ds, data_dir):
    from app import llm
    (data_dir / "deepseek_api_key.txt").write_text("sk-test")
    ds.replies = [(503, "server busy"), (200, {"choices": [{"message": {"content": ""}}]}), ok({"x": 3})]
    assert llm._call(P, "t", llm.MODEL_REASONING, "s", "u", SCHEMA, 100) == {"x": 3}  # busy, empty, then answer
    ds.replies = [(500, "x"), (500, "x"), (500, "x"), (500, "x"), ok({"x": 4})]  # pro is down → flash answers
    assert llm._call(P, "t", llm.MODEL_REASONING, "s", "u", SCHEMA, 100) == {"x": 4}
    assert ds.calls[-1]["body"]["model"] == "deepseek-flash"
    llm._exhausted.clear()
    ds.replies = [(402, {"error": {"message": "Insufficient Balance"}})] * 2
    with pytest.raises(llm.LLMUnavailable, match="балансе DeepSeek"):
        llm._call(P, "t", llm.MODEL_REASONING, "s", "u", SCHEMA, 100)


def test_ai_statement_names_deepseek(api, ds):
    from app import llm
    from app.manuscript import statements
    llm._log("pD", "write_section:results", "deepseek-v4-pro", "x")
    text = statements.ai_statement("pD")
    assert "DeepSeek; deepseek-v4-pro" in text and "Ollama" not in text
