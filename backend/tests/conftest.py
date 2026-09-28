import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "samples"))
sys.path.insert(0, str(ROOT / "backend"))

# Run the suite against PostgreSQL with: AIA_TEST_DATABASE_URL=postgresql+psycopg://… pytest
TEST_DB = os.environ.get("AIA_TEST_DATABASE_URL")


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app import config, crypto, db, storage
    monkeypatch.setenv("AI_ARTICLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_ARTICLE_KEY_FILE", str(tmp_path / "keys" / "master.key"))
    monkeypatch.delenv("AI_ARTICLE_MASTER_KEY", raising=False)
    monkeypatch.setenv("AI_ARTICLE_DATABASE_URL", TEST_DB or f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    crypto.reset_master_cache()
    storage._keys.clear()
    db.reset()
    if TEST_DB:
        db.metadata.drop_all(db.engine())
        db.metadata.create_all(db.engine())
    yield tmp_path
    db.reset()
    crypto.reset_master_cache()


def make_client(email="author@example.org", password="correct horse battery staple"):
    from fastapi.testclient import TestClient

    from app import main
    c = TestClient(main.app, headers={"x-aia": "1"})
    r = c.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def api(data_dir):
    from app import main
    from app.storage import ProjectStore
    main.store = ProjectStore(data_dir)
    return make_client()


@pytest.fixture()
def sample_df():
    from generate_sample import generate
    return generate()
