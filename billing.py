import hashlib
import hmac
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import settings
from database import Subscription, User

MP_API = "https://api.mercadopago.com"
PREMIUM_MONTHS = 1


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.mp_access_token}",
        "Content-Type": "application/json",
    }


def create_preapproval(user: User) -> dict:
    """Cria uma assinatura (preapproval) recorrente mensal no Mercado Pago."""
    if not settings.mp_access_token:
        raise HTTPException(status_code=503, detail="Mercado Pago nao configurado")
    payload = {
        "reason": "Caderno de Estudos Premium",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": float(settings.mp_premium_amount),
            "currency_id": "BRL",
        },
        "payer_email": user.email,
        "back_url": settings.mp_back_url,
        "notification_url": f"{settings.mp_back_url}/billing/webhook",
        "external_reference": f"caderno:{user.id}",
    }
    try:
        r = httpx.post(f"{MP_API}/preapproval", json=payload, headers=_headers(), timeout=20)
        data = r.json()
    except Exception as e:  # noqa
        raise HTTPException(status_code=502, detail=f"Erro ao contactar Mercado Pago: {e}")
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Mercado Pago erro {r.status_code}: {data}")
    return data


def get_preapproval(preapproval_id: str) -> dict:
    r = httpx.get(f"{MP_API}/preapproval/{preapproval_id}", headers=_headers(), timeout=20)
    return r.json() if r.status_code == 200 else {}


def get_payment(payment_id: str) -> dict:
    r = httpx.get(f"{MP_API}/v1/payments/{payment_id}", headers=_headers(), timeout=20)
    return r.json() if r.status_code == 200 else {}


def activate_user_premium(db: Session, user_id: int, preapproval_id: str) -> None:
    """Marca o usuário como premium com validade mensal, renovando se já premium."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
    now = datetime.utcnow()
    base = user.premium_until if (user.premium_until and user.premium_until > now) else now
    user.premium_until = base + timedelta(days=30)
    user.is_premium = True
    user.mp_preapproval_id = preapproval_id
    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.mp_preapproval_id == preapproval_id)
        .first()
    )
    if not sub:
        sub = Subscription(
            user_id=user_id,
            mp_preapproval_id=preapproval_id,
            status="active",
            amount=settings.mp_premium_amount,
            currency_id="BRL",
            external_reference=f"caderno:{user_id}",
        )
        db.add(sub)
    else:
        sub.status = "active"
        sub.updated_at = datetime.utcnow()
    db.commit()


def deactivate_user_premium(db: Session, user_id: int, reason: str = "cancelled") -> None:
    """Desativa o premium do usuário e marca a assinatura como cancelada/inativa."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
    user.is_premium = False
    user.premium_until = None
    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.mp_preapproval_id == user.mp_preapproval_id)
        .first()
    )
    if not sub:
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub:
        sub.status = reason
        sub.updated_at = datetime.utcnow()
    db.commit()


def verify_signature(x_signature: str, data_id: str, secret: str) -> bool:
    """Valida x-signature (ts,v1) do Mercado Pago conforme documentacao."""
    try:
        parts = {}
        for item in x_signature.split(","):
            k, _, v = item.partition("=")
            parts[k.strip()] = v.strip()
        ts = parts.get("ts", "")
        v1 = parts.get("v1", "")
        manifest = f"id:{data_id};request-id:{ts};ts:{ts};"
        expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)
    except Exception:  # noqa
        return False
