"""Stream kaikki.org (wiktextract) JSONL straight into SQLite.

The dumps total ~4.5 GB. They are never written to disk: each HTTP chunk is
split into lines, each line is one JSON object, and only the handful of fields
we care about survive. Peak disk cost is the output database alone.

The connection *will* drop over a 75-minute transfer, so the reader resumes
with a Range request from the last byte offset that ended on a newline.
"""
import gzip, hashlib, io, json, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.norm import norm

SOURCES = {
    "en": "https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl",
    "sv": "https://kaikki.org/dictionary/Swedish/kaikki.org-dictionary-Swedish.jsonl",
    "zh": "https://kaikki.org/dictionary/Chinese/kaikki.org-dictionary-Chinese.jsonl",
}
UA = {"User-Agent": "trilex-offline-dictionary-builder/1.0"}
MAX_GLOSS = 3
MAX_ETYM = 1200


def _stream_lines(url, start=0, tries=8):
    """Yield (line_bytes, offset_after_line), resuming across dropped sockets."""
    off = start
    buf = b""
    attempt = 0
    while True:
        req = urllib.request.Request(url, headers=dict(UA))
        if off:
            req.add_header("Range", f"bytes={off}-")
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                if off and r.status != 206:
                    raise IOError(f"server ignored Range (status {r.status})")
                attempt = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        if buf.strip():
                            yield buf, off + len(buf)
                        return
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for ln in lines:
                        off += len(ln) + 1
                        yield ln, off
        except Exception as e:
            attempt += 1
            if attempt > tries:
                raise
            # Drop the partial line; we resume from the last clean newline.
            buf = b""
            wait = min(60, 2 ** attempt)
            print(f"  [resume] {type(e).__name__}: {e} -- offset {off}, "
                  f"retry {attempt}/{tries} in {wait}s", flush=True)
            time.sleep(wait)


def _ipa(rec):
    for s in rec.get("sounds") or ():
        if s.get("ipa"):
            return s["ipa"]
    return None


def _glosses(rec):
    out = []
    for sense in rec.get("senses") or ():
        for g in sense.get("glosses") or ():
            g = g.strip()
            # Skip pure form-of stubs; they carry no meaning of their own.
            if g and not g.lower().startswith(("plural of", "inflection of")):
                out.append(g)
        if len(out) >= MAX_GLOSS:
            break
    return " | ".join(out[:MAX_GLOSS]) or None


def build(lang, con, resume=True):
    url = SOURCES[lang]
    key = f"kaikki:{lang}:offset"
    done_key = f"kaikki:{lang}:done"
    row = con.execute("SELECT v FROM meta WHERE k=?", (done_key,)).fetchone()
    if row and resume:
        print(f"[{lang}] already complete, skipping", flush=True)
        return
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    start = int(row["v"]) if (row and resume) else 0
    if start:
        print(f"[{lang}] resuming at byte {start:,}", flush=True)

    etym_cache = {}
    for r in con.execute("SELECT sha, id FROM etym"):
        etym_cache[r["sha"]] = r["id"]

    n = kept = 0
    t0 = time.time()
    pending = []
    cur = con.cursor()
    for raw, off in _stream_lines(url, start):
        n += 1
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if rec.get("lang_code") != lang or not rec.get("word"):
            continue
        et = (rec.get("etymology_text") or "").strip()[:MAX_ETYM] or None
        gl = _glosses(rec)
        ip = _ipa(rec)
        if not (et or gl):
            continue
        eid = None
        if et:
            sha = hashlib.sha1(et.encode()).hexdigest()
            eid = etym_cache.get(sha)
            if eid is None:
                cur.execute("INSERT OR IGNORE INTO etym(sha,txt) VALUES(?,?)", (sha, et))
                eid = cur.lastrowid or con.execute(
                    "SELECT id FROM etym WHERE sha=?", (sha,)).fetchone()["id"]
                etym_cache[sha] = eid
        pending.append((lang, rec["word"], norm(rec["word"]),
                        rec.get("pos"), ip, eid, gl))
        kept += 1
        if len(pending) >= 5000:
            cur.executemany(
                "INSERT INTO wik(lang,word,norm,pos,ipa,etym_id,gloss) "
                "VALUES(?,?,?,?,?,?,?)", pending)
            pending.clear()
            cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(off)))
            con.commit()
            mb = off / 1048576
            print(f"[{lang}] {mb:8.0f} MB  {n:>9,} lines  {kept:>8,} kept  "
                  f"{mb/max(time.time()-t0,1):.2f} MB/s", flush=True)
    if pending:
        cur.executemany(
            "INSERT INTO wik(lang,word,norm,pos,ipa,etym_id,gloss) "
            "VALUES(?,?,?,?,?,?,?)", pending)
    cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (done_key, "1"))
    con.commit()
    print(f"[{lang}] DONE  {n:,} lines -> {kept:,} entries "
          f"in {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    langs = sys.argv[1:] or ["sv", "en", "zh"]
    con = db.open_dict(Path(__file__).resolve().parents[2] / "data/dict.db")
    for lg in langs:
        build(lg, con)
    con.executescript(db.DICT_INDEXES)
    con.commit()
    print("indexes built")
