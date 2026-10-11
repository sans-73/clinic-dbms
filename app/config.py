"""All configuration comes from environment / the repo-root .env file. No clinic values are hardcoded (G9)."""
from __future__ import annotations
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    return v if v not in (None, "") else default


# --- database -------------------------------------------------------------
# DB_MODE: dev   = one MySQL account for everything (default until P3-7 grants exist)
#          roles = one MySQL account per role group (see db_credentials)
#          stub  = NO database at all: every query returns nothing. Lets you click through the UI.
DB_MODE = (env("DB_MODE", "dev") or "dev").lower()
DB_HOST = env("DB_HOST", "127.0.0.1")
DB_PORT = int(env("DB_PORT", "3307") or 3307)          # docker-compose maps 3307 -> 3306
DB_NAME = env("DB_NAME", "clinic_db")
DB_USER = env("DB_USER", "root")
DB_PASSWORD = env("DB_PASSWORD") or env("MYSQL_ROOT_PASSWORD", "")

# roles mode: DB_USER_<ROLE> / DB_PASSWORD_<ROLE>, e.g. DB_USER_DOCTOR=clinic_doctor
ROLE_GROUPS = ["OWNER", "DOCTOR", "RECEPTIONIST", "NURSE", "PHARMACY", "ACCOUNTANT", "FAMILY", "SYSTEM"]


def db_credentials(role: str | None) -> tuple[str, str]:
    if DB_MODE == "roles" and role:
        r = role.upper()
        user = env(f"DB_USER_{r}")
        if user:
            return user, env(f"DB_PASSWORD_{r}", env("DB_ROLE_PASSWORD", "")) or ""
    return DB_USER or "root", DB_PASSWORD or ""


# --- web ------------------------------------------------------------------
SECRET_KEY = env("APP_SECRET_KEY", "dev-only-change-me-please-0123456789")
SESSION_HOURS = int(env("SESSION_HOURS", "8") or 8)
HTTPS_ONLY = (env("APP_HTTPS_ONLY", "0") == "1")
TIMEZONE = env("APP_TIMEZONE", "Asia/Kolkata")            # display zone; DB stores UTC (G7)
UPLOAD_DIR = Path(env("UPLOAD_DIR", str(ROOT / "uploads")))
MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "10") or 10)
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://localhost:8000")   # used to print family links
FAMILY_TOKEN_HASH = env("FAMILY_TOKEN_HASH", "sha256")    # how family_link.token_hash is derived (ASSUMPTION A-14)
DEBUG_ERRORS = (env("APP_DEBUG", "1") == "1")              # show technical DB detail under friendly errors
