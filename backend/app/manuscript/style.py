"""Signs of AI-generated prose in academic text (English and Russian) and how to avoid them.

Sources:
- Kobak D. et al. Delving into LLM-assisted writing in biomedical publications through excess vocabulary.
  Sci Adv 2025;11:eadt3813 (the "style" excess words of PubMed abstracts after 2023; github.com/berenslab/llm-excess-vocab).
- Wikipedia: Signs of AI writing (WikiProject AI Cleanup) and the "humanizer" skills built on it
  (github.com/blader/humanizer): staged contrasts, rule of three, -ing riders, inflated significance,
  stock transitions, uniform rhythm.
- Russian: the same patterns as calques ("играет ключевую роль", "проливает свет"), chancery style
  (канцелярит) and filler openers ("стоит отметить").

Only markers that are over-used by models are listed, not ordinary scientific vocabulary ("analysis",
"these", "were" are excess words in the statistics too, but they are not a sign of anything to a reader).
"""
import re
import statistics

# (regex, advice) — English; the advice is shown to the author and given to the model
EN = [
    (r"\bdelv(e|es|ed|ing)\b", "examine / analyse"),
    (r"\bunderscor(e|es|ed|ing)\b", "show / indicate"),
    (r"\bshowcas(e|es|ed|ing)\b", "show / present"),
    (r"\bintricac(y|ies)\b|\bintricate(ly)?\b", "complex / detailed, or say what exactly"),
    (r"\bpivotal\b", "important / central, or explain why"),
    (r"\bmeticulous(ly)?\b", "careful(ly), or remove"),
    (r"\brealms?\b", "field / area"),
    (r"\bmultifaceted\b", "say which facets"),
    (r"\binterplay\b", "interaction / relationship"),
    (r"\bgroundbreaking\b|\bunparalleled\b|\bremarkabl[ey]\b|\bunprecedented\b", "remove the praise; state the fact"),
    (r"\bunveil(s|ed|ing)?\b", "show / report"),
    (r"\bharness(es|ed|ing)?\b", "use"),
    (r"\bleverag(e|es|ed|ing)\b", "use"),
    (r"\bbolster(s|ed|ing)?\b", "support / strengthen"),
    (r"\belucidat(e|es|ed|ing)\b", "explain / clarify"),
    (r"\bgarner(s|ed|ing)?\b", "receive / attract"),
    (r"\bcommendable\b|\bnoteworthy\b", "remove or state why"),
    (r"\bburgeoning\b", "growing"),
    (r"\btransformative\b|\brevolutioniz\w+", "remove; academic text does not advertise"),
    (r"\bnuanced\b", "detailed, or say what the nuance is"),
    (r"\bseamless(ly)?\b", "remove"),
    (r"\binvaluable\b", "useful, or remove"),
    (r"\bcompelling\b", "strong / convincing, or remove"),
    (r"\btapestry\b|\blandscape\b", "be specific"),
    (r"\bunderexplored\b|\buncharted\b", "rarely studied / not studied"),
    (r"\bendeavou?rs?\b", "work / study"),
    (r"\bfoster(s|ed|ing)?\b", "promote / support"),
    (r"\b(holds?|holding) (great |significant |considerable |much )?promise\b", "may be useful for…, say for what"),
    (r"\bsheds? (new )?light on\b", "clarifies / explains"),
    (r"\bpav(e|es|ed|ing) the way\b", "allows / enables"),
    (r"\ba testament to\b", "shows / reflects"),
    (r"\bplays? an? (pivotal|crucial|key|vital|central|significant) role\b", "is important for / contributes to"),
    (r"\bit is (worth|important) (noting|to note) that\b", "remove the lead-in"),
    (r"\b(valuable|novel|crucial|key) insights?\b", "data / findings, or say what was learnt"),
    (r"\b(a )?deeper understanding\b", "say what is now understood"),
    (r"\bnot only\b[^.]{0,80}\bbut also\b", "state both points plainly"),
    (r"(?:^|(?<=[.!?]\s))(Notably|Additionally|Furthermore|Moreover|Importantly|Interestingly|Overall|Ultimately),",
     "start with the subject; drop the transition"),
    (r",\s(highlighting|underscoring|emphasizing|emphasising|reflecting|showcasing|demonstrating|signifying|"
     r"illustrating)\s(the|its|their|a)\s(importance|need|potential|role|significance|value|complexity)",
     "end the sentence; if the point matters, make it a separate claim with evidence"),
    (r"\b(these|our) (findings|results|data) (highlight|underscore|emphasi[sz]e) the (importance|need|potential)",
     "say what the findings show, with the number"),
    (r"\b(paramount|holistic|game[- ]changer)\b", "remove or replace with a precise word"),
    (r"\badds? to the (growing |expanding )?body of (literature|evidence|knowledge)\b", "say what exactly it adds"),
    (r"\b(expands?|broadens?|deepens?|enriches?) (our|the) (understanding|knowledge|spectrum)\b", "say what is new"),
    (r"\b(merits?|deserves?|warrants?) (particular |special |further )?attention\b|\bof particular interest\b",
     "state the point directly"),
    (r"\billustrates? the (importance|need|necessity)\b", "say what the case shows"),
    (r"\bin the (ever[- ])?evolving\b|\brapidly evolving\b", "remove"),
]
RU = [
    (r"игра(ет|ют|ющ\w*) (ключев|важн|решающ|значим|центральн|определяющ)\w* роль", "«важен для…», «влияет на…»"),
    (r"(стоит|следует|важно|необходимо|нужно) (отметить|подчеркнуть|упомянуть)", "уберите вводную, скажите сразу"),
    (r"(в заключение|подводя итог)", "уберите; заключение и так в разделе «Заключение»"),
    (r"(^|(?<=[.!?]\s))Таким образом,", "реже: не каждый абзац должен заканчиваться выводом"),
    (r"\bв рамках\b", "«в», «при»"),
    (r"\bданн(ый|ая|ое|ого|ой|ом|ую)\b", "«этот», «наш» или конкретное название (не путать с «данные» = data)"),
    (r"на сегодняшний день|на современном этапе", "«сейчас» или год / ссылка"),
    (r"(^|(?<=[.!?]\s))В настоящее время", "уберите или дайте ссылку на обзор"),
    (r"откры(вает|вают|ло) (новые )?(возможности|перспективы|горизонты)", "назовите конкретное применение"),
    (r"пролива(ет|ют) свет", "«уточняет», «объясняет»"),
    (r"прокладыва(ет|ют) путь", "«позволяет»"),
    (r"(комплексн|всесторонн|многогранн|беспрецедентн|уникальн|неоценим|инновационн|важнейш)\w*", "уберите оценку или поясните"),
    (r"\bсинерги\w*", "«совместное действие» или уберите"),
    (r"(углубл[её]нн\w* понимани\w*|более глубок\w* понимани\w*)", "скажите, что именно стало понятно"),
    (r"\bне только\b[^.]{0,80}\bно и\b", "скажите оба утверждения прямо"),
    (r",\s(подч[её]ркивая|демонстрируя|отражая|свидетельствуя|иллюстрируя)\s(важность|значимость|необходимость|роль|потенциал)",
     "закончите предложение; важное вынесите отдельно с доказательством"),
    (r"(данные|полученные) (результаты|данные) (подч[её]ркивают|демонстрируют) (важность|значимость|необходимость)",
     "скажите, что показывают результаты, с числом"),
    (r"(^|(?<=[.!?]\s))(Кроме того|Более того|Помимо этого|Следует отметить|Интересно, что),", "начните с подлежащего"),
    (r"расширя(ет|ют) (представлени\w*|знани\w*|спектр)", "скажите, что именно нового"),
    (r"заслужива(ет|ют) (особого )?внимания|представля(ет|ют) (особый |значительный |несомненный )?интерес|"
     r"вызыва(ет|ют) (особый )?интерес", "скажите, чем именно"),
    (r"иллюстрир(ует|уют) (необходимость|важность|значимость)", "скажите, что показывает наблюдение"),
    (r"(требу\w+|вызыва\w+) настороженност\w*", "«нужно учитывать», «следует исключать»"),
    (r"\bактуальност\w*", "уберите или объясните, почему это важно сейчас"),
    (r"(^|(?<=[.!?]\s))(Проведение|Осуществление|Выполнение|Использование|Применение) \w+", "начните с действия или субъекта"),
    (r"\b(осуществля\w+|производит\w*ся|имеет место|носит \w+ характер)\b", "простой глагол: «проводили», «был»"),
]


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)) if len(s.split()) >= 3]


