"""check_manuscript (PRD 8.3, 5.4): numbers, citations, clichés, evidence wording,
abbreviations, terminology, spelling variant, required elements, counts.

severity: "blocking" (export forbidden until resolved), "warning", "info".
"""
import json
import re
from pathlib import Path

from .. import config
from . import lang as L, style
from .render import LATEX_LEFTOVER, word_count

DEFAULT_CLICHES = Path(__file__).parent / "cliches.json"
STRONG = re.compile(r"\b(demonstrat\w*|prove[sdn]?|proves|establish\w*|confirm\w*|definitive\w*|clearly show\w*|"
                    r"significant(ly)?)\b", re.I)
STRONG_RU = re.compile(r"(?<![а-яё])(доказ\w*|установлен\w*|достоверн\w*|подтвержда\w*|подтвердил\w*|"
                       r"убедительно|статистически значим\w*|значим(ая|ое|ые|ой|ых|о)\b)", re.I)
NUMBER = re.compile(r"(?<![\w.\-/:])(\d+(?:[.,]\d+)?)(?![\w\-/:]|\.\d)")
ABBR = re.compile(r"\b([A-ZА-ЯЁ][A-ZА-ЯЁ0-9&]{1,6})s?\b")
ABBR_OK = {"CI", "OR", "SD", "DNA", "RNA", "WHO", "USA", "UK", "II", "III", "IV", "VI", "HE", "AND", "OR", "NOS",
           "ДИ", "ОШ", "ОР", "МКИ", "ДНК", "РНК", "ВОЗ", "РФ", "СИ", "США"}
UK_US = [("tumour", "tumor"), ("haematoxylin", "hematoxylin"), ("oesophag", "esophag"), ("oedema", "edema"),
         ("anaemia", "anemia"), ("paediatric", "pediatric"), ("haemorrhag", "hemorrhag"), ("colour", "color"),
         ("behaviour", "behavior"), ("centre", "center"), ("analyse", "analyze"), ("characteris", "characteriz"),
         ("randomis", "randomiz"), ("organis", "organiz"), ("labelling", "labeling"), ("favour", "favor"),
         ("leukaemia", "leukemia"), ("oestrogen", "estrogen"), ("haematolog", "hematolog"), ("fibre", "fiber")]


def cliches():
    user = config.DATA_DIR / "cliches.json"
    items = json.loads(DEFAULT_CLICHES.read_text(encoding="utf-8"))
    if user.exists():
        items += json.loads(user.read_text(encoding="utf-8"))
    return items


def add_cliche(pattern, suggestion):
    re.compile(pattern)  # validate
    user = config.DATA_DIR / "cliches.json"
    items = json.loads(user.read_text(encoding="utf-8")) if user.exists() else []
    items.append({"pattern": pattern, "suggestion": suggestion})
    user.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ(\[«])", text) if s.strip()]


