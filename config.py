"""Environment-backed configuration for HABIX AI."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if os.environ.get("VERCEL") and not DATABASE_URL:
        raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database before deploying HABIX on Vercel.")
    SECRET_KEY = os.environ.get("HABIX_SECRET_KEY")
    if os.environ.get("VERCEL") and not SECRET_KEY:
        raise RuntimeError("Set HABIX_SECRET_KEY before deploying HABIX on Vercel.")
    SECRET_KEY = SECRET_KEY or os.urandom(32)
    DATABASE = os.environ.get("HABIX_DATABASE", str(BASE_DIR / "instance" / "habix.sqlite3"))
    DEBUG = os.environ.get("HABIX_DEBUG", "0").lower() in {"1", "true", "yes"}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get(
        "HABIX_COOKIE_SECURE", "1" if os.environ.get("VERCEL") else "0"
    ).lower() in {"1", "true", "yes"}
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14
