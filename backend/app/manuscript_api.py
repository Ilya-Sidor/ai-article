"""REST API of manuscript generation (Module 5) and export."""
import json
import re
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import llm
from .analysis import engine, figures as figs
from .analysis.guardrails import tier_for
from .journals import checks as jchecks, store as journals
from .literature import agent as lit_agent, service as lit
from .literature.citations import quote_in_text
from .literature_api import _comparisons, _search_fn, store
from .manuscript import agent, assets, case as mcase, checks as mchecks, export, facts as mfacts, lang as mlang
from .manuscript import fixes, statements
from .manuscript import store as ms
from .manuscript.render import Renderer, normalize_placeholders
from .storage import now_iso, read_bytes, read_json

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def _profile_template(project):
    jid = project.get("journal_id")
    if not jid:
        return None, None
    try:
        profile = journals.get(jid)
    except journals.JournalError:
        return None, None
    return profile, jchecks.template(profile, project["article_type"])


def _headings(project, template, state):
    if state.get("plan") and state["plan"].get("sections"):
        return [s["heading"] for s in state["plan"]["sections"]]
    if template and template["sections"]:
        return [h for h in template["sections"] if not h.lower().startswith(("abstract", "reference"))]
    defaults = ms.DEFAULT_SECTIONS_RU if mlang.of(template) == "ru" else ms.DEFAULT_SECTIONS
    return defaults.get(project["article_type"], defaults["_default"])


def _log_summary(analysis):
    hyps = analysis.get("hypotheses", [])
    tests = {}
    for h in hyps:
        if h.get("test"):
            tests[h["test"]] = tests.get(h["test"], 0) + 1
    env = analysis.get("environment", {})
    cl = analysis.get("clustering") or {}
    return {"tests_used": tests, "n_not_run_guardrail": sum(1 for h in hyps if h["status"] == "не выполнен"),
            "guardrails": (analysis.get("guardrails") or {}).get("rules"),
            "clustering": cl.get("method") if cl.get("status") == "ok" else None,
            "software": {"python": env.get("python"), **(env.get("packages") or {})},
            "bootstrap_resamples": env.get("bootstrap_resamples"), "base_seed": env.get("base_seed")}


def context(pid, state=None):
    project = store.get(pid)
    state = state or ms.load(store, pid)
    case_report = mcase.is_case_report(project)  # one patient: the case table instead of an analysis (CARE)
    analysis = mcase.case_data(store, pid) if case_report else (engine.latest(store, pid) or {})
    (mcase if case_report else mfacts).assign_ids(state["ids"], analysis)
    assets.sync(state, analysis)
    profile, template = _profile_template(project)
    lang = mlang.of(template)
    built = (mcase.build(analysis, state["ids"], state["terms"], lang) if case_report
             else mfacts.build(store, pid, state["ids"], state["terms"], lang))
    comps = _comparisons(pid)
    labels = {s["id"]: s["label"] for s in lit.sources(store, pid)}
    cits = [{"id": c["id"], "source": labels.get(c["source_id"]), "claim": c["claim"], "quote": c["quote"],
             "relation": c["relation"]}
            for c in lit.citations(store, pid)
            if c["decision"] == "accepted" and c["verification"]["status"] in ("supported", "partial")]
    findings = []
    for f in built["findings"]:
        comp = comps.get(f["key"]) or {}
        findings.append({**{k: f[k] for k in ("id", "title", "evidence", "statement", "wording_en", "null_result")},
                         "literature_status": comp.get("literature_status"),
                         "novelty": comp.get("novelty") if comp.get("novelty_confirmed") else None})
    tables = [{"placeholder": f"{{{{TAB:{t['id']}}}}}", "include": t["include"],
               "caption": t.get("caption") or assets.default_caption(t, analysis, state["terms"], "table", lang)}
              for t in state["tables"]]
    figures_ = [{"placeholder": f"{{{{FIG:{f['id']}}}}}", "include": f["include"],
                 "caption": f.get("caption") or assets.default_caption(f, analysis, state["terms"], "figure", lang)}
                for f in state["figures"]]
    ctx = {
        "project": project, "analysis": analysis, "facts": built["facts"], "findings": findings,
        "variables": built["variables"], "template": template, "profile": profile, "lang": lang,
        "case_report": case_report,
        "case_documents": (analysis.get("case_report") or {}).get("documents") if case_report else None,
        "journal": project.get("journal"), "article_type": project["article_type"], "focus": project.get("focus"),
        "language_variant": (template or {}).get("language_variant"), "tone": (template or {}).get("tone"),
        "tier": tier_for((analysis.get("summary") or {}).get("n", 0)).label if analysis and not case_report else None,
        "tier_code": tier_for((analysis.get("summary") or {}).get("n", 0)).code if analysis and not case_report else None,
        "key_messages": (state.get("plan") or {}).get("key_messages"),
        "novelty": [f["novelty"] for f in findings if f["novelty"]], "citations": cits,
        "tables": [t for t in tables if t["include"]], "figures": [f for f in figures_ if f["include"]],
        "all_tables": tables, "all_figures": figures_,
        "inputs": state.get("inputs"), "log": _log_summary(analysis) if analysis and not case_report else None,
    }
    return ctx, state


