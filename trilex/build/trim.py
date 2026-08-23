"""Produce a mobile-sized database.

The desktop build keeps every Wiktionary headword (~1.5M), most of which have
no translation attached. A phone only needs etymology for words the dictionary
can actually translate, which removes the bulk of the weight.

    python -m trilex.build.trim          -> data/dict-mobile.db
"""
import os, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db

DATA = Path(os.environ.get("TRILEX_DATA") or
            Path(__file__).resolve().parents[2] / "data")


def main():
    src, dst = DATA / "dict.db", DATA / "dict-mobile.db"
    if not src.exists():
        sys.exit(f"missing {src} - run the desktop build first")
    dst.unlink(missing_ok=True)
    for suf in ("-wal", "-shm"):
        Path(str(dst) + suf).unlink(missing_ok=True)
    print(f"copying {src.stat().st_size/1048576:.0f} MB ...", flush=True)
    shutil.copy2(src, dst)

    con = db.connect(dst, wal=False)
    con.execute("PRAGMA journal_mode=DELETE")
    before = con.execute("SELECT count(*) c FROM wik").fetchone()["c"]

    # Keep a Wiktionary row only when the headword is reachable from the
    # bilingual core - an etymology you can never navigate to is dead weight.
    con.execute("""DELETE FROM wik WHERE id NOT IN (
                     SELECT w.id FROM wik w
                     JOIN vocab v ON v.norm = w.norm AND v.lang = w.lang
                     WHERE v.core = 1)""")
    con.execute("DELETE FROM etym WHERE id NOT IN (SELECT etym_id FROM wik WHERE etym_id IS NOT NULL)")
    # Wiktionary-only vocab entries no longer resolve to anything.
    con.execute("DELETE FROM vocab WHERE core = 0")
    after = con.execute("SELECT count(*) c FROM wik").fetchone()["c"]
    con.commit()
    print(f"wik {before:,} -> {after:,}", flush=True)
    print("vacuuming ...", flush=True)
    con.execute("VACUUM")
    con.execute("PRAGMA optimize")
    con.close()
    print(f"{dst.name}: {dst.stat().st_size/1048576:.0f} MB")


if __name__ == "__main__":
    main()
