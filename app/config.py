from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    processing_delay: float = 2.0
    idempotency_key_ttl: int = 86400
    cleanup_interval: int = 3600


settings = Settings()
