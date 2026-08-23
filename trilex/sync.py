"""Client for the Þríauga sync service.

Sync is entirely optional. The dictionary never touches the network; only the
wordbook - the words you chose to save and their review schedule - is
exchanged, and only once you have signed in.

Conflicts resolve last-write-wins on `updated_at`, matching the server. A
deletion travels as a tombstone so removing a word on one device does not get
undone by the next device that pushes.
"""
from __future__ import annotations

import json, platform, socket, urllib.error, urllib.request
from dataclasses import dataclass
from datetime import date, datetime

DEFAULT_URL = "https://xiaodong.io/thriauga"
TIMEOUT = 25


class SyncError(Exception):
    pass


def _device_name() -> str:
    try:
        return f"{platform.system()} {socket.gethostname()}"[:80]
    except Exception:
        return platform.system()[:80]


@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    applied: int = 0
    skipped: int = 0
    rev: int = 0

    def summary(self) -> str:
        if not (self.pushed or self.pulled):
            return "Already up to date"
        bits = []
        if self.pushed:
            bits.append(f"sent {self.pushed}")
        if self.applied:
            bits.append(f"received {self.applied}")
        return ", ".join(bits) or "Up to date"


class SyncClient:
    """Talks to the server and reconciles it with the local wordbook."""

    FIELDS = ("uid", "lang", "word", "note", "added_at", "due", "stability",
              "difficulty", "reps", "lapses", "last_review", "updated_at")

    def __init__(self, con, base_url=None):
        self.con = con
        self.base_url = (base_url or self.get_state("base_url")
                         or DEFAULT_URL).rstrip("/")

    # ------------------------------------------------------------ persistence
    def get_state(self, k, default=None):
        r = self.con.execute("SELECT v FROM sync_state WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_state(self, k, v):
        self.con.execute("INSERT OR REPLACE INTO sync_state(k,v) VALUES(?,?)",
                         (k, str(v)))
        self.con.commit()

    @property
    def token(self):
        return self.get_state("token")

    @property
    def email(self):
        return self.get_state("email")

    @property
    def signed_in(self) -> bool:
        return bool(self.token)

    def sign_out(self):
        for k in ("token", "email", "rev"):
            self.con.execute("DELETE FROM sync_state WHERE k=?", (k,))
        self.con.commit()

    # ------------------------------------------------------------------ HTTP
    def _call(self, method, path, body=None, auth=True):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if auth:
            if not self.token:
                raise SyncError("Not signed in")
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SyncError("Session expired - sign in again")
            if e.code == 429:
                raise SyncError("Too many attempts. Wait a few minutes.")
            detail = ""
            try:
                detail = json.loads(e.read()).get("detail", "")
            except Exception:
                pass
            raise SyncError(detail or f"Server error {e.code}")
        except urllib.error.URLError as e:
            raise SyncError(f"Cannot reach {self.base_url} ({e.reason})")
        except socket.timeout:
            raise SyncError("Server timed out")

    # ------------------------------------------------------------------ auth
    def request_code(self, email):
        self._call("POST", "/v1/auth/request", {"email": email}, auth=False)

    def verify(self, email, code):
        r = self._call("POST", "/v1/auth/verify",
                       {"email": email, "code": code, "device": _device_name()},
                       auth=False)
        if not r.get("token"):
            raise SyncError("Server did not return a token")
        self.set_state("token", r["token"])
        self.set_state("email", r.get("email", email))
        self.set_state("base_url", self.base_url)
        return r

    def me(self):
        return self._call("GET", "/v1/me")

    # ------------------------------------------------------------------ sync
    def _local_changes(self):
        rows = self.con.execute(
            "SELECT * FROM card WHERE uid IS NOT NULL AND dirty=1").fetchall()
        out = []
        for r in rows:
            c = {f: r[f] for f in self.FIELDS}
            c["snapshot"] = json.loads(r["snapshot"]) if r["snapshot"] else None
            c["deleted"] = bool(r["deleted"])
            c["note"] = c["note"] or ""
            c["stability"] = c["stability"] or 0.0
            c["difficulty"] = c["difficulty"] or 0.0
            c["updated_at"] = c["updated_at"] or datetime.now().astimezone().isoformat()
            out.append(c)
        return out

    def _apply(self, cards) -> int:
        """Merge server cards into the local wordbook, last-write-wins."""
        applied = 0
        for c in cards:
            local = self.con.execute(
                "SELECT id, updated_at FROM card WHERE uid=?", (c["uid"],)).fetchone()
            if local and (local["updated_at"] or "") >= (c["updated_at"] or ""):
                continue
            snap = json.dumps(c.get("snapshot"), ensure_ascii=False) \
                if c.get("snapshot") else None
            vals = (c["lang"], c["word"], c.get("note") or "", snap,
                    c.get("added_at") or date.today().isoformat(),
                    c.get("due") or date.today().isoformat(),
                    c.get("stability") or 0.0, c.get("difficulty") or 0.0,
                    c.get("reps") or 0, c.get("lapses") or 0,
                    c.get("last_review"), 1 if c.get("deleted") else 0,
                    c["updated_at"], c["uid"])
            if local:
                self.con.execute(
                    "UPDATE card SET lang=?,word=?,note=?,snapshot=?,added_at=?,"
                    "due=?,stability=?,difficulty=?,reps=?,lapses=?,last_review=?,"
                    "deleted=?,updated_at=?,dirty=0 WHERE uid=?", vals)
            else:
                # A word saved on another device may collide by (lang, word)
                # here; the UNIQUE constraint would reject it, so drop the
                # older local duplicate and let the synced one win.
                self.con.execute("DELETE FROM card WHERE lang=? AND word=? AND uid<>?",
                                 (c["lang"], c["word"], c["uid"]))
                self.con.execute(
                    "INSERT INTO card(lang,word,note,snapshot,added_at,due,"
                    "stability,difficulty,reps,lapses,last_review,deleted,"
                    "updated_at,uid,dirty) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)", vals)
            applied += 1
        self.con.commit()
        return applied

    def sync(self) -> SyncResult:
        since_rev = int(self.get_state("rev", 0) or 0)
        outgoing = self._local_changes()
        pushed_uids = [c["uid"] for c in outgoing]
        res = SyncResult(pushed=len(outgoing))
        payload = {"since": since_rev, "cards": outgoing}
        r = self._call("POST", "/v1/sync", payload)
        incoming = r.get("cards", [])
        res.pulled = len(incoming)
        res.applied = self._apply(incoming)
        res.skipped = r.get("skipped", 0)
        res.rev = r.get("rev", since_rev)
        # Clear the flag only now that the server has acknowledged the push.
        if pushed_uids:
            self.con.executemany("UPDATE card SET dirty=0 WHERE uid=?",
                                 [(u,) for u in pushed_uids])
            self.con.commit()
        self.set_state("rev", res.rev)
        self.set_state("last_push", datetime.now().astimezone().isoformat(
            timespec="seconds"))
        self.set_state("last_sync", datetime.now().astimezone().isoformat(
            timespec="seconds"))
        return res
