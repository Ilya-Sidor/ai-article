"""My authors: a personal library of co-authors with their affiliations, reused across projects.

Names, e-mails and ORCIDs of colleagues are personal data: each user sees only their own library, and the
entries are sealed with the master key in the database.
"""
import json
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import crypto, db
from .db import utcnow

router = APIRouter(prefix="/api/authors")
FIELDS = ("name", "name_en", "position", "email", "orcid", "roles")


class AffiliationIn(BaseModel):
    name: str = Field(default="", max_length=500)
    name_en: str = Field(default="", max_length=500)
    address: str = Field(default="", max_length=500)


class AuthorIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    name_en: str = Field(default="", max_length=300)
    position: str = Field(default="", max_length=300)
    email: str = Field(default="", max_length=320)
    orcid: str = Field(default="", max_length=40)
    roles: List[str] = []
    affiliations: List[AffiliationIn] = []


def _uid(request):
    return request.state.user["id"]


def _rows(uid):
    with db.engine().connect() as c:
        rows = c.execute(select(db.saved_authors).where(db.saved_authors.c.user_id == uid)).mappings().all()
    return [{"id": r["id"], **json.loads(crypto.unseal(r["data"], "authors"))} for r in rows]


@router.get("")
def list_authors(request: Request):
    return sorted(_rows(_uid(request)), key=lambda a: a["name"].lower())


@router.post("")
def save_author(body: AuthorIn, request: Request):
    """Add or update (the same ORCID, or the same name without ORCID, is one person)."""
    uid = _uid(request)
    data = body.model_dump()
    data["affiliations"] = [a for a in data["affiliations"] if a["name"].strip()]
    same = next((a for a in _rows(uid) if (body.orcid and a.get("orcid") == body.orcid)
                 or (not body.orcid and a["name"].strip().lower() == body.name.strip().lower())), None)
    sealed = crypto.seal(json.dumps(data, ensure_ascii=False), "authors")
    with db.engine().begin() as c:
        if same:
            c.execute(db.saved_authors.update().where(db.saved_authors.c.id == same["id"])
                      .values(data=sealed, updated_at=utcnow()))
        else:
            c.execute(db.saved_authors.insert().values(user_id=uid, data=sealed, updated_at=utcnow()))
    return list_authors(request)


@router.delete("/{author_id}")
def delete_author(author_id: int, request: Request):
    with db.engine().begin() as c:
        n = c.execute(db.saved_authors.delete().where(db.saved_authors.c.id == author_id,
                                                      db.saved_authors.c.user_id == _uid(request))).rowcount
    if not n:
        raise HTTPException(404, "автор не найден")
    return list_authors(request)
