"""Micrographs (FR-1.6, FR-5.7): photographs of slides as multi-panel figures.

An uploaded photograph is decoded and re-encoded, so EXIF (camera, GPS, software, dates) and any other embedded
metadata are dropped before it is stored (encrypted, like the rest of the project). The author describes each
image (what it shows, stain, magnification, µm per pixel when a scale bar should be drawn) and confirms that no
label with patient data is visible (slide labels often carry names). A figure combines images into panels
A, B, C… with letters and an optional scale bar, rendered at the journal's resolution; the legend is composed
from the panel descriptions, as journals ask ("stain and magnification in legends").
"""
import io

from PIL import Image, ImageDraw, ImageFont

from ..storage import now_iso, read_bytes, write_bytes

ALLOWED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
MAX_PIXELS = 80_000_000
FIGURE_WIDTH_MM = 174  # a double-column figure in most journals
LETTERS = "ABCDEFGHIJKLMNOP"
LETTERS_RU = "АБВГДЕЖЗИКЛМНОПР"
STAINS_RU = {"h&e": "окраска гематоксилином и эозином", "he": "окраска гематоксилином и эозином",
             "hematoxylin and eosin": "окраска гематоксилином и эозином", "pas": "ШИК-реакция",
             "giemsa": "окраска по Гимзе", "masson": "окраска по Массону", "van gieson": "окраска пикрофуксином по ван Гизону"}


class ImageError(ValueError):
    pass


def _dir(store, pid):
    return store.dir(pid) / "manuscript" / "images"


