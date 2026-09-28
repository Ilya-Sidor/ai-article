"""LLM steps of manuscript generation (FR-5.1, FR-5.2, 5.4).

The model never writes numbers: it references fact ids ({{A3.p_expr}}) that the
renderer fills from the analysis engine. Citations are either accepted citation
records ([[CIT-0003]]) or new fragments from the project base proposed via
search_literature, which are checked and verified before they are kept.
"""
import json
import re

from .. import llm
from ..config import MODEL_EXTRACTION, MODEL_REASONING
from ..literature.agent import _run_with_search

FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

WRITING_RULES = """Writing rules (apply to every text you produce):
- Academic medical English for a surgical pathology journal; {variant} spelling; tone: {tone}.
- NUMBERS: never type a number that comes from the study data or analysis. Write the placeholder of the fact
  instead, e.g. "in {{{{A1.row2.col2}}}} of {{{{A1.row2.total}}}} cases ({{{{A1.row2.col2.pct}}}}%)",
  "({{{{A1.effect_expr}}}}; {{{{A1.p_expr}}}}, {{{{A1.q_expr}}}})", "median age {{{{V2.median}}}} years". Use only ids from
  the fact list. Numbers that are part of names (CD117, exon 11, pT2) and numbers given in the author's method
  inputs may be written directly.
- Tables and figures: refer to them only as {{{{TAB:<id>}}}} and {{{{FIG:<id>}}}} (they are numbered automatically).
- Evidence levels: findings marked "exploratory" or "descriptive" must be worded as observations in this series
  ("was observed", "suggests", "in this series", "warrants validation"), never as established or significant.
  Only "significant" findings (FDR-corrected) may be called statistically significant.
- Do not state facts about prior work without a citation from the provided list or from search_literature.
  Never cite from memory.
- If information needed for the text is missing (e.g. antibody clone, approval number), write
  [уточнить: <what is missing, in Russian>] at that place instead of inventing it.
- Avoid clichés: delve, pivotal/crucial role, in the realm of, it is worth noting, a testament to,
  shed light on, underscore the importance, landscape, paramount, leverage; avoid starting sentences with
  Furthermore/Moreover.
- Define each abbreviation at first use.
- Format: plain text for a Word document, paragraphs separated by a blank line; optional subheadings as lines
  starting with "### ". No LaTeX, no $...$ math and no Markdown emphasis: write symbols as Unicode characters
  (≥, ≤, ±, ×, χ², α, κ)."""

KIND_INSTRUCTIONS = {
    "introduction": "Introduction: background on the entity and the specific question, supported by citations; the "
                    "knowledge gap and what this series adds (use the confirmed novelty statements); end with the "
                    "aim. No study results and no study numbers.",
    "methods": "Materials and methods: case selection and period, histological review, immunohistochemistry "
               "(antibody, clone, dilution, platform, scoring for every marker in the inputs), molecular methods, "
               "follow-up definition, statistical analysis. The statistics paragraph must describe exactly what the "
               "analysis log shows: the tests used, effect sizes with 95% CIs, Benjamini–Hochberg correction across "
               "all {{tests_n}} tests, the size-dependent restrictions, software and reproducibility. Mention the "
               "anonymisation and ethics only briefly (statements are separate).",
    "case_presentation": "Case presentation: describe the case(s) using the facts; clinical, morphological, "
                         "immunohistochemical and molecular features; no interpretation.",
    "results": "Results: cohort characteristics (refer to Table 1), then the accepted findings in the order of the "
               "plan, each with its numbers via placeholders, effect size and p/q. Report null results the author "
               "accepted. No literature citations, minimal interpretation.",
    "discussion": "Discussion: start with the main message; one paragraph per key finding comparing it with the "
                  "literature (citations required); explain alternative explanations; a paragraph that starts with "
                  "'This study has limitations' (small series, retrospective design, no validation cohort, "
                  "{{tests_n}} tests performed with FDR correction, exploratory nature where applicable); a brief "
                  "outlook. Do not introduce new numbers.",
    "conclusion": "Conclusion: 2–4 sentences, consistent with the evidence levels; no citations; no new numbers "
                  "unless essential.",
    "abstract": "Abstract: summarise the accepted sections provided. Structure and word limit from the journal "
                "template; for a structured abstract use '### <Heading>' for each heading in the given order. Use "
                "fact placeholders for all numbers. No citations, no abbreviations without definition.",
    "other": "Write this section according to the plan points.",
}

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "new_citations": {"type": "array", "items": {
            "type": "object",
            "properties": {"marker": {"type": "string"}, "chunk_id": {"type": "string"}, "quote": {"type": "string"},
                           "claim": {"type": "string"}},
            "required": ["marker", "chunk_id", "quote", "claim"], "additionalProperties": False}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "new_citations", "questions"], "additionalProperties": False,
}

