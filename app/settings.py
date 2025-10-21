from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./app.db"
    EMBEDDINGS_BACKEND: str = "local"   # or "openai"
    OPENAI_API_KEY: str | None = None
    OFFLINE_DEMO: int = 0

    class Config:
        env_file = ".env"

settings = Settings()

