"""Analysis runs: spec → generated script → isolated execution → finding cards.

Every run writes a self-contained ``analysis.py`` (sandbox_lib + spec) and the
typed ``analysis_data.csv``. The application itself obtains its numbers by
executing exactly that script in a separate Python process, so re-running the
exported script reproduces every number (FR-2.10).
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import zlib
from importlib import metadata
from pathlib import Path

from ..config import ANALYSIS_SEED, ANALYSIS_TIMEOUT_S
from ..ingest import prepare_analysis_frame
from ..storage import ProjectStore, now_iso, read_bytes, read_json, write_bytes, write_json, write_text
from . import sandbox_lib
from .findings import build_findings
from .guardrails import ANALYZABLE, COMMON_RULES, select_test, tier_for


class AnalysisError(RuntimeError):
    pass


def _seed(key: str) -> int:
    return ANALYSIS_SEED + zlib.crc32(key.encode("utf-8")) % 100000


def _pair_key(a, b):
    x, y = sorted([a, b])
    return f"{x}|{y}"


def state(store: ProjectStore, pid: str) -> dict:
    return read_json(store.dir(pid) / "analysis" / "state.json",
                     {"runs": 0, "latest": None, "decisions": {}, "user_hypotheses": []})


def save_state(store: ProjectStore, pid: str, st: dict):
    write_json(store.dir(pid) / "analysis" / "state.json", st)


def build_spec(dictionary: list, n: int, user_hypotheses: list):
    tier = tier_for(n)
    included = [v for v in dictionary if v.get("include") and v["vtype"] in ANALYZABLE]
    variables = {v["name"]: {"vtype": v["vtype"], "levels": v.get("levels") or [], "group": v["group"]}
                 for v in included}
    by_name = {v["name"]: v for v in included}

    tests, not_run, origins = [], [], {}
    seen = set()

    def add(va, vb, origin, question=None):
        pk = _pair_key(va["name"], vb["name"])
        if pk in seen:
            if origin == "user":
                for key in origins:
                    if key.endswith("::" + pk):
                        origins[key] = "user"
                for item in not_run:
                    if item["pair"] == pk:
                        item["origin"] = "user"
            return
        seen.add(pk)
        kind, args, reason = select_test(va, vb, tier)
        if kind is None:
            not_run.append({"a": va["name"], "b": vb["name"], "pair": pk, "origin": origin, "reason": reason,
                            "question": question})
            return
        key = f"{kind}::{pk}"
        args["seed"] = _seed(key)
        tests.append({"id": "H-%03d" % (len(tests) + 1), "key": key, "kind": kind, "args": args})
        origins[key] = origin

    for i, va in enumerate(included):
        for vb in included[i + 1:]:
            add(va, vb, "auto")
    for h in user_hypotheses:
        va, vb = by_name.get(h["a"]), by_name.get(h["b"])
        if va is None or vb is None:
            not_run.append({"a": h["a"], "b": h["b"], "pair": _pair_key(h["a"], h["b"]), "origin": "user",
                            "reason": "признак исключён из анализа в словаре данных", "question": h.get("question")})
            continue
        add(va, vb, "user", h.get("question"))

    spec = {"variables": variables, "tests": tests, "clustering": None,
            "outlier_variables": [v["name"] for v in included if v["vtype"] == "quantitative"],
            "profile_variables": [v["name"] for v in included if v["vtype"] == "binary" and v["group"] == "ihc"]
            or [v["name"] for v in included if v["vtype"] == "binary"],
            "shared_variables": [v["name"] for v in included] if tier.code in ("descriptive", "minimal") else []}
    if tier.clustering != "none":
        spec["clustering"] = {"variables": list(variables), "ks": [2, 3, 4],
                              "stability": tier.clustering == "stability", "seed": _seed("clustering")}
    return spec, tier, not_run, origins


def render_script(spec: dict, meta: dict) -> str:
    lib = Path(sandbox_lib.__file__).read_text(encoding="utf-8")
    spec_json = json.dumps(spec, ensure_ascii=False, indent=1)
    literal = f"r'''{spec_json}'''" if "'''" not in spec_json else repr(spec_json)
    header = (
        "#!/usr/bin/env python3\n"
        f"# Reproducible analysis script — project {meta['project_id']}, run {meta['run_id']}\n"
        f"# Generated {meta['created_at']} by AI Article (prototype). Base seed: {ANALYSIS_SEED}.\n"
        f"# Data SHA-256: {meta['data_sha256']}\n"
        "# Usage:  python analysis.py analysis_data.csv > results.json\n"
        "# Requires numpy, pandas, scipy (versions in environment.json).\n\n"
    )
    return header + lib + f"\n\nSPEC = json.loads({literal})\n\nif __name__ == '__main__':\n    main()\n"


def environment_info():
    pkgs = {}
    for name in ("numpy", "pandas", "scipy", "matplotlib"):
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pkgs[name] = None
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": pkgs,
            "base_seed": ANALYSIS_SEED, "bootstrap_resamples": sandbox_lib.N_BOOT}


def _package_dirs():
    import numpy
    import pandas
    import scipy
    return sorted({str(Path(m.__file__).resolve().parents[1]) for m in (numpy, pandas, scipy)})


def execute_script(run_dir: Path) -> dict:
    """Run the generated script in a separate, isolated interpreter (-I).

    Run files are stored encrypted; the child gets decrypted copies in a private
    temporary directory that is removed afterwards, a minimal environment and no
    access to the project store. In deployment the same call runs inside a
    network-less container (NFR-6, see sandbox.py)."""
    from .. import sandbox
    with tempfile.TemporaryDirectory(prefix="aia-run-") as tmp:
        work = Path(tmp)
        for name in ("analysis.py", "analysis_data.csv"):
            (work / name).write_bytes(read_bytes(run_dir / name))
        return _execute(work, run_dir, sandbox)


def _execute(work: Path, run_dir: Path, sandbox) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "1",
           "OPENBLAS_NUM_THREADS": "1", "HOME": str(work)}
    # The server may run from a base interpreter with packages added to sys.path (see .claude/launch.json),
    # so the isolated child gets exactly the directories that provide numpy / pandas / scipy.
    bootstrap = ("import sys, runpy; sys.path[:0] = %r; runpy.run_path('analysis.py', run_name='__main__')"
                 % _package_dirs())
    try:
        proc = sandbox.run_python(work, bootstrap, ["analysis_data.csv"], env, ANALYSIS_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError("превышено время выполнения анализа") from exc
    if proc.returncode != 0:
        write_text(run_dir / "stderr.txt", proc.stderr)
        raise AnalysisError("скрипт анализа завершился с ошибкой: " + proc.stderr.strip().splitlines()[-1]
                            if proc.stderr.strip() else "скрипт анализа завершился с ошибкой")
    return json.loads(proc.stdout)


def run_analysis(store: ProjectStore, pid: str, actor: str = "author") -> dict:
    project = store.get(pid)
    if project["anonymization"]["status"] != "confirmed":
        raise AnalysisError("сначала подтвердите отчёт об анонимизации")
    df = store.dataset(pid)
    d = store.dir(pid)
    dictionary = read_json(d / "dictionary.json", [])
    data, problems = prepare_analysis_frame(df, dictionary)
    n = len(data)
    if n < 2:
        raise AnalysisError("для анализа нужна серия из 2 и более случаев (n = 1 — режим case report, P1)")

    st = state(store, pid)
    spec, tier, not_run, origins = build_spec(dictionary, n, st["user_hypotheses"])
    runs_root = d / "analysis" / "runs"
    existing = [int(p.name[2:]) for p in runs_root.glob("R-*") if p.name[2:].isdigit()] if runs_root.exists() else []
    st["runs"] = max([st["runs"], *existing]) + 1
    run_id = "R-%04d" % st["runs"]
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    save_state(store, pid, st)  # a failed run keeps its directory (stderr.txt) and never blocks the next one

    csv_bytes = data.to_csv(index=False).encode("utf-8")
    write_bytes(run_dir / "analysis_data.csv", csv_bytes)
    meta = {"project_id": pid, "run_id": run_id, "created_at": now_iso(),
            "data_sha256": hashlib.sha256(csv_bytes).hexdigest()}
    write_text(run_dir / "analysis.py", render_script(spec, meta))
    write_json(run_dir / "spec.json", spec)
    write_json(run_dir / "environment.json", {**environment_info(), **meta, "n": n, "tier": tier.code})

    results = execute_script(run_dir)
    write_json(run_dir / "results.json", results)

    findings = build_findings(results, spec, tier, n, st["decisions"], origins)
    write_json(run_dir / "findings.json", findings)

    hypotheses = []
    for r in results["tests"]:
        hypotheses.append({
            "id": r["id"], "key": r["spec_key"], "a": r.get("a") or r["spec_key"].split("::")[1].split("|")[0],
            "b": r.get("b") or r["spec_key"].split("|")[-1], "origin": origins.get(r["spec_key"], "auto"),
            "test": r.get("test"), "status": "выполнен" if r["status"] == "ok" else "пропущен",
            "reason": r.get("reason"), "n": r.get("n"), "p": r.get("p"), "q": r.get("q"),
            "effect": r.get("effect"),
        })
    for x in not_run:
        hypotheses.append({"id": None, "key": None, "a": x["a"], "b": x["b"], "origin": x["origin"], "test": None,
                           "status": "не выполнен", "reason": x["reason"], "n": None, "p": None, "q": None,
                           "effect": None})
    write_json(run_dir / "hypotheses.json", hypotheses)

    summary = {"run_id": run_id, "created_at": meta["created_at"], "n": n, "tier": tier.code,
               "n_tests": sum(1 for h in hypotheses if h["p"] is not None),
               "n_findings": len(findings), "data_problems": len(problems)}
    write_json(run_dir / "summary.json", summary)
    st["latest"] = run_id
    save_state(store, pid, st)
    store.update(pid, stage="analysis")
    store.audit(pid, actor, "analysis.run", summary)
    return latest(store, pid)


def latest(store: ProjectStore, pid: str):
    st = state(store, pid)
    if not st["latest"]:
        return None
    run_dir = store.dir(pid) / "analysis" / "runs" / st["latest"]
    findings = read_json(run_dir / "findings.json", [])
    for f in findings:  # decisions may have changed after the run
        dec = st["decisions"].get(f["key"], {})
        f["status"] = dec.get("status", "proposed")
        f["comments"] = dec.get("comments", [])
        f["interpretation"] = dec.get("interpretation")
    results = read_json(run_dir / "results.json", {})
    summary = read_json(run_dir / "summary.json", {})
    tier = tier_for(summary.get("n", 0))
    return {
        "summary": summary, "guardrails": {**tier.as_dict(), "common_rules": COMMON_RULES},
        "table1": results.get("table1", []), "clustering": results.get("clustering"),
        "findings": findings, "hypotheses": read_json(run_dir / "hypotheses.json", []),
        "environment": read_json(run_dir / "environment.json", {}),
        "user_hypotheses": st["user_hypotheses"],
    }


def run_dir_latest(store: ProjectStore, pid: str) -> Path:
    st = state(store, pid)
    if not st["latest"]:
        raise AnalysisError("анализ ещё не запускался")
    return store.dir(pid) / "analysis" / "runs" / st["latest"]


def set_decision(store: ProjectStore, pid: str, key: str, status: str = None, comment: str = None,
                 interpretation: dict = None):
    st = state(store, pid)
    dec = st["decisions"].setdefault(key, {"status": "proposed", "comments": []})
    if status:
        dec["status"] = status
    if comment:
        dec["comments"].append({"ts": now_iso(), "text": comment})
    if interpretation is not None:
        dec["interpretation"] = interpretation
    save_state(store, pid, st)
    store.audit(pid, "author" if interpretation is None else "agent", "finding.updated",
                {"key": key, "status": status, "comment": bool(comment), "interpretation": interpretation is not None})
    return dec


def add_user_hypothesis(store: ProjectStore, pid: str, a: str, b: str, question: str = None):
    st = state(store, pid)
    if a == b:
        raise AnalysisError("выберите два разных признака")
    if not any({h["a"], h["b"]} == {a, b} for h in st["user_hypotheses"]):
        st["user_hypotheses"].append({"a": a, "b": b, "question": question, "created_at": now_iso()})
        save_state(store, pid, st)
    store.audit(pid, "author", "hypothesis.added", {"a": a, "b": b})
    return run_analysis(store, pid)
