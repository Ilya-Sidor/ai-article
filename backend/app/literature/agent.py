"""LLM steps of the literature module (PRD 8.3 tools: search_literature, verify_citation).

Every citation the model proposes must name a chunk id returned by the
``search_literature`` tool and a verbatim quote; both are checked in code
(citations.check_evidence) and then by an independent verifier call with a
different model before the author sees them.
"""
import json

from .. import llm
from ..config import MODEL_EXTRACTION, MODEL_REASONING
from ..storage import now_iso
from .citations import check_evidence
from .index import split_sentences

MAX_SEARCHES = 6
MAX_TURNS = 10
MAX_SOURCE_CHARS = 200_000

NULLABLE_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

SEARCH_TOOL = {
    "name": "search_literature",
    "description": (
        "Search the author's own literature base (papers they uploaded) and return the best-matching fragments. "
        "Each fragment has a chunk_id you must use when citing it. Query in English with specific terms "
        "(entity, markers, genes, histotype). Returns an empty list if nothing matches."),
    "strict": True,
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                     "additionalProperties": False},
}


QUERIES_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"], "additionalProperties": False,
}


def _retrieve_then_answer(project, purpose, system, user, schema, search_fn, gate):
    """For models without reliable tool use (local): the model proposes queries, code searches,
    the model answers from the returned fragments. Citation checks downstream are unchanged."""
    q = llm._call(project, purpose + ":queries", MODEL_EXTRACTION,
                  "Propose 2-4 specific English search queries for the author's literature base that would find "
                  "the fragments needed for the task below. Return only the queries.",
                  f"Task instructions:\n{system}\n\nTask input:\n{user}", QUERIES_SCHEMA, max_tokens=1000,
                  gate=gate)
    queries = [x.strip()[:300] for x in q["queries"] if x.strip()][:MAX_SEARCHES]
    seen, fragments = set(), []
    for query in queries:
        for h in search_fn(query):
            if h["id"] not in seen and len(fragments) < 14:
                seen.add(h["id"])
                fragments.append({"chunk_id": h["id"], "source": h["source_label"], "page": h.get("page"),
                                  "section": h.get("section"), "text": h["text"]})
    answer_user = (user + "\n\nFragments returned by search_literature (cite only these, by chunk_id, with quotes "
                   "copied exactly):\n" + json.dumps(fragments, ensure_ascii=False))
    out = llm._call(project, purpose, MODEL_REASONING, system, answer_user, schema, max_tokens=8000, gate=gate)
    return out, queries


def _run_with_search(project, purpose, system, user, schema, search_fn, gate):
    """Manual tool loop: Claude may call search_literature, then returns JSON matching ``schema``."""
    if llm.provider() != "anthropic":
        return _retrieve_then_answer(project, purpose, system, user, schema, search_fn, gate)
    messages = [{"role": "user", "content": user}]
    queries = []
    for _ in range(MAX_TURNS):
        response = llm.request(
            project, purpose, MODEL_REASONING, gate=gate, max_tokens=32000, system=system, messages=messages,
            tools=[SEARCH_TOOL], output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if response.stop_reason != "tool_use":
            return llm.final_json(response), queries
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name != "search_literature":
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                "content": "unknown tool"})
                continue
            if len(queries) >= MAX_SEARCHES:
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                "content": "search limit reached — answer with the fragments you already have"})
                continue
            query = str(block.input.get("query", ""))[:300]
            queries.append(query)
            hits = [{"chunk_id": h["id"], "source": h["source_label"], "page": h.get("page"),
                     "section": h.get("section"), "text": h["text"]} for h in search_fn(query)]
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(hits, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})
    raise llm.LLMUnavailable("агент не завершил поиск за отведённое число шагов")


# ---------------------------------------------------------------------------
# FR-3.9: independent verification of a single citation
# ---------------------------------------------------------------------------

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["supported", "partial", "not_supported"]},
                   "rationale": {"type": "string"}},
    "required": ["verdict", "rationale"], "additionalProperties": False,
}
VERIFY_SYSTEM = """You check citations in a pathology manuscript. Given a claim and the source fragment it cites,
decide whether the fragment itself supports the claim.
- supported: the fragment states the claim (paraphrase allowed), including any numbers and the population.
- partial: the fragment supports part of the claim, or the claim generalises beyond the fragment
  (e.g. different population, stronger wording, numbers not in the fragment).
- not_supported: the fragment does not support the claim or contradicts it.
Judge only from the fragment, not from background knowledge. Rationale: one or two sentences in Russian."""


