"""Authentication and access control (NFR-5).

* passwords: scrypt (N=2^14, r=8, p=1), per-user salt;
* sessions: random 256-bit token in an HttpOnly cookie, only its SHA-256 in the DB,
  idle and absolute expiry;
* second factor: TOTP (RFC 6238), secret sealed with the master key, codes not reusable;
* lockout after repeated failures;
* project access: default deny — every /api/projects/<id>/… request needs a membership.
"""
import base64
import hashlib
import hmac
import io
import os
import re
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import crypto, db

router = APIRouter(prefix="/api/auth")

COOKIE = "aia_session"
SESSION_DAYS = 7
IDLE_HOURS = 12
MAX_FAILS = 5
LOCK_MINUTES = 15
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now():
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Passwords and TOTP
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=64)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=64)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def totp_code(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def totp_verify(secret_b32: str, code: str, last_counter=None, at=None):
    """Counter of the matching time step (±1 step), or None. Reuse of a used step is rejected."""
    code = (code or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    t = int((at or time.time()) // 30)
    for c in (t - 1, t, t + 1):
        if hmac.compare_digest(totp_code(secret_b32, c), code) and (last_counter is None or c > last_counter):
            return c
    return None


# ---------------------------------------------------------------------------
# Users, sessions, memberships
# ---------------------------------------------------------------------------

def _user_public(u):
    return {"id": u["id"], "email": u["email"], "is_admin": u["is_admin"], "totp_enabled": u["totp_enabled"]}


def get_user(user_id):
    with db.engine().connect() as c:
        return c.execute(select(db.users).where(db.users.c.id == user_id)).mappings().first()


def create_session(user_id) -> str:
    token = secrets.token_urlsafe(32)
    with db.engine().begin() as c:
        c.execute(db.sessions.insert().values(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user_id,
                                              created_at=now(), last_seen=now(),
                                              expires_at=now() + timedelta(days=SESSION_DAYS)))
    return token


def user_from_token(token):
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    with db.engine().begin() as c:
        s = c.execute(select(db.sessions).where(db.sessions.c.token_hash == th)).mappings().first()
        if not s:
            return None
        if _aware(s["expires_at"]) < now() or _aware(s["last_seen"]) < now() - timedelta(hours=IDLE_HOURS):
            c.execute(db.sessions.delete().where(db.sessions.c.token_hash == th))
            return None
        c.execute(db.sessions.update().where(db.sessions.c.token_hash == th).values(last_seen=now()))
        u = c.execute(select(db.users).where(db.users.c.id == s["user_id"])).mappings().first()
    return u if u and u["is_active"] else None


def job_token(job_id: str) -> str:
    return job_id + ":" + hmac.new(crypto.master_key(), job_id.encode(), hashlib.sha256).hexdigest()


def user_from_request(request: Request):
    internal = request.headers.get("x-aia-job")
    if internal:
        job_id, _, mac = internal.partition(":")
        if hmac.compare_digest(job_token(job_id), internal):
            with db.engine().connect() as c:
                j = c.execute(select(db.jobs.c.user_id).where(db.jobs.c.id == job_id)).first()
            return get_user(j[0]) if j else None
        return None
    return user_from_token(request.cookies.get(COOKIE))


def role(user, project_id):
    with db.engine().connect() as c:
        r = c.execute(select(db.memberships.c.role).where(db.memberships.c.project_id == project_id,
                                                          db.memberships.c.user_id == user["id"])).first()
    return r[0] if r else None


def add_member(project_id, user_id, role_="owner"):
    with db.engine().begin() as c:
        c.execute(db.memberships.insert().values(project_id=project_id, user_id=user_id, role=role_))


def remove_project(project_id):
    with db.engine().begin() as c:
        c.execute(db.memberships.delete().where(db.memberships.c.project_id == project_id))


def project_ids(user):
    with db.engine().connect() as c:
        return {r[0] for r in c.execute(select(db.memberships.c.project_id).where(db.memberships.c.user_id == user["id"]))}


def _adopt_orphans(user_id):
    """Local migration: projects created before accounts existed go to the first user."""
    from .storage import ProjectStore
    store = ProjectStore()
    with db.engine().connect() as c:
        owned = {r[0] for r in c.execute(select(db.memberships.c.project_id))}
    for p in store.list():
        if p["id"] not in owned:
            add_member(p["id"], user_id, "owner")


def _secure(request):
    if os.environ.get("AI_ARTICLE_COOKIE_SECURE", "auto") == "1":
        return True
    return request is not None and (request.url.scheme == "https"
                                    or request.headers.get("x-forwarded-proto") == "https")


def _set_cookie(response: Response, token, request=None):
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_DAYS * 86400,
                        secure=_secure(request), path="/")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class RegisterIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=MIN_PASSWORD, max_length=200)
    setup_code: str = ""


def setup_code():
    """One-time code required for the first (administrator) account on a public deployment.

    Set AI_ARTICLE_SETUP_CODE, or AI_ARTICLE_SETUP_CODE_FILE pointing to a file with the code.
    Without either (local development) the first account needs no code."""
    code = os.environ.get("AI_ARTICLE_SETUP_CODE", "").strip()
    path = os.environ.get("AI_ARTICLE_SETUP_CODE_FILE")
    if not code and path and os.path.exists(path):
        code = open(path, encoding="utf-8").read().strip()
    return code


@router.get("/setup")
def setup_status():
    """Public: whether the first (administrator) account still has to be created with the setup code."""
    with db.engine().connect() as c:
        n = c.execute(select(func.count()).select_from(db.users)).scalar()
    return {"setup_required": n == 0 and bool(setup_code())}


@router.post("/register")
def register(body: RegisterIn, response: Response, request: Request):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "некорректный e-mail")
    mode = os.environ.get("AI_ARTICLE_REGISTRATION", "open")  # open | closed
    with db.engine().begin() as c:
        n = c.execute(select(func.count()).select_from(db.users)).scalar()
        if n and mode == "closed":
            raise HTTPException(403, "регистрация закрыта — обратитесь к администратору")
        expected = setup_code()
        if not n and expected and not hmac.compare_digest(body.setup_code.strip(), expected):
            raise HTTPException(403, detail={"message": "для первой регистрации нужен код установки",
                                             "setup_required": True})
        if c.execute(select(db.users.c.id).where(db.users.c.email == email)).first():
            raise HTTPException(409, "пользователь с таким e-mail уже есть")
        uid = c.execute(db.users.insert().values(email=email, password_hash=hash_password(body.password),
                                                 is_admin=n == 0, created_at=now())).inserted_primary_key[0]
    if n == 0:
        _adopt_orphans(uid)
    _set_cookie(response, create_session(uid), request)
    return _user_public(get_user(uid))


