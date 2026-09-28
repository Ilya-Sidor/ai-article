"""Application settings stored in the database (set by an administrator in the UI).

Secrets (API keys) are sealed with the master key and never returned to the client.
Lookup order: database → environment variable → default.
"""
import os
import time

from sqlalchemy import select

from . import crypto, db

_cache = {}
TTL = 5.0


def _load():
    now = time.time()
    if _cache.get("_ts", 0) + TTL > now:
        return _cache["values"]
    with db.engine().connect() as c:
        values = {r["key"]: r["value"] for r in c.execute(select(db.app_settings)).mappings()}
    _cache.update({"_ts": now, "values": values})
    return values


def invalidate():
    _cache.clear()


def get(key, env=None, default=None):
    v = _load().get(key)
    if v not in (None, ""):
        return v
    if env and os.environ.get(env):
        return os.environ[env]
    return default


def get_secret(key, env=None):
    v = _load().get(key)
    if v:
        return crypto.unseal(v, key)
    return os.environ.get(env) if env else None


def set(key, value, secret=False):
    stored = crypto.seal(value, key) if (secret and value) else value
    with db.engine().begin() as c:
        c.execute(db.app_settings.delete().where(db.app_settings.c.key == key))
        if stored not in (None, ""):
            c.execute(db.app_settings.insert().values(key=key, value=stored, updated_at=db.utcnow()))
    invalidate()
