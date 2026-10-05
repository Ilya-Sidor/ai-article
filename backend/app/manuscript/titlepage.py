"""Title page: authors, affiliations, ORCID, corresponding author — bilingual for Russian journals.

Russian journals indexed in Scopus/WoS («Архив патологии» and others) ask for the title, the authors and the
affiliations in Russian and in English, the authors' names transliterated with the BSI system, an ORCID for
the submitting author, and a separate page with every author's full name, position and e-mail.

inputs["authors"]: [{name, name_en, position, affiliations: [1, 2], email, orcid, roles, corresponding}]
inputs["affiliations"]: [{name, name_en, address}]
"""
import re

# BSI transliteration (as used by translit.net "BSI" and the journals' examples: Белая → Belaia, Мельниченко →
# Mel'nichenko)
_BSI = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                 "u", "f", "kh", "ts", "ch", "sh", "shch", "\"", "y", "'", "e", "iu", "ia"]))
ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


def translit(text):
    out = []
    for ch in text or "":
        t = _BSI.get(ch.lower())
        out.append(ch if t is None else (t.capitalize() if ch.isupper() and t else t))
    return "".join(out)


def orcid_ok(orcid):
    """Format and ISO 7064 11,2 check digit."""
    o = (orcid or "").strip().replace("https://orcid.org/", "")
    if not ORCID_RE.match(o):
        return False
    digits = o.replace("-", "")
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


def _authors(inputs):
    return [a for a in (inputs or {}).get("authors") or [] if (a.get("name") or "").strip()]


def _indices(a):
    return ",".join(str(i) for i in a.get("affiliations") or [])


def blocks(inputs, front, lang):
    """The title block(s) as (kind, payload): title, authors [(name, sup)], affiliations [(sup, text)], plain."""
    inputs = inputs or {}
    authors, affs = _authors(inputs), inputs.get("affiliations") or []

    def one(title, name_key, aff_key):
        out = [("title", title)]
        if authors:
            out.append(("authors", [((a.get(name_key) or (translit(a["name"]) if name_key == "name_en" else a["name"])),
                                     _indices(a)) for a in authors]))
        for i, aff in enumerate(affs, 1):
            text = aff.get(aff_key) or aff.get("name") or ""
            address = aff.get("address_en" if aff_key == "name_en" else "address") or aff.get("address") or ""
            if text:
                out.append(("affiliation", (str(i), text + (f", {address}" if address else ""))))
        return out

    ru = lang == "ru"
    result = one(front.get("title") or "[уточнить: название статьи]", "name", "name")
    if ru:
        result.append(("break", None))
        result += one(front.get("title_en") or "[уточнить: название статьи на английском]", "name_en", "name_en")
    corr = next((a for a in authors if a.get("corresponding")), None)
    if corr:
        label = "Автор, ответственный за переписку" if ru else "Corresponding author"
        result.append(("plain", f"{label}: {corr['name']}" + (f", {corr['email']}" if corr.get("email") else "")
                       + (f", ORCID {corr['orcid']}" if corr.get("orcid") else "")))
    return result


def authors_page(inputs, lang):
    """Separate page of author details (Russian journals: «Сведения об авторах»)."""
    rows = []
    for a in _authors(inputs):
        parts = [a["name"], a.get("name_en") or translit(a["name"]), a.get("position"),
                 f"e-mail: {a['email']}" if a.get("email") else None,
                 f"ORCID: {a['orcid']}" if a.get("orcid") else None,
                 ("автор для переписки" if lang == "ru" else "corresponding author") if a.get("corresponding") else None]
        rows.append("; ".join(p for p in parts if p))
    return rows


def issues(inputs, lang):
    out = []

    def add(sev, code, message, suggestion="заполните в «2. Данные автора» → «Авторы и учреждения»"):
        out.append({"section": None, "heading": "Титульная страница", "severity": sev, "code": "titlepage_" + code,
                    "message": message, "fragment": "", "suggestion": suggestion})

    authors = _authors(inputs)
    if not authors:
        add("blocking", "authors", "не указаны авторы")
        return out
    affs = (inputs or {}).get("affiliations") or []
    if not affs:
        add("warning", "affiliations", "не указаны учреждения авторов")
    for a in authors:
        if affs and not a.get("affiliations"):
            add("warning", "author_aff", f"{a['name']}: не указано учреждение")
        bad = [i for i in a.get("affiliations") or [] if not 1 <= int(i) <= len(affs)]
        if bad:
            add("warning", "author_aff", f"{a['name']}: нет учреждения с номером {bad[0]}")
        if a.get("orcid") and not orcid_ok(a["orcid"]):
            add("warning", "orcid", f"{a['name']}: ORCID «{a['orcid']}» неверный (формат 0000-0000-0000-0000, "
                                    "проверьте последнюю цифру)")
    corr = [a for a in authors if a.get("corresponding")]
    if not corr:
        add("blocking", "corresponding", "не отмечен автор для переписки")
    elif not corr[0].get("email"):
        add("blocking", "corresponding", f"у автора для переписки ({corr[0]['name']}) нет e-mail")
    elif lang == "ru" and not corr[0].get("orcid"):
        add("warning", "orcid", "российские журналы требуют ORCID автора, подающего статью", "получите на orcid.org")
    if lang == "ru":
        if any(not (aff.get("name_en") or "").strip() for aff in affs):
            add("warning", "aff_en", "нет официального английского названия учреждения (журнал требует оба языка)",
                "сверьте написание на ror.org")
    return out