class LoginIn(BaseModel):
    email: str
    password: str
    totp: str = ""


@router.post("/login")
def login(body: LoginIn, response: Response, request: Request):
    email = body.email.strip().lower()
    with db.engine().connect() as c:
        u = c.execute(select(db.users).where(db.users.c.email == email)).mappings().first()
    if u and u["locked_until"] and _aware(u["locked_until"]) > now():
        raise HTTPException(429, "слишком много попыток — вход временно заблокирован")
    ok = bool(u) and u["is_active"] and verify_password(body.password, u["password_hash"])
    counter = None
    if ok and u["totp_enabled"]:
        if not body.totp:
            raise HTTPException(401, detail={"message": "введите код из приложения-аутентификатора",
                                             "mfa_required": True})
        counter = totp_verify(crypto.unseal(u["totp_secret"], "totp"), body.totp, u["totp_last_counter"])
        ok = counter is not None
    if not ok:
        if u:  # commit the failed attempt before answering (an exception would roll it back)
            fails = u["failed_logins"] + 1
            locked = fails >= MAX_FAILS
            with db.engine().begin() as c:
                c.execute(db.users.update().where(db.users.c.id == u["id"]).values(
                    failed_logins=0 if locked else fails,
                    locked_until=now() + timedelta(minutes=LOCK_MINUTES) if locked else u["locked_until"]))
        raise HTTPException(401, "неверный e-mail, пароль или код")
    with db.engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == u["id"]).values(
            failed_logins=0, locked_until=None, totp_last_counter=counter or u["totp_last_counter"]))
    _set_cookie(response, create_session(u["id"]), request)
    return _user_public(u)


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db.engine().begin() as c:
            c.execute(db.sessions.delete().where(db.sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


def _require(request):
    u = user_from_request(request)
    if not u:
        raise HTTPException(401, "требуется вход")
    return u


@router.get("/me")
def me(request: Request):
    return _user_public(_require(request))


@router.post("/2fa/setup")
def totp_setup(request: Request):
    u = _require(request)
    if u["totp_enabled"]:
        raise HTTPException(400, "2FA уже включена")
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    with db.engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == u["id"]).values(totp_secret=crypto.seal(secret, "totp")))
    uri = f"otpauth://totp/AI%20Article:{u['email']}?secret={secret}&issuer=AI%20Article&digits=6&period=30"
    import qrcode
    import qrcode.image.svg
    buf = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(buf)
    return {"secret": secret, "uri": uri, "svg": buf.getvalue().decode()}


class CodeIn(BaseModel):
    code: str


@router.post("/2fa/enable")
def totp_enable(body: CodeIn, request: Request):
    u = _require(request)
    if not u["totp_secret"]:
        raise HTTPException(400, "сначала получите секрет")
    counter = totp_verify(crypto.unseal(u["totp_secret"], "totp"), body.code)
    if counter is None:
        raise HTTPException(400, "код не подошёл — проверьте время на устройстве")
    with db.engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == u["id"]).values(totp_enabled=True,
                                                                            totp_last_counter=counter))
    return _user_public(get_user(u["id"]))


class DisableIn(BaseModel):
    password: str
    code: str


@router.post("/2fa/disable")
def totp_disable(body: DisableIn, request: Request):
    u = _require(request)
    if not verify_password(body.password, u["password_hash"]) or totp_verify(
            crypto.unseal(u["totp_secret"], "totp"), body.code, u["totp_last_counter"]) is None:
        raise HTTPException(400, "неверный пароль или код")
    with db.engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == u["id"]).values(totp_enabled=False, totp_secret=None))
    return _user_public(get_user(u["id"]))


class PasswordIn(BaseModel):
    old: str
    new: str = Field(min_length=MIN_PASSWORD, max_length=200)


@router.post("/password")
def change_password(body: PasswordIn, request: Request, response: Response):
    u = _require(request)
    if not verify_password(body.old, u["password_hash"]):
        raise HTTPException(400, "неверный текущий пароль")
    with db.engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == u["id"]).values(password_hash=hash_password(body.new)))
        c.execute(db.sessions.delete().where(db.sessions.c.user_id == u["id"]))  # log out everywhere
    _set_cookie(response, create_session(u["id"]), request)
    return {"ok": True}