def _fingerprints(ctx, state):
    """Inputs each section kind depends on (FR-0.4, FR-5.12)."""
    a = ctx["analysis"]
    run = (a.get("summary") or {}).get("run_id")
    findings = sorted((f["id"], f["evidence"]) for f in ctx["findings"])
    cits = sorted(c["id"] for c in ctx["citations"])
    plan = (state.get("plan") or {}).get("approved_at")
    base = {"plan": plan, "terms": ms.fingerprint(state["terms"])}
    accepted = {k: s.get("accepted_version") for k, s in state["sections"].items() if k not in ("abstract",)}
    return {
        "results": ms.fingerprint({**base, "run": run, "findings": findings,
                                   "assets": [(t["id"], t["include"]) for t in state["tables"] + state["figures"]]}),
        "methods": ms.fingerprint({**base, "run": run, "inputs": state.get("inputs")}),
        "case_presentation": ms.fingerprint({**base, "run": run, "findings": findings}),
        "introduction": ms.fingerprint({**base, "novelty": ctx["novelty"], "cits": cits}),
        "discussion": ms.fingerprint({**base, "findings": findings, "novelty": ctx["novelty"], "cits": cits}),
        "conclusion": ms.fingerprint({**base, "findings": findings}),
        "other": ms.fingerprint({**base, "findings": findings, "cits": cits}),
        "abstract": ms.fingerprint({**base, "accepted": accepted}),
        "abstract_en": ms.fingerprint({"abstract": (state["sections"].get("abstract") or {}).get("accepted_version")}),
        "statements": ms.fingerprint({"inputs": state.get("inputs"),
                                      "template": (ctx["template"] or {}).get("statements")}),
    }


def _figure_language(ctx, state):
    """Labels on figures: the author's choice, else the manuscript language."""
    return state.get("figure_language") or ctx.get("lang", "en")


def _renderer(pid, ctx, state):
    secs = [{"source": ms.current_source(s)} for s in ms.ordered(state)]
    return Renderer(ctx["facts"], state, lit.citations(store, pid), lit.sources(store, pid),
                    lit.settings(store, pid)["style"], secs, ctx.get("lang", "en"))


def _budgets(state):
    out = {}
    for s in (state.get("plan") or {}).get("sections", []):
        out[ms.slug(s["heading"])] = s.get("words")
    return out


def _check(pid, ctx, state, renderer):
    sections = [{"key": s["key"], "kind": s["kind"], "heading": s["heading"], "source": ms.current_source(s),
                 "whitelist": s.get("number_whitelist")} for s in ms.ordered(state)]
    inputs_text = json.dumps(state.get("inputs") or {}, ensure_ascii=False)
    if ctx["case_report"]:  # numbers written in the case's own documents are the author's data too
        inputs_text += " " + " ".join(ctx["case_documents"] or [])
    names = [v["label"] for v in ctx["variables"]] + [v["name"] for v in ctx["variables"]] + [
        lvl for t in state["terms"].values() for lvl in (t.get("levels") or {}).values()]
    issues, counts = mchecks.check(sections, renderer, ctx["facts"], ctx["findings"], ctx["template"],
                                   ctx["tier_code"], inputs_text, names, _budgets(state))
    for m in mfacts.untranslated(state["terms"], mfacts.terms_needed(store, pid)):
        what = ([m["name"]] if m["name_missing"] else []) + [f"{m['name']}: {x}" for x in m["levels"]]
        issues.append({"section": None, "heading": "терминология", "severity": "blocking", "code": "untranslated",
                       "message": "нет английского перевода: " + "; ".join(what), "fragment": "",
                       "suggestion": "«1. Терминология» → «Перевести автоматически» или впишите перевод"})
    if ctx["case_report"]:
        issues += mcase.care_issues(sections, renderer, state.get("front"), state.get("inputs"), ctx["template"])
    # citations used in the text must be decided and verified
    cit = {c["id"]: c for c in lit.citations(store, pid)}
    for s in sections:
        for m in re.finditer(r"CIT-\d{4}", s["source"]):
            c = cit.get(m.group(0))
            if c and c["decision"] == "pending":
                issues.append({"section": s["key"], "heading": s["heading"], "severity": "blocking",
                               "code": "citation_pending", "citation": c["id"],
                               "message": f"{c['id']}: цитата ожидает решения автора",
                               "fragment": c["quote"][:200], "suggestion": "примите или отклоните на шаге «Литература»"})
            elif c and c["decision"] == "accepted" and c["verification"]["status"] != "supported":
                issues.append({"section": s["key"], "heading": s["heading"], "severity": "blocking",
                               "code": "citation_unverified", "citation": c["id"],
                               "message": f"{c['id']}: проверка — {c['verification']['status']}",
                               "fragment": c["quote"][:200], "suggestion": "перепроверьте или замените цитату"})
    front = state.get("front") or {}
    counts.update({"title_chars": len(front["title"]) if front.get("title") else None,
                   "keywords": len(front["keywords"]) if front.get("keywords") else None,
                   "tables": sum(1 for t in state["tables"] if t["include"]),
                   "figures": sum(1 for f in state["figures"] if f["include"]),
                   "references": len(renderer.bibliography)})
    return issues, counts