CITATION_RULES = """Citations: cite accepted citations as [[CIT-0003]] (several: [[CIT-0003, CIT-0007]]). To cite a
fragment found with search_literature that is not in the accepted list, write [[NEW:1]], [[NEW:2]] … in the text
and describe each in new_citations (marker "NEW:1", chunk_id, a short verbatim quote copied exactly from the
fragment, and the claim it supports). questions: what you need from the author (in Russian)."""


def _system(ctx):
    return (f"You are drafting one section of a pathology manuscript together with its author.\n\n"
            + WRITING_RULES.format(variant=ctx.get("language_variant") or "consistent (British or American)",
                                   tone=ctx.get("tone") or "clinico-pathological, precise")
            + "\n\n" + CITATION_RULES)


def _facts_block(ctx):
    return "\n".join(f"{{{{{fid}}}}} — {f['desc']} = {f['value']}" for fid, f in ctx["facts"].items())


def _context_block(ctx):
    return json.dumps({
        "journal": ctx.get("journal"), "article_type": ctx.get("article_type"),
        "focus": ctx.get("focus"), "series_size_tier": ctx.get("tier"),
        "key_messages": ctx.get("key_messages"),
        "findings": ctx.get("findings"), "novelty": ctx.get("novelty"),
        "tables": ctx.get("tables"), "figures": ctx.get("figures"),
        "accepted_citations": ctx.get("citations"),
        "author_inputs": ctx.get("inputs"), "analysis_log": ctx.get("log"),
    }, ensure_ascii=False, indent=1)


def _call_plain(project, purpose, system, user, schema, gate=True):
    return llm._call(project, purpose, MODEL_REASONING, system, user, schema, max_tokens=32000, gate=gate,
                     output_config={"effort": "medium"}, **FALLBACK)


def write_section(project, section, ctx, search_fn=None, extra_sources=None):
    kind = section["kind"]
    instr = KIND_INSTRUCTIONS.get(kind, KIND_INSTRUCTIONS["other"])
    user = (f"Section to write: {section['heading']} (kind: {kind})\n{instr}\n\n"
            f"Plan for this section: {json.dumps(section.get('plan') or {}, ensure_ascii=False)}\n"
            f"Word budget: {section.get('budget') or 'not set'}\n\n"
            f"Study context:\n{_context_block(ctx)}\n\nFacts (copy the placeholder exactly, with double curly braces and "
            f"no backticks; the value after = is only for your reasoning):\n{_facts_block(ctx)}\n")
    if extra_sources:
        user += "\nAccepted sections of the manuscript (source text with placeholders):\n" + extra_sources
    purpose = f"write_section:{kind}"
    if search_fn and kind in ("introduction", "discussion", "other"):
        out, queries = _run_with_search(project, purpose, _system(ctx), user, SECTION_SCHEMA, search_fn, gate=True)
        out["queries"] = queries
        return out
    return _call_plain(project, purpose, _system(ctx), user, SECTION_SCHEMA)


