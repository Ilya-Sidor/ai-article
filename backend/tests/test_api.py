from generate_sample import generate


def _project(api, df=None, confirm=True):
    df = generate() if df is None else df
    pid = api.post("/api/projects", json={"title": "GIST", "journal": "Histopathology"}).json()["id"]
    r = api.post(f"/api/projects/{pid}/uploads", files={"files": ("series.csv", df.to_csv(index=False).encode())})
    assert r.status_code == 200, r.text
    if confirm:
        assert api.post(f"/api/projects/{pid}/anonymization/confirm").status_code == 200
    return pid


def test_full_flow(api):
    pid = _project(api)
    ds = api.get(f"/api/projects/{pid}/dataset").json()
    assert len(ds["rows"]) == 24 and ds["rows"][0]["case_id"] == "C-001"
    assert ds["provenance"]["C-001"][0] == {"file": "series.csv", "row": 2}

    res = api.post(f"/api/projects/{pid}/analysis/run").json()
    assert res["summary"]["tier"] == "small"
    assert res["findings"] and res["table1"]
    f = res["findings"][0]
    assert api.get(f"/api/projects/{pid}/findings/{f['id']}/figure.png").status_code == 200
    assert api.get(f"/api/projects/{pid}/analysis/figures/heatmap.png").status_code == 200
    assert api.get(f"/api/projects/{pid}/analysis/figures/oncoprint.png").status_code == 200

    api.post(f"/api/projects/{pid}/findings/{f['id']}", json={"status": "accepted", "comment": "ок"})
    again = api.get(f"/api/projects/{pid}/analysis").json()
    assert again["findings"][0]["status"] == "accepted"

    r = api.post(f"/api/projects/{pid}/hypotheses", json={"a": "Пол", "b": "Ki-67, %", "question": "пол и Ki-67?"})
    user = [x for x in r.json()["findings"] if x["origin"] == "user"]
    assert user and user[0]["title"].endswith("Ki-67, %")
    assert api.get(f"/api/projects/{pid}/analysis/export.zip").status_code == 200


def test_llm_gate_blocks_unconfirmed_data(api, monkeypatch):
    from app import llm
    pid = _project(api, confirm=False)
    project = api.get(f"/api/projects/{pid}").json()
    called = []
    monkeypatch.setattr(llm, "client", lambda: called.append(1))
    try:
        llm.parse_question(project, "связан ли KIT с CD117?", [])
    except llm.PrivacyGateError:
        pass
    else:
        raise AssertionError("gate did not block")
    assert not called


def test_outgoing_log_has_no_content(api, data_dir, monkeypatch):
    from app import llm

    class Fake:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    class B:
                        type = "text"
                        text = '{"a": "CD117", "b": "KIT мутация", "rationale": "x"}'

                    class R:
                        stop_reason = "end_turn"
                        content = [B()]
                    return R()

    monkeypatch.setattr(llm, "client", lambda: Fake)
    pid = _project(api)
    r = api.post(f"/api/projects/{pid}/hypotheses/parse", json={"question": "Связан ли KIT с CD117?"})
    assert r.json()["a"] == "CD117" and r.json()["source"] == "llm"
    log = llm.usage(pid)
    assert [x["purpose"] for x in log] == ["parse_question"]
    assert set(log[0]) == {"ts", "project", "purpose", "model", "chars"}  # metadata only (NFR-7)


def test_tiny_series_reports_no_p_values(api):
    pid = _project(api, generate().head(4))
    res = api.post(f"/api/projects/{pid}/analysis/run").json()
    assert res["summary"]["tier"] == "descriptive"
    assert all(h["p"] is None for h in res["hypotheses"])
    assert all(f["evidence"] == "descriptive" for f in res["findings"])
    assert "p =" not in str(res["findings"]) and "p <" not in str(res["findings"])


def test_unsupported_format(api):
    pid = api.post("/api/projects", json={"title": "x"}).json()["id"]
    r = api.post(f"/api/projects/{pid}/uploads", files={"files": ("scan.pdf", b"%PDF-1.4")})
    assert r.status_code == 400


def test_failed_run_does_not_block_next_run(api):
    from app import main
    pid = _project(api)
    stale = main.store.dir(pid) / "analysis" / "runs" / "R-0001"
    stale.mkdir(parents=True)  # left behind by a crashed run
    res = api.post(f"/api/projects/{pid}/analysis/run")
    assert res.status_code == 200 and res.json()["summary"]["run_id"] == "R-0002"


