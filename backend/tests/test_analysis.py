import json
import subprocess
import sys

import numpy as np
import pandas as pd

from app.analysis import sandbox_lib
from app.analysis.engine import build_spec
from app.analysis.guardrails import select_test, tier_for
from app.ingest import build_dictionary


def test_tiers():
    assert tier_for(3).code == "descriptive"
    assert tier_for(7).code == "minimal"
    assert tier_for(15).code == "small"
    assert tier_for(30).code == "full"


def test_small_series_has_no_tests():
    dictionary = [{"name": "A", "vtype": "binary", "levels": ["no", "yes"], "group": "ihc", "include": True},
                  {"name": "B", "vtype": "quantitative", "levels": [], "group": "clinical", "include": True}]
    spec, tier, not_run, _ = build_spec(dictionary, 4, [])
    assert spec["tests"] == [] and spec["clustering"] is None
    assert not_run and "n < 5" in not_run[0]["reason"]


def test_minimal_tier_only_fisher_2x2():
    tier = tier_for(7)
    a = {"name": "A", "vtype": "binary", "levels": ["n", "y"]}
    b = {"name": "B", "vtype": "binary", "levels": ["n", "y"]}
    c = {"name": "C", "vtype": "quantitative", "levels": []}
    d = {"name": "D", "vtype": "categorical", "levels": ["x", "y", "z"]}
    assert select_test(a, b, tier)[0] == "cat_cat"
    assert select_test(a, c, tier)[0] is None
    assert select_test(a, d, tier)[0] is None


def test_test_selection_by_type():
    tier = tier_for(40)
    b = {"name": "B", "vtype": "binary", "levels": ["n", "y"]}
    q = {"name": "Q", "vtype": "quantitative", "levels": []}
    o = {"name": "O", "vtype": "ordinal", "levels": ["G1", "G2", "G3"]}
    kind, args, _ = select_test(q, b, tier)
    assert kind == "group_numeric" and args["group"] == "B" and args["value"] == "Q"
    assert select_test(q, o, tier)[0] == "spearman"
    assert select_test(b, b | {"name": "B2"}, tier)[1]["allow_chi2"] is True


def test_fisher_matches_textbook():
    df = pd.DataFrame({"a": ["y"] * 6 + ["n"] * 9, "b": ["y"] * 5 + ["n"] + ["y"] + ["n"] * 8})
    r = sandbox_lib.cat_cat(df, "a", "b", ["n", "y"], ["n", "y"])
    assert r["table"] == [[8, 1], [1, 5]]
    assert abs(r["p"] - 0.010989) < 1e-5
    assert abs(r["effect"]["value"] - 40.0) < 1e-9


def test_bh_fdr():
    q = sandbox_lib.bh_fdr([0.01, 0.04, 0.03, 0.2])
    assert np.allclose(q, [0.04, 0.16 / 3, 0.16 / 3, 0.2])


def test_planted_effects_detected(sample_df, monkeypatch):
    df = sample_df.drop(columns=["ФИО", "Дата рождения", "№ истории болезни", "Телефон", "СНИЛС", "Номер блока",
                                 "Дата операции", "Дата последнего контакта", "Заключение"]).astype(str)
    df.insert(0, "case_id", [f"C-{i}" for i in range(len(df))])
    dictionary = build_dictionary(df)
    from app.ingest import prepare_analysis_frame
    frame, problems = prepare_analysis_frame(df, dictionary)
    assert not problems
    variables = {v["name"]: {"vtype": v["vtype"], "levels": v["levels"]} for v in dictionary}
    spec, _, _, _ = build_spec(dictionary, len(frame), [])
    monkeypatch.setattr(sandbox_lib, "N_BOOT", 200)
    res = sandbox_lib.run_spec(sandbox_lib.load_data(_to_csv(frame), variables), spec)
    by_pair = {frozenset((r["a"], r["b"])): r for r in res["tests"] if r["status"] == "ok"}
    kit_pdgfra = by_pair[frozenset(("KIT мутация", "PDGFRA мутация"))]
    assert kit_pdgfra["q"] < 0.05 and kit_pdgfra["effect"]["value"] < 1  # mutual exclusivity
    assert by_pair[frozenset(("Митозы на 5 мм2", "Ki-67, %"))]["q"] < 0.05


def _to_csv(frame):
    import tempfile
    path = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
    frame.to_csv(path, index=False)
    return path


def test_false_discoveries_on_noise_are_controlled(monkeypatch):
    """Pure noise: across datasets the share with any q < 0.05 stays near alpha."""
    monkeypatch.setattr(sandbox_lib, "N_BOOT", 50)
    hits = 0
    runs = 12
    for seed in range(runs):
        rng = np.random.default_rng(seed)
        n = 40
        df = pd.DataFrame({"case_id": [f"C{i}" for i in range(n)]})
        for j in range(4):
            df[f"bin{j}"] = rng.choice(["no", "yes"], n)
            df[f"num{j}"] = rng.normal(size=n).round(3).astype(str)
        dictionary = build_dictionary(df)
        variables = {v["name"]: {"vtype": v["vtype"], "levels": v["levels"]} for v in dictionary}
        spec, _, _, _ = build_spec(dictionary, n, [])
        spec["clustering"] = None
        res = sandbox_lib.run_spec(sandbox_lib.load_data(_to_csv(df), variables), spec)
        qs = [r["q"] for r in res["tests"] if r.get("q") is not None]
        assert len(qs) == 28
        hits += any(q < 0.05 for q in qs)
    assert hits <= 2


def test_exported_script_reproduces_results(api, tmp_path):
    import io
    import zipfile
    from generate_sample import generate
    content = generate().to_csv(index=False).encode()
    pid = api.post("/api/projects", json={"title": "t"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", content)})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    assert api.post(f"/api/projects/{pid}/analysis/run").status_code == 200
    z = zipfile.ZipFile(io.BytesIO(api.get(f"/api/projects/{pid}/analysis/export.zip").content))
    work = tmp_path / "repro"
    z.extractall(work)
    expected = json.loads((work / "results.json").read_text())
    for hash_seed in ("1", "2"):  # no dependence on interpreter state or hash randomization
        proc = subprocess.run([sys.executable, "analysis.py", "analysis_data.csv"], cwd=work,
                              env={"PYTHONHASHSEED": hash_seed}, capture_output=True, text=True, check=True)
        assert json.loads(proc.stdout) == expected
