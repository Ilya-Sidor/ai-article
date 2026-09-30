import json
from datetime import datetime, timedelta, timezone

import pytest

from app.journals import schema, store as journals
from app.literature import metadata as md

GUIDELINES = """Journal of Test Pathology — Author Guidelines
Original Articles should not exceed 4000 words, excluding abstract, references, tables and figure legends.
The abstract must be structured under the headings Aims, Methods and results, Conclusions and must not
exceed 250 words. A maximum of 6 tables and figures in total is permitted. Up to 50 references.
Case Reports are not accepted.
Please use British English spelling throughout.
References should be numbered consecutively in the order in which they are first cited (Vancouver style).
All manuscripts must include a statement on ethical approval. A conflict of interest statement is required
for all authors. Authors must disclose the use of generative AI and AI-assisted technologies in the
Methods section; AI tools cannot be listed as authors.
Photomicrographs must include scale bars and be supplied at a minimum resolution of 300 dpi in TIFF format.
""" + ("Additional editorial policies apply to all submissions. " * 20)


class Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {"content-type": "text/html"}

    def json(self):
        return json.loads(self.content)


class FakeClaude:
    def __init__(self, fields):
        outer = self
        self.calls = []

        class Messages:
            @staticmethod
            def create(**kw):
                outer.calls.append(kw)

                class B:
                    type = "text"
                    text = json.dumps({"fields": fields})

                class R:
                    stop_reason = "end_turn"
                    content = [B()]
                return R()

        class Beta:
            messages = Messages
        self.beta = Beta


EXTRACTED = [
    {"path": "types.original_article.words_total", "value": "4000",
     "quote": "Original Articles should not exceed 4000 words"},
    {"path": "types.original_article.abstract_structured", "value": "true",
     "quote": "The abstract must be structured under the headings Aims, Methods and results, Conclusions"},
    {"path": "types.original_article.abstract_words", "value": "250", "quote": "must not\nexceed 250 words"},
    {"path": "types.original_article.max_tables_figures", "value": "6",
     "quote": "A maximum of 6 tables and figures in total is permitted"},
    {"path": "types.original_article.max_references", "value": "50", "quote": "Up to 50 references"},
    {"path": "types.case_report.accepted", "value": "false", "quote": "Case Reports are not accepted"},
    {"path": "language_variant", "value": "UK", "quote": "Please use British English spelling throughout"},
    {"path": "statements.ethics", "value": "required", "quote": "must include a statement on ethical approval"},
    {"path": "statements.ai_disclosure", "value": "required",
     "quote": "Authors must disclose the use of generative AI and AI-assisted technologies"},
    {"path": "figures.dpi_photo", "value": "300", "quote": "minimum resolution of 300 dpi"},
    # invented quote → must be flagged
    {"path": "types.original_article.max_authors", "value": "8", "quote": "No more than 8 authors are allowed"},
    # manual value exists → must not be overwritten
    {"path": "keywords.max", "value": "10", "quote": "Please use British English spelling throughout"},
]


@pytest.fixture()
def journal(data_dir):
    p = journals.create("Journal of Test Pathology", "TestPub", "https://example.org/guidelines")
    journals.add_source(p["id"], "text", text=GUIDELINES)
    return p["id"]


def test_starter_base_has_no_hardcoded_values(data_dir):
    journals.seed()
    profiles = journals.list_profiles()
    assert len(profiles) == 8
    for s in profiles:
        p = journals.get(s["id"])
        assert p["fields"] == {} and s["status"] == "empty" and p["guidelines_url"].startswith("https://")


def test_russian_journal_names_get_distinct_ids(data_dir):
    a = journals.create("Вопросы онкологии")
    b = journals.create("Онкопедиатрия")
    assert (a["id"], b["id"]) == ("voprosy-onkologii", "onkopediatriya")


def test_extraction_grounding_and_missing(journal, monkeypatch):
    from app import llm
    from app.journals import extract
    journals.set_fields(journal, {"keywords.max": {"value": "6"}})
    monkeypatch.setattr(llm, "client", lambda: FakeClaude(EXTRACTED))
    items, meta = extract.extract_profile(journals.get(journal), journals.source_texts(journals.get(journal)))
    p = journals.apply_extraction(journal, items, meta)
    f = p["fields"]
    assert f["types.original_article.words_total"] == {**f["types.original_article.words_total"], "value": 4000,
                                                       "status": "grounded"}
    assert f["types.original_article.abstract_words"]["status"] == "grounded"  # line break in quote is fine
    assert f["types.case_report.accepted"]["value"] is False
    assert f["types.original_article.max_authors"]["status"] == "unverified"
    assert f["keywords.max"] == {**f["keywords.max"], "value": 6, "status": "manual"}
    assert f["figures.formats"]["status"] == "missing"  # not returned → explicitly "not stated"
    assert p["extraction"]["skipped_manual"] == ["keywords.max"]


