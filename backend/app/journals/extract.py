"""FR-4.2: profile extraction from the journal's Author Guidelines text.

Every value must come with a verbatim quote; quotes are checked in code
(store.apply_extraction) and fields without a matching quote are shown to the
person verifying the profile as "unverified".
"""
import json

from .. import llm
from ..config import MODEL_EXTRACTION
from ..storage import now_iso
from .schema import ARTICLE_TYPES, FIELDS

MAX_CHARS = 400_000

SYSTEM = """You extract a journal's submission requirements from its Author Guidelines into a fixed list of fields.

Rules:
- Use only the guidelines text provided. Do not use prior knowledge about the journal.
- Return a field only if the guidelines state it. Omit everything that is not stated; omitted fields are shown
  to the author as "not stated in the guidelines".
- For every field give "quote": a short passage copied exactly from the text (verbatim, no paraphrase)
  that states the value.
- value formats: int → digits only ("250"); bool → "true"/"false"; list → items separated by "; ";
  enum → exactly one of the allowed options; text → concise English.
- Article types: map the journal's own article categories onto the keys provided (original_article,
  case_series, brief_report, case_report, letter, review). Use types.<key>.name for the journal's own name of
  that category. Set types.<key>.accepted = "false" only if the guidelines say that category is not accepted.
- Requirement fields (enum required|recommended|optional|not_accepted) describe whether the element must be
  included."""


def _catalogue():
    lines = []
    for path, (ftype, label, group, hint) in FIELDS.items():
        if path.endswith("csl_id"):
            continue
        lines.append(f"{path} [{ftype}] — {label}" + (f"; {hint}" if hint else ""))
    return "\n".join(lines)


def extract_profile(profile, texts: dict):
    parts, size = [], 0
    for sid, text in texts.items():
        block = f"=== Guidelines source {sid} ===\n{text}"
        if size + len(block) > MAX_CHARS:
            block = block[: MAX_CHARS - size]
        parts.append(block)
        size += len(block)
        if size >= MAX_CHARS:
            break
    paths = [p for p in FIELDS if not p.endswith("csl_id")]
    schema = {
        "type": "object",
        "properties": {"fields": {"type": "array", "items": {
            "type": "object",
            "properties": {"path": {"type": "string", "enum": paths}, "value": {"type": "string"},
                           "quote": {"type": "string"}},
            "required": ["path", "value", "quote"], "additionalProperties": False}}},
        "required": ["fields"], "additionalProperties": False,
    }
    user = (f"Journal: {profile['name']} ({profile.get('publisher') or 'publisher unknown'})\n"
            f"Article type keys: {', '.join(ARTICLE_TYPES)}\n\nFields:\n{_catalogue()}\n\n" + "\n\n".join(parts))
    pseudo_project = {"id": f"journal:{profile['id']}", "anonymization": {"status": "confirmed"}}
    out = llm._call(pseudo_project, "extract_journal_profile", MODEL_EXTRACTION, SYSTEM, user, schema,
                    max_tokens=32000, gate=False, output_config={"effort": "medium"})
    meta = {"model": MODEL_EXTRACTION, "created_at": now_iso(), "n_returned": len(out["fields"]),
            "truncated": size >= MAX_CHARS, "sources": list(texts)}
    return out["fields"], meta


def fields_payload(items):
    return json.dumps(items, ensure_ascii=False)