def manuscript_counts(pid):
    """Counts for the journal compliance indicator (module 4)."""
    if not ms.path(store, pid).exists():
        return {}
    ctx, state = context(pid)
    _, counts = _check(pid, ctx, state, _renderer(pid, ctx, state))
    return counts


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _view(pid, ctx, state):
    renderer = _renderer(pid, ctx, state)
    issues, counts = _check(pid, ctx, state, renderer)
    issues = fixes.annotate_all(issues, state)
    fps = _fingerprints(ctx, state)
    sections = []
    budgets = _budgets(state)
    for s in ms.ordered(state):
        cur = ms.current(s)
        fp = fps.get(s["kind"], fps["other"])
        sections.append({
            "key": s["key"], "heading": s["heading"], "kind": s["kind"], "status": s["status"],
            "stale": bool(cur and cur.get("fingerprint") and cur["fingerprint"] != fp),
            "source": cur["source"] if cur else "", "segments": renderer.segments(cur["source"], s["kind"]) if cur else [],
            "versions": [{k: v.get(k) for k in ("n", "ts", "actor", "note", "questions")} for v in s["versions"]],
            "accepted_version": s.get("accepted_version"), "comments": s["comments"],
            "words": counts["sections"].get(s["key"]), "budget": budgets.get(s["key"]),
            "whitelist": s.get("number_whitelist") or [],
            "issues": [i for i in issues if i["section"] == s["key"]],
            "plan": next((p for p in (state.get("plan") or {}).get("sections", []) if ms.slug(p["heading"]) == s["key"]),
                         None),
        })
    tables = [dict(t, caption_effective=c["caption"]) for t, c in zip(state["tables"], ctx["all_tables"])]
    figures_ = [dict(f, caption_effective=c["caption"]) for f, c in zip(state["figures"], ctx["all_figures"])]
    if ctx["lang"] == "ru":  # English captions of a bilingual journal
        for items, kind in ((tables, "table"), (figures_, "figure")):
            for it in items:
                it["caption_en_effective"] = assets.default_caption(it, ctx["analysis"], state["terms"], kind, "en")
    return {
        "plan": state.get("plan"), "inputs": state.get("inputs"), "terms": state["terms"],
        "terms_needed": mfacts.terms_needed(store, pid), "front": state.get("front") or {},
        "sections": sections, "tables": tables, "figures": figures_,
        "figure_language": _figure_language(ctx, state), "lang": ctx["lang"],
        "facts": [{"id": k, "desc": v["desc"], "value": v["value"]} for k, v in ctx["facts"].items()],
        "findings": ctx["findings"], "citations": ctx["citations"], "bibliography": renderer.bibliography,
        "issues": [i for i in issues if i["section"] is None], "counts": counts,
        "blocking": sum(1 for i in issues if i["severity"] == "blocking"),
        "template": ctx["template"], "has_analysis": bool(ctx["analysis"]),
        "n_accepted_findings": len(ctx["findings"]), "case_report": ctx["case_report"],
        "n_cases": (ctx["analysis"].get("summary") or {}).get("n") if ctx["case_report"] else None,
    }


@router.get("/projects/{pid}/manuscript")
def get_manuscript(pid: str):
    ctx, state = context(pid)
    ms.save(store, pid, state)
    return _view(pid, ctx, state)


# ---------------------------------------------------------------------------
# Terminology, inputs, plan, assets
# ---------------------------------------------------------------------------

@router.post("/projects/{pid}/manuscript/terms/auto")
def auto_terms(pid: str):
    ctx, state = context(pid)
    if not mfacts.terms_needed(store, pid):
        raise HTTPException(400, "сначала загрузите данные случая" if ctx["case_report"] else "сначала выполните анализ")
    _complete_terms(pid, ctx, state)
    return get_manuscript(pid)


def _complete_terms(pid, ctx, state, strict=True):
    """Translate every variable name and value that still has no English label (or a Cyrillic one).

    strict=False (figure preview, export): a failed AI call leaves the gaps; figures then fall back to
    transliteration and the checks list what is left."""
    missing = mfacts.untranslated(state["terms"], mfacts.terms_needed(store, pid))
    if not missing:
        return []
    try:
        out = agent.translate_terms(ctx["project"], [{k: m[k] for k in ("name", "group", "vtype", "unit", "levels")}
                                                     for m in missing])
        translated = out["terms"]
    except Exception:  # noqa: BLE001 — a preview or an export never fails because of the translation
        if strict:
            raise
        return missing
    mfacts.merge_terms(state["terms"], translated, missing)
    ms.save(store, pid, state)
    store.audit(pid, "agent", "manuscript.terms_translated", {"n": len(missing)})
    return mfacts.untranslated(state["terms"], mfacts.terms_needed(store, pid))


class TermsIn(BaseModel):
    terms: Dict[str, dict]


@router.put("/projects/{pid}/manuscript/terms")
def put_terms(pid: str, body: TermsIn):
    state = ms.load(store, pid)
    for name, t in body.terms.items():
        state["terms"][name] = {"en": (t.get("en") or "").strip(),
                                "levels": {k: v.strip() for k, v in (t.get("levels") or {}).items() if (v or "").strip()},
                                "source": "author"}
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.terms_saved", {"n": len(body.terms)})
    return get_manuscript(pid)


@router.put("/projects/{pid}/manuscript/inputs")
def put_inputs(pid: str, inputs: dict):
    state = ms.load(store, pid)
    state["inputs"] = inputs
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.inputs_saved", {"fields": sorted(inputs)})
    return get_manuscript(pid)


