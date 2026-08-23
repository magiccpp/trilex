"""Þríauga sync API.

Passwordless email auth plus a last-write-wins wordbook sync. The dictionary
itself never touches this service - only the words you chose to save and their
review schedule, so the app stays fully usable offline and with sync disabled.
"""
import json, logging, os, re
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import db as database, mailer
from .config import ensure_secret, settings
from .security import (constant_time_eq, digest, iso, new_code, new_token,
                       normalise_email, now, parse_iso, rate_ok)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("thriauga")

app = FastAPI(title="Þríauga sync", version=settings.VERSION,
              root_path=settings.ROOT_PATH, docs_url=None, redoc_url=None)
if settings.ALLOW_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOW_ORIGINS,
                       allow_methods=["*"], allow_headers=["*"])

con = database.init(settings.DB_PATH)
ensure_secret(con)


# ------------------------------------------------------------------ schemas
class EmailIn(BaseModel):
    email: str = Field(max_length=254)


class VerifyIn(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(min_length=4, max_length=10)
    device: str = Field(default="", max_length=80)


class CardIn(BaseModel):
    uid: str = Field(min_length=8, max_length=64)
    lang: str = Field(max_length=8)
    word: str = Field(max_length=200)
    note: str = Field(default="", max_length=4000)
    snapshot: Any = None
    added_at: str | None = None
    due: str | None = None
    stability: float = 0.0
    difficulty: float = 0.0
    reps: int = 0
    lapses: int = 0
    last_review: str | None = None
    deleted: bool = False
    updated_at: str


class SyncIn(BaseModel):
    since: int = 0
    cards: list[CardIn] = Field(default_factory=list, max_length=2000)


# --------------------------------------------------------------------- auth
def client_ip(req: Request) -> str:
    # nginx sets X-Forwarded-For; take the left-most entry it added.
    fwd = req.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() or (req.client.host if req.client else "?"))[:64]


def current_user(authorization: str = Header(default="")) -> dict:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    row = con.execute(
        "SELECT t.id tid, t.expires_at, u.id, u.email, u.rev FROM token t "
        "JOIN user u ON u.id = t.user_id WHERE t.token_hash = ?",
        (digest(authorization[7:].strip()),)).fetchone()
    if not row or parse_iso(row["expires_at"]) < now():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    con.execute("UPDATE token SET last_used=? WHERE id=?", (iso(now()), row["tid"]))
    con.execute("UPDATE user SET last_seen=? WHERE id=?", (iso(now()), row["id"]))
    con.commit()
    return {"id": row["id"], "email": row["email"], "rev": row["rev"]}


@app.post("/v1/auth/request", status_code=204)
def auth_request(body: EmailIn, req: Request):
    """Send a login code.

    Always returns 204, whether or not the address exists or the mail was
    actually delivered. Anything else would turn this into an oracle for
    discovering which addresses have accounts.
    """
    email = normalise_email(body.email)
    ip = client_ip(req)
    if not rate_ok(con, f"ip:{ip}", settings.RATE_IP_PER_HOUR):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests")
    if not email:
        return Response(status_code=204)
    if not rate_ok(con, f"em:{email}", settings.RATE_EMAIL_PER_HOUR):
        return Response(status_code=204)

    code = new_code()
    con.execute("UPDATE login_code SET used=1 WHERE email=? AND used=0", (email,))
    con.execute(
        "INSERT INTO login_code(email,code_hash,created_at,expires_at,ip) "
        "VALUES(?,?,?,?,?)",
        (email, digest(code), iso(now()),
         iso(now() + timedelta(seconds=settings.CODE_TTL_SEC)), ip))
    con.commit()
    mailer.send_code(email, code)
    return Response(status_code=204)


