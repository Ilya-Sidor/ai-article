"""Analysis sandbox runner (NFR-6).

``AI_ARTICLE_SANDBOX=local`` (default, development): isolated interpreter
(``python -I``) in a private temporary directory with a minimal environment.

``AI_ARTICLE_SANDBOX=http`` (deployment): the script and data are sent to the
sandbox service (``sandbox_service.py``) that runs in a separate container on an
internal network without internet access, read-only filesystem and only
numpy/pandas/scipy — see docker-compose.yml. ``AI_ARTICLE_SANDBOX_URL`` and
``AI_ARTICLE_SANDBOX_TOKEN`` configure it.

``AI_ARTICLE_SANDBOX=docker``: one throw-away container per run (for hosts where
the worker may start containers).
"""
import os
import subprocess
import sys
from types import SimpleNamespace

IMAGE = os.environ.get("AI_ARTICLE_SANDBOX_IMAGE", "ai-article-sandbox:latest")


def mode():
    return os.environ.get("AI_ARTICLE_SANDBOX", "local")


def docker_command(work, args):
    return ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "2g", "--cpus", "1",
            "--tmpfs", "/tmp:rw,size=64m", "-e", "MPLBACKEND=Agg", "-v", f"{work}:/work:rw", "-w", "/work",
            "--user", "65534:65534", IMAGE, "python", "-I", "analysis.py", *args]


def _http(work, args, timeout):
    import httpx
    url = os.environ["AI_ARTICLE_SANDBOX_URL"].rstrip("/") + "/run"
    r = httpx.post(url, json={"script": (work / "analysis.py").read_text(encoding="utf-8"),
                              "data": (work / "analysis_data.csv").read_text(encoding="utf-8"), "args": args},
                   headers={"Authorization": f"Bearer {os.environ.get('AI_ARTICLE_SANDBOX_TOKEN', '')}"},
                   timeout=timeout + 30)
    r.raise_for_status()
    return SimpleNamespace(**r.json())


def run_python(work, bootstrap, args, env, timeout):
    m = mode()
    if m == "http":
        return _http(work, args, timeout)
    if m == "docker":
        return subprocess.run(docker_command(work, args), capture_output=True, text=True, timeout=timeout)
    return subprocess.run([sys.executable, "-I", "-c", bootstrap, *args], cwd=work, env=env,
                          capture_output=True, text=True, timeout=timeout)
