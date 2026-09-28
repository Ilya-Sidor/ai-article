import io
import json
import re
import zipfile

import pytest
from generate_sample import generate


class Claude:
    """Scripted model: answers by request type; section texts are set per test."""

    def __init__(self):
        self.sections = {}
        self.calls = []
        outer = self

        class Messages:
            @staticmethod
            def create(**kw):
                outer.calls.append(kw)
                return outer.respond(kw)

        class Beta:
            messages = Messages
        self.beta = Beta

    @staticmethod
    def _r(obj):
        class B:
            type = "text"
            text = json.dumps(obj)

        class R:
            stop_reason = "end_turn"
            content = [B()]
        return R()

    def respond(self, kw):
        system = kw.get("system", "")
        user = kw["messages"][0]["content"]
        if "Translate variable names" in system:
            names = json.loads(user)
            return self._r({"terms": [{"name": n["name"], "en": "EN " + n["name"],
                                       "levels": [{"value": v, "en": "en-" + v} for v in n["levels"]]} for n in names]})
        if "You plan a pathology manuscript" in system:
            heads = re.search(r"Section headings: (\[.*?\])", user).group(1)
            heads = json.loads(heads.replace("'", '"'))
            return self._r({"key_messages": ["KIT and PDGFRA mutations were mutually exclusive in this series."],
                            "sections": [{"heading": h, "points": ["пункт"], "words": 400} for h in heads],
                            "tables": ["{{TAB:t1}}"], "figures": [], "rationale": "ok"})
        if "front matter" in system:
            return self._r({"titles": ["Title A", "Title B", "Title C"], "running_title": "Run",
                            "keywords": ["GIST", "PDGFRA", "KIT", "CD117"], "highlights": ["H1", "H2", "H3"]})
        if "check citations" in system:
            items = json.loads(user)
            if isinstance(items, list):
                return self._r({"results": [{"index": it["index"], "verdict": "supported", "rationale": "ok"}
                                            for it in items]})
            return self._r({"verdict": "supported", "rationale": "ok"})
        if "Revise the section" in user:
            return self._r({"text": "Revised text without numbers.", "new_citations": [], "questions": []})
        kind = re.search(r"\(kind: (\w+)\)", user).group(1)
        return self._r({"text": self.sections.get(kind, f"Text of {kind}."), "new_citations": [], "questions": []})


@pytest.fixture()
def claude(monkeypatch):
    from app import llm
    c = Claude()
    monkeypatch.setattr(llm, "client", lambda: c)
    return c


