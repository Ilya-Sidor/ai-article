"""File-based project storage for the prototype.

Layout of ``data/projects/<id>/``::

    project.json            project metadata and stage
    pending/<upload>.csv    anonymized upload awaiting author confirmation
    pending/<upload>.json   its anonymization report
    dataset.csv             confirmed, anonymized case table (case_id first)
    provenance.json         case_id -> source file / row
    dictionary.json         data dictionary (FR-1.11)
    private/link_map.json   original identifier -> internal ID (never sent anywhere)
    analysis/state.json     finding decisions, user hypotheses, run counter
    analysis/runs/R-0001/   analysis.py, analysis_data.csv, results.json, ...
    audit.jsonl             audit log (FR-0.7)
    .key                    the project's data key, wrapped by the master key

Original uploaded files are never written to disk. Every file of a project is
encrypted with the project's key (AES-256-GCM, see crypto.py); use
read_bytes / write_bytes / read_json / write_json for all project files.
"""
import json
import re
import io
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config, crypto

_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Encrypted file layer
# ---------------------------------------------------------------------------

_keys = {}
_key_lock = threading.Lock()
_audit_lock = threading.RLock()


def _project_of(path):
    """(project_dir, relative path) if ``path`` is inside a project directory."""
    parts = Path(path).parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "projects" and _ID_RE.match(parts[i + 1]) and i + 2 < len(parts):
            return Path(*parts[:i + 2]), "/".join(parts[i + 2:])
    return None


def project_key(project_dir: Path, create=True):
    kf = project_dir / ".key"
    with _key_lock:
        cached = _keys.get(str(project_dir))
        if cached and kf.exists():
            return cached
        if not kf.exists():
            if not create:
                raise crypto.CryptoError("ключ проекта уничтожен")
            project_dir.mkdir(parents=True, exist_ok=True)
            kf.write_bytes(crypto.new_wrapped_key())
            os.chmod(kf, 0o600)
        key = crypto.unwrap(kf.read_bytes())
        _keys[str(project_dir)] = key
        return key


def forget_key(project_dir: Path):
    with _key_lock:
        _keys.pop(str(project_dir), None)


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def read_bytes(path: Path) -> bytes:
    path = Path(path)
    data = path.read_bytes()
    loc = _project_of(path)
    if loc and crypto.is_encrypted(data):
        return crypto.decrypt(project_key(loc[0], create=False), data, loc[1].encode())
    return data  # global (non-patient) files, or legacy plaintext awaiting migration


def write_bytes(path: Path, data: bytes):
    path = Path(path)
    loc = _project_of(path)
    if loc and path.name != ".key":
        data = crypto.encrypt(project_key(loc[0]), data, loc[1].encode())
    _atomic_write(path, data)


def read_text(path: Path) -> str:
    return read_bytes(path).decode("utf-8")


def write_text(path: Path, text: str):
    write_bytes(path, text.encode("utf-8"))


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(read_bytes(path).decode("utf-8"))


def write_json(path: Path, data):
    write_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def encrypt_existing(project_dir: Path) -> int:
    """Migrate plaintext files of a project to encrypted form. Returns the number of files encrypted."""
    n = 0
    for p in project_dir.rglob("*"):
        if p.is_file() and p.name != ".key" and not p.name.endswith(".tmp"):
            head = p.read_bytes()[:4]
            if head != crypto.MAGIC:
                write_bytes(p, p.read_bytes())
                n += 1
    return n


class NotFound(Exception):
    pass


class ProjectStore:
    def __init__(self, root: Path = None):
        self.root = Path(root or config.DATA_DIR) / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- projects -------------------------------------------------------
    def dir(self, pid: str) -> Path:
        if not _ID_RE.match(pid or ""):
            raise NotFound(pid)
        d = self.root / pid
        if not d.exists():
            raise NotFound(pid)
        return d

    def list(self):
        out = []
        for d in sorted(self.root.iterdir()):
            p = read_json(d / "project.json")
            if p:
                out.append(p)
        return sorted(out, key=lambda p: p["updated_at"], reverse=True)

    def create(self, data: dict) -> dict:
        pid = uuid.uuid4().hex[:12]
        ts = now_iso()
        project = {
            "id": pid, **data, "created_at": ts, "updated_at": ts, "stage": "created",
            "anonymization": {"status": "none", "confirmed_at": None},
            "n_cases": 0,
        }
        (self.root / pid).mkdir(parents=True)
        project_key(self.root / pid)
        write_json(self.root / pid / "project.json", project)
        self.audit(pid, "author", "project.create", {"title": data.get("title")})
        return project

    def get(self, pid: str) -> dict:
        return read_json(self.dir(pid) / "project.json")

    def update(self, pid: str, **changes) -> dict:
        p = self.get(pid)
        p.update(changes)
        p["updated_at"] = now_iso()
        write_json(self.dir(pid) / "project.json", p)
        return p

    def delete(self, pid: str):
        """Crypto-shredding first (the key), then the files."""
        d = self.dir(pid)
        kf = d / ".key"
        if kf.exists():
            kf.write_bytes(os.urandom(len(kf.read_bytes())))
            kf.unlink()
        forget_key(d)
        shutil.rmtree(d)

    # ---- audit ----------------------------------------------------------
    def audit(self, pid: str, actor: str, action: str, details: dict = None):
        line = {"ts": now_iso(), "actor": actor, "action": action, "details": details or {}}
        path = self.dir(pid) / "audit.jsonl"
        with _audit_lock:
            old = read_text(path) if path.exists() else ""
            write_text(path, old + json.dumps(line, ensure_ascii=False) + "\n")

    def audit_log(self, pid: str):
        path = self.dir(pid) / "audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in read_text(path).splitlines() if x.strip()]

    # ---- data -----------------------------------------------------------
    def save_frame(self, path: Path, df: pd.DataFrame):
        write_bytes(path, df.to_csv(index=False).encode("utf-8"))

    def load_frame(self, path: Path) -> pd.DataFrame:
        return pd.read_csv(io.BytesIO(read_bytes(path)), dtype=str, keep_default_na=False)

    def dataset(self, pid: str):
        path = self.dir(pid) / "dataset.csv"
        return self.load_frame(path) if path.exists() else None

    def link_map(self, pid: str) -> dict:
        return read_json(self.dir(pid) / "private" / "link_map.json", {})

    def save_link_map(self, pid: str, mapping: dict):
        write_json(self.dir(pid) / "private" / "link_map.json", mapping)
