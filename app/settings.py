import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database URL - automatically provided by Replit Database in production
    # Falls back to SQLite for local development
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    
    EMBEDDINGS_BACKEND: str = "openai"   # "openai" for production deployment (lightweight)
    OPENAI_API_KEY: str | None = None
    OFFLINE_DEMO: int = 0

    class Config:
        env_file = ".env"

settings = Settings()

# Warn if using SQLite in what appears to be a production environment
if "sqlite" in settings.DATABASE_URL.lower():
    if os.getenv("REPL_DEPLOYMENT") or os.getenv("REPLIT_DEPLOYMENT"):
        print("⚠️  WARNING: Using SQLite in production deployment!")
        print("⚠️  SQLite data will be lost on each deployment.")
        print("⚠️  Add a PostgreSQL database in the Replit Deployments pane.")
    else:
        print("ℹ️  Using SQLite for development (production will use PostgreSQL)")

