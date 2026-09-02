import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./caderno.db"
    secret_key: str = "troque-esta-chave"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 dias (revogavel via token_version)
    cookie_name: str = "caderno_access_token"  # cookie httpOnly que guarda o JWT (MÉDIA-4)
    mp_access_token: str = ""
    mp_public_key: str = ""
    mp_webhook_secret: str = ""
    mp_premium_amount: str = "4.90"
    mp_back_url: str = "https://caderno-fy36.onrender.com"
    mp_webhook_url: str = "https://caderno-app.onrender.com/billing/webhook"
    # Origens permitidas para CORS (separadas por ","). Padrao de seguranca: so o front.
    cors_origins: str = "https://caderno-fy36.onrender.com,http://localhost:8000"

    class Config:
        env_file = ".env"


def _is_render_production() -> bool:
    """Heuristica para impedir secret fraco em producao (Render).

    Em qualquer ambiente com RENDER (Render) presente, exigimos um SECRET_KEY real.
    Tambem tratamos como producao quando RENDER_SERVICE_ID / PORT existem.
    """
    render_markers = [os.environ.get("RENDER"), os.environ.get("RENDER_SERVICE_ID"),
                      os.environ.get("RENDER_INSTANCE_ID")]
    return any(m for m in render_markers if m)


_settings = Settings()

if _is_render_production():
    # Fail-closed: em producao NAO usamos o default fraco. Se SECRET_KEY nao foi
    # definida explicitamente no ambiente, o app nao sobe.
    required = {k: os.environ.get(k) for k in ("SECRET_KEY",)}
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(
            f"VARIAVEIS OBRIGATORIAS AUSENTES EM PRODUCAO: {missing}. "
            "Defina SECRET_KEY forte (ex.: `python -c \"import secrets;print(secrets.token_urlsafe(48))\"`)."
        )

if _settings.secret_key == "troque-esta-chave" and _settings.access_token_expire_minutes > 60 * 24 * 30:
    # guarda redundante: nunca usar JWT de 30+ dias com o secret padrao
    raise RuntimeError("access_token_expire_minutes acima do limite com secret padrao.")

settings = _settings
