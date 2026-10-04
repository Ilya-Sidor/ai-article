"""Administrator settings: which AI provider and models the application uses."""
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import auth, db, llm, settings

router = APIRouter(prefix="/api/admin")
PROVIDERS = {"deepseek": "DeepSeek API (платно, недорого)",
             "free": "Бесплатная цепочка: Gemini + OpenRouter", "gemini": "Только Google Gemini API",
             "openrouter": "Только OpenRouter (бесплатные модели)", "anthropic": "Anthropic Claude API (платно)",
             "ollama": "Локальная модель (Ollama)"}


def _admin(request: Request):
    if not request.state.user["is_admin"]:
        raise HTTPException(403, "только для администратора")


def _hint(key):
    return ("…" + key[-4:]) if key else None


def _free_openrouter_models():
    caps = llm._openrouter_caps()
    return sorted(({"id": mid, "structured": "structured_outputs" in c} for mid, c in caps.items()
                   if mid.endswith(":free") or mid == "openrouter/free"), key=lambda m: (not m["structured"], m["id"]))


def _view():
    key = llm.gemini_key()
    reasoning, extraction = llm.gemini_models()
    chain_r, chain_e = llm.chains()
    return {
        "provider": llm.provider(), "providers": PROVIDERS, "status": llm.status(),
        "gemini": {"configured": bool(key), "key_hint": _hint(key),
                   "model_reasoning": reasoning, "model_extraction": extraction, "models": llm.GEMINI_CHAIN},
        "openrouter": {"configured": bool(llm.openrouter_key()), "key_hint": _hint(llm.openrouter_key()),
                       "free_models": _free_openrouter_models()},
        "chains": {"reasoning": chain_r, "extraction": chain_e,
                   "default_reasoning": llm.DEFAULT_CHAIN_REASONING, "default_extraction": llm.DEFAULT_CHAIN_EXTRACTION},
        "deepseek": {"configured": bool(llm.deepseek_key()), "key_hint": _hint(llm.deepseek_key()),
                     "key_file": str(llm.deepseek_key_file()), "key_in_file": llm.deepseek_key_file().exists(),
                     "models": dict(zip(("reasoning", "extraction"), llm.deepseek_models()))},
        "exhausted": llm.exhausted_models(),
    }


@router.get("/llm")
def get_llm(request: Request):
    _admin(request)
    return _view()


class LLMIn(BaseModel):
    provider: Optional[str] = None
    gemini_api_key: Optional[str] = Field(default=None, max_length=300)
    openrouter_api_key: Optional[str] = Field(default=None, max_length=300)
    deepseek_api_key: Optional[str] = Field(default=None, max_length=300)
    chain_reasoning: Optional[str] = Field(default=None, max_length=5000)
    chain_extraction: Optional[str] = Field(default=None, max_length=5000)
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
    if body.openrouter_api_key is not None:
        settings.set("llm.openrouter_api_key", body.openrouter_api_key.strip(), secret=True)
    if body.deepseek_api_key is not None:
        settings.set("llm.deepseek_api_key", body.deepseek_api_key.strip(), secret=True)
    for field, key in (("chain_reasoning", "llm.chain_reasoning"), ("chain_extraction", "llm.chain_extraction")):
        v = getattr(body, field)
        if v is not None:
            lines = [x.strip() for x in v.splitlines() if x.strip()]
            bad = [x for x in lines if x.split(":", 1)[0] not in ("gemini", "openrouter") or ":" not in x]
            if bad:
                raise HTTPException(400, "каждая строка — «gemini:модель» или «openrouter:модель»; ошибка: " + bad[0])
            settings.set(key, "\n".join(lines))
    if body.gemini_api_key or body.openrouter_api_key or body.deepseek_api_key:  # a new key lifts "rejected" blocks
        llm._exhausted.clear()
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


# ---------------------------------------------------------------------------
# Colleagues: invitations and accounts
# ---------------------------------------------------------------------------

class InviteIn(BaseModel):
    email: str = Field(default="", max_length=320)
    days: int = Field(default=auth.INVITE_DAYS, ge=1, le=30)


def _people():
    with db.engine().connect() as c:
        owned = dict(c.execute(select(db.memberships.c.user_id, func.count()).where(db.memberships.c.role == "owner")
                               .group_by(db.memberships.c.user_id)).all())
        users = [{"id": u["id"], "email": u["email"], "is_admin": u["is_admin"], "is_active": u["is_active"],
                  "totp_enabled": u["totp_enabled"], "created_at": u["created_at"].isoformat(),
                  "projects": owned.get(u["id"], 0)}
                 for u in c.execute(select(db.users).order_by(db.users.c.id)).mappings()]
        by_id = {u["id"]: u["email"] for u in users}
        now = auth.now()
        invites = []
        for i in c.execute(select(db.invites).order_by(db.invites.c.created_at.desc()).limit(50)).mappings():
            status = "used" if i["used_at"] else ("expired" if auth._aware(i["expires_at"]) < now else "active")
            invites.append({"id": i["token_hash"][:12], "email": i["email"], "status": status,
                            "created_at": i["created_at"].isoformat(), "expires_at": i["expires_at"].isoformat(),
                            "used_by": by_id.get(i["used_by"])})
    # the address colleagues should use in invitation links (a stable page that redirects to the current
    # tunnel address); without it the browser's current address is used
    return {"users": users, "invites": invites,
            "entry_url": settings.get("app.entry_url", "AI_ARTICLE_ENTRY_URL", "") or ""}


@router.get("/people")
def people(request: Request):
    _admin(request)
    return _people()


@router.post("/invites")
def create_invite(body: InviteIn, request: Request):
    _admin(request)
    email = body.email.strip().lower()
    if email and not auth.EMAIL_RE.match(email):
        raise HTTPException(400, "некорректный e-mail")
    token = auth.create_invite(request.state.user["id"], email or None, body.days)
    return {**_people(), "token": token}


@router.delete("/invites/{invite_id}")
def revoke_invite(invite_id: str, request: Request):
    _admin(request)
    with db.engine().begin() as c:
        n = c.execute(db.invites.delete().where(db.invites.c.token_hash.like(invite_id[:12] + "%"),
                                                db.invites.c.used_at.is_(None))).rowcount
    if not n:
        raise HTTPException(404, "приглашение не найдено или уже использовано")
    return _people()


class UserIn(BaseModel):
    is_active: bool


@router.put("/users/{user_id}")
def set_user_active(user_id: int, body: UserIn, request: Request):
    """Block or unblock a colleague's account; a blocked user's sessions end at once, data stays."""
    _admin(request)
    if user_id == request.state.user["id"]:
        raise HTTPException(400, "нельзя заблокировать собственную учётную запись")
    with db.engine().begin() as c:
        if not c.execute(db.users.update().where(db.users.c.id == user_id).values(is_active=body.is_active)).rowcount:
            raise HTTPException(404, "пользователь не найден")
        if not body.is_active:
            c.execute(db.sessions.delete().where(db.sessions.c.user_id == user_id))
    return _people()
