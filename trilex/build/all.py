"""One-shot build of the dictionary database.

    python -m trilex.build.all              full build (needs network, ~30 min)
    python -m trilex.build.all --no-etym    skip the 4.5 GB etymology pass

Only this step touches the network. Once dict.db exists the app never does.
Every stage is resumable: re-running picks up where an interrupted run stopped.
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.build import fetch, core, kaikki, merge


def main():
    t0 = time.time()
    skip_etym = "--no-etym" in sys.argv
    print("[1/4] fetching bilingual sources")
    fetch.fetch()

    print("[2/4] building CC-CEDICT + Folkets -> core.db")
    p = core.DATA / "core.db"
    if p.exists():
        p.unlink()
    con = db.open_dict(p)
    sink = core.Sink(con)
    core.build_cedict(sink)
    core.build_folkets(sink, "folkets_en_sv.xml", "en", "sv")
    core.build_folkets(sink, "folkets_sv_en.xml", "sv", "en")
    con.commit()
    con.executescript(db.DICT_INDEXES)
    con.commit()
    con.close()

    if skip_etym:
        print("[3/4] skipping etymology (--no-etym)")
    else:
        print("[3/4] streaming Wiktionary etymologies (~4.5 GB, not stored)")
        dcon = db.open_dict(core.DATA / "dict.db")
        for lg in ("sv", "en", "zh"):
            kaikki.build(lg, dcon)
        dcon.close()

    print("[4/4] merging and indexing")
    merge.main(vacuum="--vacuum" in sys.argv)
    print(f"\nBuild complete in {(time.time()-t0)/60:.1f} min.")
    print(f"Database: {core.DATA / 'dict.db'}")


if __name__ == "__main__":
    main()
