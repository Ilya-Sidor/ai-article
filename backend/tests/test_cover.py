import io
import json
import zipfile

import pytest
from generate_sample import generate

GOOD_BODY = ("We submit the manuscript entitled \"PDGFRA-mutant GIST: a series\" as an Original Article. "
             "The series of {{n}} cases shows mutually exclusive KIT and PDGFRA mutations, an observation that fits "
             "the diagnostic focus of your readers.\n\nThe work is original, has not been published and is not under "
             "consideration elsewhere. All authors have approved the manuscript. The authors declare no competing "
             "interests.\n\nThank you for considering our manuscript.")


class Claude:
    def __init__(self, body):
        self.body = body
        self.calls = []
        outer = self

        class Messages:
            @staticmethod
            def create(**kw):
                outer.calls.append(kw)

                class B:
                    type = "text"
                    text = json.dumps({"body": outer.body, "questions": ["уточните отделение"],
                                       "requirements_coverage": [{"requirement": "Explain novelty", "addressed": True,
                                                                  "where": "The series of"}]})

                class R:
                    stop_reason = "end_turn"
                    content = [B()]
                return R()

        class Beta:
            messages = Messages
        self.beta = Beta


@pytest.fixture()
def manuscript(api, data_dir):
    from app.journals import store as journals
    j = journals.create("Journal of Test Pathology", "TP", "https://example.org/g")
    journals.add_source(j["id"], "text", text="Cover letters must explain the novelty of the work. " * 20)
    journals.set_fields(j["id"], {"cover_letter.requirements": {"value": "Explain novelty"},
                                  "cover_letter.editor": {"value": "Jane Smith"}})
    pid = api.post("/api/projects", json={"title": "GIST", "article_type": "original_article"}).json()["id"]
    api.put(f"/api/projects/{pid}/journal", json={"journal_id": j["id"]})
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    res = api.post(f"/api/projects/{pid}/analysis/run").json()
    kit = next(f for f in res["findings"] if "KIT" in f["title"] and "PDGFRA" in f["title"])
    api.post(f"/api/projects/{pid}/findings/{kit['id']}", json={"status": "accepted"})
    api.put(f"/api/projects/{pid}/manuscript/plan", json={"key_messages": ["m"], "sections": [
        {"heading": h, "points": [], "words": 100} for h in ("Introduction", "Results", "Discussion")]})
    m = api.post(f"/api/projects/{pid}/manuscript/plan/approve").json()
    for s in m["sections"]:
        api.put(f"/api/projects/{pid}/manuscript/sections/{s['key']}", json={"source": f"Text of {s['heading']}."})
        api.post(f"/api/projects/{pid}/manuscript/sections/{s['key']}/accept")
    return pid


def test_prerequisites(api, manuscript):
    v = api.get(f"/api/projects/{manuscript}/cover").json()
    assert v["settings"]["editor"] == "Jane Smith"  # from the journal profile
    assert any("название" in p for p in v["prerequisites"])
    r = api.post(f"/api/projects/{manuscript}/cover/generate")
    assert r.status_code == 400 and "FR-6.1" in r.json()["detail"]


def test_cover_flow(api, manuscript, monkeypatch):
    from app import llm
    pid = manuscript
    api.put(f"/api/projects/{pid}/manuscript/front", json={"title": "PDGFRA-mutant GIST: a series"})
    claude = Claude(GOOD_BODY.replace("All authors have approved the manuscript. ", "") + " This groundbreaking work.")
    monkeypatch.setattr(llm, "client", lambda: claude)
    api.put(f"/api/projects/{pid}/cover/settings", json={
        "tone": "personal", "corresponding_name": "Anna Author", "corresponding_email": "anna@example.org",
        "suggested_reviewers": "Prof. Reviewer One, Univ X, one@example.org — GIST expert"})
    v = api.post(f"/api/projects/{pid}/cover/generate").json()
    codes = {i["code"] for i in v["issues"]}
    assert {"missing_approval", "requirements", "cover_not_accepted"} <= codes and "hype" in codes
    assert v["coverage"][0]["addressed"] and v["questions"] == ["уточните отделение"]
    letter = "\n".join(b["text"] for b in v["letter"])
    assert "Dear Dr Smith," in letter and "one@example.org" in letter and "Anna Author" in letter
    assert "24 cases" in letter  # fact placeholder rendered by code

    # reviewers and the corresponding author never reach the model
    blob = json.dumps([c["messages"] for c in claude.calls], ensure_ascii=False)
    assert "one@example.org" not in blob and "Anna Author" not in blob and "anna@example.org" not in blob

    v = api.put(f"/api/projects/{pid}/cover/body", json={"body": GOOD_BODY}).json()
    assert not {"missing_approval", "hype"} & {i["code"] for i in v["issues"]}
    d = api.get(f"/api/projects/{pid}/cover/diff", params={"a": 1, "b": 2}).json()["diff"]
    assert any("groundbreaking" in line for line in d)
    v = api.post(f"/api/projects/{pid}/cover/confirm", json={"requirement": "Explain novelty", "done": True}).json()
    v = api.post(f"/api/projects/{pid}/cover/accept").json()
    assert v["status"] == "accepted" and v["blocking"] == 0

    j = api.get(f"/api/projects/{pid}/journal").json()
    assert {i["key"]: i for i in j["checklist"]}["cover_letter"]["status"] == "pass"
    r = api.post(f"/api/projects/{pid}/manuscript/export", params={"mode": "draft"})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "cover_letter.docx" in z.namelist()
    import docx
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(z.read("cover_letter.docx"))).paragraphs)
    assert "Dear Dr Smith," in text and "Yours sincerely," in text
