"""Generate the public download page.

Sizes and checksums are read from the built artifacts rather than hard-coded,
so the page cannot drift from what is actually being served.
"""
import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "data" / "dist"
PACKS = ROOT / "data" / "packs"

CSS = """
:root{--bg:#fbfaf8;--fg:#1c1a17;--muted:#6b6459;--line:#e2ddd4;--card:#fff;
      --accent:#1a5fb4;--accentfg:#fff;--code:#f2efe9}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
      --bg:#161513;--fg:#ece8e1;--muted:#9c9487;--line:#2e2b27;--card:#1e1d1a;
      --accent:#6aa6ff;--accentfg:#0d1117;--code:#232220}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,"Noto Sans",sans-serif;
     -webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:56px 22px 80px}
header{text-align:center;margin-bottom:48px}
h1{font-size:44px;letter-spacing:-1px;margin:0 0 6px}
.sub{color:var(--muted);font-size:18px;margin:0}
.langs{margin-top:14px;color:var(--muted);font-size:15px}
.langs b{color:var(--fg);font-weight:600}
h2{font-size:14px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);
   margin:44px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:640px){.cards{grid-template-columns:1fr}h1{font-size:34px}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px}
.card h3{margin:0 0 4px;font-size:19px}
.meta{color:var(--muted);font-size:13px;margin:0 0 16px}
a.btn{display:block;text-align:center;background:var(--accent);color:var(--accentfg);
      text-decoration:none;padding:12px 16px;border-radius:8px;font-weight:600;
      transition:opacity .15s}
a.btn:hover{opacity:.88}
ol{margin:16px 0 0;padding-left:20px}
ol li{margin:9px 0}
code{background:var(--code);padding:2px 7px;border-radius:5px;font-size:13.5px;
     font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--code);padding:13px 15px;border-radius:8px;overflow-x:auto;
    font-size:13.5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    margin:10px 0 0}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
      border-radius:8px;padding:16px 18px;margin:18px 0}
.note b{display:block;margin-bottom:5px}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:15px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:13px;text-transform:uppercase;
   letter-spacing:.6px}
td.num{text-align:right;color:var(--muted);white-space:nowrap}
footer{margin-top:56px;padding-top:22px;border-top:1px solid var(--line);
       color:var(--muted);font-size:13.5px}
footer a{color:var(--accent)}
.sha{font-family:ui-monospace,monospace;font-size:11px;color:var(--muted);
     word-break:break-all;margin-top:10px}
"""


def human(n):
    return f"{n/1048576:.0f} MB" if n >= 1048576 else f"{n/1024:.0f} KB"


def build():
    inst = json.loads((DIST / "installers.json").read_text())
    manifest = json.loads((PACKS / "manifest.json").read_text())
    lin = inst["files"]["linux"]
    win = inst["files"]["windows"]
    dic = manifest.get("dictionary", {})
    packs = {p["name"]: p for p in manifest.get("packs", [])}

    rows = ""
    if dic:
        rows += (f"<tr><td><b>Dictionary</b><br><span style='color:var(--muted);"
                 f"font-size:13px'>Downloaded automatically on first launch</span></td>"
                 f"<td class='num'>{human(dic['bytes'])} download<br>"
                 f"{human(dic['uncompressed'])} installed</td></tr>")
    for key, label, detail in (
            ("images", "Illustrations", "Pictures for concrete nouns"),
            ("audio", "Pronunciation audio", "Human recordings, one per word")):
        p = packs.get(key)
        if p:
            rows += (f"<tr><td>{label}<br><span style='color:var(--muted);"
                     f"font-size:13px'>{detail} — optional, from inside the app</span></td>"
                     f"<td class='num'>{human(p['bytes'])}<br>"
                     f"{p['rows']:,} entries</td></tr>")

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Þríauga — offline trilingual dictionary</title>
<meta name="description" content="Offline English, Swedish and Chinese dictionary
with etymology, inflections, example sentences and spaced repetition.">
<style>{CSS}</style>
</head><body><div class="wrap">

<header>
  <h1>Þríauga</h1>
  <p class="sub">An offline dictionary for three languages</p>
  <p class="langs"><b>English</b> &nbsp;·&nbsp; <b>Svenska</b> &nbsp;·&nbsp; <b>中文</b></p>