@pytest.fixture()
def project(api):
    pid = api.post("/api/projects", json={"title": "GIST", "article_type": "original_article"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    res = api.post(f"/api/projects/{pid}/analysis/run").json()
    by_title = {f["title"]: f for f in res["findings"]}
    kit = next(f for t, f in by_title.items() if "KIT" in t and "PDGFRA" in t)
    expl = next(f for f in res["findings"] if f["evidence"] == "exploratory")
    for f in (kit, expl):
        api.post(f"/api/projects/{pid}/findings/{f['id']}", json={"status": "accepted"})
    return pid, kit, expl


def _sec(m, kind):
    return next(s for s in m["sections"] if s["kind"] == kind)


def test_facts_come_from_analysis(api, project):
    pid, kit, _ = project
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    facts = {f["id"]: f for f in m["facts"]}
    assert facts["n"]["value"] == "24"
    a1 = next(f for f in m["findings"] if f["title"] == kit["title"])["id"]
    assert facts[f"{a1}.p_expr"]["value"].startswith("p ")
    assert facts[f"{a1}.row2.col2"]["value"] == str(kit["result"]["table"][1][1])
    assert [t["id"] for t in m["tables"]][0] == "t1"


def test_full_flow(api, project, claude):
    pid, kit, expl = project
    m = api.post(f"/api/projects/{pid}/manuscript/terms/auto").json()
    assert m["terms"]["Пол"]["en"] == "EN Пол"
    ids = {f["title"]: f["id"] for f in m["findings"]}
    a_kit, a_expl = ids[kit["title"]], ids[expl["title"]]

    gen = api.post(f"/api/projects/{pid}/manuscript/sections/introduction/generate")
    assert gen.status_code == 404  # sections exist only after the plan is approved
    m = api.post(f"/api/projects/{pid}/manuscript/plan").json()
    assert m["plan"]["status"] == "draft" and len(m["plan"]["sections"]) == 5
    m = api.post(f"/api/projects/{pid}/manuscript/plan/approve").json()
    assert [s["kind"] for s in m["sections"]] == ["abstract", "introduction", "methods", "results", "discussion",
                                                  "conclusion", "statements"]
    K = {s["kind"]: s["key"] for s in m["sections"]}
    assert api.post(f"/api/projects/{pid}/manuscript/sections/{K['methods']}/generate").status_code == 400  # order

    claude.sections = {
        "introduction": "GIST is the commonest mesenchymal tumour of the gastrointestinal tract.",
        "methods": "Immunohistochemistry for CD117 was performed [уточнить: клон и разведение CD117].",
        "results": (f"The series comprised {{{{n}}}} cases ({{{{TAB:t1}}}}). KIT and PDGFRA mutations were mutually "
                    f"exclusive ({{{{{a_kit}.effect_expr}}}}; {{{{{a_kit}.p_expr}}}}). This demonstrates "
                    f"that {{{{{a_expl}.p_expr}}}} holds. In 173 cases there was necrosis. {{{{Z9.p}}}}"),
        "discussion": "Mutual exclusivity was observed. This study has limitations.",
        "conclusion": "KIT and PDGFRA alterations were mutually exclusive in this series.",
        "abstract": "### Aims\n\nTo describe {{n}} GISTs.",
    }
    for kind in ("introduction", "methods", "results", "discussion", "conclusion"):
        key = K[kind]
        r = api.post(f"/api/projects/{pid}/manuscript/sections/{key}/generate")
        assert r.status_code == 200, r.text
        m = r.json()
        sec = _sec(m, kind)
        assert sec["status"] == "draft" and sec["versions"][-1]["actor"] == "agent"
        if kind == "methods":
            assert any(i["code"] == "todo" and i["severity"] == "blocking" for i in sec["issues"])
        if kind == "results":
            codes = {i["code"]: i for i in sec["issues"]}
            assert codes["number_unmatched"]["severity"] == "blocking" and "173" in codes["number_unmatched"]["message"]
            assert codes["unknown_ref"]["severity"] == "blocking"
            assert codes["evidence_wording"]["severity"] == "warning"
            rendered = "".join(s["v"] for p in sec["segments"] for s in p["segments"])
            assert "24 cases (Table 1)" in rendered and "{{" in rendered  # unknown placeholder stays visible
            # author fixes: removes the unknown ref, confirms "17" manually
            fixed = sec["source"].replace("{{Z9.p}}", "").replace("This demonstrates", "This suggests")
            m = api.put(f"/api/projects/{pid}/manuscript/sections/{key}", json={"source": fixed}).json()
            m = api.post(f"/api/projects/{pid}/manuscript/sections/{key}/whitelist", json={"number": "173"}).json()
            assert not [i for i in _sec(m, "results")["issues"] if i["severity"] == "blocking"]
            assert len(_sec(m, "results")["versions"]) == 2
        api.post(f"/api/projects/{pid}/manuscript/sections/{key}/accept")

    # version history: diff and rollback (FR-0.3)
    d = api.get(f"/api/projects/{pid}/manuscript/sections/results/diff", params={"a": 1, "b": 2}).json()["diff"]
    assert any(line.startswith("-") and "demonstrates" in line for line in d)
    m = api.post(f"/api/projects/{pid}/manuscript/sections/results/rollback", json={"n": 1}).json()
    assert _sec(m, "results")["versions"][-1]["note"] == "откат к версии 1"
    m = api.post(f"/api/projects/{pid}/manuscript/sections/results/rollback", json={"n": 2}).json()
    api.post(f"/api/projects/{pid}/manuscript/sections/results/accept")

    # revise by instruction on a selection
    m = api.post(f"/api/projects/{pid}/manuscript/sections/{K['conclusion']}/revise",
                 json={"instruction": "сократи", "selection": "mutually exclusive"}).json()
    assert _sec(m, "conclusion")["source"] == "Revised text without numbers." and _sec(m, "conclusion")["status"] == "draft"
    api.post(f"/api/projects/{pid}/manuscript/sections/{K['conclusion']}/accept")

    m = api.post(f"/api/projects/{pid}/manuscript/sections/abstract/generate").json()
    api.post(f"/api/projects/{pid}/manuscript/sections/abstract/accept")
    m = api.post(f"/api/projects/{pid}/manuscript/sections/statements/generate").json()
    st = _sec(m, "statements")
    assert "Declaration of generative AI use" in st["source"] and "claude" in st["source"].lower()
    assert any(i["code"] == "todo" for i in st["issues"])  # ethics etc. not provided yet
    inputs = {"ethics": {"committee": "Local Ethics Committee", "approval_number": "12/2025"},
              "consent": {"status": "waived"}, "coi": "The authors declare no competing interests.",
              "funding": "None.", "authors": [{"name": "A. Author", "roles": ["Conceptualization"]}],
              "ihc": [{"marker": "CD117", "clone": "YR145", "dilution": "1:200"}]}
    api.put(f"/api/projects/{pid}/manuscript/inputs", json=inputs)
    m = api.post(f"/api/projects/{pid}/manuscript/sections/statements/generate").json()
    assert not [i for i in _sec(m, "statements")["issues"] if i["code"] == "todo"]
    api.post(f"/api/projects/{pid}/manuscript/sections/statements/accept")

    m = api.post(f"/api/projects/{pid}/manuscript/front/generate").json()
    assert m["front"]["title_candidates"][0] == "Title A"
    api.put(f"/api/projects/{pid}/manuscript/front", json={"title": "Title A"})

    # dependencies: un-accepting a finding makes Results stale and its placeholders unknown
    api.post(f"/api/projects/{pid}/findings/{expl['id']}", json={"status": "rejected"})
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    res = _sec(m, "results")
    assert res["stale"] and any(i["code"] == "unknown_ref" for i in res["issues"])
    api.post(f"/api/projects/{pid}/findings/{expl['id']}", json={"status": "accepted"})

    # export: final blocked (methods TODO, no journal), draft allowed
    status = api.get(f"/api/projects/{pid}/manuscript/export/status").json()
    codes = {b["code"] for b in status["blocking"]}
    assert "todo" in codes and "no_journal" in codes
    assert api.post(f"/api/projects/{pid}/manuscript/export", params={"mode": "final"}).status_code == 409
    r = api.post(f"/api/projects/{pid}/manuscript/export", params={"mode": "draft"})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"manuscript.docx", "manuscript.md", "supplementary/analysis.py"} <= names
    import docx
    doc = docx.Document(io.BytesIO(z.read("manuscript.docx")))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Title A" in text and "24 cases (Table 1)" in text and "DRAFT" in doc.sections[0].header.paragraphs[0].text
    assert len(doc.tables) == 1 and doc.tables[0].rows[0].cells[1].text == "All cases (n = 24)"

    # LLM calls carried the privacy gate context and never case-level rows
    for call in claude.calls:
        blob = json.dumps(call.get("messages"), default=str, ensure_ascii=False)
        assert "C-001" not in blob and "PT-0001" not in blob


def test_new_citations_are_checked(api, project, monkeypatch):
    from app import manuscript_api
    from app.literature import service as lit
    pid, _, _ = project
    monkeypatch.setattr(lit, "chunks_by_id", lambda store, pid: {
        "S-001:001": {"id": "S-001:001", "source_id": "S-001", "page": 1, "section": "Results",
                      "text": "CD117 was negative in 9 of 20 PDGFRA-mutant tumours."}})
    added = []
    monkeypatch.setattr(lit, "add_citation", lambda *a, **k: added.append(a) or {"id": "CIT-0042"})
    from app.literature import agent as lit_agent
    monkeypatch.setattr(lit_agent, "verify_citations",
                        lambda project, items: [{"status": "supported", "rationale": ""} for _ in items])
    out = {"text": "Loss of CD117 is described [[NEW:1]] and also [[NEW:2]] and [[NEW:3]].",
           "new_citations": [
               {"marker": "NEW:1", "chunk_id": "S-001:001", "quote": "CD117 was negative in 9 of 20", "claim": "c"},
               {"marker": "NEW:2", "chunk_id": "S-001:001", "quote": "CD117 was negative in all", "claim": "c"},
               {"marker": "NEW:3", "chunk_id": "S-404:001", "quote": "whatever text here", "claim": "c"}]}
    text, report = manuscript_api._apply_new_citations(pid, {"id": pid}, "discussion", out)
    assert "[[CIT-0042]]" in text and text.count("[уточнить") == 2
    assert [r["status"] for r in report] == ["added", "rejected", "rejected"] and len(added) == 1


def test_checks_unit():
    from app.manuscript import checks
    from app.manuscript.render import Renderer
    facts = {"A1.p_expr": {"value": "p = 0.010", "desc": "", "kind": "expr"},
             "n": {"value": "24", "desc": "", "kind": "number"}}
    r = Renderer(facts, {"tables": [], "figures": []}, [], [], "nlm-citation-sequence", [])
    secs = [{"key": "discussion", "kind": "discussion", "heading": "Discussion", "whitelist": [],
             "source": "We delve into the tumor biology. Furthermore, x. Moreover, y. Additionally, z. "
                       "Immunohistochemistry (IHC) was used; FISH was positive. The series had 24 cases."}]
    issues, counts = checks.check(secs, r, facts, [], {"language_variant": "UK"}, "small", "", [], {})
    codes = [i["code"] for i in issues]
    assert "cliche" in codes and "spelling" in codes and "limitations" in codes
    assert any(i["code"] == "abbreviation" and "FISH" in i["message"] for i in issues)
    assert not any(i["code"] == "abbreviation" and "IHC" in i["message"] for i in issues)
    assert any(i["code"] == "number_literal" for i in issues)  # 24 typed instead of {{n}}


def test_latex_from_model_json_does_not_break_export():
    """Regression: a model wrote "$q \\bigwedge ge 0.05$" in JSON; "\\b" decoded to a backspace and python-docx
    refused the whole draft export (Internal Server Error)."""
    import io
    import json

    from docx import Document

    from app import llm
    from app.manuscript import checks, export
    from app.manuscript.render import plain_text
    raw = '{"text": "with $p < 0.05$ and $q \\\\geq 0.05$, \\\\chi^2 test; q \\bigwedge 1 \\frac12 \\u0007"}'
    text = llm._parse_json(raw)["text"]
    assert "\x08" not in text and "\x0c" not in text and "\x07" not in text
    assert "\\bigwedge" in text and "\\frac12" in text
    out = plain_text(text)
    assert "p < 0.05" in out and "q ≥ 0.05" in out and "χ" in out and "$" not in out
    assert plain_text("costs $5 and $10 each") == "costs $5 and $10 each"
    assert plain_text("bad \x08eta \x0crac \x01x") == "bad β \\frac x"
    assert checks.LATEX_LEFTOVER.search(out).group(0) == "\\bigwedge"

    class R:
        bibliography = [{"text": "Ref \x0b1"}]

        def segments(self, source):
            return [{"type": "p", "segments": [{"t": "text", "v": source}]}]
    ctx = {"renderer": R(), "front": {"title": "T\x08", "keywords": ["k\x01"]}, "authors": [],
           "sections": [("Methods", "methods", "q \x08igwedge ge 0.05")], "counts": {}, "tables": [
               {"caption": "c\x02", "columns": ["a\x03"], "rows": [["v\x04"]], "footnote": "f\x05"}],
           "figures": []}
    data = export.build_docx(ctx, True, [{"severity": "warning", "heading": "h", "message": "m\x06"}])
    body = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    assert "q \\bigwedge ge 0.05" in body
    json.dumps(body)


def test_fact_ids_in_backticks_are_rendered_as_values():
    """Regression: a model wrote `V2.median` instead of {{V2.median}}; the draft showed raw ids to the reader."""
    from app.manuscript.checks import LATEX_LEFTOVER
    from app.manuscript.render import normalize_placeholders
    known = {"n": {}, "V2.median": {}, "V3.L1.pct": {}}
    src = "series (`n` cases), median `V2.median` years, female [`{{V3.L1.pct}}`%], {TAB:t1}, see `KIT` and {x}"
    out = normalize_placeholders(src, known)
    assert out == "series ({{n}} cases), median {{V2.median}} years, female [{{V3.L1.pct}}%], {{TAB:t1}}, " \
                  "see `KIT` and {x}"
    assert normalize_placeholders("{{n}} stays", known) == "{{n}} stays"
    assert LATEX_LEFTOVER.search(out).group(0) == "`KIT`"


def test_subheading_without_blank_line_keeps_paragraph_and_facts():
    """Regression: a structured abstract came as "### Results\\n<text with {{A1.p_expr}}>" and the whole
    paragraph was rendered as a heading with raw placeholders."""
    from app.manuscript.render import Renderer
    r = Renderer.__new__(Renderer)
    r.facts = {"A1.p_expr": {"value": "p = 0.003", "desc": "p", "kind": "text"}}
    paras = r.segments("## Results\nKi-67 correlated ({{A1.p_expr}}).\n### Conclusions\nDone.")
    assert [p["type"] for p in paras] == ["heading", "p", "heading", "p"]
    assert paras[0]["segments"][0]["v"] == "Results"
    assert any(s["t"] == "fact" and s["v"] == "p = 0.003" for s in paras[1]["segments"])
