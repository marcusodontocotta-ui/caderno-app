import hashlib
import hmac
import logging
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import settings
from coupons import aplicar_desconto
from database import CadernoCupom, Subscription, User

logger = logging.getLogger("caderno.billing")

MP_API = "https://api.mercadopago.com"
PREMIUM_MONTHS = 1


def obter_cupom(db: Session, codigo: str):
    """Busca um cupom ativo por codigo (case-insensitive). None se inexistente/inativo."""
    if not codigo:
        return None
    return (
        db.query(CadernoCupom)
        .filter(CadernoCupom.codigo == codigo.strip().upper(), CadernoCupom.ativo.is_(True))
        .first()
    )


def calcular_valor_final(valor_base: float, cupom: CadernoCupom) -> dict:
    """Calcula o valor com desconto de um cupom ja resolvido (ativo)."""
    return aplicar_desconto(valor_base, cupom.percentual)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.mp_access_token}",
        "Content-Type": "application/json",
    }


def create_preapproval(user: User, db: Session, cupom_codigo: str = None) -> dict:
    """Cria uma assinatura (preapproval) recorrente mensal no Mercado Pago.

    Aceita um codigo de cupom opcional; o desconto entra no transaction_amount.
    Nunca permite valor final <= R$0.01 (Mercado Pago rejeita assinaturas tao baixas).
    """
    if not settings.mp_access_token:
        raise HTTPException(status_code=503, detail="Mercado Pago nao configurado")

    valor_mensal = float(settings.mp_premium_amount)
    cupom_ok = False
    if cupom_codigo:
        cupom = obter_cupom(db, cupom_codigo)
        if cupom is None:
            raise HTTPException(status_code=404, detail="Cupom nao encontrado ou inativo")
        valores = aplicar_desconto(valor_mensal, cupom.percentual)
        valor_mensal = valores["valor_final"]
        cupom_ok = True

    payload = {
        "reason": "Caderno de Estudos Premium",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": valor_mensal,
            "currency_id": "BRL",
        },
        "payer_email": user.email,
        "back_url": settings.mp_back_url,
        "notification_url": settings.mp_webhook_url,
        "external_reference": f"caderno:{user.id}",
    }
    try:
        r = httpx.post(f"{MP_API}/preapproval", json=payload, headers=_headers(), timeout=20)
        data = r.json()
    except Exception as e:  # noqa
        # Loga o detalhe interno; nao ecoa ao cliente (evita vazar conteudo/estrutura).
        logger.exception("Erro ao contactar Mercado Pago (create_preapproval): %s", e)
        raise HTTPException(status_code=502, detail="Nao foi possivel gerar o checkout. Tente novamente.")
    if r.status_code not in (200, 201):
        # Loga a resposta completa do MP internamente; retorna mensagem generica.
        logger.error(
            "Mercado Pago erro %s ao criar preapproval (user=%s): %s",
            r.status_code, user.id, data,
        )
        raise HTTPException(status_code=502, detail="Nao foi possivel gerar o checkout. Tente novamente.")
    data["_cupom_aplicado"] = cupom_ok
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
