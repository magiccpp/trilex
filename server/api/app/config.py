"""Configuration, entirely from environment. No secret ever lives in the image."""
import os, secrets
from pathlib import Path

def _int(k, d): 
    try: return int(os.environ.get(k, d))
    except ValueError: return d

class Settings:
    APP_NAME     = "Thriauga"
    DISPLAY_NAME = "Þríauga"
    VERSION      = "1.0.0"

    # Mounted under a path prefix on an existing nginx vhost.
    ROOT_PATH    = os.environ.get("ROOT_PATH", "")
    DB_PATH      = os.environ.get("DB_PATH", "/data/thriauga.db")

    # Signing key for code/token hashes. Generated on first boot and persisted
    # if not supplied, so a fresh deploy is never silently insecure.
    SECRET_KEY   = os.environ.get("SECRET_KEY", "")

    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "log")   # log | smtp
    SMTP_HOST    = os.environ.get("SMTP_HOST", "mail")
    SMTP_PORT    = _int("SMTP_PORT", 25)
    SMTP_USER    = os.environ.get("SMTP_USER", "")
    SMTP_PASS    = os.environ.get("SMTP_PASS", "")
    SMTP_STARTTLS= os.environ.get("SMTP_STARTTLS", "0") == "1"
    MAIL_FROM    = os.environ.get("MAIL_FROM", "thriauga@xiaodong.io")
    MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Þríauga")

    CODE_TTL_SEC     = _int("CODE_TTL_SEC", 600)      # 10 minutes
    CODE_MAX_ATTEMPTS= _int("CODE_MAX_ATTEMPTS", 5)
    TOKEN_TTL_DAYS   = _int("TOKEN_TTL_DAYS", 365)
    MAX_CARDS        = _int("MAX_CARDS", 100_000)

    # Rate limits (requests per window per key)
    RATE_EMAIL_PER_HOUR = _int("RATE_EMAIL_PER_HOUR", 5)
    RATE_IP_PER_HOUR    = _int("RATE_IP_PER_HOUR", 20)

    ALLOW_ORIGINS = [o for o in os.environ.get("ALLOW_ORIGINS", "").split(",") if o]


settings = Settings()


def ensure_secret(con):
    """Persist a generated secret so tokens survive a container restart."""
    if settings.SECRET_KEY:
        return settings.SECRET_KEY
    row = con.execute("SELECT v FROM meta WHERE k='secret_key'").fetchone()
    if row:
        settings.SECRET_KEY = row["v"]
    else:
        settings.SECRET_KEY = secrets.token_urlsafe(48)
        con.execute("INSERT INTO meta(k,v) VALUES('secret_key',?)",
                    (settings.SECRET_KEY,))
        con.commit()
    return settings.SECRET_KEY
