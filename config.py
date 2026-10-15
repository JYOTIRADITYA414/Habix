"""Environment-backed configuration for HABIX AI."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.environ.get("HABIX_SECRET_KEY") or os.urandom(32)
    DATABASE = os.environ.get("HABIX_DATABASE", str(BASE_DIR / "instance" / "habix.sqlite3"))
    DEBUG = os.environ.get("HABIX_DEBUG", "0").lower() in {"1", "true", "yes"}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("HABIX_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14