@router.post("/projects/{pid}/manuscript/plan")
def generate_plan(pid: str):
    ctx, state = context(pid)
    if ctx["case_report"]:
        if not ctx["analysis"]:
            raise HTTPException(400, "сначала загрузите данные случая и подтвердите анонимизацию")
    elif not ctx["analysis"]:
        raise HTTPException(400, "сначала выполните анализ")
    elif not ctx["findings"]:
        raise HTTPException(400, "нет принятых находок — примите находки на шаге «Анализ»")
    headings = _headings(ctx["project"], ctx["template"], {"plan": None})
    ctx["tables"], ctx["figures"] = ctx["all_tables"], ctx["all_figures"]  # the plan chooses among all
    out = agent.make_plan(ctx["project"], ctx, headings, (ctx["template"] or {}).get("limits", {}).get("words_total"))
    ids = {t["id"] for t in state["tables"]} | {f["id"] for f in state["figures"]}
    chosen = {x.split(":")[-1].strip("{} ") for x in out["tables"] + out["figures"]}
    for item in state["tables"] + state["figures"]:
        item["include"] = item["id"] in chosen or item["id"] == "t1"
    state["plan"] = {"key_messages": out["key_messages"], "sections": out["sections"], "rationale": out["rationale"],
                     "status": "draft", "created_at": now_iso(), "model": llm.used_model(llm.effective_model(llm.MODEL_REASONING)),
                     "unknown_assets": sorted(chosen - ids)}
    ms.save(store, pid, state)
    store.audit(pid, "agent", "manuscript.plan_generated", {"sections": len(out["sections"])})
    return get_manuscript(pid)


class PlanIn(BaseModel):
    key_messages: List[str]
    sections: List[dict]


@router.put("/projects/{pid}/manuscript/plan")
def put_plan(pid: str, body: PlanIn):
    state = ms.load(store, pid)
    plan = state.get("plan") or {"created_at": now_iso()}
    plan.update({"key_messages": [m for m in body.key_messages if m.strip()],
                 "sections": [{"heading": s["heading"].strip(), "points": [p for p in s.get("points", []) if p.strip()],
                               "words": int(s.get("words") or 0)} for s in body.sections if s.get("heading", "").strip()],
                 "status": "draft"})
    plan.pop("approved_at", None)
    state["plan"] = plan
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.plan_edited", {})
    return get_manuscript(pid)


@router.post("/projects/{pid}/manuscript/plan/approve")
def approve_plan(pid: str):
    state = ms.load(store, pid)
    if not state.get("plan") or not state["plan"].get("sections"):
        raise HTTPException(400, "план пуст")
    state["plan"]["status"] = "approved"
    state["plan"]["approved_at"] = now_iso()
    _, template = _profile_template(store.get(pid))
    ms.ensure_sections(state, [s["heading"] for s in state["plan"]["sections"]], mlang.of(template))
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.plan_approved", {})
    return get_manuscript(pid)


class AssetsIn(BaseModel):
    tables: List[dict] = []
    figures: List[dict] = []
    figure_language: Optional[str] = None


@router.put("/projects/{pid}/manuscript/assets")
def put_assets(pid: str, body: AssetsIn):
    state = ms.load(store, pid)
    for kind, items in (("tables", body.tables), ("figures", body.figures)):
        by_id = {x["id"]: x for x in items}
        for it in state[kind]:
            if it["id"] in by_id:
                upd = by_id[it["id"]]
                it["include"] = bool(upd.get("include", it["include"]))
                it["caption"] = (upd.get("caption") or "").strip() or None
                if "caption_en" in upd:  # English caption of a bilingual (Russian) journal
                    it["caption_en"] = (upd.get("caption_en") or "").strip() or None
    if body.figure_language is not None:
        if body.figure_language not in figs.LANGUAGES:
            raise HTTPException(400, "язык подписей: ru или en")
        state["figure_language"] = body.figure_language
    ms.save(store, pid, state)
    return get_manuscript(pid)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _section(state, key):
    sec = state["sections"].get(key)
    if sec is None:
        raise HTTPException(404, "раздел не найден")
    return sec


def _require_order(state, sec):
    if (state.get("plan") or {}).get("status") != "approved":
        raise HTTPException(400, "сначала согласуйте план статьи (FR-5.1)")
    if sec["kind"] == "abstract":
        need = [s for s in ms.ordered(state) if s["kind"] in ("results", "discussion", "case_presentation")]
    elif sec["kind"] == "abstract_en":  # translated from the accepted Russian abstract
        need = [s for s in ms.ordered(state) if s["kind"] == "abstract"]
    elif sec["kind"] == "statements":
        need = []
    else:
        need = [s for s in ms.ordered(state) if s["kind"] not in ("abstract", "abstract_en", "statements")
                and s["order"] < sec["order"]]
    missing = [s["heading"] for s in need if s["status"] != "accepted"]
    if missing:
        raise HTTPException(400, "сначала примите: " + ", ".join(missing))


