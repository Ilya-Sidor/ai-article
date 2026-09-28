"""REST API of ethics and transparency (Module 7)."""
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .ethics import reid, similarity, transparency
from .literature import service as lit
from .literature_api import store
from .manuscript import store as ms
from .storage import read_json, write_json

router = APIRouter(prefix="/api")
DEFAULT_SETTINGS = {"age_bands": True, "age_band_width": 10, "exclude_text": True, "drop_columns": []}


def settings(pid):
    return {**DEFAULT_SETTINGS, **read_json(store.dir(pid) / "ethics.json", {})}


def supplementary_csv(pid):
    df = store.dataset(pid)
    dictionary = read_json(store.dir(pid) / "dictionary.json", [])
    return reid.csv_bytes(reid.supplementary_frame(df, dictionary, settings(pid)))


def review(pid):
    """All module-7 checks; issues use the manuscript issue format."""
    from .manuscript_api import _renderer, context
    ctx, state = context(pid)
    project = ctx["project"]
    renderer = _renderer(pid, ctx, state)
    sections = [{"key": s["key"], "heading": s["heading"], "source": ms.current_source(s)}
                for s in ms.ordered(state) if s["kind"] != "statements" and ms.current_source(s)]
    cover = (state.get("cover") or {}).get("versions")
    if cover:
        sections.append({"key": "cover_letter", "heading": "Cover letter", "source": cover[-1]["body"]})
    n = (ctx["analysis"].get("summary") or {}).get("n", 0) if ctx["analysis"] else 0
    text_issues = reid.text_risks(sections, renderer, n, project["article_type"] == "case_report")
    labels = {s["id"]: s["label"] for s in lit.sources(store, pid)}
    overlap, overlap_stats = similarity.check(sections, renderer, lit.chunks(store, pid), labels)
    authors = transparency.authorship_issues(state.get("inputs"))
    df = store.dataset(pid)
    dictionary = read_json(store.dir(pid) / "dictionary.json", [])
    data = reid.data_risk(df, dictionary, settings(pid)) if df is not None else None
    data_issues = []
    if data and data["after"]["unique_cases"]:
        data_issues.append({"section": None, "heading": "Supplementary", "severity": "warning", "code": "reid_data",
                            "message": f"в таблице случаев {len(data['after']['unique_cases'])} из {data['after']['n']} "
                                       f"уникальны по {', '.join(data['after']['columns'])} (k = 1)",
                            "fragment": ", ".join(data["after"]["unique_cases"][:10]),
                            "suggestion": "обобщите возраст, исключите столбец или объедините редкие значения"})
    return {
        "ai_usage": transparency.usage_log(pid), "ai_statement": transparency.statement_preview(pid),
        "ai_policy": (ctx["template"] or {}).get("ai_policy"),
        "ai_requirement": next((s["requirement"] for s in (ctx["template"] or {}).get("statements", [])
                                if s["key"] == "ai_disclosure"), None),
        "reminders": transparency.reminders(project, state.get("inputs"), n, {"clinical": False}, ctx["template"]),
        "authorship": authors, "text_risks": text_issues, "data_risk": data, "data_issues": data_issues,
        "overlap": overlap, "overlap_stats": overlap_stats,
        "issues": authors + text_issues + data_issues + overlap,
    }


@router.get("/projects/{pid}/ethics")
def get_ethics(pid: str):
    store.get(pid)
    r = review(pid)
    r["blocking"] = sum(1 for i in r["issues"] if i["severity"] == "blocking")
    return r


class SettingsIn(BaseModel):
    age_bands: Optional[bool] = None
    age_band_width: Optional[int] = Field(default=None, ge=5, le=30)
    exclude_text: Optional[bool] = None
    drop_columns: Optional[List[str]] = None


@router.put("/projects/{pid}/ethics/settings")
def put_settings(pid: str, body: SettingsIn):
    store.get(pid)
    s = settings(pid)
    s.update(body.model_dump(exclude_none=True))
    write_json(store.dir(pid) / "ethics.json", s)
    store.audit(pid, "author", "ethics.supplementary_settings", s)
    return get_ethics(pid)