@app.post("/v1/auth/verify")
def auth_verify(body: VerifyIn, req: Request):
    email = normalise_email(body.email)
    if not rate_ok(con, f"vfy:{client_ip(req)}", settings.RATE_IP_PER_HOUR * 3):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts")
    bad = HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired code")
    if not email:
        raise bad
    row = con.execute(
        "SELECT * FROM login_code WHERE email=? AND used=0 "
        "ORDER BY id DESC LIMIT 1", (email,)).fetchone()
    if not row or parse_iso(row["expires_at"]) < now():
        raise bad
    if row["attempts"] >= settings.CODE_MAX_ATTEMPTS:
        con.execute("UPDATE login_code SET used=1 WHERE id=?", (row["id"],))
        con.commit()
        raise bad
    con.execute("UPDATE login_code SET attempts=attempts+1 WHERE id=?", (row["id"],))
    con.commit()
    if not constant_time_eq(row["code_hash"], digest(body.code.strip())):
        raise bad

    con.execute("UPDATE login_code SET used=1 WHERE id=?", (row["id"],))
    u = con.execute("SELECT id FROM user WHERE email=?", (email,)).fetchone()
    if u:
        uid = u["id"]
    else:
        cur = con.execute("INSERT INTO user(email,created_at) VALUES(?,?)",
                          (email, iso(now())))
        uid = cur.lastrowid
        log.info("[auth] created account for %s", email)
    token = new_token()
    con.execute(
        "INSERT INTO token(user_id,token_hash,device,created_at,expires_at) "
        "VALUES(?,?,?,?,?)",
        (uid, digest(token), (body.device or "")[:80], iso(now()),
         iso(now() + timedelta(days=settings.TOKEN_TTL_DAYS))))
    con.commit()
    return {"token": token, "email": email,
            "expires_in_days": settings.TOKEN_TTL_DAYS}


@app.get("/v1/me")
def me(user=Depends(current_user)):
    n = con.execute("SELECT count(*) c FROM card WHERE user_id=? AND deleted=0",
                    (user["id"],)).fetchone()["c"]
    devices = con.execute("SELECT count(*) c FROM token WHERE user_id=? AND expires_at > ?",
                          (user["id"], iso(now()))).fetchone()["c"]
    return {"email": user["email"], "cards": n, "devices": devices, "rev": user["rev"]}


@app.post("/v1/auth/logout", status_code=204)
def logout(authorization: str = Header(default=""), user=Depends(current_user)):
    con.execute("DELETE FROM token WHERE token_hash=?",
                (digest(authorization[7:].strip()),))
    con.commit()
    return Response(status_code=204)


@app.delete("/v1/account", status_code=204)
def delete_account(user=Depends(current_user)):
    """Full erasure - cards, tokens, account row."""
    con.execute("DELETE FROM user WHERE id=?", (user["id"],))
    con.execute("DELETE FROM card WHERE user_id=?", (user["id"],))
    con.commit()
    log.info("[account] deleted %s", user["email"])
    return Response(status_code=204)


# --------------------------------------------------------------------- sync
CARD_COLS = ("uid", "lang", "word", "note", "snapshot", "added_at", "due",
             "stability", "difficulty", "reps", "lapses", "last_review",
             "deleted", "updated_at")


def _row_to_card(r) -> dict:
    d = {c: r[c] for c in CARD_COLS}
    d["deleted"] = bool(d["deleted"])
    d["snapshot"] = json.loads(r["snapshot"]) if r["snapshot"] else None
    d["rev"] = r["rev"]
    return d


def _changes_since(uid: int, since: int, limit: int = 5000):
    return [_row_to_card(r) for r in con.execute(
        "SELECT * FROM card WHERE user_id=? AND rev > ? ORDER BY rev LIMIT ?",
        (uid, since, limit))]


@app.get("/v1/sync")
def sync_pull(since: int = 0, user=Depends(current_user)):
    return {"rev": user["rev"], "cards": _changes_since(user["id"], since)}


