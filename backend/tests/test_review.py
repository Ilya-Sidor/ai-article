"""Consistency check and simulated reviewer: remarks quote the manuscript, unverifiable ones are dropped."""
import pytest
from test_manuscript import Claude, _sec, project  # noqa: F401


class ReviewClaude(Claude):
    def respond(self, kw):
        system = kw.get("system", "")
        system = system if isinstance(system, str) else " ".join(b["text"] for b in system)
        if "internal consistency" in system:
            return self._r({"issues": [
                {"kind": "conclusion_overreach", "section": "Conclusion", "quote": "KIT and PDGFRA alterations were",
                 "problem": "вывод сильнее данных", "suggestion": "смягчи вывод"},
                {"kind": "other", "section": "Results", "quote": "a sentence that is not in the text at all",
                 "problem": "x", "suggestion": "y"}]})
        if "peer reviewer" in system:
            return self._r({"recommendation": "major_revision", "summary": "Хорошая серия.", "strengths": ["данные"],
                            "comments": [{"severity": "minor", "section": "Discussion", "quote": "Mutual exclusivity was observed",
                                          "comment": "мало литературы", "suggestion": "добавь сравнение"},
                                         {"severity": "major", "section": "Methods", "quote": "Immunohistochemistry for CD117",
                                          "comment": "нет клона", "suggestion": "укажи клон"}]})
        return super().respond(kw)


@pytest.fixture()
def claude(monkeypatch):
    from app import llm
    c = ReviewClaude()
    monkeypatch.setattr(llm, "client", lambda: c)
    return c


def test_consistency_and_review(api, project, claude):  # noqa: F811
    pid, kit, expl = project
    api.post(f"/api/projects/{pid}/manuscript/terms/auto")
    api.post(f"/api/projects/{pid}/manuscript/plan")
    m = api.post(f"/api/projects/{pid}/manuscript/plan/approve").json()
    K = {s["kind"]: s["key"] for s in m["sections"]}
    claude.sections = {"introduction": "GIST is common.", "methods": "Immunohistochemistry for CD117 was performed.",
                       "results": "The series comprised {{n}} cases.",
                       "discussion": "Mutual exclusivity was observed. This study has limitations.",
                       "conclusion": "KIT and PDGFRA alterations were mutually exclusive."}
    for kind in ("introduction", "methods", "results", "discussion", "conclusion"):
        api.post(f"/api/projects/{pid}/manuscript/sections/{K[kind]}/generate")
        api.post(f"/api/projects/{pid}/manuscript/sections/{K[kind]}/accept")

    m = api.post(f"/api/projects/{pid}/manuscript/consistency").json()
    assert [k["id"] for k in m["consistency"]["issues"]] == ["K1"] and m["consistency"]["dropped"] == 1
    assert m["consistency"]["issues"][0]["section"] == K["conclusion"]
    warn = next(i for s in m["sections"] for i in s["issues"] if i["code"] == "consistency")
    assert warn["fix"]["actions"][0]["type"] == "revise" and warn["fix"]["actions"][0]["instruction"] == "смягчи вывод"

    m = api.post(f"/api/projects/{pid}/manuscript/review", json={"strictness": "strict"}).json()
    r = m["review"]
    assert r["recommendation"] == "major_revision" and [c["severity"] for c in r["comments"]] == ["major", "minor"]
    assert r["comments"][0]["section"] == K["methods"] and not r["comments"][0]["addressed"]
    call = claude.calls[-1]
    sys_text = call["system"] if isinstance(call["system"], str) else " ".join(b["text"] for b in call["system"])
    assert "Reviewer 2" in sys_text and "24 cases" in call["messages"][0]["content"]  # rendered numbers

    # the author rewrites the passage: the remark shows as probably addressed; dismissing a consistency remark
    api.put(f"/api/projects/{pid}/manuscript/sections/{K['methods']}",
            json={"source": "CD117 (clone YR145) was stained."})
    m = api.post(f"/api/projects/{pid}/manuscript/remarks/K1", json={"status": "dismissed"}).json()
    assert m["review"]["comments"][0]["addressed"]
    assert not [i for s in m["sections"] for i in s["issues"] if i["code"] == "consistency"]
