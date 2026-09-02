from datetime import datetime, timedelta
import bcrypt
from jose import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db, User
from config import settings

# BAIXA-5: removemos passlib (legado) e usamos bcrypt diretamente.
# Compatibilidade garantida: hashes antigos do passlib são bcrypt puro
# (`$2b$12$...`) e verificam com bcrypt.checkpw (testado e documentado).
_BCRYPT_MAX_BYTES = 72


def _bcrypt_password_bytes(password: str) -> bytes:
    """Normaliza a senha para os 72 bytes máximos do algoritmo bcrypt.

    Mantém o comportamento exato do passlib (truncate_error=False): somente os
    primeiros 72 bytes participam do hash/verificação — senhas de usuários
    antigos (hashadas via passlib) continuam validando sem alteração.
    """
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_password_bytes(plain), hashed.encode("utf-8"))
    except ValueError:
        # hash malformado ou senha fora dos limites -> falha segura (nunca 500)
        return False


def create_access_token(user_id: int, token_version: int = 0) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire, "ver": token_version}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Autentica via cookie httpOnly (MÉDIA-4) ou, como fallback, header Bearer.

    O cookie `caderno_access_token` e definido no login/registro (httpOnly,
    SameSite=Lax, Secure em HTTPS) e nao e legivel por JavaScript. O header
    `Authorization: Bearer` continua aceito (compatibilidade com front antigo,
    testes e Swagger).
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais invalidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = request.cookies.get(settings.cookie_name, "") or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        raise credentials_exc
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = int(payload.get("sub"))
        token_version = int(payload.get("ver", 0))
    except Exception:
        raise credentials_exc
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exc
    # Revogacao: se o token foi emitido com uma versao antiga (por ex. apos
    # logout ou troca de senha), ele e considerado invalido.
    current_ver = getattr(user, "token_version", 0) or 0
    if token_version != current_ver:
        raise credentials_exc
    if user.is_premium and user.premium_until and user.premium_until < datetime.utcnow():
        user.is_premium = False
        user.premium_until = None
        try:
            db.commit()
        except Exception:
            db.rollback()
    return user
