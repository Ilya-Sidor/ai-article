"""Journal suggestions and similar published cases from PubMed / Europe PMC (queries written by the model)."""
import pytest

SUMMARY = {"result": {"uids": ["1", "2", "3"],
                      "1": {"uid": "1", "title": "Epithelioid GIST of the stomach.", "fulljournalname": "Histopathology",
                            "source": "Histopathology", "pubdate": "2024 Jan", "authors": [{"name": "Ivanova A"}],
                            "articleids": [{"idtype": "doi", "value": "10.1/x"}]},
                      "2": {"uid": "2", "title": "PDGFRA GIST.", "fulljournalname": "Modern pathology", "source": "Mod Pathol",
                            "pubdate": "2023", "authors": [], "articleids": []},
                      "3": {"uid": "3", "title": "Another GIST.", "fulljournalname": "Histopathology",
                            "source": "Histopathology", "pubdate": "2022", "authors": [], "articleids": []}}}


class R:
    def __init__(self, data, status=200):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


@pytest.fixture()
def net(monkeypatch):
    from app import llm
    from app.literature import metadata as md
    calls = []

    def fetch(url, params=None, accept=None):
        calls.append((url, params))
        if "esearch" in url:
            return R({"esearchresult": {"idlist": ["1", "2", "3"]}})
        if "esummary" in url:
            return R(SUMMARY)
        if "europepmc" in url:
            return R({"resultList": {"result": [{"pmid": "1", "isOpenAccess": "Y"},
                                                {"pmid": "9", "title": "EPMC case.", "journalTitle": "J", "pubYear": "2025",
                                                 "isOpenAccess": "N"}]}})
        return R({}, 404)
    monkeypatch.setattr(md, "fetch", fetch)
    monkeypatch.setattr(llm, "_call", lambda *a, **k: {"topic_query": "gastrointestinal stromal tumors[mh] AND epithelioid",
                                                        "cases_query": "epithelioid GIST stomach", "keywords": ["GIST"]})
    return calls


def test_discover_journals_and_cases(api, net):
    from app.journals import store as journals
    journals.seed()
    pid = api.post("/api/projects", json={"title": "Epithelioid GIST", "article_type": "case_report"}).json()["id"]
    r = api.post(f"/api/projects/{pid}/literature/discover", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    js = out["journals"]["journals"]
    assert js[0]["journal"] == "Histopathology" and js[0]["n"] == 2 and js[0]["profile"]["id"] == "histopathology"
    assert js[1]["profile"]["id"] == "modern-pathology"  # matched despite case differences
    assert "case reports[pt]" in out["cases"]["query"]
    cases = {c["pmid"]: c for c in out["cases"]["cases"]}
    assert cases["1"]["open_access"] is True and cases["9"]["source"] == "Europe PMC"
    assert api.get(f"/api/projects/{pid}/literature/discover").json()["at"] == out["at"]

    # the author edits the queries: no AI call, the edited query is used
    from app import llm
    llm._call = None  # would fail if called
    out = api.post(f"/api/projects/{pid}/literature/discover",
                   json={"queries": {"topic_query": "my query", "cases_query": "my cases"}}).json()
    assert out["queries"]["topic_query"] == "my query"
    assert any("my query" in (p or {}).get("term", "") for _, p in net)


def test_novelty_claim_flagged_when_similar_cases_exist(api, net):
    pid = api.post("/api/projects", json={"title": "GIST", "article_type": "case_report"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("c.csv", "Локализация,CD117\nжелудок,+\n".encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    api.put(f"/api/projects/{pid}/manuscript/plan", json={"key_messages": ["k"],
                                                         "sections": [{"heading": "Discussion", "points": [], "words": 100}]})
    api.post(f"/api/projects/{pid}/manuscript/plan/approve")
    api.put(f"/api/projects/{pid}/manuscript/sections/discussion", json={"source": "This is the first reported case."})
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    assert not [i for s in m["sections"] for i in s["issues"] if i["code"] == "novelty_claim"]  # nothing searched yet
    api.post(f"/api/projects/{pid}/literature/discover", json={})
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    claim = next(i for s in m["sections"] for i in s["issues"] if i["code"] == "novelty_claim")
    assert claim["fragment"] == "first reported" and claim["fix"]["actions"][0]["type"] == "revise"
