"""Consistency check and a simulated journal reviewer (before submission).

Both read the rendered manuscript (numbers already substituted). Every remark must quote the manuscript
verbatim; the quote is checked by code, so a remark always points at a real place, and a remark whose quote is
no longer in the text is treated as addressed. Fixes go through the ordinary section revision.
"""
import re

from .. import llm
from ..config import MODEL_REASONING
from ..storage import now_iso

STRICTNESS = {
    "mentor": "a supportive senior colleague: constructive, explains how to improve, no harsh tone",
    "standard": "a typical expert reviewer of this journal: balanced, specific, professional",
    "strict": "a demanding 'Reviewer 2': sceptical of every claim, insists on evidence, limitations and precision",
}

CONSISTENCY_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["abstract_vs_body", "conclusion_overreach", "text_vs_table",
                                                "internal_contradiction", "methods_gap", "other"]},
            "section": {"type": "string"}, "quote": {"type": "string"},
            "problem": {"type": "string"}, "suggestion": {"type": "string"}},
        "required": ["kind", "section", "quote", "problem", "suggestion"], "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False,
}
CONSISTENCY_SYSTEM = """You check the internal consistency of a pathology manuscript before submission.
Look for: statements in the abstract that the results do not contain or that differ from them; conclusions or
discussion claims stronger than the results and their evidence level (exploratory findings stated as
established, causality from association, generalisation from a single case); text that disagrees with a table
or with another section; methods missing for a result that is reported; contradictions between sections.
Report only real problems (at most 12). quote: copy the problematic passage from the manuscript character for
character (one sentence or part of it). section: the heading of the section the quote is in. problem and
suggestion: in Russian, concrete; the suggestion must be an instruction that an editor could apply to that
passage. If everything is consistent, return an empty list."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string", "enum": ["accept", "minor_revision", "major_revision", "reject"]},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "comments": {"type": "array", "items": {
            "type": "object",
            "properties": {"severity": {"type": "string", "enum": ["major", "minor"]}, "section": {"type": "string"},
                           "quote": {"type": "string"}, "comment": {"type": "string"},
                           "suggestion": {"type": "string"}},
            "required": ["severity", "section", "quote", "comment", "suggestion"], "additionalProperties": False}},
    },
    "required": ["recommendation", "summary", "strengths", "comments"], "additionalProperties": False,
}
REVIEW_SYSTEM = """You are a peer reviewer for the journal described below, reviewing a pathology manuscript. Act as {persona}.
Judge as the journal's reviewers would: fit to the journal's scope and article type; novelty against the
literature the authors cite and the similar published cases found; validity of the diagnosis and of the
methods (immunohistochemistry clones and scoring, molecular methods, statistics appropriate to the series
size); whether conclusions match the evidence level; completeness of the reporting guideline ({guideline});
limitations; quality of figures and tables as described; clarity. Do not ask for data the study design cannot
give (e.g. survival analysis in a single case).
summary: 3-5 sentences in Russian. strengths: 2-4 items in Russian. comments: numbered remarks as a reviewer
would write them, major first; each with section (heading), quote — a passage copied character for character
from the manuscript that the remark is about (for a remark about something missing, quote the sentence nearest
to where it should go), comment and suggestion in Russian; the suggestion is an instruction the authors can
apply to that passage. At most 15 comments."""


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("ё", "е")).strip().lower()


def manuscript_text(sections, renderer):
    """[(key, heading, kind, rendered text)] of the written sections."""
    out = []
    for s in sections:
        if s["source"].strip():
            out.append((s["key"], s["heading"], s["kind"], renderer.text(s["source"], s["kind"])))
    return out


def _locate(quote, heading, parts):
    """Section key of a quote (the named section first, then any); None if the quote is not in the text."""
    q = _norm(quote)
    if len(q) < 8:
        return None
    named = [p for p in parts if _norm(p[1]) == _norm(heading)]
    for key, _, _, text in named + parts:
        if q in _norm(text):
            return key
    return None


def _as_text(parts):
    return "\n\n".join(f"## {h}\n{t}" for _, h, _, t in parts)


def consistency(project, parts, ctx):
    user = (f"Evidence levels of the findings: {[(f['id'], f['title'], f['evidence']) for f in ctx.get('findings') or []]}\n"
            f"Article type: {'case report' if ctx.get('case_report') else ctx.get('article_type')}\n\n"
            f"Manuscript:\n{_as_text(parts)}")
    out = llm._call(project, "consistency", MODEL_REASONING, CONSISTENCY_SYSTEM, user, CONSISTENCY_SCHEMA,
                    max_tokens=8000)
    issues = []
    for i, it in enumerate(out.get("issues") or [], 1):
        key = _locate(it["quote"], it["section"], parts)
        if key:  # a remark that does not point at the text is dropped
            issues.append({"id": f"K{i}", "kind": it["kind"], "section": key, "quote": it["quote"],
                           "problem": it["problem"], "suggestion": it["suggestion"]})
    return {"at": now_iso(), "model": llm.used_model(llm.effective_model(MODEL_REASONING)), "issues": issues,
            "dropped": len(out.get("issues") or []) - len(issues)}


def review(project, parts, ctx, strictness="standard", similar_cases=0):
    tpl = ctx.get("template") or {}
    profile = ctx.get("profile") or {}
    scope = ((profile.get("fields") or {}).get("scope") or {}).get("value")
    guideline = "CARE" if ctx.get("case_report") else (", ".join(tpl.get("reporting_guidelines") or []) or
                                                       "the guideline appropriate to the design")
    system = REVIEW_SYSTEM.format(persona=STRICTNESS.get(strictness, STRICTNESS["standard"]), guideline=guideline)
    journal = {"journal": ctx.get("journal"), "scope": scope, "tone": tpl.get("tone"),
               "article_type": tpl.get("type_name") or ctx.get("article_type"), "limits": tpl.get("limits"),
               "language": ctx.get("lang")}
    user = (f"Journal: {journal}\n"
            f"Findings and evidence levels: {[(f['title'], f['evidence']) for f in ctx.get('findings') or []]}\n"
            f"Cited sources: {[c['source'] for c in ctx.get('citations') or []]}\n"
            f"Similar published cases found by the authors' search: {similar_cases}\n"
            f"Tables: {[t['caption'] for t in ctx.get('tables') or []]}; figures: "
            f"{[f['caption'] for f in ctx.get('figures') or []]}\n\nManuscript:\n{_as_text(parts)}")
    out = llm._call(project, "review", MODEL_REASONING, system, user, REVIEW_SCHEMA, max_tokens=12000)
    comments = []
    for i, c in enumerate(sorted(out.get("comments") or [], key=lambda c: c["severity"] != "major"), 1):
        comments.append({"id": f"R{i}", "severity": c["severity"], "section": _locate(c["quote"], c["section"], parts),
                         "heading": c["section"], "quote": c["quote"], "comment": c["comment"],
                         "suggestion": c["suggestion"], "status": "open"})
    return {"at": now_iso(), "model": llm.used_model(llm.effective_model(MODEL_REASONING)), "strictness": strictness,
            "recommendation": out["recommendation"], "summary": out["summary"], "strengths": out.get("strengths") or [],
            "comments": comments}


def still_there(quote, parts):
    q = _norm(quote)
    return any(q in _norm(t) for *_, t in parts)