def analyze(text, lang="en"):
    """{"score": 0..100, "hits": [{fragment, advice}], "metrics": {...}} for rendered text."""
    text = text or ""
    words = len(re.findall(r"\w+", text))
    hits = []
    for rx, advice in (RU if lang == "ru" else EN):
        for m in re.finditer(rx, text, re.I | re.M):
            hits.append({"fragment": m.group(0).strip(), "advice": advice, "pos": m.start()})
    sents = _sentences(text)
    lengths = [len(s.split()) for s in sents]
    cv = statistics.pstdev(lengths) / statistics.mean(lengths) if len(lengths) >= 8 else None  # too few to judge
    dashes = text.count("—") if lang == "en" else 0  # in Russian the dash is ordinary punctuation
    metrics = {"words": words, "sentences": len(sents), "mean_sentence": round(statistics.mean(lengths), 1) if lengths else 0,
               "rhythm_cv": round(cv, 2) if cv is not None else None, "em_dashes": dashes}
    flat_rhythm = cv is not None and cv < 0.33  # human academic prose varies sentence length more
    per_k = 1000 / max(words, 150)
    raw = (len(hits) + max(0, dashes - 1)) * per_k * 4 + (15 if flat_rhythm else 0)
    hits.sort(key=lambda h: h["pos"])
    return {"score": min(100, round(raw)), "hits": hits, "flat_rhythm": flat_rhythm, "metrics": metrics}


