# Þríauga

An offline English · Svenska · 中文 dictionary with etymology, inflection
tables, spaced repetition on a forgetting curve, and optional wordbook sync.

> **Þríauga** — Old Norse *þrí-* "three" + *auga* "eye". Odin gave one eye at
> Mímir's well for wisdom; this gives you three, one per tongue.

The dictionary runs **entirely offline**. Only the optional wordbook sync
touches the network, and only once you sign in.

---

## What it does

| | |
|---|---|
| **Search any of three languages** | Type English, Swedish or Chinese and see the other two at once. Pinyin works too — `shuijiao` finds 水饺. |
| **Autocomplete** | Prefix completion across all three languages, ~0.5 ms over 1.8M surface forms. |
| **Fuzzy fallback** | `watter` → water, `beutiful` → beautiful, `vaten` → vatten. ~12 ms. |
| **Etymology** | 550k prose etymologies. *vatten* → Old Norse *vatn* → Proto-Germanic **watōr* → PIE **wódr̥*. |
| **Inflections** | Full English conjugation and Swedish declension. Searching `husen` or `ran` resolves to `hus` / `run`. |
| **Example sentences** | 284,481 sentences across all three languages. The headword is bolded in whatever form it appears. |
| **Pronunciation audio** | Human recordings from Wikimedia Commons, transcoded to ~3 KB MP3 and stored in the database. Click the speaker or press `Ctrl+P`; review cards play automatically on reveal. |
| **Illustrations** | Small freely-licensed images for concrete nouns, with attribution. |
| **Wordbook** | Save words, add notes, export CSV. |
| **Forgetting curve** | FSRS scheduling; the review queue is ordered by *what you're closest to forgetting*, not by due date. |
| **Sync** | Optional, email sign-in, multi-device, last-write-wins. |

---

## Install

**Download page: https://xiaodong.io/thriauga/**

Installers are ~50-70 KB: they carry only the source and an install script.
Dependencies come from PyPI and the 212 MB dictionary is fetched on first run,
so a dictionary update never requires a new installer.

| Platform | Artifact | Steps |
|---|---|---|
| Linux | `thriauga-1.0.0-linux.tar.gz` | `tar xzf`, then `./install.sh` |
| Windows | `thriauga-1.0.0-windows.zip` | unzip, double-click `install.bat` |

Both require **Python 3.10+** already present; each then builds its own
isolated virtualenv, installs a launcher and a menu shortcut.

> **No native Windows `.exe` is provided.** PyInstaller must run on the
> platform it targets, and this project is built on Linux. The Windows archive
> includes `build-exe.bat`, which produces a standalone `Thriauga.exe` when run
> on a Windows machine.

### Building from source

Requires Python 3.10+.

```bash
git clone <this repo> && cd trilex
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
```

### Build the dictionary (once, needs network)

```bash
.venv/bin/python -m trilex.build.all
```

This takes roughly 45 minutes and produces `data/dict.db` (~550 MB).
It streams ~4.5 GB of Wiktionary data **without ever storing it** — each line
is parsed as it arrives, so peak disk use is the output database alone.

Every stage is resumable: re-run after an interruption and it continues from
the last committed byte offset.

```bash
.venv/bin/python -m trilex.build.all --no-etym   # skip the 4.5 GB pass (~3 min)
.venv/bin/python -m trilex.build.forms           # inflection tables (~20 min)
.venv/bin/python -m trilex.build.examples        # dictionary + corpus examples (~4 min)
.venv/bin/python -m trilex.build.senseex         # Wiktionary sense examples (~25 min)
.venv/bin/python -m trilex.build.images 12000    # illustrations (~90 min)
.venv/bin/python -m trilex.build.audio           # pronunciation audio (hours)
.venv/bin/python -m trilex.build.pack            # split media into add-on packs
.venv/bin/python -m trilex.build.trim            # smaller DB for mobile
```

### Optional add-ons

The base install ships **text only** — definitions, translations, etymology,
inflections and example sentences. Images and pronunciation audio are large
and optional, so they are downloaded afterwards from inside the app:

**File → Add-ons (images & audio)…**

| Pack | Size | Contents |
|---|---|---|
| Illustrations | 3 MB | 713 pictures for concrete nouns, with attribution |
| Pronunciation audio | 78 MB | 16,514 human recordings, ~3 KB each |

Each pack is a standalone SQLite file dropped into the data directory. The app
`ATTACH`es whatever it finds, so a pack starts working the moment it finishes
downloading — no restart — and removing one is just deleting the file.
Downloads resume with HTTP Range if interrupted and are verified by SHA-256.