def test_meta_journal_suggestions_come_from_the_journal_base(api):
    names = [j["name"] for j in api.get("/api/meta").json()["journals"]]
    assert "Архив патологии" in names and "Histopathology" in names
    api.post("/api/journals", json={"name": "My Local Journal", "publisher": "", "guidelines_url": ""})
    assert "My Local Journal" in [j["name"] for j in api.get("/api/meta").json()["journals"]]


def test_interface_is_versioned_and_revalidated(api):
    r = api.get("/")
    assert r.status_code == 200 and 'src="app.js?v=' in r.text and 'href="styles.css?v=' in r.text
    js = api.get("/app.js")
    assert js.headers["cache-control"] == "no-cache" and api.get("/api/meta").headers["cache-control"] == "no-store"


def test_delete_case(api):
    import io
    from generate_sample import generate
    pid = api.post("/api/projects", json={"title": "Del"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    r = api.delete(f"/api/projects/{pid}/dataset/cases/C-002").json()
    ids = [row["case_id"] for row in r["rows"]]
    assert "C-002" not in ids and len(ids) == 23 and r["project"]["n_cases"] == 23
    assert "C-002" not in r["provenance"]
    assert api.delete(f"/api/projects/{pid}/dataset/cases/C-002").status_code == 404
    # a new upload never reuses a deleted (or existing) case id
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("t.csv", b"Age,Sex\n50,M\n")})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    ids = [row["case_id"] for row in api.get(f"/api/projects/{pid}/dataset").json()["rows"]]
    assert len(ids) == len(set(ids)) and "C-025" in ids
    assert any(a["action"] == "dataset.case_deleted" for a in api.get(f"/api/projects/{pid}/audit").json())


def test_delete_last_case_returns_to_upload(api):
    pid = api.post("/api/projects", json={"title": "One", "article_type": "case_report"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("c.csv", "Возраст\n54\n".encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    r = api.delete(f"/api/projects/{pid}/dataset/cases/C-001").json()
    assert r["rows"] == [] and r["project"]["stage"] == "created" and r["project"]["anonymization"]["status"] == "none"


def test_restore_deleted_case(api):
    from generate_sample import generate
    pid = api.post("/api/projects", json={"title": "Restore"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    before = api.get(f"/api/projects/{pid}/dataset").json()
    row = next(r for r in before["rows"] if r["case_id"] == "C-005")
    r = api.delete(f"/api/projects/{pid}/dataset/cases/C-005").json()
    assert r["deleted"][0]["case_id"] == "C-005" and len(r["rows"]) == 23
    # a new upload does not take the id of the case in the trash
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("t.csv", b"Age,Sex\n50,M\n")})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    r = api.post(f"/api/projects/{pid}/dataset/deleted/C-005/restore").json()
    ids = [x["case_id"] for x in r["rows"]]
    assert ids.index("C-005") == 4 and len(ids) == len(set(ids)) == 25 and r["deleted"] == []
    restored = next(x for x in r["rows"] if x["case_id"] == "C-005")
    assert all(restored[k] == v for k, v in row.items()) and restored.get("Age", "") == ""
    assert r["provenance"]["C-005"] == before["provenance"]["C-005"] and r["project"]["n_cases"] == 25
    # purge: gone for good
    api.delete(f"/api/projects/{pid}/dataset/cases/C-005")
    assert api.delete(f"/api/projects/{pid}/dataset/deleted/C-005").status_code == 200
    assert api.post(f"/api/projects/{pid}/dataset/deleted/C-005/restore").status_code == 404


def test_restore_last_case_reopens_the_project(api):
    pid = api.post("/api/projects", json={"title": "One", "article_type": "case_report"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("c.csv", "Возраст\n54\n".encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    api.delete(f"/api/projects/{pid}/dataset/cases/C-001")
    r = api.post(f"/api/projects/{pid}/dataset/deleted/C-001/restore").json()
    assert [x["case_id"] for x in r["rows"]] == ["C-001"] and r["project"]["stage"] == "data_confirmed"
    assert r["project"]["anonymization"]["status"] == "confirmed"