@app.post("/v1/sync")
def sync_push(body: SyncIn, user=Depends(current_user)):
    """Push local changes, then pull everything newer in one round trip.

    Conflicts resolve last-write-wins on the client's `updated_at`. A card is
    never hard-deleted: removal is a tombstone, otherwise a delete on one
    device would be resurrected by the next sync from another.
    """
    uid = user["id"]
    total = con.execute("SELECT count(*) c FROM card WHERE user_id=?", (uid,)).fetchone()["c"]
    applied = skipped = 0

    for card in body.cards:
        existing = con.execute(
            "SELECT updated_at FROM card WHERE user_id=? AND uid=?",
            (uid, card.uid)).fetchone()
        if existing and parse_iso(existing["updated_at"]) >= parse_iso(card.updated_at):
            skipped += 1          # server copy is newer or identical
            continue
        if not existing and total >= settings.MAX_CARDS:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                "wordbook limit reached")
        rev = con.execute("UPDATE user SET rev = rev + 1 WHERE id=? RETURNING rev",
                          (uid,)).fetchone()["rev"]
        con.execute(f"""
            INSERT INTO card(user_id,{','.join(CARD_COLS)},rev)
            VALUES(?,{','.join('?' * len(CARD_COLS))},?)
            ON CONFLICT(user_id,uid) DO UPDATE SET
              {', '.join(f'{c}=excluded.{c}' for c in CARD_COLS if c != 'uid')},
              rev=excluded.rev""",
            (uid, card.uid, card.lang, card.word, card.note,
             json.dumps(card.snapshot, ensure_ascii=False) if card.snapshot else None,
             card.added_at, card.due, card.stability, card.difficulty,
             card.reps, card.lapses, card.last_review,
             1 if card.deleted else 0, card.updated_at, rev))
        applied += 1
        if not existing:
            total += 1
    con.commit()

    new_rev = con.execute("SELECT rev FROM user WHERE id=?", (uid,)).fetchone()["rev"]
    return {"rev": new_rev, "applied": applied, "skipped": skipped,
            "cards": _changes_since(uid, body.since)}


# ------------------------------------------------------------- media packs
# Images and audio are optional add-ons: the base install ships text only, and
# these are downloaded afterwards from here. Served straight off disk, and
# unauthenticated - they are the same freely-licensed media anyone can fetch
# from Wikimedia, just prepared and packaged.
PACK_DIR = Path(os.environ.get("PACK_DIR", "/packs"))


@app.get("/v1/packs")
def list_packs():
    mf = PACK_DIR / "manifest.json"
    if not mf.exists():
        return {"version": 1, "packs": []}
    try:
        data = json.loads(mf.read_text())
    except Exception:
        raise HTTPException(500, "pack manifest unreadable")
    # Only advertise packs whose file is actually present.
    data["packs"] = [p for p in data.get("packs", [])
                     if (PACK_DIR / p.get("file", "")).exists()]
    return data


@app.get("/", response_class=HTMLResponse)
def home():
    """Public download page."""
    page = PACK_DIR / "index.html"
    if not page.is_file():
        return HTMLResponse("<h1>Thriauga</h1><p>No release published yet.</p>",
                            status_code=503)
    return HTMLResponse(page.read_text(encoding="utf-8"))


# Installer names are fixed by the build; match them exactly rather than
# sanitising a caller-supplied path.
_INSTALLER = re.compile(r"^thriauga-\d+\.\d+\.\d+-(linux\.tar\.gz|windows\.zip)$")


@app.get("/download/{name}")
def download_installer(name: str):
    if not _INSTALLER.match(name):
        raise HTTPException(404, "unknown file")
    path = PACK_DIR / name
    if not path.is_file():
        raise HTTPException(404, "not published")
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@app.get("/v1/dictionary")
def download_dictionary():
    """The compressed core dictionary, fetched by a new install on first run."""
    path = PACK_DIR / "dict.db.xz"
    if not path.is_file():
        raise HTTPException(404, "dictionary not published")
    return FileResponse(path, media_type="application/x-xz", filename=path.name)


@app.get("/v1/packs/{name}")
def download_pack(name: str):
    # Reject anything that is not a bare pack name; no path traversal.
    if not name.isalnum() or len(name) > 32:
        raise HTTPException(400, "bad pack name")
    path = PACK_DIR / f"media-{name}.db"
    if not path.is_file():
        raise HTTPException(404, "pack not found")
    return FileResponse(path, media_type="application/vnd.sqlite3",
                        filename=path.name)


@app.get("/v1/health")
def health():
    return {"ok": True, "app": settings.DISPLAY_NAME, "version": settings.VERSION,
            "mail": settings.MAIL_BACKEND}
