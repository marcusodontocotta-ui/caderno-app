from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db, User
from config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


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