STYLE_RULES_EN = """Style (the text must read like an experienced pathologist wrote it, not a language model):
- Plain, precise academic English. Say what was found and what it means; no promotional or inflated wording
  (no "groundbreaking", "pivotal", "remarkable", "unprecedented", "a testament to", "paves the way", "sheds light").
- Avoid words that language models over-use: delve, underscore, showcase, intricate, meticulous, realm,
  multifaceted, interplay, leverage, harness, bolster, elucidate, garner, foster, nuanced, invaluable, compelling,
  noteworthy, landscape, tapestry, holds promise, crucial insights, deeper understanding.
- No staged contrasts ("not only… but also", "not X but Y"), no rule-of-three lists for rhythm, no sentence tails
  with "-ing" that add significance (", highlighting the importance of…"), no em dashes as connectors.
- Do not open sentences with Notably/Additionally/Furthermore/Moreover/Overall; start with the subject.
- Vary sentence length the way human writing does: short statements of fact next to longer explanatory ones.
- Do not end paragraphs with a one-sentence summary of the paragraph; the evidence speaks for itself.
- Hedge only where the evidence level requires it; otherwise state findings directly."""

STYLE_RULES_RU = """Стиль (текст должен читаться как написанный опытным патологоанатомом, а не языковой моделью):
- Научный русский язык без кальк с английского и без оценочных слов: не «играет ключевую роль», «проливает свет»,
  «открывает новые перспективы», «уникальный», «беспрецедентный», «всесторонний», «комплексный подход».
- Без вводных-пустышек: «стоит отметить», «важно подчеркнуть», «следует отметить», «в настоящее время»,
  «на сегодняшний день», «в заключение»; не начинайте предложения с «Кроме того,», «Более того,».
- Без канцелярита: «данный», «в рамках», «является» через предложение, цепочки отглагольных существительных
  («проведение оценки выраженности»).
- Без деепричастных «хвостов», добавляющих значимость («…, подчёркивая важность…»), без «не только…, но и».
- Разная длина предложений, как у живого автора: короткие утверждения факта рядом с развёрнутыми объяснениями.
- Не заканчивайте каждый абзац выводом «Таким образом, …»; данные говорят сами за себя.
- Осторожные формулировки — только там, где этого требует уровень доказательности."""


def rules(lang):
    return STYLE_RULES_RU if lang == "ru" else STYLE_RULES_EN


def humanize_instruction(result, lang):
    """A targeted rewrite instruction from the detected markers."""
    found = []
    for h in result["hits"][:20]:
        if h["fragment"] not in [f for f, _ in found]:
            found.append((h["fragment"], h["advice"]))
    lines = [f"«{f}» → {a}" for f, a in found]
    head = ("Перепиши раздел так, чтобы он не звучал как текст языковой модели, сохранив академический стиль, "
            "все факты, числа, плейсхолдеры {{…}}, ссылки [[…]] и [уточнить: …]. ")
    if lines:
        head += "Замени или убери эти места: " + "; ".join(lines) + ". "
    if result.get("flat_rhythm"):
        head += "Предложения слишком одинаковой длины — чередуй короткие и длинные. "
    head += "Ничего не добавляй по существу и не убирай факты. Язык статьи: " + ("русский." if lang == "ru" else "английский.")
    return head
