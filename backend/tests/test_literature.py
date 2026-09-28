import io
import json

import pytest

from app.literature import citations as cit_mod
from app.literature import metadata as md
from app.literature.index import BM25, make_chunks, split_sentences
from app.literature.parsing import jats_blocks, pdf_blocks, pdf_pages

DOI = "10.1000/gist.2024.001"
TITLE = "PDGFRA-mutant gastrointestinal stromal tumours of the stomach: a clinicopathological series"


def make_pdf(title=TITLE, doi=DOI, body=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    body = body or {
        "Abstract": ["We studied 31 gastric GISTs with PDGFRA mutation."],
        "Introduction": ["Gastrointestinal stromal tumours usually harbour KIT mutations."],
        "Results": ["Epithelioid morphology was present in 24 of 31 PDGFRA-mutant tumours.",
                    "CD117 expression was weak or absent in 12 of 31 tumours, whereas DOG1 was positive in all cases."],
        "Discussion": ["Loss of CD117 staining in PDGFRA-mutant GIST may lead to misdiagnosis."],
        "References": ["1. Smith J. KIT mutations in GIST. Some Journal 2001;1:1-10."],
    }
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800

    def line(text, size=10):
        nonlocal y
        if y < 60:
            c.showPage()
            y = 800
        c.setFont("Helvetica", size)
        c.drawString(50, y, text)
        y -= 16

    line(title, 12)
    line(f"https://doi.org/{doi}")
    line("")
    for head, paras in body.items():
        line("")
        line(head, 11)
        for p in paras:
            line(p)
    c.save()
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


CROSSREF = {"type": "journal-article", "title": [TITLE], "DOI": DOI.upper(),
            "author": [{"family": "Ivanova", "given": "Anna"}, {"family": "Brown", "given": "Tom"}],
            "container-title": ["Journal of Tumour Pathology"], "short-container-title": ["J Tumour Pathol"],
            "published-print": {"date-parts": [[2024, 5]]}, "volume": "12", "issue": "3", "page": "100-110"}
PUBMED_SUMMARY = {"uid": "39000001", "title": "PDGFRA-mutant GIST of the stomach.", "pubdate": "2023 Mar 4",
                  "authors": [{"name": "Petrov AB", "authtype": "Author"}], "source": "Virchows Arch",
                  "fulljournalname": "Virchows Archiv", "volume": "482", "issue": "3", "pages": "501-9",
                  "articleids": [{"idtype": "doi", "value": "10.1000/other.2023"}, {"idtype": "pmc",
                                                                                    "value": "PMC1234567"}],
                  "pubtype": ["Journal Article"]}
JATS = b"""<pmc-articleset><article><front><article-meta><abstract><p>Twenty PDGFRA-mutant GISTs were analysed.</p>
</abstract></article-meta></front><body><sec><title>Results</title><p>CD117 was negative in 9 of 20 tumours.</p>
<table-wrap><label>Table 1</label><caption><p>Immunophenotype</p></caption><table><tr><td>DOG1</td><td>20/20</td></tr>
</table></table-wrap></sec><sec><title>References</title><p>Should not be indexed.</p></sec></body></article>
</pmc-articleset>"""


@pytest.fixture()
def fake_http(monkeypatch):
    calls = []

    def fetch(url, params=None, accept=None):
        calls.append((url, params))
        if "api.crossref.org/works/" in url:
            doi = url.split("/works/")[1].lower()
            return FakeResponse(200, {"message": CROSSREF}) if doi == DOI else FakeResponse(404)
        if url.endswith("/works"):
            return FakeResponse(200, {"message": {"items": [CROSSREF]}})
        if "esearch" in url:
            return FakeResponse(200, {"esearchresult": {"idlist": ["39000001"] if "doi" not in params["term"]
                                                        else []}})
        if "esummary" in url:
            return FakeResponse(200, {"result": {"uids": ["39000001"], "39000001": PUBMED_SUMMARY}})
        if "efetch" in url and params["db"] == "pmc":
            return FakeResponse(200, content=JATS)
        if "efetch" in url:
            return FakeResponse(200, content=b"<x><AbstractText>Abstract text here.</AbstractText></x>")
        return FakeResponse(404)

    monkeypatch.setattr(md, "fetch", fetch)
    return calls


# ---------------------------------------------------------------------------
# parsing, chunking, retrieval
# ---------------------------------------------------------------------------

def test_pdf_sections_and_references_excluded():
    pages = pdf_pages(make_pdf())
    blocks = pdf_blocks(pages)
    sections = {b["section"] for b in blocks}
    assert {"Abstract", "Introduction", "Results", "Discussion", "References"} <= sections
    chunks = make_chunks("S-001", blocks)
    assert all(c["section"] != "References" for c in chunks)
    assert not any("Some Journal" in c["text"] for c in chunks)
    assert all(c["page"] == 1 for c in chunks)


def test_jats_blocks():
    blocks = jats_blocks(JATS)
    text = " ".join(b["text"] for b in blocks)
    assert "CD117 was negative in 9 of 20" in text and "DOG1 | 20/20" in text
    assert "Should not be indexed" not in text


def test_bm25_ranking_and_synonyms():
    chunks = [
        {"id": "S-001:001", "source_id": "S-001", "page": 1, "section": "Results",
         "text": "KIT expression was lost in PDGFRA-mutant tumours with epithelioid morphology."},
        {"id": "S-002:001", "source_id": "S-002", "page": 1, "section": "Introduction",
         "text": "Leiomyosarcoma is a smooth muscle tumour with desmin expression."},
    ]
    hits = BM25(chunks).search("CD117 loss PDGFRA")
    assert hits[0]["id"] == "S-001:001"
    assert len(hits) == 1


def test_sentence_split_keeps_abbreviations():
    s = split_sentences("As shown by Smith et al. in 2001, GIST is rare. Fig. 2 shows staining. It was strong.")
    assert len(s) == 3 and s[0].startswith("As shown by Smith et al. in 2001")


# ---------------------------------------------------------------------------
# citations
# ---------------------------------------------------------------------------

CHUNK_TEXT = ("CD117 expression was weak or absent in 12 of 31 tumours, whereas DOG1 was posi-\ntive in all "
              "cases. Epithelioid morphology predominated.")


def test_quote_verification():
    assert cit_mod.quote_in_text("DOG1 was positive in all cases", CHUNK_TEXT)
    assert cit_mod.quote_in_text("CD117 expression was weak or absent in 12 of 31 tumours", CHUNK_TEXT)
    assert not cit_mod.quote_in_text("CD117 expression was absent in all 31 tumours", CHUNK_TEXT)
    assert not cit_mod.quote_in_text("tumours DOG1 cases morphology CD117", CHUNK_TEXT)
    assert not cit_mod.quote_in_text("short", CHUNK_TEXT)
    assert cit_mod.quote_in_text("CD117 expression was weak … DOG1 was positive in all cases", CHUNK_TEXT)
    assert cit_mod.quote_in_text("“DOG1 was positive in all cases”", CHUNK_TEXT)
    assert not cit_mod.quote_in_text("DOG1 was not positive in all cases", CHUNK_TEXT)


def test_check_evidence_rejects_invented_sources():
    chunks = {"S-001:001": {"id": "S-001:001", "source_id": "S-001", "text": CHUNK_TEXT}}
    ok, rejected = cit_mod.check_evidence([
        {"chunk_id": "S-001:001", "quote": "DOG1 was positive in all cases"},
        {"chunk_id": "S-009:001", "quote": "anything at all here"},
        {"chunk_id": "S-001:001", "quote": "KIT was mutated in every tumour"},
    ], chunks)
    assert len(ok) == 1 and ok[0]["source_id"] == "S-001"
    assert [r["reason"] for r in rejected] == ["фрагмента нет в базе проекта", "цитата не найдена во фрагменте"]


def test_bibliography_styles():
    sources = [
        {"id": "S-001", "csl": md.crossref_to_csl(CROSSREF)},
        {"id": "S-002", "csl": md.pubmed_to_csl(PUBMED_SUMMARY)},
    ]
    r = cit_mod.render(sources, ["S-002", "S-001"], "nlm-citation-sequence")
    assert [e["source_id"] for e in r["entries"]] == ["S-002", "S-001"]
    assert r["markers"]["S-002"] == "(1)" and r["markers"]["S-001"] == "(2)"
    assert "Petrov AB" in r["entries"][0]["text"]
    apa = cit_mod.render(sources, ["S-002", "S-001"], "apa")
    assert [e["source_id"] for e in apa["entries"]] == ["S-001", "S-002"]  # alphabetical
    assert apa["markers"]["S-001"].startswith("(Ivanova")


def test_reference_file_parsing():
    ris = "TY  - JOUR\nTI  - One\nDO  - 10.1/ABC\nER  - \nTY  - JOUR\nTI  - Two\nAN  - 12345678\nER  - \n"
    assert md.parse_reference_file(ris) == [{"doi": "10.1/abc", "pmid": None, "title": "One"},
                                             {"doi": None, "pmid": "12345678", "title": "Two"}]
    bib = "@article{a,\n title = {Three},\n doi = {10.2/XY},\n}\n@article{b,\n title={Four},\n pmid={7654321}\n}"
    recs = md.parse_reference_file(bib)
    assert recs[0]["doi"] == "10.2/xy" and recs[1]["pmid"] == "7654321"


def test_pubmed_metadata_and_retraction():
    csl = md.pubmed_to_csl(PUBMED_SUMMARY)
    assert csl["author"] == [{"family": "Petrov", "given": "A. B."}]
    assert csl["issued"] == {"date-parts": [[2023, 3, 4]]} and csl["PMCID"] == "PMC1234567"
    assert md.pubmed_retraction(dict(PUBMED_SUMMARY, pubtype=["Retracted Publication"]))["status"] == "retracted"
    assert md.crossref_retraction({"updated-by": [{"type": "retraction", "DOI": "10.9/r"}]})["status"] == "retracted"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _project(api):
    return api.post("/api/projects", json={"title": "GIST"}).json()["id"]


def test_add_pdf_verifies_metadata(api, fake_http):
    pid = _project(api)
    r = api.post(f"/api/projects/{pid}/literature/pdf", files={"files": ("paper.pdf", make_pdf())}).json()
    assert not r["errors"]
    src = r["added"][0]
    assert src["doi"] == DOI and src["verification"]["status"] == "verified"
    assert src["label"] == "Ivanova 2024" and src["n_chunks"] >= 3
    dup = api.post(f"/api/projects/{pid}/literature/pdf", files={"files": ("copy.pdf", make_pdf())}).json()
    assert "уже в базе" in dup["errors"][0]["error"]
    wrong = make_pdf(title="Completely different title about lymphoma", doi="10.1000/unknown",
                     body={"Results": ["Diffuse large B-cell lymphoma cases were reviewed."]})
    r2 = api.post(f"/api/projects/{pid}/literature/pdf", files={"files": ("x.pdf", wrong)}).json()
    assert r2["added"][0]["verification"]["status"] == "not_found"
    hits = api.get(f"/api/projects/{pid}/literature/search", params={"q": "CD117 absent PDGFRA"}).json()
    assert hits[0]["source_id"] == src["id"] and hits[0]["source_label"] == "Ivanova 2024"


def test_add_pmid_uses_pmc_fulltext(api, fake_http):
    pid = _project(api)
    r = api.post(f"/api/projects/{pid}/literature/identifiers", json={"values": ["39000001"]}).json()
    src = r["added"][0]
    assert src["metadata_source"] == "PubMed" and src["fulltext"]["status"] == "pmc"
    chunks = api.get(f"/api/projects/{pid}/literature/sources/{src['id']}/chunks").json()
    assert any("9 of 20" in c["text"] for c in chunks)
    comp = api.get(f"/api/projects/{pid}/literature").json()["completeness"]
    assert comp["unused"] == [src["id"]]


class FakeClaude:
    """Scripted Claude: one search, then a final answer with one real and two invalid citations."""

    def __init__(self):
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
    def _msg(stop, blocks):
        class R:
            stop_reason = stop
            content = blocks
        return R()

    @staticmethod
    def _text(obj):
        class B:
            type = "text"
            text = json.dumps(obj)
        return B()

    def respond(self, kw):
        system = kw.get("system", "")
        if "check citations" in system:
            return self._msg("end_turn", [self._text({"verdict": "supported", "rationale": "фрагмент подтверждает"})])
        if kw.get("tools"):
            if len(kw["messages"]) == 1:
                class T:
                    type = "tool_use"
                    id = "tu_1"
                    name = "search_literature"
                    input = {"query": "PDGFRA CD117 negative epithelioid"}
                return self._msg("tool_use", [T()])
            results = json.loads(kw["messages"][-1]["content"][0]["content"])
            real = results[0]["chunk_id"]
            return self._msg("end_turn", [self._text({
                "literature_status": "described_consistent", "summary": "Описано в серии Ivanova 2024.",
                "novelty": "Наблюдение согласуется с литературой.",
                "evidence": [
                    {"chunk_id": real, "quote": "CD117 expression was weak or absent in 12 of 31 tumours",
                     "relation": "supports", "note": "CD117 was weak or absent in 12 of 31 PDGFRA-mutant GISTs."},
                    {"chunk_id": "S-099:001", "quote": "invented fragment text", "relation": "supports",
                     "note": "x"},
                    {"chunk_id": real, "quote": "CD117 was negative in all PDGFRA-mutant tumours",
                     "relation": "supports", "note": "y"},
                ]})])
        raise AssertionError("unexpected call")


def test_compare_finding_keeps_only_grounded_citations(api, fake_http, monkeypatch):
    from generate_sample import generate

    from app import llm
    pid = _project(api)
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    api.post(f"/api/projects/{pid}/analysis/run")
    api.post(f"/api/projects/{pid}/literature/pdf", files={"files": ("paper.pdf", make_pdf())})
    fake = FakeClaude()
    monkeypatch.setattr(llm, "client", lambda: fake)

    findings = api.get(f"/api/projects/{pid}/literature/findings").json()
    target = next(f for f in findings if "PDGFRA" in f["title"] and "CD117" in f["title"])
    r = api.post(f"/api/projects/{pid}/literature/findings/{target['id']}/compare")
    assert r.status_code == 200, r.text
    comp = next(f for f in r.json() if f["id"] == target["id"])["comparison"]
    assert comp["literature_status"] == "described_consistent"
    assert len(comp["citations"]) == 1 and len(comp["rejected"]) == 2
    cit = comp["citations"][0]
    assert cit["verification"]["status"] == "supported" and cit["decision"] == "pending"
    assert cit["source_label"] == "Ivanova 2024" and cit["page"] == 1

    # tool loop: the tool result went back in the same conversation, gate applied
    loop_calls = [c for c in fake.calls if c.get("tools")]
    assert len(loop_calls) == 2 and loop_calls[1]["messages"][1]["role"] == "assistant"

    analysis = api.get(f"/api/projects/{pid}/analysis").json()
    f = next(x for x in analysis["findings"] if x["id"] == target["id"])
    assert f["literature"]["n_sources_with_evidence"] == 1

    # bibliography contains only accepted citations
    assert api.get(f"/api/projects/{pid}/literature/bibliography").json()["entries"] == []
    api.post(f"/api/projects/{pid}/literature/citations/{cit['id']}", json={"decision": "accepted"})
    bib = api.get(f"/api/projects/{pid}/literature/bibliography").json()
    assert len(bib["entries"]) == 1 and "Ivanova" in bib["entries"][0]["text"]
    assert api.get(f"/api/projects/{pid}/literature").json()["completeness"]["blocking"] == []


def test_manual_citation_and_blocking(api, fake_http):
    pid = _project(api)
    src = api.post(f"/api/projects/{pid}/literature/pdf", files={"files": ("p.pdf", make_pdf())}).json()["added"][0]
    chunk = api.get(f"/api/projects/{pid}/literature/sources/{src['id']}/chunks").json()[0]
    bad = api.post(f"/api/projects/{pid}/literature/citations",
                   json={"claim": "x claim", "chunk_id": chunk["id"], "quote": "text that is not there at all"})
    assert bad.status_code == 400
    missing = api.post(f"/api/projects/{pid}/literature/citations", json={"claim": "x claim", "chunk_id": "S-9:001"})
    assert missing.status_code == 400
    ok = api.post(f"/api/projects/{pid}/literature/citations",
                  json={"claim": "Claim about GIST", "chunk_id": chunk["id"]}).json()
    api.post(f"/api/projects/{pid}/literature/citations/{ok['id']}", json={"decision": "accepted"})
    blocking = api.get(f"/api/projects/{pid}/literature").json()["completeness"]["blocking"]
    assert blocking and "не подтверждена" in blocking[0]


def test_literature_llm_gate_for_findings(api, fake_http, monkeypatch):
    """Comparing a finding sends aggregated patient-derived data: gate must apply."""
    from app import llm
    from app.literature import agent
    called = []
    monkeypatch.setattr(llm, "client", lambda: called.append(1))
    project = {"id": "x", "anonymization": {"status": "pending"}, "focus": ""}
    with pytest.raises(llm.PrivacyGateError):
        agent.compare_finding(project, {"title": "t"}, lambda q: [], {})
    assert not called


TEI_XML = b"""<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>PDGFRA GIST</title></titleStmt>
<sourceDesc><biblStruct><idno type="DOI">10.1000/GIST.2024.001</idno></biblStruct></sourceDesc></fileDesc>
<profileDesc><abstract><div><p coords="1,10,10,100,10">We studied 31 gastric GISTs.</p></div></abstract></profileDesc>
</teiHeader><text><body><div><head>Results</head><p coords="4,1,1,1,1">CD117 was absent in 12 of 31 tumours.</p></div>
<div><head>References</head><p>Should not be indexed.</p></div>
<figure><head>Figure 2</head><figDesc>DOG1 staining.</figDesc></figure></body></text></TEI>"""


def test_grobid_blocks_used_when_configured(api, fake_http, monkeypatch):
    from app.literature import grobid
    monkeypatch.setenv("AI_ARTICLE_GROBID_URL", "http://grobid:8070")
    monkeypatch.setattr(grobid, "post", lambda endpoint, content: FakeResponse(200, content=TEI_XML))
    pid = api.post("/api/projects", json={"title": "g"}).json()["id"]
    src = api.post(f"/api/projects/{pid}/literature/pdf", files={"files": ("p.pdf", make_pdf())}).json()["added"][0]
    assert src["fulltext"]["parser"] == "GROBID"
    chunks = api.get(f"/api/projects/{pid}/literature/sources/{src['id']}/chunks").json()
    assert any(c["page"] == 4 and c["section"] == "Results" and "12 of 31" in c["text"] for c in chunks)
    assert not any("Should not be indexed" in c["text"] for c in chunks)
    monkeypatch.setattr(grobid, "post", lambda endpoint, content: FakeResponse(503))
    pid2 = api.post("/api/projects", json={"title": "g2"}).json()["id"]
    src2 = api.post(f"/api/projects/{pid2}/literature/pdf", files={"files": ("p.pdf", make_pdf())}).json()["added"][0]
    assert src2["fulltext"]["parser"] == "pypdf"  # graceful fallback
