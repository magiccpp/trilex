"""Offline pronunciation audio.

Two phases:

  collect  - stream the kaikki dumps and record which Commons audio file goes
             with which headword (metadata only, no downloads).
  fetch    - download each file, transcode to ~24 kbps mono Opus with ffmpeg,
             and store the bytes in the database.

Transcoding is what makes this practical: the source recordings are 20-60 KB
of Ogg Vorbis or WAV, while a one-second word at 24 kbps Opus is 3-5 KB. The
audio ships inside dict.db, so playback never touches the network.

Every file comes from Wikimedia Commons, which by policy accepts only freely
licensed media (unlike en.wikipedia, which also hosts fair-use uploads). The
originating filename is stored with each clip so attribution stays traceable.
"""
import json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.build.kaikki import SOURCES, _stream_lines
from trilex.norm import norm

DATA = Path(__file__).resolve().parents[2] / "data"
# Wikimedia's User-Agent policy requires a contact URL and address. A generic
# agent gets throttled hard: measured 4/8 requests returning 429 without this,
# 8/8 succeeding with it.
UA = {"User-Agent": "TrilexDictionaryBuilder/1.0 "
                    "(https://xiaodong.io/thriauga; ken@xiaodong.io) "
                    "python-urllib/3.12"}
BITRATE = "24k"
# MP3, not Opus: both are ~4-5 KB at this length, but Windows Media
# Foundation has no native Opus decoder, so Opus would play on Linux and fail
# silently on Windows. MP3 decodes everywhere Qt runs.
SAMPLE_RATE = "22050"
MAX_SECONDS = 8.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_ref(
  lang  TEXT NOT NULL,
  norm  TEXT NOT NULL,
  word  TEXT NOT NULL,
  url   TEXT NOT NULL,
  fname TEXT NOT NULL,
  tags  TEXT,
  pref  INTEGER NOT NULL DEFAULT 5,   -- lower = preferred accent
  PRIMARY KEY(lang, norm, fname)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS audio(
  lang    TEXT NOT NULL,
  norm    TEXT NOT NULL,
  word    TEXT NOT NULL,
  data    BLOB NOT NULL,
  bytes   INTEGER,
  fname   TEXT,
  accent  TEXT,
  PRIMARY KEY(lang, norm)
) WITHOUT ROWID;
"""


def _accent(lang, tags):
    """Rank pronunciations so one canonical accent wins per word."""
    t = " ".join(tags or []).lower()
    if lang == "en":
        if "us" in t.split() or "general-american" in t or "america" in t:
            return 0, "US"
        if "uk" in t.split() or "received-pronunciation" in t or "britain" in t:
            return 1, "UK"
        return 3, ""
    if lang == "zh":
        if "mandarin" in t:
            return 0, "Mandarin"
        if "cantonese" in t:
            return 4, "Cantonese"
        return 2, ""
    return 0, ""


def collect(lang, con, resume=True):
    key, done = f"audio:{lang}:offset", f"audio:{lang}:done"
    if resume and con.execute("SELECT 1 FROM meta WHERE k=?", (done,)).fetchone():
        print(f"[{lang}] audio refs already collected", flush=True)
        return
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    start = int(row["v"]) if (row and resume) else 0
    if start:
        print(f"[{lang}] resuming at byte {start:,}", flush=True)

    vocab = {r["norm"] for r in con.execute(
        "SELECT norm FROM vocab WHERE lang=? AND core=1", (lang,))}
    print(f"[{lang}] {len(vocab):,} headwords to match", flush=True)

    cur, pending, n, kept = con.cursor(), [], 0, 0
    t0 = time.time()
    for raw, off in _stream_lines(SOURCES[lang], start):
        n += 1
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if rec.get("lang_code") != lang or not rec.get("word"):
            continue
        nw = norm(rec["word"])
        if nw not in vocab:
            continue
        for s in rec.get("sounds") or ():
            fname = s.get("audio")
            url = s.get("ogg_url") or s.get("mp3_url")
            if not fname or not url:
                continue
            pref, _ = _accent(lang, s.get("tags"))
            pending.append((lang, nw, rec["word"], url, fname,
                            " ".join(s.get("tags") or []), pref))
            kept += 1
        if len(pending) >= 4000:
            cur.executemany("INSERT OR IGNORE INTO audio_ref"
                            "(lang,norm,word,url,fname,tags,pref)"
                            " VALUES(?,?,?,?,?,?,?)", pending)
            pending.clear()
            cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(off)))
            con.commit()
            mb = off / 1048576
            print(f"[{lang}] {mb:7.0f} MB  {n:>9,} lines  {kept:>7,} refs  "
                  f"{mb/max(time.time()-t0,1):.2f} MB/s", flush=True)
    if pending:
        cur.executemany("INSERT OR IGNORE INTO audio_ref"
                        "(lang,norm,word,url,fname,tags,pref)"
                        " VALUES(?,?,?,?,?,?,?)", pending)
    cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (done, "1"))
    con.commit()
    print(f"[{lang}] DONE {kept:,} refs in {(time.time()-t0)/60:.1f} min", flush=True)


def transcode(raw: bytes) -> bytes | None:
    """Any input format -> mono 24 kbps MP3.

    Output goes to a temporary file rather than a pipe: ffmpeg can only write
    the Xing/Info header (which carries duration and channel count) if it can
    seek back to the start, which a pipe does not allow. Without it players
    report "Duration: N/A" and some refuse to play at all.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "clip.mp3")
        try:
            p = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", "pipe:0", "-t", str(MAX_SECONDS),
                 "-ac", "1", "-ar", SAMPLE_RATE,
                 "-c:a", "libmp3lame", "-b:a", BITRATE,
                 "-write_xing", "1",
                 out_path],
                input=raw, capture_output=True, timeout=60)
            if p.returncode != 0 or not os.path.exists(out_path):
                return None
            with open(out_path, "rb") as fh:
                data = fh.read()
            return data or None
        except Exception:
            return None


