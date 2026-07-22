from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str
    BATCH_SIZE: int

    LOG_LEVEL: str = "INFO"
    JSON_LOGS: bool = False

    class Config:
        env_file = ".env"


settings = Settings()