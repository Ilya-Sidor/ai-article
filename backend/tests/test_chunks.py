"""Large files in parts: the public tunnel drops request bodies over ~1 MB (FR-1.1, FR-3.1)."""
import json

from conftest import make_client
from test_literature import DOI, fake_http, make_pdf  # noqa: F401 — fixture reused


def _send(client, content, name, size=4096, skip=()):
    uid = "u" + str(abs(hash((name, len(content)))))
    total = -(-len(content) // size)
    for i in range(total):
        if i in skip:
            continue
        r = client.post(f"/api/chunks?upload_id={uid}&index={i}&total={total}&name={name}",
                        content=content[i * size:(i + 1) * size], headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200, r.text
    return uid


def test_data_file_in_parts(api):
    from generate_sample import generate
    pid = api.post("/api/projects", json={"title": "T"}).json()["id"]
    csv = generate().to_csv(index=False).encode()
    uid = _send(api, csv, "series.csv")
    _send(api, csv[:4096], "series.csv")  # a repeated part after a dropped connection is harmless
    r = api.post(f"/api/projects/{pid}/uploads", data={"refs": json.dumps([uid])})
    assert r.status_code == 200, r.text
    assert r.json()[0]["n_rows"] == 24
    again = api.post(f"/api/projects/{pid}/uploads", data={"refs": json.dumps([uid])})
    assert again.status_code == 400  # parts are used once and dropped


def test_parts_are_private_and_complete(api):
    pid = api.post("/api/projects", json={"title": "T"}).json()["id"]
    uid = _send(api, b"a,b\n1,2\n" * 2000, "x.csv")
    other = make_client("other@example.org")
    opid = other.post("/api/projects", json={"title": "O"}).json()["id"]
    assert other.post(f"/api/projects/{opid}/uploads", data={"refs": json.dumps([uid])}).status_code == 400
    partial = _send(api, b"a,b\n1,2\n" * 2000, "y.csv", skip={1})
    r = api.post(f"/api/projects/{pid}/uploads", data={"refs": json.dumps([partial])})
    assert r.status_code == 400 and "не полностью" in r.json()["detail"]
    assert api.post("/api/chunks?upload_id=bad/id&index=0&total=1", content=b"x").status_code == 400


def test_article_pdf_in_parts_as_background_job(api, fake_http):  # noqa: F811
    from app import jobs, main
    pid = api.post("/api/projects", json={"title": "GIST"}).json()["id"]
    pdf = make_pdf()
    uid = _send(api, pdf, "paper.pdf", size=1024)
    r = api.post(f"/api/projects/{pid}/literature/pdf?async=1", data={"refs": json.dumps([uid])})
    assert r.status_code == 202
    jobs.run_one(main.app)
    j = api.get(f"/api/jobs/{r.json()['job_id']}").json()
    assert j["status"] == "done", j
    src = j["result"]["added"][0]
    assert src["doi"] == DOI
    assert api.get(f"/api/projects/{pid}/literature/sources/{src['id']}/file.pdf").content == pdf
