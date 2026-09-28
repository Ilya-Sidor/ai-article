"""Local model provider (Ollama): structured calls, retrieval flow, metadata."""
import json

import pytest
from generate_sample import generate


class FakeOllama:
    """Minimal /api/chat and /api/tags; answers by purpose markers in the prompt."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def post(self, url, payload, timeout):
        import httpx
        self.calls.append(payload)
        system = payload["messages"][0]["content"]
        answer = next(v for k, v in self.answers if k in system)
        body = {"message": {"role": "assistant", "content": json.dumps(answer)}, "done": True, "done_reason": "stop"}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    def get(self, url):
        import httpx
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]}, request=httpx.Request("GET", url))


@pytest.fixture()
def local(monkeypatch):
    from app import llm
    monkeypatch.setenv("AI_ARTICLE_LLM_PROVIDER", "ollama")
    fake = FakeOllama([])
    monkeypatch.setattr(llm, "_http_post", fake.post)
    monkeypatch.setattr(llm, "_http_get", fake.get)
    monkeypatch.setattr(llm, "client", lambda: (_ for _ in ()).throw(AssertionError("Claude must not be called")))
    return fake


def test_status_and_structured_call(api, local):
    from app import llm
    s = llm.status()
    assert s == {"provider": "ollama", "reasoning_model": "qwen3:8b", "extraction_model": "qwen3:8b", "local": True,
                 "available": True}
    local.answers = [("map a pathologist", {"a": "CD117", "b": "KIT мутация", "rationale": "ok"})]
    pid = api.post("/api/projects", json={"title": "t"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    r = api.post(f"/api/projects/{pid}/hypotheses/parse", json={"question": "KIT и CD117?"}).json()
    assert r["a"] == "CD117" and r["source"] == "llm"
    call = local.calls[-1]
    assert call["model"] == "qwen3:8b" and call["format"]["properties"]["a"]["enum"][0]
    assert call["stream"] is False and call["think"] is False  # extraction-type call
    assert llm.usage(pid)[-1]["model"] == "qwen3:8b"
    meta = api.get("/api/meta").json()["llm"]
    assert meta["local"] and meta["available"]


def test_gate_applies_to_local_model(local):
    from app import llm
    with pytest.raises(llm.PrivacyGateError):
        llm.parse_question({"id": "x", "anonymization": {"status": "pending"}}, "q", [])
    assert local.calls == []


def test_literature_retrieval_flow_without_tools(api, local, monkeypatch):
    """Compare-finding works with a local model: queries → code search → answer; bad citations still rejected."""
    from app.literature import agent, citations
    chunk = {"id": "S-001:001", "source_id": "S-001", "page": 2, "section": "Results",
             "text": "CD117 was weak or absent in 12 of 31 PDGFRA-mutant tumours."}
    local.answers = [
        ("Propose 2-4 specific English search queries", {"queries": ["PDGFRA CD117 negative", "PDGFRA epithelioid"]}),
        ("place one finding from their own case series", {
            "literature_status": "described_consistent", "summary": "Описано.", "novelty": "",
            "evidence": [{"chunk_id": "S-001:001", "quote": "CD117 was weak or absent in 12 of 31", "relation": "supports",
                          "note": "n"},
                         {"chunk_id": "S-009:001", "quote": "invented", "relation": "supports", "note": "x"}]}),
    ]
    searched = []

    def search(q, top_k=8):
        searched.append(q)
        return [dict(chunk, source_label="Ivanova 2024")]
    project = {"id": "p", "anonymization": {"status": "confirmed"}, "focus": ""}
    out = agent.compare_finding(project, {"title": "t"}, search, {"S-001:001": chunk})
    assert searched == ["PDGFRA CD117 negative", "PDGFRA epithelioid"]
    assert [e["chunk_id"] for e in out["evidence"]] == ["S-001:001"] and len(out["rejected"]) == 1
    final = local.calls[-1]
    assert "S-001:001" in final["messages"][1]["content"] and final["think"] is True  # reasoning call
    assert out["model"] == "qwen3:8b"


def test_ai_statement_names_local_model(api, local):
    from app import llm
    from app.manuscript import statements
    llm._log("pX", "write_section:results", "qwen3:8b", "x")
    text = statements.ai_statement("pX")
    assert "open-weight large language model run locally (qwen3:8b, via Ollama)" in text and "Claude" not in text


def test_unavailable_local_model_is_reported(monkeypatch):
    import httpx

    from app import llm
    monkeypatch.setenv("AI_ARTICLE_LLM_PROVIDER", "ollama")

    def refuse(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(llm, "_http_get", refuse)
    monkeypatch.setattr(llm, "_http_post", refuse)
    assert llm.status()["available"] is False and "Ollama" in llm.status()["reason"]
    with pytest.raises(llm.LLMUnavailable):
        llm._call({"id": "p", "anonymization": {"status": "confirmed"}}, "x", "m", "s", "u", {}, 100, gate=False)
