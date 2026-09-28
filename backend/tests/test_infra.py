import time

import pytest
from conftest import make_client
from generate_sample import generate


def _project_with_data(api):
    pid = api.post("/api/projects", json={"title": "Secret series"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("s.csv", generate().to_csv(index=False).encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    return pid


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------

def test_project_files_are_encrypted(api, data_dir):
    from app import crypto
    pid = _project_with_data(api)
    api.post(f"/api/projects/{pid}/analysis/run")
    d = data_dir / "projects" / pid
    files = [p for p in d.rglob("*") if p.is_file() and p.name != ".key"]
    assert files and all(p.read_bytes().startswith(crypto.MAGIC) for p in files)
    raw = b"".join(p.read_bytes() for p in files)
    for secret in (b"Secret series", "желудок".encode(), b"PT-0001", b"analysis_data"):
        assert secret not in raw
    # the application still reads everything
    assert api.get(f"/api/projects/{pid}/dataset").json()["rows"][0]["case_id"] == "C-001"
    assert api.get(f"/api/projects/{pid}/analysis").json()["summary"]["n"] == 24


def test_tampering_and_swapping_are_detected(api, data_dir):
    from app import crypto, storage
    pid = _project_with_data(api)
    d = data_dir / "projects" / pid
    blob = bytearray((d / "project.json").read_bytes())
    blob[-1] ^= 1
    (d / "tampered.json").write_bytes(bytes(blob))
    with pytest.raises(crypto.CryptoError):
        storage.read_bytes(d / "tampered.json")
    (d / "copy.json").write_bytes((d / "dataset.csv").read_bytes())  # valid ciphertext, wrong location
    with pytest.raises(crypto.CryptoError):
        storage.read_bytes(d / "copy.json")


def test_crypto_shredding_and_migration(api, data_dir):
    from app import crypto, storage
    pid = _project_with_data(api)
    d = data_dir / "projects" / pid
    snapshot = (d / "dataset.csv").read_bytes()
    assert api.delete(f"/api/projects/{pid}").status_code == 204
    assert not d.exists()
    other = data_dir / "projects" / "abcdefabcdef"
    other.mkdir(parents=True)
    (other / "dataset.csv").write_bytes(snapshot)  # even with the ciphertext, no key → unreadable
    with pytest.raises(crypto.CryptoError):
        storage.read_bytes(other / "dataset.csv")

    legacy = data_dir / "projects" / "0123456789ab"
    legacy.mkdir(parents=True)
    (legacy / "notes.json").write_text('{"a": 1}')
    assert storage.read_json(legacy / "notes.json") == {"a": 1}
    assert storage.encrypt_existing(legacy) == 1
    assert (legacy / "notes.json").read_bytes().startswith(crypto.MAGIC)
    assert storage.read_json(legacy / "notes.json") == {"a": 1}


# ---------------------------------------------------------------------------
# Authentication and access control
# ---------------------------------------------------------------------------

def test_default_deny_and_csrf(data_dir):
    from fastapi.testclient import TestClient

    from app import main
    anon = TestClient(main.app, headers={"x-aia": "1"})
    assert anon.get("/api/projects").status_code == 401
    assert anon.get("/api/meta").status_code == 401
    c = make_client()
    raw = TestClient(main.app)
    raw.cookies.update(c.cookies)
    assert raw.post("/api/projects", json={"title": "x"}).status_code == 403  # no CSRF header
    assert raw.get("/api/projects").status_code == 200


def test_isolation_between_users(api):
    pid = api.post("/api/projects", json={"title": "mine"}).json()["id"]
    other = make_client("other@example.org")
    assert other.get("/api/projects").json() == []
    assert other.get(f"/api/projects/{pid}").status_code == 404
    assert other.post(f"/api/projects/{pid}/analysis/run").status_code == 404
    assert other.delete(f"/api/projects/{pid}").status_code == 404
    assert other.get("/api/auth/me").json()["is_admin"] is False  # only the first user is admin


def test_lockout_and_password_change(data_dir):
    from fastapi.testclient import TestClient

    from app import main
    make_client("u@example.org", "a very long password")
    c = TestClient(main.app, headers={"x-aia": "1"})
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "u@example.org", "password": "wrong"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "u@example.org", "password": "a very long password"})
    assert r.status_code == 429
    short = c.post("/api/auth/register", json={"email": "s@example.org", "password": "short"})
    assert short.status_code == 422


def test_totp_second_factor(data_dir):
    from fastapi.testclient import TestClient

    from app import auth, main
    c = make_client("mfa@example.org", "another long password")
    setup = c.post("/api/auth/2fa/setup").json()
    assert setup["uri"].startswith("otpauth://totp/") and "<svg" in setup["svg"]
    secret = setup["secret"]
    now = int(time.time() // 30)
    assert c.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    assert c.post("/api/auth/2fa/enable", json={"code": auth.totp_code(secret, now)}).json()["totp_enabled"]
    fresh = TestClient(main.app, headers={"x-aia": "1"})
    creds = {"email": "mfa@example.org", "password": "another long password"}
    r = fresh.post("/api/auth/login", json=creds)
    assert r.status_code == 401 and r.json()["detail"]["mfa_required"]
    # the code used to enable 2FA cannot be replayed
    assert fresh.post("/api/auth/login", json=dict(creds, totp=auth.totp_code(secret, now))).status_code == 401
    ok = fresh.post("/api/auth/login", json=dict(creds, totp=auth.totp_code(secret, now + 1)))
    assert ok.status_code == 200 and fresh.get("/api/auth/me").json()["email"] == "mfa@example.org"


def test_totp_rfc6238_vector():
    import base64

    from app import auth
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth.totp_code(secret, 59 // 30) == "287082"  # RFC 6238 test vector (SHA-1, 6 digits)


def test_commenter_role_cannot_modify(api):
    from app import auth, db
    pid = api.post("/api/projects", json={"title": "shared"}).json()["id"]
    other = make_client("c@example.org")
    uid = other.get("/api/auth/me").json()["id"]
    auth.add_member(pid, uid, "commenter")
    assert other.get(f"/api/projects/{pid}").status_code == 200
    assert other.patch(f"/api/projects/{pid}", json={"title": "x"}).status_code == 403


def test_starter_journal_needs_admin(api):
    api.get("/api/journals")  # seeds the starter base
    other = make_client("j@example.org")
    r = other.put("/api/journals/histopathology/fields", json={"updates": {"language_variant": {"value": "UK"}}})
    assert r.status_code == 403
    mine = other.post("/api/journals", json={"name": "Private Journal"}).json()
    assert mine["id"] in {j["id"] for j in api.get("/api/journals").json()}  # the admin sees all
    third = make_client("t@example.org")
    assert mine["id"] not in {j["id"] for j in third.get("/api/journals").json()}
    assert third.get(f"/api/journals/{mine['id']}").status_code == 404
    assert third.get(f"/api/journals/{mine['id']}/sources/G-1").status_code == 404


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------

def test_async_job_runs_with_owner_rights(api):
    from app import jobs, main
    pid = _project_with_data(api)
    r = api.post(f"/api/projects/{pid}/analysis/run?async=1")
    assert r.status_code == 202
    jid = r.json()["job_id"]
    assert api.get(f"/api/jobs/{jid}").json()["status"] == "queued"
    assert [j["id"] for j in api.get(f"/api/jobs?project={pid}").json()] == [jid]
    other = make_client("x@example.org")
    assert other.get(f"/api/jobs/{jid}").status_code == 404
    assert jobs.run_one(main.app)
    j = api.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "done" and j["result"]["summary"]["n"] == 24
    assert api.get(f"/api/jobs?project={pid}").json() == []
    audit = api.get(f"/api/projects/{pid}/audit").json()
    assert audit[0]["action"] == "analysis.run"


def test_failed_job_reports_error(api):
    from app import jobs, main
    pid = api.post("/api/projects", json={"title": "empty"}).json()["id"]
    jid = api.post(f"/api/projects/{pid}/analysis/run?async=1").json()["job_id"]
    jobs.run_one(main.app)
    j = api.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "error" and "анонимизации" in j["error"]


def test_interrupted_jobs_are_marked(api):
    from app import db, jobs
    pid = api.post("/api/projects", json={"title": "p"}).json()["id"]
    jid = api.post(f"/api/projects/{pid}/analysis/run?async=1").json()["job_id"]
    with db.engine().begin() as c:
        c.execute(db.jobs.update().values(status="running"))
    jobs.recover()
    assert api.get(f"/api/jobs/{jid}").json()["status"] == "error"


def test_docker_sandbox_command():
    from app import sandbox
    cmd = sandbox.docker_command("/tmp/run", ["analysis_data.csv"])
    for flag in ("--network", "none", "--read-only", "--cap-drop", "ALL", "--pids-limit"):
        assert flag in cmd


def test_http_sandbox_roundtrip(api, monkeypatch):
    """The API talks to the sandbox service; results are identical to local execution."""
    import httpx
    from fastapi.testclient import TestClient

    import sandbox_service
    monkeypatch.setattr(sandbox_service, "TOKEN", "t0ken")
    svc = TestClient(sandbox_service.app)
    assert svc.post("/run", json={"script": "print(1)", "data": ""}).status_code == 401

    def fake_post(url, json=None, headers=None, timeout=None):
        r = svc.post("/run", json=json, headers=headers)
        return httpx.Response(r.status_code, json=r.json(), request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("AI_ARTICLE_SANDBOX", "http")
    monkeypatch.setenv("AI_ARTICLE_SANDBOX_URL", "http://sandbox:8080")
    monkeypatch.setenv("AI_ARTICLE_SANDBOX_TOKEN", "t0ken")
    pid = _project_with_data(api)
    res = api.post(f"/api/projects/{pid}/analysis/run").json()
    assert res["summary"]["n"] == 24 and res["findings"]


def test_first_account_requires_setup_code_when_configured(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    monkeypatch.setenv("AI_ARTICLE_SETUP_CODE", "setup-123456")
    c = TestClient(main.app, headers={"x-aia": "1"})
    assert c.get("/api/auth/setup").json() == {"setup_required": True}
    body = {"email": "admin@example.org", "password": "a long enough password"}
    r = c.post("/api/auth/register", json=body)
    assert r.status_code == 403 and r.json()["detail"]["setup_required"]
    assert c.post("/api/auth/register", json=dict(body, setup_code="wrong")).status_code == 403
    assert c.post("/api/auth/register", json=dict(body, setup_code="setup-123456")).json()["is_admin"]
    assert c.get("/api/auth/setup").json() == {"setup_required": False}
    monkeypatch.setenv("AI_ARTICLE_REGISTRATION", "closed")
    other = TestClient(main.app, headers={"x-aia": "1"})
    assert other.post("/api/auth/register", json={"email": "x@example.org", "password": "a long enough password",
                                                  "setup_code": "setup-123456"}).status_code == 403


def test_secure_cookie_behind_https_proxy(data_dir):
    from fastapi.testclient import TestClient

    from app import main
    c = TestClient(main.app, headers={"x-aia": "1", "x-forwarded-proto": "https"})
    r = c.post("/api/auth/register", json={"email": "p@example.org", "password": "a long enough password"})
    assert "secure" in r.headers["set-cookie"].lower()
    local = TestClient(main.app, headers={"x-aia": "1"})
    r = local.post("/api/auth/register", json={"email": "q@example.org", "password": "a long enough password"})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_security_headers_on_denied_requests(data_dir):
    from fastapi.testclient import TestClient

    from app import main
    anon = TestClient(main.app, headers={"x-forwarded-proto": "https"})
    r = anon.get("/api/projects")
    assert r.status_code == 401
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and "max-age" in r.headers["strict-transport-security"]