def revise_section(project, section, source, instruction, selection, ctx, search_fn=None):
    user = (f"Revise the section '{section['heading']}' according to the author's instruction.\n"
            f"Instruction (may be in Russian): {instruction}\n"
            + (f"Apply it to this fragment only, keep the rest unchanged: «{selection}»\n" if selection else "")
            + "Keep all placeholders and citation markers that remain relevant; return the full revised section.\n\n"
            f"Current source text:\n{source}\n\nStudy context:\n{_context_block(ctx)}\n\n"
            f"Facts:\n{_facts_block(ctx)}\n")
    if search_fn and section["kind"] in ("introduction", "discussion", "other"):
        out, _ = _run_with_search(project, "revise_section", _system(ctx), user, SECTION_SCHEMA, search_fn, gate=True)
        return out
    return _call_plain(project, "revise_section", _system(ctx), user, SECTION_SCHEMA)


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "key_messages": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {"heading": {"type": "string"}, "points": {"type": "array", "items": {"type": "string"}},
                           "words": {"type": "integer"}},
            "required": ["heading", "points", "words"], "additionalProperties": False}},
        "tables": {"type": "array", "items": {"type": "string"}},
        "figures": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["key_messages", "sections", "tables", "figures", "rationale"], "additionalProperties": False,
}


def make_plan(project, ctx, headings, word_limit):
    system = (
        "You plan a pathology manuscript before any text is written. Build 1-3 key messages from the accepted "
        "findings (respect evidence levels) and the confirmed novelty. For each section heading given (keep them and "
        "their order) list 3-7 concrete points and a word budget; the budgets of the main sections must sum to at "
        f"most {word_limit or 'a reasonable length for this article type'}. Choose which tables and figures "
        "(by id) to include. Points and rationale in Russian; key messages in English. Do not write numbers — "
        "refer to findings by id.")
    user = f"Section headings: {headings}\n\nStudy context:\n{_context_block(ctx)}"
    return _call_plain(project, "plan", system, user, PLAN_SCHEMA)


FRONT_SCHEMA = {
    "type": "object",
    "properties": {"titles": {"type": "array", "items": {"type": "string"}}, "running_title": {"type": "string"},
                   "keywords": {"type": "array", "items": {"type": "string"}},
                   "highlights": {"type": "array", "items": {"type": "string"}}},
    "required": ["titles", "running_title", "keywords", "highlights"], "additionalProperties": False,
}


def front_matter(project, ctx, template, sections_text):
    t = template or {}
    system = ("You propose front matter for a pathology manuscript: 3-5 title options (informative, no hype, "
              f"max {t.get('title', {}).get('max_chars') or 'about 150'} characters, no numbers except in names), "
              f"a running title (max {t.get('running_title_chars') or 'about 50'} characters), "
              f"{t.get('keywords', {}).get('min') or 4}-{t.get('keywords', {}).get('max') or 6} keywords"
              + (" from MeSH" if t.get("keywords", {}).get("mesh") else "")
              + " and 3-5 highlights (each under 85 characters, evidence-level consistent, no numbers). "
              + WRITING_RULES.split("\n- NUMBERS")[0].format(variant=ctx.get("language_variant") or "consistent",
                                                             tone=ctx.get("tone") or "precise"))
    user = f"Key messages: {ctx.get('key_messages')}\n\nManuscript sections:\n{sections_text}"
    return _call_plain(project, "front", system, user, FRONT_SCHEMA)


TERMS_SCHEMA = {
    "type": "object",
    "properties": {"terms": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "en": {"type": "string"},
                       "levels": {"type": "array", "items": {
                           "type": "object", "properties": {"value": {"type": "string"}, "en": {"type": "string"}},
                           "required": ["value", "en"], "additionalProperties": False}}},
        "required": ["name", "en", "levels"], "additionalProperties": False}}},
    "required": ["terms"], "additionalProperties": False,
}


def translate_terms(project, needed):
    system = ("Translate variable names and category values of a pathology dataset into standard English "
              "terminology for a manuscript: WHO tumour classification terms, HGNC gene symbols, standard IHC "
              "notation (e.g. 'KIT mutation', 'CD117 expression', 'Ki-67 labelling index, %', 'epithelioid'). "
              "Keep marker and gene symbols unchanged. Return every name and every value exactly once.")
    user = json.dumps(needed, ensure_ascii=False)
    return llm._call(project, "terms", MODEL_EXTRACTION, system, user, TERMS_SCHEMA, max_tokens=8000, gate=True,
                     output_config={"effort": "low"})


NEW_MARKER = re.compile(r"\[\[\s*NEW:(\d+)\s*\]\]")