def verify_citation(project, claim, chunk, quote):
    user = json.dumps({"claim": claim, "quote": quote, "fragment": chunk["text"],
                       "fragment_location": {"page": chunk.get("page"), "section": chunk.get("section")}},
                      ensure_ascii=False)
    out = llm._call(project, "verify_citation", MODEL_EXTRACTION, VERIFY_SYSTEM, user, VERIFY_SCHEMA,
                    max_tokens=2000, gate=False, output_config={"effort": "low"})
    status = {"supported": "supported", "partial": "partial", "not_supported": "not_supported"}[out["verdict"]]
    return {"status": status, "rationale": out["rationale"], "model": llm.effective_model(MODEL_EXTRACTION),
            "ts": now_iso()}


# ---------------------------------------------------------------------------
# FR-3.4: key theses of a source
# ---------------------------------------------------------------------------

_ITEM = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "summary": {"type": "string"}, "n_positive": NULLABLE_INT,
                   "n_total": NULLABLE_INT, "chunk_id": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["name", "summary", "n_positive", "n_total", "chunk_id", "quote"], "additionalProperties": False,
}
_FINDING = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "chunk_id": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["text", "chunk_id", "quote"], "additionalProperties": False,
}
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "design": {"type": "string"}, "entity": {"type": "string"}, "n_cases": NULLABLE_INT,
        "histotypes": {"type": "array", "items": {"type": "string"}},
        "markers": {"type": "array", "items": _ITEM}, "molecular": {"type": "array", "items": _ITEM},
        "key_findings": {"type": "array", "items": _FINDING},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["design", "entity", "n_cases", "histotypes", "markers", "molecular", "key_findings", "limitations"],
    "additionalProperties": False,
}
EXTRACT_SYSTEM = """You extract structured facts from one published pathology paper for a literature matrix.
The paper is given as fragments, each headed by [chunk_id | page | section].
- design: e.g. "retrospective case series", "cohort", "case report", "review".
- entity: tumour entity studied. n_cases: number of cases/patients analysed (null if not stated).
- markers: immunohistochemical markers with their frequency in this paper's own cases.
  molecular: gene alterations (mutation, fusion, amplification) with frequency.
  n_positive / n_total only if both are stated or directly given in the text; otherwise null.
- key_findings: the paper's main own results (not background statements).
- For every item give the chunk_id and a short verbatim quote (copied exactly) that states it.
- Do not use knowledge from outside the fragments. If the paper is a review, extract what it reports and say so
  in design. Write summaries and findings in English, as in the paper."""


def extract_source(project, source, chunks):
    parts, size = [], 0
    budget = min(MAX_SOURCE_CHARS, llm.context_budget_chars())
    for c in chunks:
        block = f"[{c['id']} | p.{c.get('page') or '-'} | {c.get('section')}]\n{c['text']}"
        if size + len(block) > budget:
            break
        parts.append(block)
        size += len(block)
    user = f"Paper: {source.get('title')}\n\n" + "\n\n".join(parts)
    out = llm._call(project, "extract_source", MODEL_EXTRACTION, EXTRACT_SYSTEM, user, EXTRACT_SCHEMA,
                    max_tokens=16000, gate=False, output_config={"effort": "medium"})
    by_id = {c["id"]: c for c in chunks}
    for key in ("markers", "molecular", "key_findings"):
        ok, rejected = check_evidence(out[key], by_id)
        for item in ok:
            item["grounded"] = True
            for n in ("n_positive", "n_total"):  # numbers must literally appear in the quote
                if item.get(n) is not None and str(item[n]) not in item["quote"]:
                    item["grounded"] = False
                    item["issue"] = "число не встречается в цитате"
        for item in rejected:
            item["grounded"] = False
            item["issue"] = item.pop("reason")
        out[key] = ok + rejected
    out.update({"model": llm.effective_model(MODEL_EXTRACTION), "created_at": now_iso(),
                "truncated": len(parts) < len(chunks)})
    return out


# ---------------------------------------------------------------------------
# FR-2.6 / FR-3.7: finding versus literature, gap and novelty
# ---------------------------------------------------------------------------

COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "literature_status": {"type": "string", "enum": [
            "described_consistent", "described_contradicting", "described_small_series", "not_described",
            "insufficient_sources"]},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}, "quote": {"type": "string"},
                           "relation": {"type": "string", "enum": ["supports", "contradicts", "context"]},
                           "note": {"type": "string"}},
            "required": ["chunk_id", "quote", "relation", "note"], "additionalProperties": False}},
        "novelty": {"type": "string"},
    },
    "required": ["literature_status", "summary", "evidence", "novelty"], "additionalProperties": False,
}
COMPARE_SYSTEM = """You help a pathologist place one finding from their own case series in the context of the
literature THEY uploaded. You can only see that literature through the search_literature tool.

Steps: search with 2-5 specific English queries (entity, markers, genes, the association itself); then decide
whether the association is described in the base, how large those series are, and whether they agree.

Rules:
- Cite only fragments returned by search_literature, by their chunk_id, with a short quote copied exactly from
  the fragment text. Never cite from memory; if the base does not cover the topic, say so
  (literature_status = insufficient_sources or not_described).
- evidence.note: one English sentence stating what the source reports, precise enough to be checked against the
  fragment (population, marker, frequency as written).
- described_small_series: described only in series of fewer than ~10 cases or case reports.
- summary (Russian, 2-4 sentences): what the base says relative to the finding, with source labels.
- novelty (Russian): a cautious one-sentence statement of what the finding adds, matching its evidence level
  (exploratory findings "suggest", never "establish"); empty string if it adds nothing new.
- Do not restate or recompute the finding's statistics."""


def compare_finding(project, finding, search_fn, chunks_lookup):
    card = {k: finding.get(k) for k in ("title", "evidence", "statement", "statistics", "limitations")}
    user = json.dumps({"finding": card, "article_focus": project.get("focus", "")}, ensure_ascii=False)
    out, queries = _run_with_search(project, "compare_finding", COMPARE_SYSTEM, user, COMPARE_SCHEMA, search_fn,
                                    gate=True)
    ok, rejected = check_evidence(out["evidence"], chunks_lookup)
    out.update({"evidence": ok, "rejected": rejected, "queries": queries, "model": llm.effective_model(MODEL_REASONING),
                "created_at": now_iso()})
    return out


# ---------------------------------------------------------------------------
# FR-3.8: citations for a text
# ---------------------------------------------------------------------------

GROUND_SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "status": {"type": "string", "enum": ["cited", "own_result", "general_knowledge", "needs_source"]},
            "citations": {"type": "array", "items": {
                "type": "object", "properties": {"chunk_id": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["chunk_id", "quote"], "additionalProperties": False}},
        },
        "required": ["index", "status", "citations"], "additionalProperties": False}}},
    "required": ["sentences"], "additionalProperties": False,
}
GROUND_SYSTEM = """You place references in a draft paragraph of a pathology manuscript, using only the author's
literature base available through search_literature.

For every numbered sentence decide:
- cited: a factual statement about prior work or established knowledge that fragments in the base support;
  give 1-3 citations (chunk_id + short verbatim quote from that fragment).
- own_result: the sentence reports the author's own data — no citation.
- general_knowledge: textbook-level statement that needs no reference (use sparingly; it will be flagged for the
  author to check).
- needs_source: needs a reference but nothing in the base supports it. Never fill such gaps from memory.
Search as needed (specific English queries). Return every sentence index exactly once."""


def ground_text(project, text, search_fn, chunks_lookup):
    sentences = split_sentences(text)
    if not sentences:
        raise ValueError("пустой текст")
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    out, queries = _run_with_search(project, "ground_text", GROUND_SYSTEM, numbered, GROUND_SCHEMA, search_fn,
                                    gate=False)
    by_index = {s["index"]: s for s in out["sentences"]}
    result = []
    for i, sent in enumerate(sentences):
        item = by_index.get(i, {"status": "needs_source", "citations": []})
        ok, rejected = check_evidence(item["citations"], chunks_lookup)
        status = item["status"]
        if status == "cited" and not ok:
            status = "needs_source"
        result.append({"index": i, "text": sent, "status": status, "evidence": ok, "rejected": rejected})
    return {"sentences": result, "queries": queries, "model": llm.effective_model(MODEL_REASONING),
            "created_at": now_iso()}