def check(sections, renderer, facts, findings, template, tier_code, inputs_text, names, budgets):
    """sections: ordered list of {key, kind, heading, source, whitelist}. Returns (issues, counts)."""
    issues = []

    def add(sec, severity, code, message, fragment="", suggestion=""):
        issues.append({"section": sec["key"], "heading": sec["heading"], "severity": severity, "code": code,
                       "message": message, "fragment": fragment[:200], "suggestion": suggestion})

    fact_values = {f["value"].replace(",", ".") for f in facts.values() if f["kind"] == "number"}
    allowed_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", inputs_text)) | set(re.findall(r"\d+", " ".join(names)))
    evidence = {f["id"]: f["evidence"] for f in findings}
    name_tokens = {t.upper() for n in names for t in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]*", n)}
    variant = template.get("language_variant") if template else None
    lang = L.of(template)
    main_text, abstract_text = [], ""
    counts = {"sections": {}}
    all_cl = cliches()

    for sec in sections:
        src = sec["source"]
        if not src.strip():
            continue
        segs = renderer.segments(src, sec["kind"])
        text = renderer.text(src, sec["kind"])
        russian = lang == "ru" and sec["kind"] != "abstract_en"
        wc = word_count(text)
        counts["sections"][sec["key"]] = wc
        if sec["kind"] == "abstract":
            abstract_text = text
        elif sec["kind"] not in ("statements", "abstract_en"):
            main_text.append(text)
        tex = LATEX_LEFTOVER.search(text)
        if tex:
            add(sec, "warning", "markup", "в тексте осталась разметка (LaTeX или `код`) — перепишите это место обычным текстом",
                tex.group(0), "например: q ≥ 0.05")
        budget = budgets.get(sec["key"])
        if budget and wc > budget * 1.1:
            add(sec, "warning", "budget", f"{wc} слов при бюджете {budget}")

        whitelist = set(sec.get("whitelist") or [])
        for p in segs:
            for seg in p["segments"]:
                if seg["t"] == "unknown":
                    add(sec, "blocking", "unknown_ref", f"ссылка на несуществующий факт/таблицу: {seg['raw']}",
                        seg["raw"], "находка не принята или удалена — исправьте текст")
                elif seg["t"] == "todo":
                    add(sec, "blocking", "todo", f"требуется информация от автора: {seg['v']}", seg["raw"])
                elif seg["t"] == "cite" and seg["bad"]:
                    add(sec, "blocking", "citation_bad", "цитата отклонена или отсутствует: " + ", ".join(seg["bad"]),
                        seg["raw"])
                elif seg["t"] == "text":
                    for m in NUMBER.finditer(seg["v"]):
                        num = m.group(1)
                        tail = seg["v"][m.end():m.end() + 6]
                        head = seg["v"][max(0, m.start() - 8):m.start()]
                        manual = r"(Table|Figure|Fig\.|[Тт]абл\.?|[Тт]аблиц\w*|[Рр]ис\.?|[Рр]исун\w*)\s*$"
                        if num in whitelist or num in allowed_numbers or tail.startswith(("% CI", "% ДИ")) \
                                or re.search(manual, head):
                            if re.search(manual, head):
                                add(sec, "warning", "manual_ref", f"номер «{head.strip()} {num}» набран вручную",
                                    head + num, "используйте {{TAB:…}} / {{FIG:…}}")
                            continue
                        if num.replace(",", ".") in fact_values:
                            add(sec, "warning", "number_literal", f"число {num} набрано вручную, а не из результатов",
                                num, "замените на ссылку {{…}} на факт анализа")
                        else:
                            sev = "blocking" if sec["kind"] in ("results", "abstract") else "warning"
                            add(sec, sev, "number_unmatched",
                                f"число {num} не найдено в результатах анализа и данных автора", seg["v"][
                                    max(0, m.start() - 40):m.end() + 40],
                                "проверьте; если верно — отметьте как проверенное вручную")
        # evidence-level wording
        for s in _sentences(src):
            ids = set(re.findall(r"\{\{\s*(A\d+)\.", s))
            weak = [i for i in ids if evidence.get(i) in ("exploratory", "descriptive")]
            m = (STRONG_RU if russian else STRONG).search(s)
            if weak and m:
                sev = "blocking" if tier_code == "minimal" and sec["kind"] in ("abstract", "conclusion") else "warning"
                add(sec, sev, "evidence_wording",
                    f"«{m.group(0)}» — слишком сильно для находки уровня exploratory/описательная ({', '.join(weak)})",
                    s, "«наблюдалось в данной серии» / «может указывать» / «требует подтверждения»" if russian
                    else "suggests / was observed in this series / warrants validation")
        # signs of AI prose (style.py) and the author's own cliché list
        st = style.analyze(text, "ru" if russian else "en")
        counts.setdefault("style", {})[sec["key"]] = st["score"]
        seen_frag = set()
        for h in st["hits"]:
            if h["fragment"].lower() in seen_frag or len(seen_frag) >= 6:
                continue
            seen_frag.add(h["fragment"].lower())
            add(sec, "warning", "ai_style", f"звучит как ИИ: «{h['fragment']}»", h["fragment"], h["advice"])
        if st["flat_rhythm"]:
            add(sec, "warning", "ai_rhythm", "предложения почти одинаковой длины — характерно для текста ИИ", "",
                "чередуйте короткие и длинные предложения")
        for c in all_cl:
            for m in re.finditer(c["pattern"], text, re.I):
                if m.group(0).lower() not in seen_frag:
                    add(sec, "warning", "cliche", f"клише: «{m.group(0)}»", m.group(0), c["suggestion"])
        starts = len(re.findall(r"(?:^|[.!?]\s+)(Furthermore|Moreover|Additionally),", text))
        if starts > 2:
            add(sec, "warning", "cliche", f"«Furthermore/Moreover/Additionally» в начале предложений: {starts} раз(а)",
                "", "перестройте переходы")
        # spelling variant
        if variant in ("UK", "US"):
            for uk, us in UK_US:
                wrong = us if variant == "UK" else uk
                if re.search(r"\b" + wrong + r"(?!igen)", text, re.I):
                    add(sec, "warning", "spelling", f"вариант «{wrong}…» не соответствует {variant} English", wrong,
                        uk if variant == "UK" else us)
        plain = "".join(seg["v"] for par in segs for seg in par["segments"] if seg["t"] not in ("todo", "unknown"))
        cyr = [] if russian else re.findall(r"[А-Яа-яЁё][А-Яа-яЁё\s-]*", plain)
        if cyr:
            add(sec, "blocking", "cyrillic", "кириллица в англоязычном тексте: " + ", ".join(sorted(set(cyr))[:5]),
                cyr[0], "переведите термины на шаге «Терминология» и исправьте текст")
        if sec["kind"] == "discussion" and not re.search(r"limitation|ограничени", text, re.I):
            add(sec, "blocking", "limitations", "в обсуждении нет абзаца об ограничениях исследования (FR-5.5)")

    # abbreviations: defined at first use, separately for the abstract and the main text
    for label, scope in (("abstract", [s for s in sections if s["kind"] == "abstract"]),
                         ("abstract", [s for s in sections if s["kind"] == "abstract_en"]),
                         ("main", [s for s in sections if s["kind"] not in ("abstract", "abstract_en", "statements")])):
        seen = set()
        for sec in scope:
            text = renderer.text(sec["source"], sec["kind"])
            for m in ABBR.finditer(text):
                a = m.group(1)
                if a in seen or a in ABBR_OK or a in name_tokens or re.fullmatch(r"[IVX]+", a):
                    continue
                seen.add(a)
                before, after = text[max(0, m.start() - 1):m.start()], text[m.end():m.end() + 2]
                if before != "(" and not after.startswith(" ("):
                    add(sec, "warning", "abbreviation", f"аббревиатура {a} не расшифрована при первом упоминании"
                        + (" в abstract" if label == "abstract" else ""), a)

    # the discussion should not open by repeating the introduction's first sentence (a typical model habit)
    first = {}
    for s in sections:
        if s["kind"] in ("introduction", "discussion") and s["source"].strip():
            body = [p for p in renderer.segments(s["source"], s["kind"]) if p["type"] == "p"]
            if body:
                text = "".join(seg["v"] for seg in body[0]["segments"])
                first[s["kind"]] = (s, re.split(r"(?<=[.!?])\s+", text.strip())[0])
    if "introduction" in first and "discussion" in first:
        a, b = (set(re.findall(r"\w{4,}", first[k][1].lower())) for k in ("introduction", "discussion"))
        if a and b and len(a & b) / len(a | b) >= 0.45:
            sec = first["discussion"][0]
            add(sec, "warning", "ai_repeat", "обсуждение начинается с повторения первой фразы введения",
                first["discussion"][1], "начните обсуждение с главного результата этой работы")

    # terminology consistency across the manuscript
    full = " ".join(renderer.text(s["source"], s["kind"]) for s in sections)
    variants = {}
    for tok in re.findall(r"\b[A-Za-z]{1,5}-?\d{1,4}[A-Za-z]?\b", full):
        variants.setdefault(tok.replace("-", "").lower(), set()).add(tok)
    for forms in variants.values():
        if len(forms) > 1:
            issues.append({"section": None, "heading": "вся рукопись", "severity": "warning", "code": "terminology",
                           "message": "разные написания: " + ", ".join(sorted(forms)), "fragment": "",
                           "suggestion": "выберите одно написание"})

    counts["words"] = sum(word_count(t) for t in main_text) if main_text else None
    counts["abstract_words"] = word_count(abstract_text) if abstract_text else None
    return issues, counts
