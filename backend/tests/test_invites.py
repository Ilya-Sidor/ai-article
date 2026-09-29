"""Colleagues join by one-time invitations from the administrator; the administrator can block accounts."""
import pytest
from conftest import make_client
from fastapi.testclient import TestClient


@pytest.fixture()
def closed(api, monkeypatch):
    monkeypatch.setenv("AI_ARTICLE_REGISTRATION", "closed")
    return api  # the first account (administrator) already exists


def _anon():
    from app import main
    return TestClient(main.app, headers={"x-aia": "1"})


def test_invite_flow(closed):
    admin = closed
    guest = _anon()
    body = {"email": "colleague@example.org", "password": "correct horse battery staple"}
    assert guest.post("/api/auth/register", json=body).status_code == 403  # closed without an invite

    r = admin.post("/api/admin/invites", json={"email": "Colleague@Example.org", "days": 3}).json()
    token = r["token"]
    assert r["invites"][0]["status"] == "active" and r["invites"][0]["email"] == "colleague@example.org"
    assert token not in str(admin.get("/api/admin/people").json())  # only the hash is stored
    assert guest.get(f"/api/auth/invite/{token}").json() == {"valid": True, "email": "colleague@example.org"}
    assert guest.get("/api/auth/invite/nonsense").json()["valid"] is False

    other = dict(body, email="someone@example.org", invite=token)
    assert guest.post("/api/auth/register", json=other).status_code == 403  # bound to another e-mail
    me = guest.post("/api/auth/register", json=dict(body, invite=token))
    assert me.status_code == 200 and me.json()["is_admin"] is False
    assert guest.get("/api/projects").status_code == 200  # signed in right away
    assert _anon().post("/api/auth/register", json=dict(body, email="x@example.org", invite=token)).status_code == 403
    p = admin.get("/api/admin/people").json()
    assert p["invites"][0]["status"] == "used" and p["invites"][0]["used_by"] == "colleague@example.org"
    assert [u["email"] for u in p["users"]] == ["author@example.org", "colleague@example.org"]

    # colleagues are not administrators
    assert guest.get("/api/admin/people").status_code == 403
    assert guest.post("/api/admin/invites", json={}).status_code == 403


def test_open_invite_revoke_expiry_and_blocking(closed):
    from datetime import timedelta

    from app import auth, db
    admin = closed
    r = admin.post("/api/admin/invites", json={}).json()  # any e-mail
    token, inv_id = r["token"], r["invites"][0]["id"]
    assert admin.delete(f"/api/admin/invites/{inv_id}").status_code == 200
    assert _anon().get(f"/api/auth/invite/{token}").json()["valid"] is False

    token = admin.post("/api/admin/invites", json={}).json()["token"]
    with db.engine().begin() as c:  # the link has expired
        c.execute(db.invites.update().values(expires_at=auth.now() - timedelta(minutes=1)))
    guest = _anon()
    body = {"email": "late@example.org", "password": "correct horse battery staple", "invite": token}
    assert guest.post("/api/auth/register", json=body).status_code == 403
    assert admin.get("/api/admin/people").json()["invites"][0]["status"] == "expired"

    token = admin.post("/api/admin/invites", json={}).json()["token"]
    assert guest.post("/api/auth/register", json=dict(body, invite=token)).status_code == 200
    uid = next(u["id"] for u in admin.get("/api/admin/people").json()["users"] if u["email"] == "late@example.org")
    me_id = next(u["id"] for u in admin.get("/api/admin/people").json()["users"] if u["is_admin"])
    assert admin.put(f"/api/admin/users/{me_id}", json={"is_active": False}).status_code == 400
    assert admin.put(f"/api/admin/users/{uid}", json={"is_active": False}).status_code == 200
    assert guest.get("/api/projects").status_code == 401  # session ended at once
    assert guest.post("/api/auth/login", json={"email": "late@example.org", "password": body["password"]}).status_code == 401
    admin.put(f"/api/admin/users/{uid}", json={"is_active": True})
    assert guest.post("/api/auth/login", json={"email": "late@example.org", "password": body["password"]}).status_code == 200


def test_entry_url_for_links(closed, monkeypatch):
    monkeypatch.setenv("AI_ARTICLE_ENTRY_URL", "https://example.github.io/aia/")
    from app import settings
    settings.invalidate()
    assert closed.get("/api/admin/people").json()["entry_url"] == "https://example.github.io/aia/"


def test_open_registration_still_works_without_invite(api):
    assert make_client("free@example.org").get("/api/projects").status_code == 200
