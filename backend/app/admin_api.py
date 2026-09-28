"""Administrator settings: which AI provider and models the application uses."""
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import llm, settings

router = APIRouter(prefix="/api/admin")
PROVIDERS = {"gemini": "Google Gemini API", "anthropic": "Anthropic Claude API", "ollama": "Локальная модель (Ollama)"}


def _admin(request: Request):
    if not request.state.user["is_admin"]:
        raise HTTPException(403, "только для администратора")


def _view():
    key = llm.gemini_key()
    reasoning, extraction = llm.gemini_models()
    return {
        "provider": llm.provider(), "providers": PROVIDERS, "status": llm.status(),
        "gemini": {"configured": bool(key), "key_hint": ("…" + key[-4:]) if key else None,
                   "model_reasoning": reasoning, "model_extraction": extraction, "models": llm.GEMINI_MODELS},
    }


@router.get("/llm")
def get_llm(request: Request):
    _admin(request)
    return _view()


class LLMIn(BaseModel):
    provider: Optional[str] = None
    gemini_api_key: Optional[str] = Field(default=None, max_length=300)
    gemini_model_reasoning: Optional[str] = Field(default=None, max_length=100)
    gemini_model_extraction: Optional[str] = Field(default=None, max_length=100)


@router.put("/llm")
def put_llm(body: LLMIn, request: Request):
    _admin(request)
    if body.provider is not None:
        if body.provider not in PROVIDERS:
            raise HTTPException(400, "неизвестный провайдер")
        settings.set("llm.provider", body.provider)
    if body.gemini_api_key is not None:
        settings.set("llm.gemini_api_key", body.gemini_api_key.strip(), secret=True)
    for field, key in (("gemini_model_reasoning", "llm.gemini_model_reasoning"),
                       ("gemini_model_extraction", "llm.gemini_model_extraction")):
        v = getattr(body, field)
        if v is not None:
            if v and not v.startswith("gemini-"):
                raise HTTPException(400, "ожидается идентификатор модели Gemini")
            settings.set(key, v.strip())
    return _view()


@router.post("/llm/test")
def test_llm(request: Request):
    """One small structured request with the current settings (no patient data)."""
    _admin(request)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
              "additionalProperties": False}
    t = time.time()
    try:
        out = llm._call({"id": "admin:test", "anonymization": {"status": "confirmed"}}, "settings_test",
                        llm.MODEL_REASONING, "Answer in one word.", "Which organ is examined in gastric biopsy?",
                        schema, max_tokens=2000, gate=False)
    except llm.LLMUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "answer": out.get("answer"), "seconds": round(time.time() - t, 1),
            "model": llm.effective_model(llm.MODEL_REASONING)}