def _apply_new_citations(pid, project, key, out):
    """Validate [[NEW:k]] citations proposed by the model; keep only grounded + verified ones."""
    chunks = lit.chunks_by_id(store, pid)
    text = out["text"]
    report = []
    grounded = []
    for c in out.get("new_citations", []):
        k = c["marker"].split(":")[-1].strip()
        chunk = chunks.get(c["chunk_id"])
        marker = re.compile(r"\[\[\s*NEW:" + re.escape(k) + r"\s*\]\]")
        if chunk is None or not quote_in_text(c["quote"], chunk["text"]):
            text = marker.sub("[уточнить: источник — предложенная цитата не прошла проверку]", text)
            report.append({"marker": c["marker"], "status": "rejected",
                           "reason": "фрагмента нет в базе" if chunk is None else "цитата не найдена во фрагменте"})
            continue
        report.append(None)  # filled after the batch verification, keeps the order of markers
        grounded.append((c, chunk, marker, len(report) - 1))
    checks = lit_agent.verify_citations(project, [(c["claim"], chunk, c["quote"]) for c, chunk, _, _ in grounded])
    for (c, chunk, marker, slot), verification in zip(grounded, checks):
        cit = lit.add_citation(store, pid, c["claim"], {"type": "section", "key": key}, chunk, c["quote"],
                               "supports", "agent", verification)
        text = marker.sub(f"[[{cit['id']}]]", text)
        report[slot] = {"marker": c["marker"], "status": "added", "citation": cit["id"],
                        "verification": verification["status"]}
    text = agent.NEW_MARKER.sub("[уточнить: источник для этого утверждения]", text)
    return text, report


def _accepted_sources(state, exclude_key):
    return "\n\n".join(f"## {s['heading']}\n{ms.current_source(s)}" for s in ms.ordered(state)
                       if s["key"] != exclude_key and s["status"] == "accepted" and s["kind"] != "statements")


@router.post("/projects/{pid}/manuscript/sections/{key}/generate")
def generate_section(pid: str, key: str):
    ctx, state = context(pid)
    sec = _section(state, key)
    _require_order(state, sec)
    fps = _fingerprints(ctx, state)
    fp = fps.get(sec["kind"], fps["other"])
    if sec["kind"] == "statements":
        source = statements.build(pid, state.get("inputs") or {}, ctx["template"], ctx["lang"])
        ms.add_version(state, key, source, "system", "сгенерировано из данных автора", fp)
        ms.save(store, pid, state)
        return get_manuscript(pid)
    plan = next((p for p in state["plan"]["sections"] if ms.slug(p["heading"]) == key), {})
    target = dict(sec, plan=plan, budget=plan.get("words") or (
        (ctx["template"] or {}).get("abstract", {}).get("words") if sec["kind"] == "abstract" else None))
    extra = _accepted_sources(state, key) if sec["kind"] in ("abstract", "discussion", "conclusion") else None
    if sec["kind"] == "abstract_en":
        ab = state["sections"]["abstract"]
        extra = "## Accepted Russian abstract\n" + ms.current_source(ab)
        target["budget"] = (ctx["template"] or {}).get("abstract", {}).get("words")
    if sec["kind"] == "abstract" and ctx["template"]:
        target["plan"] = {"structured": ctx["template"]["abstract"]["structured"],
                          "headings": ctx["template"]["abstract"]["headings"],
                          "words": ctx["template"]["abstract"]["words"]}
    has_lit = bool(lit.chunks(store, pid))
    out = agent.write_section(ctx["project"], target, ctx, _search_fn(pid) if has_lit else None, extra)
    text, report = _apply_new_citations(pid, ctx["project"], key, out)
    text = normalize_placeholders(text, ctx["facts"])
    ms.add_version(state, key, text, "agent", "черновик агента", fp,
                   {"questions": out.get("questions", []), "citations_report": report,
                    "queries": out.get("queries", [])})
    ms.save(store, pid, state)
    store.audit(pid, "agent", "manuscript.section_generated", {"section": key, "new_citations": len(report)})
    return get_manuscript(pid)


class ReplaceIn(BaseModel):
    find: str = Field(min_length=1, max_length=2000)
    replace: str = Field(default="", max_length=5000)


@router.post("/projects/{pid}/manuscript/sections/{key}/replace")
def replace_in_section(pid: str, key: str, body: ReplaceIn):
    """Quick fixes from the list of issues: fill an [уточнить: …] gap, drop a dead reference."""
    state = ms.load(store, pid)
    sec = _section(state, key)
    source = ms.current_source(sec)
    if body.find not in source:
        raise HTTPException(400, "этот фрагмент уже исправлен или изменён — обновите страницу")
    text = re.sub(r"[ \t]{2,}", " ", source.replace(body.find, body.replace.strip(), 1))
    ms.add_version(state, key, text, "author", "исправление из списка замечаний")
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.quick_fix", {"section": key})
    return get_manuscript(pid)


class SourceIn(BaseModel):
    source: str = Field(max_length=200_000)
    note: str = "правка автора"


@router.put("/projects/{pid}/manuscript/sections/{key}")
def edit_section(pid: str, key: str, body: SourceIn):
    state = ms.load(store, pid)
    sec = _section(state, key)
    ms.add_version(state, key, body.source, "author", body.note)
    if sec["status"] == "accepted":
        sec["status"] = "draft"
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.section_edited", {"section": key})
    return get_manuscript(pid)


class ReviseIn(BaseModel):
    instruction: str = Field(min_length=3, max_length=4000)
    selection: Optional[str] = Field(default=None, max_length=20000)


