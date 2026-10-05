"""My authors: a private, sealed library of co-authors with affiliations."""
from conftest import make_client


def test_library(api):
    a = {"name": "Иванов Иван Иванович", "name_en": "Ivanov Ivan", "position": "зав. отделением", "email": "i@x.org",
         "orcid": "0000-0002-1825-0097", "roles": ["Conceptualization"],
         "affiliations": [{"name": "ФГБУ «НМИЦ»", "name_en": "NMRC", "address": "Москва"}, {"name": ""}]}
    lib = api.post("/api/authors", json=a).json()
    assert len(lib) == 1 and lib[0]["affiliations"] == [{"name": "ФГБУ «НМИЦ»", "name_en": "NMRC", "address": "Москва"}]
    lib = api.post("/api/authors", json=dict(a, position="профессор")).json()  # same ORCID → updated
    assert len(lib) == 1 and lib[0]["position"] == "профессор"
    lib = api.post("/api/authors", json={"name": "Петров Пётр"}).json()
    assert [x["name"] for x in lib] == ["Иванов Иван Иванович", "Петров Пётр"]

    other = make_client("colleague@example.org")
    assert other.get("/api/authors").json() == []  # private to each user
    assert other.delete(f"/api/authors/{lib[0]['id']}").status_code == 404

    from app import db
    with db.engine().connect() as c:
        raw = " ".join(r[0] for r in c.execute(db.saved_authors.select().with_only_columns(db.saved_authors.c.data)))
    assert "Иванов" not in raw and "0000-0002" not in raw  # sealed
    lib = api.delete(f"/api/authors/{lib[1]['id']}").json()
    assert [x["name"] for x in lib] == ["Иванов Иван Иванович"]
