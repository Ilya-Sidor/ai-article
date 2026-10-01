"""Case report (n = 1): no statistics, manuscript per the CARE guidelines (FR-1.10)."""
import io
import json
import zipfile

import pandas as pd
import pytest
from test_manuscript import Claude, _sec

REPORT = ("Пациент 54 лет поступил с жалобами на слабость. При обследовании выявлена опухоль желудка 42 мм. "
          "Выполнена резекция желудка. Гистологически — эпителиоидная гастроинтестинальная стромальная опухоль, "
          "митозы 3 на 5 мм2. ИГХ: CD117 положительный, DOG1 положительный. Наблюдение 24 мес без рецидива.")


class CaseClaude(Claude):
    def respond(self, kw):
        system = kw.get("system", "")
        system = system if isinstance(system, str) else " ".join(b["text"] for b in system)
        if "You plan a pathology CASE REPORT" in system:
            user = kw["messages"][0]["content"]
            heads = json.loads(user.split("Section headings: ")[1].split("\n")[0].replace("'", '"'))
            return self._r({"key_messages": ["Epithelioid GIST may mimic carcinoma."],
                            "sections": [{"heading": h, "points": ["пункт"], "words": 300} for h in heads],
                            "tables": ["{{TAB:t1}}"], "figures": [], "rationale": "CARE"})
        return super().respond(kw)


@pytest.fixture()
def claude(monkeypatch):
    from app import llm
    c = CaseClaude()
    monkeypatch.setattr(llm, "client", lambda: c)
    return c


@pytest.fixture()
def case(api):
    pid = api.post("/api/projects", json={"title": "ГИСО: наблюдение", "article_type": "case_report"}).json()["id"]
    row = pd.DataFrame([{"Возраст": "54", "Пол": "М", "Локализация": "желудок", "Размер, мм": "42",
                         "CD117": "положительный", "Заключение": REPORT}])
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("case.csv", row.to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    return pid


def test_case_report_without_analysis(api, case, claude):
    pid = case
    assert api.post(f"/api/projects/{pid}/analysis/run").status_code == 400  # statistics need a series, as before
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    assert m["case_report"] and m["has_analysis"] and m["n_cases"] == 1
    facts = {f["id"]: f for f in m["facts"]}
    size = next(k for k, f in facts.items() if "Размер" in f["desc"])
    age = next(k for k, f in facts.items() if "Возраст" in f["desc"])
    assert facts[size]["value"] == "42" and size.endswith(".c1")
    assert [t["kind"] for t in m["tables"]] == ["case_data"] and m["figures"] == []
    assert {t["name"] for t in m["terms_needed"]} >= {"Возраст", "Пол", "CD117"}

    api.post(f"/api/projects/{pid}/manuscript/terms/auto")
    m = api.post(f"/api/projects/{pid}/manuscript/plan").json()
    assert m["plan"]["key_messages"] == ["Epithelioid GIST may mimic carcinoma."]
    m = api.post(f"/api/projects/{pid}/manuscript/plan/approve").json()
    assert [s["kind"] for s in m["sections"]] == ["abstract", "introduction", "case_presentation", "discussion",
                                                  "conclusion", "statements"]

    claude.sections = {
        "introduction": "Epithelioid GIST is rare.",
        "case_presentation": (f"### Patient information\n\nA {{{{{age}}}}}-year-old man.\n\n"
                              f"### Clinical findings\n\nA gastric mass of {{{{{size}}}}} mm ({{{{TAB:t1}}}}).\n\n"
                              "### Diagnostic assessment\n\nCD117 was positive.\n\n"
                              "### Therapeutic intervention\n\nGastric resection.\n\n"
                              "### Follow-up and outcomes\n\nNo recurrence at 24 months."),
        "discussion": "This case adds to the literature. This report has limitations.",
        "conclusion": "Epithelioid GIST should be considered.",
    }
    for kind in ("introduction", "case_presentation", "discussion", "conclusion"):
        key = _sec(m, kind)["key"]
        r = api.post(f"/api/projects/{pid}/manuscript/sections/{key}/generate")
        assert r.status_code == 200, r.text
        m = r.json()
        if kind == "case_presentation":
            sec = _sec(m, kind)
            rendered = "".join(s["v"] for p in sec["segments"] for s in p["segments"])
            assert "A 54-year-old man" in rendered and "42 mm (Table 1)" in rendered
            # "24" is written in the case documents (the author's data), so it is not an unmatched number
            assert not [i for i in sec["issues"] if i["code"] == "number_unmatched" and i["severity"] == "blocking"]
        api.post(f"/api/projects/{pid}/manuscript/sections/{key}/accept")
    call = next(c for c in claude.calls if "(kind: case_presentation)" in c["messages"][0]["content"])
    assert "CARE 5–10" in call["messages"][0]["content"] and "эпителиоидная" in call["messages"][0]["content"]

    status = api.get(f"/api/projects/{pid}/manuscript/export/status").json()
    codes = {i["code"] for i in status["blocking"] + status["warnings"]}
    assert "care_consent" in {b["code"] for b in status["blocking"]}  # CARE 13
    assert "care_timeline" in codes  # no timeline written yet (CARE 7)
    assert "care_patient_information" not in codes and "care_strengths" not in codes

    api.put(f"/api/projects/{pid}/manuscript/inputs", json={"consent": {"status": "written"},
                                                           "patient_perspective": "I am grateful."})
    m = api.post(f"/api/projects/{pid}/manuscript/sections/statements/generate").json()
    assert "### Patient perspective" in _sec(m, "statements")["source"]
    status = api.get(f"/api/projects/{pid}/manuscript/export/status").json()
    assert "care_consent" not in {b["code"] for b in status["blocking"] + status["warnings"]}

    r = api.post(f"/api/projects/{pid}/manuscript/export", params={"mode": "draft"})
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert not [n for n in z.namelist() if n.startswith(("supplementary/", "figures/"))]
    import docx
    doc = docx.Document(io.BytesIO(z.read("manuscript.docx")))
    t = doc.tables[0]
    assert [c.text for c in t.rows[0].cells] == ["Feature", "Value"]
    assert any(r.cells[1].text == "42" for r in t.rows)


def test_case_series_is_unchanged(api):
    pid = api.post("/api/projects", json={"title": "S", "article_type": "case_series"}).json()["id"]
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    assert not m["case_report"] and not m["has_analysis"]
    assert api.post(f"/api/projects/{pid}/manuscript/plan").status_code == 400
