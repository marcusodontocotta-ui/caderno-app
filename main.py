from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime

from database import init_db, get_db, User, Notebook, Page, Subscription
from auth import hash_password, verify_password, create_access_token, get_current_user
from config import settings
from billing import create_preapproval, get_preapproval, get_payment, activate_user_premium, deactivate_user_premium, verify_signature
from billing import obter_cupom, calcular_valor_final
from coupons import aplicar_desconto

app = FastAPI(title="Caderno de Estudos API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


# ---------------- Schemas ----------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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


# ---------------- Auth ----------------
@app.post("/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email ja cadastrado")
    user = User(email=req.email, hashed_password=hash_password(req.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(
        access_token=create_access_token(user.id),
        email=user.email,
        is_premium=user.is_premium,
    )


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")
    return TokenResponse(
        access_token=create_access_token(user.id),
        email=user.email,
        is_premium=user.is_premium,
    )


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
@app.put("/notebooks/{notebook_id}/pages/{page_id}", response_model=PageOut)
def update_page(notebook_id: int, page_id: int, req: PageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page = db.query(Page).filter(Page.id == page_id, Page.notebook_id == notebook_id).first()
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
    page = db.query(Page).filter(Page.id == page_id, Page.notebook_id == notebook_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Pagina nao encontrada")
    db.delete(page)
    db.commit()
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}


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
    if secret and not verify_signature(x_signature, str(data_id), secret):
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
