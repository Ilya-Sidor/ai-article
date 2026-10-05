"""Micrographs: metadata stripped, multi-panel figure with letters/scale bar, legend, checks, export."""
import io
import json
import zipfile

from PIL import Image


def _jpeg_with_exif(color, size=(800, 600)):
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "SecretScopeVendor"  # Make
    exif[0x0131] = "Patient Ivanov viewer"  # Software
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def test_micrographs_flow(api):
    pid = api.post("/api/projects", json={"title": "GIST", "article_type": "case_report"}).json()["id"]
    api.post(f"/api/projects/{pid}/uploads", files={"files": ("c.csv", "Локализация,CD117\nжелудок,+\n".encode())})
    api.post(f"/api/projects/{pid}/anonymization/confirm")
    r = api.post(f"/api/projects/{pid}/manuscript/images",
                 files=[("files", ("Ivanov_slide.jpg", _jpeg_with_exif((200, 120, 160)))),
                        ("files", ("ihc.jpg", _jpeg_with_exif((120, 80, 40)))),
                        ("files", ("notes.txt", b"not an image"))]).json()
    assert [a["id"] for a in r["added"]] == ["I1", "I2"] and r["errors"][0]["file"] == "notes.txt"

    from app.manuscript import micro
    from app.manuscript_api import store
    raw = micro.read_bytes(micro._dir(store, pid) / "I1.png")
    img = Image.open(io.BytesIO(raw))
    assert img.format == "PNG" and not img.getexif() and b"Secret" not in raw and b"Ivanov" not in raw

    thumb = api.get(f"/api/projects/{pid}/manuscript/images/I1.jpg?size=200")
    assert thumb.status_code == 200 and max(Image.open(io.BytesIO(thumb.content)).size) == 200

    api.put(f"/api/projects/{pid}/manuscript/images/I1", json={"description": "Epithelioid cells", "stain": "H&E",
                                                              "magnification": "×200", "um_per_px": 0.5})
    m = api.post(f"/api/projects/{pid}/manuscript/figures/micro", json={"panels": ["I1", "I2"], "columns": 2}).json()
    fig = next(f for f in m["figures"] if f["kind"] == "micro")
    assert fig["id"] == "fm1" and "(A) Epithelioid cells; H&E, ×200." in fig["caption_effective"]
    assert "Scale bars as indicated" in fig["caption_effective"]
    codes = {(i["code"], i.get("image")) for i in m["issues"]}
    assert ("micro_phi", "I1") in codes and ("micro_legend", "I2") in codes
    assert next(i for i in m["issues"] if i["code"] == "micro_phi")["fix"]["goto"]["tab"] == "assets"

    prev = api.get(f"/api/projects/{pid}/manuscript/figures/fm1.png")
    assert prev.status_code == 200
    composed = Image.open(io.BytesIO(prev.content))
    assert composed.width == int(174 / 25.4 * 150)
    # panel letter area is white over the coloured image; the scale bar is drawn in black on panel A
    assert composed.getpixel((3, 3)) == (255, 255, 255)

    for iid in ("I1", "I2"):
        api.put(f"/api/projects/{pid}/manuscript/images/{iid}", json={"phi_checked": True})
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    assert not [i for i in m["issues"] if i["code"] == "micro_phi"]

    r = api.post(f"/api/projects/{pid}/manuscript/export", params={"mode": "draft"})
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    fig_files = [n for n in z.namelist() if n.startswith("figures/")]
    assert fig_files == ["figures/Figure_1.png"]
    out = Image.open(io.BytesIO(z.read(fig_files[0])))
    assert out.width == int(174 / 25.4 * 300) and round(out.info["dpi"][0]) == 300

    m = api.delete(f"/api/projects/{pid}/manuscript/images/I2").json()
    assert next(f for f in m["figures"] if f["kind"] == "micro")["panels"] == ["I1"]
    assert json.dumps(m["images"]).count('"id"') == 1
