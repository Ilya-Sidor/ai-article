"""Sandbox service (NFR-6): executes one generated analysis script per request.

Runs in its own container on an internal network without internet access, with
a read-only root filesystem and only numpy/pandas/scipy installed. The API/worker
sends the script and the data; nothing is stored after the request.

    uvicorn sandbox_service:app --host 0.0.0.0 --port 8080
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="AI Article sandbox")
TOKEN = os.environ.get("SANDBOX_TOKEN", "")
TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "600"))


class RunIn(BaseModel):
    script: str = Field(max_length=2_000_000)
    data: str = Field(max_length=100_000_000)
    args: list = ["analysis_data.csv"]


@app.post("/run")
def run(body: RunIn, authorization: str = Header(default="")):
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "unauthorized")
    with tempfile.TemporaryDirectory(prefix="run-") as tmp:
        work = Path(tmp)
        (work / "analysis.py").write_text(body.script, encoding="utf-8")
        (work / "analysis_data.csv").write_text(body.data, encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "1"}
        try:
            p = subprocess.run([sys.executable, "-I", "analysis.py", *body.args], cwd=work, env=env,
                               capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "", "stderr": "timeout"}
    return {"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr[-20000:]}


@app.get("/health")
def health():
    return {"ok": True}
