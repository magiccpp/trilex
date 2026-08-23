"""Wordbook storage and review scheduling.

Scheduling is delegated to `forgetting.py` (FSRS). The queue is ordered by how
much of each word you are predicted to still remember *right now*, lowest
first, so the words closest to being forgotten come back first - which is the
whole point of a forgetting curve and something a fixed interval cannot do.

Deletions are tombstones rather than row removals, so that removing a word on
one device propagates through sync instead of being resurrected by the next
device that pushes.
"""
from __future__ import annotations

import json, uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import forgetting as F
from .forgetting import AGAIN, EASY, GOOD, HARD, fmt_interval, fmt_retention

GRADE_NAME = {AGAIN: "Again", HARD: "Hard", GOOD: "Good", EASY: "Easy"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass
class Card:
    id: int
    lang: str
    word: str
    note: str
    snapshot: dict
    added_at: str
    due: str
    stability: float
    difficulty: float
    reps: int
    lapses: int
    last_review: str | None
    uid: str
    updated_at: str
    deleted: bool = False

    @property
    def memory(self) -> F.Memory:
        return F.Memory(self.stability, self.difficulty, self.reps, self.lapses)

    @property
    def due_date(self) -> date:
        return date.fromisoformat(self.due)

    @property
    def is_due(self) -> bool:
        return self.due_date <= date.today()

    @property
    def is_new(self) -> bool:
        return self.reps == 0 or self.stability <= 0

    @property
    def elapsed_days(self) -> float:
        if not self.last_review:
            return 0.0
        try:
            return max(0.0, (date.today() - date.fromisoformat(
                self.last_review[:10])).days)
        except ValueError:
            return 0.0

    @property
    def retention(self) -> float:
        """Predicted probability you would recall this word right now."""
        return 1.0 if self.is_new else F.retrievability(self.elapsed_days,
                                                        self.stability)

    def curve(self, days=60):
        return F.curve(self.stability or 1.0, days)


def _row(r) -> Card:
    return Card(r["id"], r["lang"], r["word"], r["note"] or "",
                json.loads(r["snapshot"]) if r["snapshot"] else {},
                r["added_at"], r["due"], r["stability"] or 0.0,
                r["difficulty"] or 0.0, r["reps"], r["lapses"],
                r["last_review"], r["uid"] or "", r["updated_at"] or "",
                bool(r["deleted"]))


def preview(card: Card) -> dict:
    return F.preview(card.memory, card.elapsed_days)


class Wordbook:
    def __init__(self, con, retention_target=F.DEFAULT_RETENTION):
        self.con = con
        self.retention_target = retention_target

    # ------------------------------------------------------------------ CRUD
    def add(self, lang, word, snapshot=None, note="") -> Card:
        existing = self.con.execute(
            "SELECT id FROM card WHERE lang=? AND word=?", (lang, word)).fetchone()
        if existing:
            # Re-adding a tombstoned word revives it rather than duplicating.
            self.con.execute(
                "UPDATE card SET deleted=0, updated_at=?, dirty=1 WHERE id=?",
                (_now_iso(), existing["id"]))
        else:
            self.con.execute(
                "INSERT INTO card(lang,word,note,snapshot,added_at,due,uid,"
                "updated_at,dirty) VALUES(?,?,?,?,?,?,?,?,1)",
                (lang, word, note,
                 json.dumps(snapshot, ensure_ascii=False) if snapshot else None,
                 _now_iso(), date.today().isoformat(), str(uuid.uuid4()), _now_iso()))
        self.con.commit()
        return self.get(lang, word)

    def get(self, lang, word) -> Card | None:
        r = self.con.execute("SELECT * FROM card WHERE lang=? AND word=?",
                             (lang, word)).fetchone()
        return _row(r) if r else None

    def by_uid(self, uid) -> Card | None:
        r = self.con.execute("SELECT * FROM card WHERE uid=?", (uid,)).fetchone()
        return _row(r) if r else None

    def has(self, lang, word) -> bool:
        return self.con.execute(
            "SELECT 1 FROM card WHERE lang=? AND word=? AND deleted=0",
            (lang, word)).fetchone() is not None

    def remove(self, card_id):
        """Tombstone, so the deletion survives a sync round trip."""
        self.con.execute("UPDATE card SET deleted=1, updated_at=?, dirty=1 WHERE id=?",
                         (_now_iso(), card_id))
        self.con.commit()

    def purge_deleted(self, older_than_days=60):
        cut = (date.today() - timedelta(days=older_than_days)).isoformat()
        self.con.execute("DELETE FROM card WHERE deleted=1 AND updated_at < ?", (cut,))
        self.con.commit()

    def set_note(self, card_id, note):
        self.con.execute("UPDATE card SET note=?, updated_at=?, dirty=1 WHERE id=?",
                         (note, _now_iso(), card_id))
        self.con.commit()

    def all(self, order="due") -> list[Card]:
        col = {"due": "due, added_at", "added": "added_at DESC",
               "word": "word", "lang": "lang, word"}.get(order, "due")
        return [_row(r) for r in self.con.execute(
            f"SELECT * FROM card WHERE deleted=0 ORDER BY {col}")]

    def all_including_deleted(self) -> list[Card]:
        return [_row(r) for r in self.con.execute("SELECT * FROM card")]

    # ------------------------------------------------------------- scheduling
    def due(self, limit=None, on=None) -> list[Card]:
        """Cards to review, most-forgotten first.

        Ordering by predicted retention rather than by due date means that if
        you skip a few days, the words you are actually losing surface first
        instead of whatever happened to be scheduled earliest.
        """
        d = (on or date.today()).isoformat()
        cards = [_row(r) for r in self.con.execute(
            "SELECT * FROM card WHERE deleted=0 AND due<=? ", (d,))]
        cards.sort(key=lambda c: (not c.is_new, c.retention, c.due))
        return cards[:limit] if limit else cards

    def counts(self) -> dict:
        today = date.today().isoformat()
        c = self.con.execute(
            "SELECT count(*) total, sum(due<=?) due, sum(reps=0) new,"
            " sum(due<=? AND reps>0) review FROM card WHERE deleted=0",
            (today, today)).fetchone()
        return {"total": c["total"] or 0, "due": c["due"] or 0,
                "new": c["new"] or 0, "review": c["review"] or 0}

    def grade(self, card: Card, g: int) -> Card:
        mem = card.memory.review(g, card.elapsed_days)
        iv = mem.next_interval(self.retention_target)
        due = (date.today() + timedelta(days=max(1, round(iv)))).isoformat()
        self.con.execute(
            "UPDATE card SET stability=?, difficulty=?, reps=?, lapses=?, "
            "due=?, interval=?, last_review=?, updated_at=?, dirty=1 WHERE id=?",
            (mem.stability, mem.difficulty, mem.reps, mem.lapses, due, iv,
             date.today().isoformat(), _now_iso(), card.id))
        self.con.execute(
            "INSERT INTO review(card_id,ts,grade,interval) VALUES(?,?,?,?)",
            (card.id, _now_iso(), g, iv))
        self.con.commit()
        return self.by_uid(card.uid) or self.get(card.lang, card.word)

    def average_retention(self) -> float:
        cards = [c for c in self.all() if not c.is_new]
        return sum(c.retention for c in cards) / len(cards) if cards else 1.0

    def history(self, days=30):
        return self.con.execute(
            "SELECT date(ts) d, count(*) n FROM review WHERE ts >= date('now', ?) "
            "GROUP BY d ORDER BY d", (f"-{int(days)} days",)).fetchall()

    def forecast(self, days=14):
        return self.con.execute(
            "SELECT due d, count(*) n FROM card WHERE deleted=0 AND "
            "due <= date('now', ?) GROUP BY due ORDER BY due",
            (f"+{int(days)} days",)).fetchall()

    def accuracy_grades(self):
        return [r["grade"] for r in self.con.execute(
            "SELECT grade FROM review ORDER BY id DESC LIMIT 500")]