def test_verification_workflow(journal, monkeypatch):
    from app import llm
    from app.journals import extract
    monkeypatch.setattr(llm, "client", lambda: FakeClaude(EXTRACTED))
    items, meta = extract.extract_profile(journals.get(journal), journals.source_texts(journals.get(journal)))
    journals.apply_extraction(journal, items, meta)
    with pytest.raises(journals.JournalError, match="без подтверждающей цитаты"):
        journals.verify(journal, "Dr Test")
    journals.set_fields(journal, {"types.original_article.max_authors": {"missing": True}})
    p = journals.verify(journal, "Dr Test", "по сайту журнала")
    assert journals.status(p) == "verified" and p["verification"]["guidelines_url"] == "https://example.org/guidelines"
    p = journals.set_fields(journal, {"keywords.max": {"value": "5"}})
    assert journals.status(p) == "outdated_verification"
    p = journals.verify(journal, "Dr Test")
    p["verification"]["verified_at"] = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    assert journals.status(p) == "stale"


def test_guidelines_change_detected(journal):
    sid = journals.get(journal)["sources"][0]["id"]
    same = journals.add_source(journal, "text", text=GUIDELINES + "\n", replaces=sid)
    assert same["changed"] is False
    new_text = GUIDELINES.replace("4000 words", "3500 words")
    r = journals.add_source(journal, "text", text=new_text, replaces=sid)
    assert r["changed"] and any("3500" in line for line in r["diff"]["diff"])
    assert journals.status(journals.get(journal)) == "changed"
    with pytest.raises(journals.JournalError):
        journals.verify(journal, "Dr Test")
    journals.acknowledge_change(journal)
    assert journals.get(journal)["guidelines_changed"] is None


def test_url_fetch_reports_bot_challenge(data_dir, monkeypatch):
    p = journals.create("Blocked Journal")
    monkeypatch.setattr(md, "fetch", lambda url, params=None, accept=None: Resp(
        200, b"<html><title>Client Challenge</title><body>Client Challenge. Enable JavaScript</body></html>"))
    with pytest.raises(journals.JournalError, match="не робот"):
        journals.add_source(p["id"], "url", url="https://example.org/g")
    monkeypatch.setattr(md, "fetch", lambda url, params=None, accept=None: Resp(403, b"Forbidden"))
    with pytest.raises(journals.JournalError, match="403"):
        journals.add_source(p["id"], "url", url="https://example.org/g")
    html = ("<html><body><nav>menu</nav><h1>Guide for authors</h1><p>" + GUIDELINES + "</p>"
            "<script>var x=1</script></body></html>").encode()
    monkeypatch.setattr(md, "fetch", lambda url, params=None, accept=None: Resp(200, html))
    r = journals.add_source(p["id"], "url", url="https://example.org/g")
    text = journals.source_texts(journals.get(p["id"]))[r["source"]["id"]]
    assert "4000 words" in text and "var x" not in text and "menu" not in text


def test_parse_values():
    assert schema.parse_value("int", "not exceed 3,500 words") == 3500
    assert schema.parse_value("list", "TIFF, EPS; PDF") == ["TIFF", "EPS", "PDF"]
    assert schema.parse_value("enum:UK|US", "uk") == "UK"
    with pytest.raises(ValueError):
        schema.parse_value("enum:UK|US", "Australian")


DEPENDENT = (b'<style xmlns="http://purl.org/net/xbiblio/csl"><info><title>Journal of Test Pathology</title>'
             b'<link href="http://www.zotero.org/styles/nlm-citation-sequence" rel="independent-parent"/></info>'
             b'</style>')


def test_project_journal_applies_style_and_checklist(api, journal, monkeypatch):
    def fetch(url, params=None, accept=None):
        if url.endswith("/dependent/journal-of-test-pathology.csl"):
            return Resp(200, DEPENDENT)
        return Resp(404, b"")
    monkeypatch.setattr(md, "fetch", fetch)
    journals.set_fields(journal, {"citation.csl_id": {"value": "journal-of-test-pathology"},
                                  "types.original_article.max_references": {"value": "0"},
                                  "statements.ai_disclosure": {"value": "required"}})
    pid = api.post("/api/projects", json={"title": "T", "article_type": "original_article"}).json()["id"]
    r = api.put(f"/api/projects/{pid}/journal", json={"journal_id": journal}).json()
    assert r["applied"]["style"] == "journal-of-test-pathology"
    assert r["project"]["journal"] == "Journal of Test Pathology"
    lit = api.get(f"/api/projects/{pid}/literature").json()
    assert lit["settings"]["style"] == "journal-of-test-pathology"
    assert "journal-of-test-pathology" in api.get("/api/literature/styles").json()

    j = api.get(f"/api/projects/{pid}/journal").json()
    by_key = {i["key"]: i for i in j["checklist"]}
    assert by_key["profile_verified"]["status"] == "fail" and by_key["profile_verified"]["blocking"]
    assert by_key["citation_style"]["status"] == "pass"
    assert by_key["statement_ai_disclosure"]["status"] == "pending"
    assert by_key["references_limit"]["status"] == "pass"  # 0 cited, limit 0
    assert j["template"]["limits"]["references"] == 0
    j = api.post(f"/api/projects/{pid}/journal/checklist", json={"key": "ethics_approval", "done": True}).json()
    assert {i["key"]: i for i in j["checklist"]}["ethics_approval"]["status"] == "pass"