@router.post("/projects/{pid}/manuscript/sections/{key}/revise")
def revise(pid: str, key: str, body: ReviseIn):
    ctx, state = context(pid)
    sec = _section(state, key)
    source = ms.current_source(sec)
    if not source:
        raise HTTPException(400, "раздел ещё не написан")
    has_lit = bool(lit.chunks(store, pid))
    out = agent.revise_section(ctx["project"], sec, source, body.instruction, body.selection, ctx,
                               _search_fn(pid) if has_lit else None)
    text, report = _apply_new_citations(pid, ctx["project"], key, out)
    text = normalize_placeholders(text, ctx["facts"])
    ms.add_version(state, key, text, "agent", f"по инструкции: {body.instruction[:120]}", None,
                   {"questions": out.get("questions", []), "citations_report": report})
    sec["status"] = "draft"
    ms.save(store, pid, state)
    store.audit(pid, "agent", "manuscript.section_revised", {"section": key})
    return get_manuscript(pid)


@router.post("/projects/{pid}/manuscript/sections/{key}/accept")
def accept(pid: str, key: str):
    ctx, state = context(pid)
    ms.accept(state, key)
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.section_accepted", {"section": key})
    return get_manuscript(pid)


@router.post("/projects/{pid}/manuscript/sections/{key}/reopen")
def reopen(pid: str, key: str):
    state = ms.load(store, pid)
    _section(state, key)["status"] = "draft"
    ms.save(store, pid, state)
    return get_manuscript(pid)


class RollbackIn(BaseModel):
    n: int


@router.post("/projects/{pid}/manuscript/sections/{key}/rollback")
def rollback(pid: str, key: str, body: RollbackIn):
    state = ms.load(store, pid)
    ms.rollback(state, key, body.n)
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.section_rollback", {"section": key, "to": body.n})
    return get_manuscript(pid)


@router.get("/projects/{pid}/manuscript/sections/{key}/diff")
def diff(pid: str, key: str, a: int, b: int):
    state = ms.load(store, pid)
    return {"diff": ms.diff(_section(state, key), a, b)}


class CommentIn(BaseModel):
    quote: str = ""
    text: str = Field(min_length=1, max_length=4000)


@router.post("/projects/{pid}/manuscript/sections/{key}/comments")
def comment(pid: str, key: str, body: CommentIn):
    state = ms.load(store, pid)
    _section(state, key)
    ms.add_comment(state, key, body.quote, body.text)
    ms.save(store, pid, state)
    return get_manuscript(pid)


@router.post("/projects/{pid}/manuscript/sections/{key}/comments/{cid}/resolve")
def resolve(pid: str, key: str, cid: str):
    state = ms.load(store, pid)
    for c in _section(state, key)["comments"]:
        if c["id"] == cid:
            c["resolved"] = True
    ms.save(store, pid, state)
    return get_manuscript(pid)


class NumberIn(BaseModel):
    number: str = Field(pattern=r"^\d+(?:[.,]\d+)?$")


@router.post("/projects/{pid}/manuscript/sections/{key}/whitelist")
def whitelist(pid: str, key: str, body: NumberIn):
    state = ms.load(store, pid)
    sec = _section(state, key)
    wl = sec.setdefault("number_whitelist", [])
    if body.number not in wl:
        wl.append(body.number)
    ms.save(store, pid, state)
    store.audit(pid, "author", "manuscript.number_confirmed", {"section": key, "number": body.number})
    return get_manuscript(pid)


# ---------------------------------------------------------------------------
# Front matter, clichés, export
# ---------------------------------------------------------------------------

@router.post("/projects/{pid}/manuscript/front/generate")
def generate_front(pid: str):
    ctx, state = context(pid)
    if not any(s["status"] == "accepted" for s in state["sections"].values()):
        raise HTTPException(400, "сначала примите хотя бы один раздел")
    renderer = _renderer(pid, ctx, state)
    text = "\n\n".join(f"## {s['heading']}\n{renderer.text(ms.current_source(s), s['kind'])}" for s in ms.ordered(state)
                       if s["status"] == "accepted" and s["kind"] not in ("statements", "abstract_en"))
    tpl = dict(ctx["template"] or {}, running_title_chars=journals.value(ctx["profile"], "running_title.max_chars")
               if ctx["profile"] else None)
    out = agent.front_matter(ctx["project"], ctx, tpl, text)
    front = state.get("front") or {}
    if out.get("titles_en"):
        front["title_candidates_en"] = out["titles_en"]
        front["keywords_en"] = front.get("keywords_en") or out.get("keywords_en") or []
    front.update({"title_candidates": out["titles"], "running_title": front.get("running_title") or out["running_title"],
                  "keywords": front.get("keywords") or out["keywords"],
                  "highlights": front.get("highlights") or out["highlights"],
                  "suggested": {"running_title": out["running_title"], "keywords": out["keywords"],
                                "highlights": out["highlights"]}})
    state["front"] = front
    ms.save(store, pid, state)
    return get_manuscript(pid)


class FrontIn(BaseModel):
    title: Optional[str] = None
    title_en: Optional[str] = None
    running_title: Optional[str] = None
    keywords: Optional[List[str]] = None
    keywords_en: Optional[List[str]] = None
    highlights: Optional[List[str]] = None


@router.put("/projects/{pid}/manuscript/front")
def put_front(pid: str, body: FrontIn):
    state = ms.load(store, pid)
    front = state.get("front") or {}
    for k, v in body.model_dump(exclude_none=True).items():
        front[k] = [x.strip() for x in v if x.strip()] if isinstance(v, list) else v.strip()
    state["front"] = front
    ms.save(store, pid, state)
    return get_manuscript(pid)