</header>

<p>Search a word in any of the three languages and see the other two at once,
with etymology, full conjugation and declension tables, example sentences with
translations, and review scheduling based on a forgetting curve. After the
first launch it works with <b>no internet connection at all</b>.</p>

<h2>Download</h2>
<div class="cards">

  <div class="card">
    <h3>Linux</h3>
    <p class="meta">{human(lin['bytes'])} &nbsp;·&nbsp; x86-64 &nbsp;·&nbsp; v{inst['version']}</p>
    <a class="btn" href="download/{lin['file']}">Download for Linux</a>
    <ol>
      <li>Extract:<pre>tar xzf {lin['file']}</pre></li>
      <li>Run the installer:<pre>cd thriauga-{inst['version']}
./install.sh</pre></li>
      <li>Launch it from your applications menu, or type <code>trilex</code></li>
    </ol>
  </div>

  <div class="card">
    <h3>Windows</h3>
    <p class="meta">{human(win['bytes'])} &nbsp;·&nbsp; Windows 10/11 &nbsp;·&nbsp; v{inst['version']}</p>
    <a class="btn" href="download/{win['file']}">Download for Windows</a>
    <ol>
      <li>Unzip the file (right-click → <i>Extract All</i>)</li>
      <li>Double-click <code>install.bat</code></li>
      <li>Launch <b>Thriauga</b> from the Start Menu</li>
    </ol>
  </div>

</div>

<div class="note">
  <b>Python 3.10 or newer is required</b>
  Both installers set up their own isolated environment, but they need Python
  present first. Most Linux systems already have it. On Windows, install it with
  <code>winget install Python.Python.3.12</code> or from
  <a href="https://www.python.org/downloads/">python.org</a> — and tick
  <i>“Add Python to PATH”</i> during setup.
</div>

<h2>What gets downloaded</h2>
<p>The installer itself is tiny. Everything else is fetched afterwards, so you
only download what you actually want.</p>
<table>
  <tr><th>Component</th><th class="num">Size</th></tr>
  {rows}
</table>
<p style="color:var(--muted);font-size:14px;margin-top:14px">
Images and audio are optional and can be added or removed at any time from
<b>File → Add-ons</b> inside the app. The dictionary works fully without them.</p>

<h2>What it does</h2>
<table>
  <tr><td>Search in any of the three languages, including pinyin</td></tr>
  <tr><td>Autocomplete, plus suggestions when a word is misspelled</td></tr>
  <tr><td>Etymology — <i>vatten</i> → Old Norse <i>vatn</i> → Proto-Germanic
      <i>*watōr</i> → PIE <i>*wódr̥</i></td></tr>
  <tr><td>Every tense and inflected form; searching <i>husen</i> finds <i>hus</i></td></tr>
  <tr><td>284,000 example sentences, nearly all with translations</td></tr>
  <tr><td>A wordbook that schedules reviews by what you are closest to forgetting</td></tr>
  <tr><td>Optional sync across devices, using your email address</td></tr>
</table>

<footer>
  <p>Built from <a href="https://www.mdbg.net/chinese/dictionary?page=cc-cedict">CC-CEDICT</a>,
  <a href="https://folkets-lexikon.csc.kth.se/">Folkets lexikon</a>,
  <a href="https://en.wiktionary.org">Wiktionary</a> via
  <a href="https://kaikki.org">kaikki.org</a>,
  <a href="https://tatoeba.org">Tatoeba</a> and
  <a href="https://commons.wikimedia.org">Wikimedia Commons</a> — all under
  free licences, each credited in the application.</p>
  <p>Chinese–Swedish pairs are derived through English; no free direct lexicon
  exists. The app labels those results.</p>
  <div class="sha">
    SHA-256 &nbsp; {lin['file']} &nbsp; {lin['sha256']}<br>
    SHA-256 &nbsp; {win['file']} &nbsp; {win['sha256']}
  </div>
  <p style="margin-top:16px">Built {inst['built']}</p>
</footer>

</div></body></html>"""
    out = DIST / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"  {out}  ({len(html)/1024:.1f} KB)")
    return out


if __name__ == "__main__":
    build()