# One clip must never be able to hold up the pool. Every attempt is bounded,
# and the job as a whole has a hard deadline.
# Tuned by measurement. 10 workers pushed Wikimedia into rate-limiting and
# success collapsed to 8%; 4 with real backoff held ~96%. Throughput here is
# irrelevant to users anyway - this runs once, and the result is served as a
# prepared pack.
CONNECT_TIMEOUT = 25
JOB_DEADLINE = 90


def _ascii_url(u: str) -> str:
    """Percent-encode non-ASCII characters in a URL.

    urllib requires an ASCII URL. Wikimedia filenames are frequently not:
    "Zh-yixie.ogg" is fine but "Zh-y\u00ecxi\u0113.ogg" or "zh-yue-XO\u91ac.opus"
    raise UnicodeEncodeError before any request is made. This silently killed
    89% of Chinese audio, which is mostly stored under pinyin-with-tone-marks
    or Han filenames. `safe` keeps existing %XX escapes from being re-encoded.
    """
    parts = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc,
        urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~"),
        urllib.parse.quote(parts.query, safe="=&%:/?@!$'()*+,;~"),
        parts.fragment))


def _download(url, deadline):
    req = urllib.request.Request(_ascii_url(url), headers=dict(UA))
    for attempt in range(3):
        if time.monotonic() > deadline:
            return None
        try:
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # The error object owns an open response body. Failing to close it
            # leaves the socket in CLOSE-WAIT until the process exits, which
            # is how the first run leaked fds and eventually wedged.
            try:
                e.read()
                e.close()
            except Exception:
                pass
            if e.code in (429, 503) and attempt < 2:
                time.sleep(2 + 3 * attempt)
                continue
            return None
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            return None
    return None


def _one(job):
    lang, nw, word, url, fname, tags = job
    raw = _download(url, time.monotonic() + JOB_DEADLINE)
    if not raw or len(raw) < 200:
        return None
    out = transcode(raw)
    if not out or len(out) < 100:
        return None
    _, accent = _accent(lang, tags.split() if tags else [])
    return (lang, nw, word, out, len(out), fname, accent)


def fetch(con, limit=None, workers=3):
    """Download and transcode every referenced clip.

    Results are consumed with as_completed rather than map: map returns in
    submission order, so a single slow URL blocks every result behind it. Here
    a straggler only delays itself.
    """
    have = {(r["lang"], r["norm"]) for r in con.execute("SELECT lang,norm FROM audio")}
    # Interleave the three languages proportionally rather than finishing one
    # before starting the next. Ordering by (lang, norm) put English first in
    # the whole queue: Swedish sat 4,100 items deep and Chinese 8,900, so a
    # trilingual dictionary had English-only audio for hours. Ranking each row
    # by its fractional position within its own language makes any stopping
    # point give balanced coverage.
    rows = con.execute("""
        SELECT lang, norm, word, url, fname, tags FROM (
            SELECT lang, norm, word, url, fname, tags,
                   ROW_NUMBER() OVER (PARTITION BY lang ORDER BY norm) * 1.0
                     / COUNT(*)  OVER (PARTITION BY lang) AS frac
            FROM audio_ref
            WHERE (lang, norm, pref) IN (
                SELECT lang, norm, MIN(pref) FROM audio_ref GROUP BY lang, norm)
            GROUP BY lang, norm)
        ORDER BY frac, lang""").fetchall()
    jobs = [tuple(r) for r in rows if (r["lang"], r["norm"]) not in have]
    if limit:
        jobs = jobs[:limit]
    print(f"{len(jobs):,} clips to fetch ({len(have):,} already stored), "
          f"{workers} workers", flush=True)

    done = ok = 0
    batch = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_one, j): j for j in jobs}
        for fut in as_completed(futures):
            done += 1
            try:
                r = fut.result()
            except Exception:
                r = None
            if r:
                batch.append(r)
                ok += 1
            if len(batch) >= 100 or (done == len(jobs) and batch):
                con.executemany("INSERT OR REPLACE INTO audio"
                                "(lang,norm,word,data,bytes,fname,accent)"
                                " VALUES(?,?,?,?,?,?,?)", batch)
                con.commit()
                batch.clear()
            if done % 200 == 0:
                el = time.time() - t0
                rate = done / max(el, 1)
                print(f"  {done:>6}/{len(jobs)}  stored={ok}  {rate:.1f}/s  "
                      f"eta {(len(jobs)-done)/max(rate, 0.01)/60:.0f} min",
                      flush=True)
    if batch:
        con.executemany("INSERT OR REPLACE INTO audio"
                        "(lang,norm,word,data,bytes,fname,accent)"
                        " VALUES(?,?,?,?,?,?,?)", batch)
        con.commit()
    print(f"  fetched {ok:,}/{len(jobs):,} in {(time.time()-t0)/60:.1f} min",
          flush=True)


if __name__ == "__main__":
    con = db.open_dict(DATA / "dict.db")
    con.executescript(SCHEMA)
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("collect", "all"):
        for lg in ("sv", "en", "zh"):
            collect(lg, con)
    if mode in ("fetch", "all"):
        fetch(con, limit=int(sys.argv[2]) if len(sys.argv) > 2 else None)
    n, size = con.execute(
        "SELECT count(*), coalesce(sum(bytes),0) FROM audio").fetchone()
    print(f"\n{n:,} clips, {size/1048576:.1f} MB "
          f"(avg {size/max(n,1)/1024:.1f} KB)")