class ClicheIn(BaseModel):
    pattern: str = Field(min_length=2, max_length=200)
    suggestion: str = ""


@router.get("/manuscript/cliches")
def list_cliches():
    return mchecks.cliches()


@router.post("/manuscript/cliches")
def add_cliche(body: ClicheIn):
    try:
        mchecks.add_cliche(body.pattern, body.suggestion)
    except re.error as exc:
        raise HTTPException(400, f"некорректное выражение: {exc}")
    return mchecks.cliches()


@router.get("/projects/{pid}/manuscript/figures/{fid}.png")
def figure_preview(pid: str, fid: str):
    ctx, state = context(pid)
    item = next((f for f in state["figures"] if f["id"] == fid), None)
    if item is None:
        raise HTTPException(404, "рисунок не найден")
    run_dir = engine.run_dir_latest(store, pid)
    spec = read_json(run_dir / "spec.json")
    # rendered as in the export (language and terminology can change at any time, so no cache)
    lang = _figure_language(ctx, state)
    if lang == "en":
        _complete_terms(pid, ctx, state, strict=False)
    out = run_dir / "figures" / f"manuscript_{fid}.png"
    out.parent.mkdir(exist_ok=True)
    if item["kind"] == "finding":
        f = assets.figure_data(item, ctx["analysis"], state["terms"])["finding"]
        path = figs.render_finding(run_dir, f, spec, out_path=out, terms=state["terms"], lang=lang) if f else None
    else:
        path = figs.render_overview(run_dir, item["kind"], spec, read_json(run_dir / "results.json", {}).get("clustering"),
                                    out_path=out, terms=state["terms"], lang=lang)
    if not path:
        raise HTTPException(404, "график недоступен")
    return Response(read_bytes(path), media_type="image/png")