def clean(content: bytes):
    """Decode and re-encode: pixels only, no metadata. Returns (png bytes, width, height)."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception as exc:
        raise ImageError("не удалось прочитать изображение (поддерживаются JPEG, PNG, TIFF)") from exc
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = Image.new(img.mode, img.size)  # a fresh image: only the pixels are copied
    out.paste(img)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=False)  # no exif / icc / text chunks are written
    return buf.getvalue(), img.size[0], img.size[1]


def add(store, pid, state, filename, content):
    data, w, h = clean(content)
    images = state.setdefault("images", [])
    iid = "I%d" % (max([int(i["id"][1:]) for i in images] + [0]) + 1)
    write_bytes(_dir(store, pid) / f"{iid}.png", data)
    item = {"id": iid, "name": (filename or "image").rsplit("/", 1)[-1][:120], "width": w, "height": h,
            "description": "", "description_en": "", "stain": "", "magnification": "", "um_per_px": None,
            "has_scale_bar": False, "phi_checked": False, "uploaded_at": now_iso()}
    images.append(item)
    return item


def load(store, pid, iid):
    return Image.open(io.BytesIO(read_bytes(_dir(store, pid) / f"{iid}.png"))).convert("RGB")


def thumbnail(store, pid, iid, size=480):
    img = load(store, pid, iid)
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def _font(px):
    try:
        from matplotlib import font_manager
        return ImageFont.truetype(font_manager.findfont("DejaVu Sans:bold"), px)
    except Exception:
        return ImageFont.load_default()


def _scale_bar_um(width_um):
    """A round bar length about a fifth of the panel width."""
    for v in (1000, 500, 200, 100, 50, 20, 10, 5, 2, 1):
        if v <= width_um / 4:
            return v
    return 1


def compose(store, pid, state, figure, dpi=300, fmt="png", lang="en"):
    """The figure as image bytes: panels in a grid, letters, scale bars."""
    images = {i["id"]: i for i in state.get("images") or []}
    panels = [p for p in figure.get("panels") or [] if p in images]
    if not panels:
        raise ImageError("в рисунке нет изображений")
    cols = max(1, min(int(figure.get("columns") or 2), len(panels)))
    rows = -(-len(panels) // cols)
    width = int(FIGURE_WIDTH_MM / 25.4 * dpi)
    gap = max(4, width // 200)
    cell_w = (width - gap * (cols - 1)) // cols
    scaled = []
    for pid_ in panels:
        img = load(store, pid, pid_)
        ratio = cell_w / img.width
        scaled.append((pid_, img.resize((cell_w, max(1, int(img.height * ratio))), Image.LANCZOS), ratio))
    row_h = [max(s[1].height for s in scaled[r * cols:(r + 1) * cols]) for r in range(rows)]
    canvas = Image.new("RGB", (width, sum(row_h) + gap * (rows - 1)), "white")
    draw = ImageDraw.Draw(canvas)
    letter_font = _font(max(14, cell_w // 14))
    bar_font = _font(max(10, cell_w // 28))
    letters = LETTERS_RU if lang == "ru" else LETTERS
    y = 0
    for r in range(rows):
        x = 0
        for k, (iid, img, ratio) in enumerate(scaled[r * cols:(r + 1) * cols]):
            canvas.paste(img, (x, y))
            n = r * cols + k
            pad = cell_w // 60
            lb = draw.textbbox((0, 0), letters[n], font=letter_font)
            draw.rectangle([x, y, x + lb[2] + 2 * pad, y + lb[3] + 2 * pad], fill="white")
            draw.text((x + pad, y + pad - lb[1] // 2), letters[n], fill="black", font=letter_font)
            meta = images[iid]
            if meta.get("um_per_px") and not meta.get("has_scale_bar"):
                um_px = float(meta["um_per_px"]) / ratio
                length_um = _scale_bar_um(img.width * um_px)
                bar_px = int(length_um / um_px)
                bh = max(3, cell_w // 120)
                bx, by = x + img.width - bar_px - 3 * pad, y + img.height - 4 * pad - bh
                label = f"{length_um} {'мкм' if lang == 'ru' else 'µm'}"
                tb = draw.textbbox((0, 0), label, font=bar_font)
                draw.rectangle([bx - pad, by - tb[3] - 2 * pad, bx + bar_px + pad, by + bh + pad], fill="white")
                draw.rectangle([bx, by, bx + bar_px, by + bh], fill="black")
                draw.text((bx + (bar_px - tb[2]) // 2, by - tb[3] - pad), label, fill="black", font=bar_font)
            x += cell_w + gap
        y += row_h[r] + gap
    buf = io.BytesIO()
    canvas.save(buf, format="TIFF" if fmt == "tif" else "PNG", dpi=(dpi, dpi),
                **({"compression": "tiff_lzw"} if fmt == "tif" else {}))
    return buf.getvalue()


def _stain(stain, lang):
    if lang != "ru" or not stain:
        return stain
    return STAINS_RU.get(stain.strip().lower(), stain)


def caption(state, figure, lang="en", english=False):
    """Legend from the panels: '(A) … H&E, ×200. Scale bar, 100 µm.'"""
    images = {i["id"]: i for i in state.get("images") or []}
    ru = lang == "ru" and not english
    letters = LETTERS_RU if ru else LETTERS
    parts = []
    for n, iid in enumerate(p for p in figure.get("panels") or [] if p in images):
        im = images[iid]
        desc = (im.get("description_en") if english and im.get("description_en") else im.get("description")) or \
            "[уточнить: что на изображении]"
        tail = ", ".join(x for x in (_stain(im.get("stain"), "ru" if ru else "en"), im.get("magnification")) if x)
        parts.append(f"({letters[n]}) {desc.rstrip('.')}" + (f"; {tail}" if tail else "") + ".")
    if any(images[p].get("um_per_px") and not images[p].get("has_scale_bar") for p in figure.get("panels") or []
           if p in images):
        parts.append("Масштабный отрезок указан на панелях." if ru else "Scale bars as indicated.")
    lead = (figure.get("title_en") if english else figure.get("title")) or ""
    return (lead.rstrip(".") + ". " if lead else "") + " ".join(parts)


def issues(state, template):
    """Legend completeness, scale bars, and the patient-data check of the images used in included figures."""
    out = []
    images = {i["id"]: i for i in state.get("images") or []}
    need_bar = bool(((template or {}).get("figures") or {}).get("scale_bars"))
    for f in state.get("figures") or []:
        if f.get("kind") != "micro" or not f.get("include"):
            continue
        for iid in f.get("panels") or []:
            im = images.get(iid)
            if not im:
                continue

            def add(sev, code, msg, sug=""):
                out.append({"section": None, "heading": "Микрофотографии", "severity": sev, "code": code,
                            "message": f"рисунок {f['id']}, изображение «{im['name']}»: {msg}", "fragment": "",
                            "suggestion": sug, "image": iid})
            if not im.get("phi_checked"):
                add("blocking", "micro_phi", "не подтверждено, что на снимке нет надписей с данными пациента",
                    "проверьте этикетку стекла и подписи на фото")
            if not im.get("stain") or not im.get("magnification"):
                add("warning", "micro_legend", "в подписи нет метода окраски или увеличения",
                    "журналы требуют указывать окраску и увеличение")
            if not im.get("description"):
                add("warning", "micro_legend", "нет описания того, что видно на снимке")
            if need_bar and not im.get("has_scale_bar") and not im.get("um_per_px"):
                add("blocking", "micro_scale", "журнал требует масштабный отрезок",
                    "укажите размер пикселя (мкм/пиксель) — отрезок нарисуется сам, или отметьте, что он уже есть")
    return out
