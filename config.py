from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./caderno.db"
    secret_key: str = "troque-esta-chave"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 dias
    mp_access_token: str = ""
    mp_public_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
