import re
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime

from database import init_db, get_db, User, Notebook, Page, Subscription, engine
from auth import hash_password, verify_password, create_access_token, get_current_user
from config import settings
from billing import create_preapproval, get_preapproval, get_payment, activate_user_premium, deactivate_user_premium, verify_signature
from billing import obter_cupom, calcular_valor_final
from coupons import aplicar_desconto


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown via lifespan (substitui o deprecado @app.on_event('startup')).

    Mantém o comportamento anterior: `init_db()` (criação de tabelas + migrações
    idempotentes + seed de cupons não-destrutivo) é executado na subida.
    """
    init_db()
    yield


app = FastAPI(title="Caderno de Estudos API", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS restrito (MÉDIA-2): origens explicitas do front (e localhost p/ dev).
# allow_credentials=True so com allow_origins fixa (nunca "*"), porque a sessao
# do usuario agora viaja em cookie httpOnly (MÉDIA-4). A lista de origens e a
# mesma para ambos os casos; o middleware do Starlette so ecoa a origem quando
# ela esta na lista, e a autenticacao continua via header/cookie (sem CSRF via
# forms, pois SameSite=Lax + Content-Type application/json).
# ---------------------------------------------------------------------------
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Rate-limit / lockout no login (MÉDIA-1) — em memoria, thread-safe.
# Sem dependencia de Redis. Documentado em config.py e README.
#   - MAX_FAILURES (default 5) tentativas erradas dentro de WINDOW segundos
#     resultam em LOCKOUT segundos de bloqueio para aquele e-mail (e por IP).
# Uma vez disparado o bloqueio (429), ele dura LOGIN_LOCKOUT_SECONDS e depois o
# contador e zerado automaticamente (backoff/backoff total, nao janela corrediça).
# State: { key: {"fails": [timestamps...], "until": timestamp|None} }.
# ---------------------------------------------------------------------------
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300   # 5 min
LOGIN_LOCKOUT_SECONDS = 900  # 15 min

_login_lock = threading.Lock()
_login_state = {}


def _record_failure(key: str) -> int:
    """Registra uma falha e, ao atingir o limite, dispara o lockout."""
    now = time.time()
    with _login_lock:
        entry = _login_state.setdefault(key, {"fails": [], "until": None})
        if entry["until"] and entry["until"] <= now:
            # lockout anterior expirado: reinicia o contador
            entry["fails"] = []
            entry["until"] = None
        cutoff = now - LOGIN_WINDOW_SECONDS
        entry["fails"] = [t for t in entry["fails"] if t >= cutoff]
        entry["fails"].append(now)
        n = len(entry["fails"])
        if n >= LOGIN_MAX_FAILURES:
            entry["until"] = now + LOGIN_LOCKOUT_SECONDS
        return n


def _clear_failures(key: str) -> None:
    with _login_lock:
        _login_state.pop(key, None)


def _is_locked(key: str) -> bool:
    now = time.time()
    with _login_lock:
        entry = _login_state.get(key)
        if entry and entry["until"]:
            if entry["until"] > now:
                return True
            # lockout expirado: limpa para nao travar o login apos o periodo
            entry["fails"] = []
            entry["until"] = None
    return False


def _check_lock(key: str) -> None:
    if _is_locked(key):
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas de login. Aguarde alguns minutos e tente novamente.",
        )


# ---------------------------------------------------------------------------
# Politica de senha forte (MÉDIA-1): minimo 8 caracteres + letra + numero.
# ---------------------------------------------------------------------------
PASSWORD_MIN_LENGTH = 8


def _validate_password(p: str) -> None:
    if not p or len(p) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Senha deve ter no minimo {PASSWORD_MIN_LENGTH} caracteres "
            "(combinando letras e numeros)."
        )
    if not re.search(r"[A-Za-z]", p) or not re.search(r"[0-9]", p):
        raise ValueError("Senha fraca: use letras e numeros (minimo 8 caracteres).")


# ---------------- Schemas ----------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        _validate_password(v)
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def _check_password_present(cls, v: str) -> str:
        if not v:
            raise ValueError("Senha obrigatoria")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    is_premium: bool


class PageOut(BaseModel):
    id: int
    position: int
    text: str
    updated_at: datetime


class PageIn(BaseModel):
    position: Optional[int] = None
    text: Optional[str] = ""


class NotebookIn(BaseModel):
    name: str = "Novo caderno"


class NotebookOut(BaseModel):
    id: int
    name: str
    pages: List[PageOut] = []


# ---------------- Auth helpers (cookie httpOnly, MÉDIA-4) ----------------
def _cookie_flags(request: Request) -> dict:
    """Flags padrao do cookie de sessao.

    httpOnly + SameSite=Lax + Secure em HTTPS. Viaja junto com `Authorization`
    (fallback) no mesmo dominio/API, ambos aceitos por get_current_user.
    """
    return {
        "key": settings.cookie_name,
        "httponly": True,
        "samesite": "lax",
        "secure": request.url.scheme == "https",
        "path": "/",
    }


def _auth_response(request: Request, user: User) -> JSONResponse:
    """Monta a resposta 200/201 de auth com JWT no corpo (compat) e em cookie."""
    token = create_access_token(user.id, getattr(user, "token_version", 0) or 0)
    body = TokenResponse(
        access_token=token,
        email=user.email,
        is_premium=user.is_premium,
    )
    resp = JSONResponse(content=body.model_dump())
    resp.set_cookie(
        **_cookie_flags(request),
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
    )
    return resp


# ---------------- Auth ----------------
@app.post("/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email ja cadastrado")
    user = User(email=req.email, hashed_password=hash_password(req.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return _auth_response(request, user)


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Rate-limit/lockout por e-mail e por IP (MÉDIA-1).
    client_ip = request.client.host if request.client else "unknown"
    email_key = f"email:{req.email.lower()}"
    ip_key = f"ip:{client_ip}"
    _check_lock(email_key)
    _check_lock(ip_key)

    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        _record_failure(email_key)
        _record_failure(ip_key)
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")

    _clear_failures(email_key)
    _clear_failures(ip_key)
    return _auth_response(request, user)


@app.post("/auth/logout", response_model=dict)
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Invalida todas as sessoes do usuario e apaga o cookie httpOnly (MÉDIA-3/4).

    Incrementa token_version: qualquer JWT emitido antes (cookie ou header)
    deixa de ser aceito. O front limpa o cookie deste navegador via max_age=0.
    """
    user.token_version = (getattr(user, "token_version", 0) or 0) + 1
    db.commit()
    resp = JSONResponse(content={"ok": True})
    resp.set_cookie(**_cookie_flags(request), value="", max_age=0, expires=0)
    return resp