def export_status(pid):
    ctx, state = context(pid)
    renderer = _renderer(pid, ctx, state)
    issues, counts = _check(pid, ctx, state, renderer)
    blocking = [i for i in issues if i["severity"] == "blocking"]
    for s in ms.ordered(state):
        if s["status"] != "accepted":
            blocking.append({"section": s["key"], "heading": s["heading"], "severity": "blocking",
                             "code": "not_accepted", "message": "раздел не принят автором", "fragment": "",
                             "suggestion": ""})
    if not (state.get("front") or {}).get("title"):
        blocking.append({"section": None, "heading": "Титульная страница", "severity": "blocking", "code": "title",
                         "message": "не выбрано название статьи", "fragment": "", "suggestion": ""})
    from .journals_api import project_journal
    pj = project_journal(pid)
    for item in (pj.get("checklist") or []):
        if item["blocking"]:
            blocking.append({"section": None, "heading": "Чек-лист журнала", "severity": "blocking",
                             "code": "journal_" + item["key"], "message": item["text"], "fragment": item["detail"],
                             "suggestion": ""})
    if not pj.get("profile"):
        blocking.append({"section": None, "heading": "Журнал", "severity": "blocking", "code": "no_journal",
                         "message": "не выбран целевой журнал", "fragment": "", "suggestion": ""})
    analysis = ctx["analysis"]
    if analysis:
        ru = ctx["lang"] == "ru"  # a Russian manuscript: only the English caption must be free of Cyrillic
        for t in state["tables"]:
            if t["include"]:
                data = assets.table_data(t, analysis, state["terms"], ctx["lang"])
                blob = data["caption_en"] if ru else " ".join(
                    [data["caption"], *data["columns"], *(c for row in data["rows"] for c in row)])
                if re.search(r"[А-Яа-яЁё]", blob):
                    blocking.append({"section": None, "heading": "Таблицы", "severity": "blocking", "code": "cyrillic_table",
                                     "message": f"в таблице «{data['caption'][:60]}» есть кириллица",
                                     "fragment": ", ".join(sorted(set(re.findall(r"[А-Яа-яЁё][А-Яа-яЁё ,%-]*", blob)))[:4]),
                                     "suggestion": "заполните английские термины на шаге «Терминология»"})
        for f in state["figures"]:
            fd = assets.figure_data(f, analysis, state["terms"], ctx["lang"])
            if f["include"] and re.search(r"[А-Яа-яЁё]", fd["caption_en"] if ru else fd["caption"]):
                blocking.append({"section": None, "heading": "Рисунки", "severity": "blocking", "code": "cyrillic_figure",
                                 "message": f"в подписи рисунка {f['id']} есть кириллица", "fragment": "", "suggestion": ""})
    from .ethics_api import review
    ethics = review(pid)
    blocking += [i for i in ethics["issues"] if i["severity"] == "blocking"]
    issues = issues + [i for i in ethics["issues"] if i["severity"] != "blocking"]
    from .cover_api import cover_status
    cover_issues, _ = cover_status(pid, ctx, state)
    blocking += [i for i in cover_issues if i["severity"] == "blocking"]
    issues = issues + [i for i in cover_issues if i["severity"] != "blocking"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    blocking, warnings = fixes.annotate_all(blocking, state), fixes.annotate_all(warnings, state)
    return {"blocking": blocking, "warnings": warnings, "counts": counts, "ctx": ctx, "state": state,
            "renderer": renderer}


@router.get("/projects/{pid}/manuscript/export/status")
def get_export_status(pid: str):
    st = export_status(pid)
    return {"blocking": st["blocking"], "warnings": st["warnings"], "counts": st["counts"],
            "ready": not st["blocking"]}


@router.post("/projects/{pid}/manuscript/export")
def do_export(pid: str, mode: str = "draft"):
    from .ethics_api import supplementary_csv
    ctx0, state0 = context(pid)
    _complete_terms(pid, ctx0, state0, strict=False)  # the manuscript is English: tables and figures too
    st = export_status(pid)
    if mode == "final" and st["blocking"]:
        raise HTTPException(409, f"экспорт для подачи заблокирован: {len(st['blocking'])} проблем(ы)")
    ctx, state, renderer = st["ctx"], st["state"], st["renderer"]
    analysis = ctx["analysis"]
    if not analysis:
        raise HTTPException(400, "нет данных случая" if ctx["case_report"] else "нет результатов анализа")
    lang = ctx["lang"]
    tables = [assets.table_data(t, analysis, state["terms"], lang) for t in state["tables"] if t["include"]]
    figures_ = [assets.figure_data(f, analysis, state["terms"], lang) for f in state["figures"] if f["include"]]
    tpl = ctx["template"] or {}
    formats = [x.lower() for x in (tpl.get("figures", {}).get("formats") or [])]
    fmt = "tif" if any("tif" in x for x in formats) else "png"
    dpi = tpl.get("figures", {}).get("dpi_photo") or tpl.get("figures", {}).get("dpi_line") or 300
    sections = [(s["heading"], s["kind"], ms.current_source(s)) for s in ms.ordered(state)]
    ectx = {"renderer": renderer, "front": state.get("front") or {}, "sections": sections, "tables": tables,
            "figures": figures_, "counts": st["counts"], "authors": (state.get("inputs") or {}).get("authors"),
            "figure_format": fmt, "dpi": int(dpi), "terms": state["terms"], "lang": lang,
            "figure_language": _figure_language(ctx, state)}
    if ctx["case_report"]:
        # no statistics to reproduce; the record of a single patient is not shared as supplementary data
        run_dir, spec, results, extra = None, None, {}, {}
    else:
        run_dir = engine.run_dir_latest(store, pid)
        spec, results = read_json(run_dir / "spec.json"), read_json(run_dir / "results.json", {})
        extra = {
            "supplementary/cases_anonymized.csv": supplementary_csv(pid),
            "supplementary/analysis.py": read_bytes(run_dir / "analysis.py"),
            "supplementary/analysis_data.csv": read_bytes(run_dir / "analysis_data.csv"),
            "supplementary/hypothesis_log.json": read_bytes(run_dir / "hypotheses.json"),
        }
    from .cover_api import letter_docx
    letter = letter_docx(pid, draft=mode != "final")
    if letter:
        extra["cover_letter.docx"] = letter
    data = export.package(ectx, mode != "final", st["blocking"] + st["warnings"], run_dir, spec, results, extra)
    store.audit(pid, "author", "manuscript.exported", {"mode": mode, "blocking": len(st["blocking"])})
    name = "manuscript_submission.zip" if mode == "final" else "manuscript_DRAFT.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


STATEMENT_HEADINGS = {"ethics": "Ethics approval", "consent": "Consent", "coi": "Conflict of interest",
                      "funding": "Funding", "data_availability": "Data availability",
                      "author_contributions": "Author contributions", "acknowledgements": "Acknowledgements",
                      "ai_disclosure": "Declaration of generative AI use"}


def checklist_updates(pid, tpl):
    """Resolve journal checklist items that depend on the manuscript text."""
    if not ms.path(store, pid).exists():
        return {}
    state = ms.load(store, pid)
    out = {}
    st = state["sections"].get("statements")
    if st and st["status"] == "accepted":
        src = ms.current_source(st)
        for key, heading in STATEMENT_HEADINGS.items():
            ok = f"### {heading}" in src and "[уточнить" not in src.split(f"### {heading}", 1)[1].split("###", 1)[0]
            out[f"statement_{key}"] = ("pass" if ok else "fail", "в разделе Statements" if ok else "нет или не заполнено")
    plan = state.get("plan") or {}
    if plan.get("status") == "approved" and tpl.get("sections"):
        want = [h.lower() for h in tpl["sections"] if not h.lower().startswith(("abstract", "reference"))]
        have = [s["heading"].lower() for s in plan["sections"]]
        out["sections"] = ("pass" if want == have else "fail", " → ".join(s["heading"] for s in plan["sections"]))
    ab = state["sections"].get("abstract")
    if ab and ab["versions"] and tpl.get("abstract", {}).get("structured"):
        heads = [h.strip().lower() for h in re.findall(r"^### (.+)$", ms.current_source(ab), re.M)]
        want = [h.lower() for h in tpl["abstract"]["headings"]]
        out["abstract_structure"] = ("pass" if heads == want else "fail", "в тексте: " + " / ".join(heads))
    cover = state.get("cover")
    if cover and cover.get("versions"):
        reqs = tpl.get("cover_letter", {}).get("requirements") or []
        done = cover["status"] == "accepted" and all(cover.get("confirmed", {}).get(r) for r in reqs)
        out["cover_letter"] = ("pass" if done else "fail",
                               "принят, требования подтверждены" if done else "не принят или требования не подтверждены")
    return out
