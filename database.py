from sqlalchemy import create_engine, Column, String, Text, Integer, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

from config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_premium = Column(Boolean, default=False)
    premium_until = Column(DateTime, nullable=True)
    mp_preapproval_id = Column(String, nullable=True)
    token_version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notebook(Base):
    __tablename__ = "notebooks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Page(Base):
    __tablename__ = "pages"
    id = Column(Integer, primary_key=True)
    notebook_id = Column(Integer, index=True, nullable=False)
    position = Column(Integer, default=0)
    text = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    mp_preapproval_id = Column(String, index=True, nullable=True)
    status = Column(String, default="pending")
    amount = Column(String, default="4.90")
    currency_id = Column(String, default="BRL")
    external_reference = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CadernoCupom(Base):
    """Cupons de desconto proprios do Caderno (nao confundir com a tabela
    generica `cupons` do SISGERSA). Percentual e um inteiro 0..100; nunca
    usar 100% pois o Mercado Pago rejeita assinatura recorrente <= R$0.01."""
    __tablename__ = "caderno_cupons"
    id = Column(Integer, primary_key=True)
    codigo = Column(String, unique=True, index=True, nullable=False)
    percentual = Column(Integer, default=0, nullable=False)
    ativo = Column(Boolean, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)


SEED_CUPONS = (("CADERNO50", 50), ("CADERNO80", 80))


def _seed_cupons_sqlite():
    """Insere os cupons padrao no SQLite (idempotente), caso ainda nao existam."""
    from sqlalchemy import text
    with SessionLocal() as s:
        for codigo, percentual in SEED_CUPONS:
            existe = s.execute(
                text("SELECT 1 FROM caderno_cupons WHERE codigo = :c"), {"c": codigo}
            ).first()
            if not existe:
                s.execute(
                    text(
                        "INSERT INTO caderno_cupons (codigo, percentual, ativo, criado_em) "
                        "VALUES (:c, :p, TRUE, :d)"
                    ),
                    {"c": codigo, "p": percentual, "d": datetime.utcnow()},
                )
        s.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate()
    if engine.dialect.name != "postgresql":
        _seed_cupons_sqlite()


def _migrate():
    """Migracoes idempotentes para bancos existentes (ex.: sisgersa compartilhado)."""
    from sqlalchemy import text

    is_postgres = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        if is_postgres:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_until TIMESTAMP WITHOUT TIME ZONE"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mp_preapproval_id VARCHAR"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER DEFAULT 0"
            ))
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    mp_preapproval_id VARCHAR,
                    status VARCHAR,
                    amount VARCHAR,
                    currency_id VARCHAR,
                    external_reference VARCHAR,
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            ))
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS caderno_cupons (
                    id SERIAL PRIMARY KEY,
                    codigo VARCHAR UNIQUE NOT NULL,
                    percentual INTEGER NOT NULL DEFAULT 0,
                    ativo BOOLEAN DEFAULT TRUE,
                    criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                )
                """
            ))
            # Seed idempotente e nao-destrutivo: utilizamos INSERT ... ON CONFLICT
            # DO NOTHING para que uma desativacao manual (ativo=TRUE -> FALSE) feita
            # por um operador persista entre boots. (Antes era DO UPDATE SET ativo=TRUE,
            # o que reativava cupons desativados a cada deploy.) Caderno 50/80 sao
            # criados ativos na primeira vez; se alguem os desativar manualmente, isso
            # nao e revertido aqui.
            for codigo, percentual in SEED_CUPONS:
                conn.execute(text(
                    """
                    INSERT INTO caderno_cupons (codigo, percentual, ativo)
                    VALUES (:codigo, :percentual, TRUE)
                    ON CONFLICT (codigo) DO NOTHING
                    """
                ), {"codigo": codigo, "percentual": percentual})
        else:
            # SQLite: create_all ja reflete os novos models; nada a fazer aqui,
            # pois o SQLite nao suporta ALTER TABLE ... IF NOT EXISTS via SQL.
            pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
