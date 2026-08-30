"""Logica de cupons de desconto do Caderno.

Regra critica: o Mercado Pago rejeita assinaturas recorrentes com valor <= R$0.01.
Por isso NUNCA permitimos um cupom que resulte em valor_final <= 0.01. Cupons de
100% (ou que zerariam o valor) sao rejeitados com erro claro. O Caderno usa apenas
CADERNO50 (50%) e CADERNO80 (80%).
"""
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException


def _money(value) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def aplicar_desconto(valor: float, percentual: int) -> dict:
    """Aplica percentual ao valor base e valida o resultado.

    Retorna {'desconto', 'valor_final', 'percentual'}. Levanta HTTPException(400)
    se o desconto zerasse/ultrapassasse o valor (resultando em <= R$0.01).
    """
    valor = _money(valor)
    if percentual is None or percentual <= 0:
        raise HTTPException(status_code=400, detail="Cupom sem percentual valido")
    if percentual >= 100:
        raise HTTPException(status_code=400, detail="Cupom invalido: desconto nao pode zerar o valor mensal")
    desconto = _money(valor * percentual / 100)
    valor_final = _money(valor - desconto)
    if valor_final <= 0.01:
        raise HTTPException(
            status_code=400,
            detail="Cupom invalido: desconto resultaria em valor mensal muito baixo (minimo R$0.02)",
        )
    return {"percentual": percentual, "desconto": desconto, "valor_final": valor_final}
