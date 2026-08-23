"""Hashing, tokens and rate limiting.

Login codes and bearer tokens are stored only as HMAC digests keyed by the
server secret, so a copy of the database yields neither a usable token nor a
guessable code.
"""
import hmac, hashlib, re, secrets
from datetime import datetime, timedelta, timezone

from .config import settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def parse_iso(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalise_email(raw: str) -> str | None:
    e = (raw or "").strip().lower()
    if not EMAIL_RE.match(e) or len(e) > 254:
        return None
    return e


def digest(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(),
                    hashlib.sha256).hexdigest()


def new_code() -> str:
    """Six digits, uniformly random. 10-minute life, five attempts."""
    return f"{secrets.randbelow(1_000_000):06d}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def rate_ok(con, key: str, limit: int) -> bool:
    """Fixed-window counter. Coarse, but it is the difference between someone
    being able to mail-bomb an address and not."""
    window = now().strftime("%Y%m%d%H")
    con.execute("INSERT OR IGNORE INTO rate(key,window,n) VALUES(?,?,0)", (key, window))
    con.execute("UPDATE rate SET n = n + 1 WHERE key=? AND window=?", (key, window))
    row = con.execute("SELECT n FROM rate WHERE key=? AND window=?", (key, window)).fetchone()
    con.execute("DELETE FROM rate WHERE window < ?",
                ((now() - timedelta(hours=3)).strftime("%Y%m%d%H"),))
    con.commit()
    return row["n"] <= limit
