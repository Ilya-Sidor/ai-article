import io
import json
import zipfile

import pandas as pd
from generate_sample import generate

from app.ethics import reid, similarity, transparency
from app.manuscript.render import Renderer


def _renderer():
    return Renderer({}, {"tables": [], "figures": []}, [], [], "nlm-citation-sequence", [])


def test_text_reidentification():
    secs = [{"key": "case", "heading": "Case", "source":
             "A 54-year-old female farmer was admitted to Springfield Hospital in March 2019. "
             "The tumour measured 38 mm."}]
    issues = reid.text_risks(secs, _renderer(), 24, False)
    assert len(issues) == 1 and "точный возраст" in issues[0]["message"] and "возраст диапазоном" in issues[0]["suggestion"]
    mild = [{"key": "r", "heading": "R", "source": "Patients were aged 38–81 years; 12 were female."}]
    assert reid.text_risks(mild, _renderer(), 24, False) == []


def test_k_anonymity_and_supplementary():
    df = generate().astype(str)
    df.insert(0, "case_id", [f"C-{i:03d}" for i in range(len(df))])
    df = df.drop(columns=["ФИО", "Телефон", "СНИЛС", "Дата рождения", "Дата операции", "Дата последнего контакта"])
    from app.ingest import build_dictionary
    dictionary = build_dictionary(df)
    risk = reid.data_risk(df, dictionary, {"age_bands": True, "age_band_width": 10, "exclude_text": True,
                                           "drop_columns": []})
    assert {q["kind"] for q in risk["qi_columns"]} == {"age", "sex", "site"}
    assert len(risk["after"]["unique_cases"]) < len(risk["before"]["unique_cases"])
    out = reid.supplementary_frame(df, dictionary, {"age_bands": True, "exclude_text": True, "drop_columns": ["Пол"]})
    assert "Заключение" not in out.columns and "Пол" not in out.columns
    assert "№ истории болезни" not in out.columns
    assert out["Возраст"].str.contains("–").all()


def test_overlap_detection():
    source = ("gastrointestinal stromal tumours with pdgfra mutations are typically located in the stomach and show "
              "epithelioid morphology with weak or absent kit expression in a substantial proportion of cases")
    chunks = [{"id": "S-001:001", "source_id": "S-001", "page": 3, "section": "Discussion", "text": source}]
    copied = ("As reported, gastrointestinal stromal tumours with PDGFRA mutations are typically located in the "
              "stomach and show epithelioid morphology with weak or absent KIT expression in a substantial "
              "proportion of cases.")
    short = "Tumours with PDGFRA mutations are typically located in the stomach and were studied here."
    quoted = f'Smith wrote: "{source}".'
    secs = [{"key": "d", "heading": "Discussion", "source": copied}, {"key": "i", "heading": "Intro", "source": short},
            {"key": "q", "heading": "Quote", "source": quoted}]
    issues, stats = similarity.check(secs, _renderer(), chunks, {"S-001": "Wu 2023"})
    assert len(issues) == 1 and issues[0]["section"] == "d" and issues[0]["severity"] == "blocking"
    assert "Wu 2023" in issues[0]["message"] and stats["d"]["percent"] > 80 and stats["q"]["matched"] == 0


def test_ai_author_is_blocked():
    issues = transparency.authorship_issues({"authors": [{"name": "Anna Author"}, {"name": "ChatGPT"}]})
    assert len(issues) == 1 and issues[0]["severity"] == "blocking"


def test_ethics_api_and_export(api, data_dir):
    pid = api.post("/api/projects", json={"title": "T", "article_type": "case_series"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    api.post(f"/api/projects/{pid}/analysis/run")
    # simulate AI usage in this project's outgoing log
    from app import llm
    for p in ("write_section:results", "write_section:discussion", "verify_citation"):
        llm._log(pid, p, "claude-opus-5-5", "x" * 100)
    e = api.get(f"/api/projects/{pid}/ethics").json()
    assert e["ai_usage"]["total_calls"] == 3
    assert {s["purpose"] for s in e["ai_usage"]["summary"]} == {"write_section", "verify_citation"}
    assert "drafting manuscript text" in e["ai_statement"]
    assert {r["key"]: r["status"] for r in e["reminders"]}["ethics"] == "missing"
    assert e["data_risk"]["after"]["unique_cases"]
    e = api.put(f"/api/projects/{pid}/ethics/settings", json={"age_band_width": 20, "drop_columns": ["Локализация"]}).json()
    assert e["data_risk"]["after"]["k_min"] >= 2 and not e["data_issues"]

    api.put(f"/api/projects/{pid}/manuscript/inputs", json={"authors": [{"name": "Claude", "roles": []}]})
    status = api.get(f"/api/projects/{pid}/manuscript/export/status").json()
    assert "ai_author" in {b["code"] for b in status["blocking"]}
    r = api.post(f"/api/projects/{pid}/manuscript/export", params={"mode": "draft"})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    supp = pd.read_csv(io.BytesIO(z.read("supplementary/cases_anonymized.csv")), dtype=str)
    assert "Заключение" not in supp.columns and "Локализация" not in supp.columns
    assert supp["Возраст"].str.contains("–").all()
