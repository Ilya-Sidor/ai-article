"""NER layer of anonymization (FR-1.2): Russian named entities with Natasha.

Runs after the rule layer. PER spans are replaced; LOC/ORG spans are reported as
quasi-identifiers for the author to review. Medical eponyms (Van Gieson, Crohn,
Langerhans…) are excluded so that the morphology text is not damaged.
Disable with AI_ARTICLE_NER=0.
"""
import os
import re
import threading

EPONYM_STEMS = [
    "ван гиз", "гизон", "крон", "лангерган", "лангханс", "ходжкин", "беркитт", "юинг", "вильмс", "педжет", "боуэн",
    "шифф", "гомори", "перлс", "грокотт", "циль", "нильсен", "романовск", "гимз", "папаникол", "лейдиг", "сертоли",
    "ашофф", "рид", "штернберг", "капоши", "шегрен", "хашимото", "грейвс", "аддисон", "кушинг", "барретт", "маллори",
    "вирхов", "клатскин", "крукенберг", "шницлер", "хиршпрунг", "меккел", "бреннер", "шиллер", "дюваль", "кикучи",
    "розаи", "дорфман", "кастлман", "вальденстр", "сезари", "лёффлер", "леффлер", "гассер", "реклингхаузен",
    "борман", "лорен", "глисон", "ноттингем", "блум", "ричардсон", "фурман", "миеттинен", "бреслоу", "кларк",
    "вейгерт", "массон", "малори", "гейденгайн", "ван-гизон", "кэпоши", "альцгеймер", "паркинсон", "хантер",
]
_lock = threading.Lock()
_models = None


def enabled():
    if os.environ.get("AI_ARTICLE_NER", "1") == "0":
        return False
    try:
        import natasha  # noqa: F401
        return True
    except ImportError:
        return False


def _load():
    global _models
    with _lock:
        if _models is None:
            from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter
            emb = NewsEmbedding()
            _models = (Doc, Segmenter(), NewsNERTagger(emb))
    return _models


def _is_eponym(text):
    t = text.lower().replace("ё", "е")
    return any(stem.replace("ё", "е") in t for stem in EPONYM_STEMS)


def entities(text):
    """[(start, end, type, text)] for PER / LOC / ORG, eponyms excluded."""
    if not text or not re.search(r"[А-ЯЁ]", text):
        return []
    Doc, segmenter, tagger = _load()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(tagger)
    out = []
    for s in doc.spans:
        if s.type == "PER" and _is_eponym(s.text):
            continue
        if "[" in s.text or "]" in s.text:  # already replaced by the rule layer
            continue
        out.append((s.start, s.stop, s.type, s.text))
    return out
