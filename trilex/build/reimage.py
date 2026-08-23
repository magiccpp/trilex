"""Re-fetch images already in the database at the current THUMB size.

Used when the thumbnail resolution changes: only the words known to have a
usable, freely-licensed image are re-requested, which is a few hundred
requests rather than a full re-scan of every noun.
"""
import sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.build import images as IM


def main():
    con = db.open_dict(Path(__file__).resolve().parents[2] / "data/dict.db")
    words = [r["word"] for r in con.execute("SELECT word FROM image ORDER BY word")]
    print(f"re-fetching {len(words)} images at {IM.THUMB}px", flush=True)
    kept, t0 = 0, time.time()
    for i in range(0, len(words), IM.BATCH):
        batch = words[i:i + IM.BATCH]
        try:
            found = IM.fetch_batch(batch)
        except Exception as e:
            print(f"  batch failed: {e}", flush=True)
            continue
        if not found:
            continue
        lic = IM.fetch_licenses(sorted({v["file"] for v in found.values()}))
        todo = [(w, m, *lic.get(m["file"], ("", "")))
                for w, m in found.items()
                if IM.is_free(lic.get(m["file"], ("", ""))[0])]
        with ThreadPoolExecutor(max_workers=4) as ex:
            blobs = list(ex.map(lambda t: IM.download(t[1]["thumb"]), todo))
        rows = []
        for (w, meta, L, artist), got in zip(todo, blobs):
            if not got:
                continue
            data, (ww, hh) = got
            rows.append(("en", IM.norm(w), w, data, ww, hh, L, artist,
                         meta["file"], meta["page"]))
        con.executemany("INSERT OR REPLACE INTO image"
                        "(lang,norm,word,data,w,h,license,artist,file,page)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        kept += len(rows)
        done = i + len(batch)
        print(f"  {done:>4}/{len(words)}  updated={kept}  "
              f"{done/max(time.time()-t0,1):.1f}/s", flush=True)
        time.sleep(0.4)
    n, tot, aw, ah = con.execute(
        "SELECT count(*), sum(length(data)), avg(w), avg(h) FROM image").fetchone()
    print(f"\nDONE {n:,} images, {tot/1048576:.1f} MB, avg {aw:.0f}x{ah:.0f}, "
          f"{tot/n/1024:.1f} KB each", flush=True)


if __name__ == "__main__":
    main()