@app.get("/auth/me", response_model=TokenResponse)
def me(user: User = Depends(get_current_user)):
    return TokenResponse(
        access_token="",
        email=user.email,
        is_premium=user.is_premium,
    )


# ---------------- Notebooks ----------------
def _serialize_notebook(db, nb: Notebook) -> NotebookOut:
    pages = db.query(Page).filter(Page.notebook_id == nb.id).order_by(Page.position).all()
    return NotebookOut(
        id=nb.id,
        name=nb.name,
        pages=[PageOut(id=p.id, position=p.position, text=p.text, updated_at=p.updated_at) for p in pages],
    )


@app.get("/notebooks", response_model=List[NotebookOut])
def list_notebooks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    nbs = db.query(Notebook).filter(Notebook.user_id == user.id).order_by(Notebook.updated_at.desc()).all()
    return [_serialize_notebook(db, nb) for nb in nbs]


@app.post("/notebooks", response_model=NotebookOut)
def create_notebook(req: NotebookIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.is_premium:
        count = db.query(Notebook).filter(Notebook.user_id == user.id).count()
        if count >= 1:
            raise HTTPException(status_code=402, detail="Plano gratuito limite de 1 caderno. Faca upgrade para premium.")
    nb = Notebook(user_id=user.id, name=req.name)
    db.add(nb)
    db.commit()
    db.refresh(nb)
    # cria uma página inicial
    page = Page(notebook_id=nb.id, position=0, text="<p></p>")
    db.add(page)
    db.commit()
    return _serialize_notebook(db, nb)


@app.put("/notebooks/{notebook_id}", response_model=NotebookOut)
def rename_notebook(notebook_id: int, req: NotebookIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    nb = db.query(Notebook).filter(Notebook.id == notebook_id, Notebook.user_id == user.id).first()
    if not nb:
        raise HTTPException(status_code=404, detail="Caderno nao encontrado")
    nb.name = req.name
    db.commit()
    return _serialize_notebook(db, nb)


@app.delete("/notebooks/{notebook_id}")
def delete_notebook(notebook_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    nb = db.query(Notebook).filter(Notebook.id == notebook_id, Notebook.user_id == user.id).first()
    if not nb:
        raise HTTPException(status_code=404, detail="Caderno nao encontrado")
    db.query(Page).filter(Page.notebook_id == nb.id).delete()
    db.delete(nb)
    db.commit()
    return {"ok": True}


# ---------------- Pages ----------------
def _get_owned_page(db, user, notebook_id, page_id):
    """Busca uma pagina garantindo que o caderno pai pertence ao usuario autenticado.

    Retorna None caso a pagina nao exista OU nao pertenca a um caderno do usuario,
    para que o chamador responda 404 sem vazar a existencia do recurso.
    """
    return (
        db.query(Page)
        .join(Notebook, Notebook.id == Page.notebook_id)
        .filter(
            Page.id == page_id,
            Page.notebook_id == notebook_id,
            Notebook.user_id == user.id,
        )
        .first()
    )


@app.put("/notebooks/{notebook_id}/pages/{page_id}", response_model=PageOut)
def update_page(notebook_id: int, page_id: int, req: PageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page = _get_owned_page(db, user, notebook_id, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Pagina nao encontrada")
    if req.text is not None:
        page.text = req.text
    if req.position is not None:
        page.position = req.position
    page.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(page)
    return PageOut(id=page.id, position=page.position, text=page.text, updated_at=page.updated_at)


@app.post("/notebooks/{notebook_id}/pages", response_model=PageOut)
def add_page(notebook_id: int, req: PageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    nb = db.query(Notebook).filter(Notebook.id == notebook_id, Notebook.user_id == user.id).first()
    if not nb:
        raise HTTPException(status_code=404, detail="Caderno nao encontrado")
    max_pos = db.query(Page).filter(Page.notebook_id == notebook_id).count()
    page = Page(notebook_id=notebook_id, position=max_pos, text=req.text or "<p></p>")
    db.add(page)
    db.commit()
    db.refresh(page)
    return PageOut(id=page.id, position=page.position, text=page.text, updated_at=page.updated_at)


@app.delete("/notebooks/{notebook_id}/pages/{page_id}")
def delete_page(notebook_id: int, page_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page = _get_owned_page(db, user, notebook_id, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Pagina nao encontrada")
    db.delete(page)
    db.commit()
    return {"ok": True}


@app.get("/health")
def health():
    """Liveness: sempre 200 se o processo está de pé (não derruba deploy).

    Inclui o estado do banco no corpo para observabilidade, mas jamais responde
    5xx aqui — um crash-loop por falha pontual do Postgres derrubaria o serviço.
    Para checagem estrita do banco, use `/health/db`.
    """
    return {"status": "ok", "db": _check_db()}


@app.get("/health/db")
def health_db():
    """Readiness do banco de dados.

    Responde 200 quando o Postgres responde `SELECT 1` e 503 quando não. Este
    endpoint NÃO é usado pelo healthcheck do Render (que aponta para `/health`),
    então uma falha pontual do DB não causa restart/crash-loop; serve para
    observabilidade e para carga/teste.
    """
    if _check_db() == "ok":
        return {"db": "ok"}
    raise HTTPException(status_code=503, detail="banco de dados indisponivel")


def _check_db() -> str:
    """Executa `SELECT 1` no engine atual. Retorna 'ok' ou 'error'."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


# ---------------- Billing / Mercado Pago ----------------
class CheckoutResponse(BaseModel):
    init_point: str
    preapproval_id: Optional[str] = None


class BillingStatus(BaseModel):
    is_premium: bool
    premium_until: Optional[str] = None


class CupomValidarResponse(BaseModel):
    codigo: str
    percentual: int
    desconto: float
    valor_final: float
    valor_original: float


class PremiumRequest(BaseModel):
    cupom: Optional[str] = None


@app.post("/billing/premium", response_model=CheckoutResponse)
def subscribe_premium(req: Optional[PremiumRequest] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cupom_codigo = (req.cupom if req and req.cupom else None) or None
    data = create_preapproval(user, db, cupom_codigo)
    init_point = data.get("init_point")
    if not init_point:
        raise HTTPException(status_code=502, detail="Nao foi possivel gerar o checkout")
    return CheckoutResponse(
        init_point=init_point,
        preapproval_id=(str(data["id"]) if data.get("id") else None),
    )


@app.get("/billing/cupom/validar", response_model=CupomValidarResponse)
def validar_cupom(codigo: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cupom = obter_cupom(db, codigo)
    if cupom is None:
        raise HTTPException(status_code=404, detail="Cupom nao encontrado ou inativo")
    base = float(settings.mp_premium_amount)
    valores = aplicar_desconto(base, cupom.percentual)
    return CupomValidarResponse(
        codigo=cupom.codigo,
        percentual=cupom.percentual,
        desconto=valores["desconto"],
        valor_final=valores["valor_final"],
        valor_original=_money_round(base),
    )


def _money_round(v: float) -> float:
    return round(float(v) + 1e-9, 2)


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    x_signature = request.headers.get("x-signature", "")
    form = await request.form()
    type_ = form.get("type", "")
    data_id = form.get("data.id", "")

    secret = (settings.mp_webhook_secret or "").strip()
    if not secret:
        # Fail-closed: sem secret configurado, recusamos a requisicao para nao
        # aceitar webhooks sem assinatura (proteger a promocao de premium).
        raise HTTPException(status_code=503, detail="Webhook nao configurado")
    if not verify_signature(x_signature, str(data_id), secret):
        raise HTTPException(status_code=401, detail="Assinatura invalida")

    db = next(get_db())
    try:
        if type_ == "preapproval" and data_id:
            pa = get_preapproval(str(data_id))
            status = pa.get("status", "")
            ext = pa.get("external_reference", "") or ""
            if ext.startswith("caderno:"):
                try:
                    user_id = int(ext.split(":")[1])
                except (IndexError, ValueError):
                    return {"ok": True}
                if status in ("authorized", "active"):
                    activate_user_premium(db, user_id, str(data_id))
                elif status in ("cancelled", "paused"):
                    deactivate_user_premium(db, user_id, status)
        elif type_ == "payment" and data_id:
            p = get_payment(str(data_id))
            preapproval_id = p.get("preapproval_id")
            pays_status = p.get("status")
            if preapproval_id:
                subs = db.query(Subscription).filter(Subscription.mp_preapproval_id == str(preapproval_id)).all()
                if pays_status == "approved":
                    for sub in subs:
                        if sub.user_id:
                            activate_user_premium(db, sub.user_id, str(preapproval_id))
                elif pays_status in ("refunded", "chargeback", "cancelled"):
                    for sub in subs:
                        if sub.user_id:
                            deactivate_user_premium(db, sub.user_id, pays_status)
        return {"ok": True}
    finally:
        db.close()


@app.get("/billing/status", response_model=BillingStatus)
def billing_status(user: User = Depends(get_current_user)):
    return BillingStatus(
        is_premium=user.is_premium,
        premium_until=user.premium_until.isoformat() if user.premium_until else None,
    )
