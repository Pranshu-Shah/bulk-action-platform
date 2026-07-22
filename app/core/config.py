from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str
    BATCH_SIZE: int

    LOG_LEVEL: str = "INFO"
    JSON_LOGS: bool = False

    # Only enforced for bulk actions that supply an account_id - see
    # app/core/rate_limiter.py.
    RATE_LIMIT_PER_MINUTE: int = 10_000

    class Config:
        env_file = ".env"


settings = Settings()