Packs are served from your own server (see `server/`), so no third party is
involved at runtime.

### Run

```bash
./run.sh          # Linux / macOS
run.bat           # Windows
```

The database is looked up in `%APPDATA%\trilex` / `~/.local/share/trilex`,
falling back to `./data/`. Override with `TRILEX_DATA`.

Ship `dict-core.db` (produced by `trilex.build.pack`) renamed to `dict.db` —
it is the media-free build. Alongside it the app looks for `media-images.db`
and `media-audio.db`, which are the optional packs.

### Keys

`Ctrl+L` search · `Ctrl+D` save word · `Ctrl+P` play audio · `Ctrl+1/2/3` tabs ·
`Alt+←/→` history · `Ctrl+Shift+S` sync · `Space` reveal · `1`–`4` grade

---

## The forgetting curve

Each card carries a **stability** *S* (days until recall drops to 90%) and a
**difficulty** *D*. Retention decays as

```
R(t) = (1 + (19/81)·t/S) ^ -0.5
```

chosen so that `R(S) = 0.90` exactly. A word is due when *R* reaches your
target retention (default 90%), and the queue sorts by **lowest R first**.

That last part is the reason for using a memory model at all: if you skip a
week, the words you are genuinely losing come back first, rather than whatever
happened to be scheduled earliest. Grading `Again` collapses stability (62d →
6d in practice) and raises difficulty; mean reversion stops one bad day from
permanently marking a card as hard.

`user.db` (your words) is deliberately a **separate file** from `dict.db`
(the generated dictionary), so rebuilding the dictionary can never touch your
wordbook.

---

## Sync service

Optional. Self-hosted, containerised, deploy-ready.

```
server/
  api/        FastAPI + SQLite — passwordless email auth, wordbook sync
  mail/       Postfix + OpenDKIM, internal network only, no public SMTP
  deploy/     nginx snippet + apply script
```

```bash
cd server
cp .env.example .env          # set SECRET_KEY: openssl rand -base64 48
docker compose up -d --build
```

Put it behind an existing TLS vhost with `deploy/apply-nginx.sh`, which backs
up the config, validates with `nginx -t`, and rolls back automatically if the
config is rejected.

### API

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/v1/packs` | List available media packs (public). |
| `GET`  | `/v1/packs/{name}` | Download a pack; supports HTTP Range for resume. |
| `POST` | `/v1/auth/request` | Email a 6-digit code. Always 204 — never reveals whether an account exists. |
| `POST` | `/v1/auth/verify` | Code → bearer token. |
| `GET`  | `/v1/me` | Account summary. |
| `POST` | `/v1/sync` | Push local changes, pull everything newer, one round trip. |
| `GET`  | `/v1/sync?since=N` | Pull only. |
| `POST` | `/v1/auth/logout` | Revoke this device's token. |
| `DELETE` | `/v1/account` | Full erasure. |

**Security posture.** Login codes and bearer tokens are stored only as HMAC
digests, so a database copy yields neither. Codes expire in 10 minutes and
burn after 5 wrong attempts. Rate limits apply per email and per IP. The
mail container publishes **no ports** — it is reachable only on the private
compose network, so the host runs no public SMTP listener and cannot be used
as an open relay.

**Sync model.** Every card has a client-generated `uid`, an `updated_at`
timestamp, and a `deleted` tombstone. Conflicts resolve last-write-wins.
Deletions are tombstones, never row removals — otherwise deleting on one
device would be undone by the next device to push. Locally, an explicit
`dirty` flag marks rows needing upload; a timestamp high-water mark cannot do
this job, because an edit made in the same second as a sync compares equal and
is silently never sent.

### Mail delivery

`MAIL_BACKEND=log` writes codes to `docker logs thriauga-sync` — auth works
immediately with nothing to configure. For real email set `MAIL_BACKEND=smtp`
and add these DNS records, or mail will land in spam:

```
thriauga._domainkey.<domain>  TXT  v=DKIM1; ...   (printed by the mail container on first boot)
<domain>                      TXT  v=spf1 ip4:<your-ip> -all
```

Note that a generic reverse-DNS name (`<ip>.<provider>.com`) hurts
deliverability regardless of DKIM, and only your hosting provider can change
it. If Gmail or Outlook reject your mail, set `RELAY_HOST` to send through a
transactional provider instead.

---

## Data sources & licences

| Source | Content | Licence |
|---|---|---|
| [CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cc-cedict) | 124,880 Chinese↔English entries | CC BY-SA 4.0 |
| [Folkets lexikon](https://folkets-lexikon.csc.kth.se/) | 95,443 Swedish↔English entries | CC BY-SA 2.5 |
| [Wiktionary](https://en.wiktionary.org) via [wiktextract](https://kaikki.org) | 1.6M entries, 550k etymologies, 1.1M inflected forms | CC BY-SA 4.0 / GFDL |
| [Tatoeba](https://tatoeba.org) | 118,692 aligned sentence pairs (English↔Swedish, English↔Chinese) | CC BY 2.0 FR |
| [Wikipedia / Wikimedia Commons](https://commons.wikimedia.org) | illustrations, free licences only | per-image, shown in the UI |

**A real limitation:** no free Chinese↔Swedish lexicon exists, so those pairs
are derived **through English**. The UI labels them *"Matched through English"*
and shows which English sense bridged them. Direct English↔Swedish and
English↔Chinese pairs come straight from the source dictionaries.

---

## Pronunciation audio

Human recordings from Wikimedia Commons — the same files Wiktionary plays —
transcoded with ffmpeg to mono 24 kbps MP3, roughly 2-4 KB per word, stored
as blobs in `dict.db`. Playback reads straight from memory, so nothing is
written to disk and no network call is made.

**MP3, not Opus.** Both land at 4-5 KB for a one-second clip, but Windows
Media Foundation has no native Opus decoder — Opus would have played on Linux
and failed silently on Windows.

**ffmpeg writes to a temp file, not a pipe.** It can only emit the Xing header
carrying duration and channel count if it can seek back to the start. Piped
output produced clips that players reported as `Duration: N/A`.

**The build needs a policy-compliant User-Agent.** Wikimedia throttles generic
agents hard: measured 4 of 8 requests returning HTTP 429 without a contact URL
and address in the header, and 8 of 8 succeeding with one. If you fork this,
put *your* contact details in `UA` in `build/audio.py` — it is a condition of
their service, not a formality.

English recordings prefer a US accent, then UK; Chinese prefers Mandarin over
Cantonese. The accent is shown next to the speaker icon.

**Be careful with concurrency when rebuilding.** Wikimedia rate-limits by IP
and will impose a temporary block if pushed. Measured here: 4 workers held
~96% success; 10 workers collapsed to 8% and earned a cooldown during which
even single sequential requests returned 429. The fetcher is resumable, so the
correct response to a block is to stop and continue later, not to retry harder.

---

## Example sentences

284,481 sentences from three sources, ranked so the most useful appears first:

| Source | Count | Translated |
|---|---|---|
| Tatoeba corpus | 199,356 | 100% |
| Wiktionary sense examples | 34,563 | 29% |
| Folkets dictionary examples | 24,340 | 99% |
| Wiktionary quotations | 19,755 | 3% |
| Folkets idioms | 6,467 | 99% |

**Ranking is by usefulness, not by source.** Hand-written dictionary examples
come first, then translated corpus sentences, then untranslated usage
examples, with literary quotations last — they are often archaic and long.
Within a band, shorter sentences win.

Coverage of headwords that have at least one example:

| | headwords | share |
|---|---|---|
| English | 20,428 | 58% |
| Svenska | 18,028 | 24% |
| 中文 | 26,643 | — |

Sentences are filed under the **lemma**, using the inflection table, so a
sentence containing *vattnet* is found under *vatten* and one containing *ran*
under *run*.

Two asymmetries are worth knowing about. English Wiktionary examples carry
**no translation** — an English entry explains itself to English readers — so
they rank below every translated example and appear only when nothing better
exists. And English has both Swedish- and Chinese-translated corpus sentences;
a Latin-script headword leads with Swedish, a CJK one with Chinese.

---

## Architecture

```
trilex/
  norm.py         normalisation, pinyin tone placement
  db.py           schema, migrations
  search.py       lookup, autocomplete, fuzzy, inflections, images
  forgetting.py   FSRS forgetting curve (pure functions, no I/O)
  srs.py          wordbook + scheduling
  sync.py         sync client
  render.py       result → HTML (testable without Qt)
  gui.py          PySide6 UI
  build/          fetch · core · kaikki · forms · examples · senseex · images · merge · trim
```

`dict.db` is plain SQLite with no extensions, so the same file works unchanged
from any language — which is what an iOS port would read.
