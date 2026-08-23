"""Render a search Result to HTML for QTextBrowser.

Kept out of gui.py so the output can be checked without starting Qt.
Internal links use the q: scheme; the window turns those into new searches.
"""
import re
from html import escape as _e

from .search import LANG_NAME, Result

CJK_STACK = ('"Noto Sans CJK SC","Microsoft YaHei","PingFang SC","Source Han Sans SC",'
             '"WenQuanYi Micro Hei","Droid Sans Fallback"')
UI_STACK = f'"Segoe UI","Inter","Noto Sans","DejaVu Sans",system-ui,{CJK_STACK},sans-serif'


def css(t: dict) -> str:
    return f"""
    body   {{ background:{t['bg']}; color:{t['fg']}; font-family:{UI_STACK};
              font-size:{t['size']}pt; line-height:1.62; margin:0;
              padding:18px 22px 26px 22px; }}
    a      {{ color:{t['accent']}; text-decoration:none; }}
    .hw    {{ font-size:{t['size']+15}pt; font-weight:600; letter-spacing:-0.6px; }}
    .zh .hw{{ font-family:{CJK_STACK}; font-size:{t['size']+21}pt; }}
    .rd    {{ color:{t['accent']}; font-size:{t['size']+1}pt; }}
    .badge {{ color:{t['faint']}; font-size:{t['size']-2}pt; letter-spacing:0.8px; }}
    .pos   {{ color:{t['muted']}; font-style:italic; font-size:{t['size']-1}pt; }}
    /* The only ornament: a small lozenge. Geometric, not a rune. */
    .tick  {{ color:{t['accent']}; font-size:{t['size']-3}pt; }}
    h2     {{ font-size:{t['size']-1}pt; text-transform:uppercase;
              letter-spacing:1.6px; color:{t['muted']}; font-weight:700;
              margin:26px 0 10px 0; border-bottom:1px solid {t['line_soft']};
              padding-bottom:6px; }}
    .defs  {{ margin:12px 0 0 0; }}
    .def   {{ margin:4px 0 4px 22px; text-indent:-22px; }}
    .num   {{ color:{t['faint']}; margin-right:8px; }}
    .row   {{ margin:8px 0; }}
    .w     {{ font-weight:600; font-size:{t['size']+2}pt; }}
    .zhw   {{ font-family:{CJK_STACK}; font-weight:600; font-size:{t['size']+5}pt; }}
    .py    {{ color:{t['accent']}; font-size:{t['size']}pt; }}
    .gl    {{ color:{t['muted']}; }}
    .via   {{ color:{t['faint']}; font-size:{t['size']-2}pt; }}
    .ety   {{ margin:10px 0 16px 0; padding-left:13px;
              border-left:2px solid {t['line']}; }}
    .etyh  {{ font-weight:600; }}
    .etyt  {{ color:{t['fg']}; }}
    .note  {{ color:{t['muted']}; font-style:italic; }}
    .warn  {{ background:{t['chip']}; padding:11px 14px; border-radius:8px;
              color:{t['muted']}; margin:12px 0; }}
    .big   {{ font-size:{t['size']+28}pt; font-weight:600; letter-spacing:-1px; }}
    .zh .big, .bigzh {{ font-family:{CJK_STACK}; font-size:{t['size']+36}pt;
                        font-weight:600; }}
    .ctr   {{ text-align:center; padding:30px 12px; }}
    .credit{{ color:{t['faint']}; font-size:{t['size']-3}pt; }}
    .spk   {{ color:{t['accent']}; font-size:{t['size']+9}pt;
              text-decoration:none; }}
    .acc   {{ color:{t['faint']}; font-size:{t['size']-2}pt; letter-spacing:0.8px; }}
    .infl  {{ margin:3px 0; }}
    .il    {{ color:{t['muted']}; font-size:{t['size']-1}pt; }}
    .iv    {{ font-weight:600; }}
    .from  {{ background:{t['chip']}; padding:8px 12px; border-radius:8px;
              color:{t['muted']}; margin:0 0 12px 0; }}
    .ex    {{ margin:10px 0 10px 0; padding-left:13px;
              border-left:2px solid {t['line']}; }}
    .ext   {{ color:{t['muted']}; font-style:italic; }}
    """


def _link(word, lang):
    return f'<a href="q:{_e(word)}">{_e(word)}</a>'


def _wordspan(word, lang):
    cls = "zhw" if lang == "zh" else "w"
    return f'<span class="{cls}"><a href="q:{_e(word)}">{_e(word)}</a></span>'


def _entry_head(e, audio=None):
    bits = [f'<span class="hw">{_e(e.word)}</span>']
    if audio:
        # play: links are handled by the window, same as q: links.
        bits.append(f'&nbsp;<a class="spk" href="play:{_e(e.lang)}:{_e(e.word)}"'
                    f' title="Play pronunciation">&#128266;</a>&nbsp;')
        if audio.get("accent"):
            bits.append(f'<span class="acc">{_e(audio["accent"])}</span>')
    if e.reading:
        bits.append(f'&nbsp;<span class="rd">{_e(e.reading)}</span>')
    if e.traditional:
        bits.append(f'&nbsp;&nbsp;<span class="badge">&middot;&nbsp; '
                    f'trad {_e(e.traditional)}</span>')
    if e.pos:
        bits.append(f'&nbsp;<span class="pos">{_e(e.pos)}</span>')
    bits.append(f'&nbsp;&nbsp;<span class="badge">&middot;&nbsp; '
                f'{LANG_NAME[e.lang]}</span>')
    return f'<div class="{e.lang}">' + "".join(bits) + "</div>"


def _section(h, title, items, note=None):
    h.append(f"<h2><span class=tick>&#9670;</span>&nbsp; {title}</h2>")
    if note:
        h.append(f'<div class="note" style="margin-bottom:6px">{note}</div>')
    for word, lang, reading, glosses, via in items:
        line = [f'<div class="row">{_wordspan(word, lang)}']
        if reading:
            line.append(f'&nbsp;<span class="py">{_e(reading)}</span>')
        if glosses:
            line.append(f'&nbsp;&nbsp;<span class="gl">{_e("; ".join(glosses[:3]))}</span>')
        if via:
            line.append(f'&nbsp;&nbsp;<span class="via">&larr; {_e(", ".join(via[:2]))}</span>')
        line.append("</div>")
        h.append("".join(line))


def _head_with_image(res, t) -> str:
    """Headword and illustration side by side.

    Qt's rich-text engine does not reflow text around a CSS float, which left
    the headword stranded under the picture. A two-cell table is the layout
    primitive it does honour.
    """
    primary = res.entries[0]
    head = _entry_head(primary, res.audio)
    im = res.image
    if not im:
        return head
    credit = " · ".join(x for x in (im.get("artist"), im.get("license")) if x)
    img = (f'<img src="img:en:{_e(im["word"])}" '
           f'width="{im["w"]}" height="{im["h"]}">'
           f'<div class="credit">{_e(credit[:60])}</div>')
    return (f'<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td valign="top">{head}</td>'
            f'<td valign="top" align="right" width="{im["w"] + 10}">{img}</td>'
            f'</tr></table>')


def _highlight(sentence: str, forms) -> str:
    """Bold the headword (in any of its inflected forms) inside a sentence."""
    out = _e(sentence)
    for f in sorted(forms, key=len, reverse=True):
        if not f or len(f) < 2:
            continue
        pat = re.compile(rf"(?<!\w)({re.escape(_e(f))})(?!\w)", re.IGNORECASE)
        new, n = pat.subn(r"<b>\1</b>", out, count=2)
        if n:
            return new
    return out


def render(res: Result, theme: dict) -> str:
    t = theme
    h = [f"<style>{css(t)}</style>"]

    if not res.found:
        h.append(f'<div class="warn"><b>{_e(res.query)}</b> is not in the dictionary.</div>')
        if res.did_you_mean:
            h.append("<h2><span class=tick>&#9670;</span>&nbsp; Did you mean</h2>")
            for w, lg in res.did_you_mean:
                cls = "zhw" if lg == "zh" else "w"
                h.append(f'<div class="row"><span class="{cls}">{_link(w, lg)}</span>'
                         f'&nbsp;<span class="badge">{LANG_NAME[lg]}</span></div>')
        else:
            h.append('<div class="note">No similar words found either.</div>')
        return "".join(h)

    primary = res.entries[0]
    if res.resolved_from:
        h.append(f'<div class="from">{_e(res.resolved_from)} is a form of '
                 f'<b>{_e(primary.word)}</b></div>')
    h.append(_head_with_image(res, t))
    if res.also_form_of:
        lg, lemma, tags = res.also_form_of
        h.append(f'<div class="note">also the {_e(tags)} of '
                 f'{_link(lemma, lg)}</div>')

    # Homographs: 'water' is a noun and a verb, and Folkets stores them apart.
    others = [e for e in res.entries[1:] if e.word == primary.word]
    pos_list = [e.pos for e in res.entries if e.word == primary.word and e.pos]
    seen_pos, uniq_pos = set(), []
    for p_ in pos_list:
        if p_ not in seen_pos:
            seen_pos.add(p_)
            uniq_pos.append(p_)
    if len(uniq_pos) > 1:
        h.append(f'<div class="note">also: {_e(" · ".join(uniq_pos[1:]))}</div>')

    # --- Definition: what the word means in its own language --------------
    own = []
    for e in res.entries[:4]:
        for sn in e.senses:
            if sn.lang == e.lang and sn.gloss not in own:
                own.append(sn.gloss)
    own += [g for g in res.definitions.get(primary.lang, []) if g not in own]
    if own:
        h.append("<h2><span class=tick>&#9670;</span>&nbsp; Definition</h2>")
        h.append('<div class="defs">')
        for i, g in enumerate(own[:10], 1):
            h.append(f'<div class="def"><span class="num">{i}.</span>&nbsp;{_e(g)}</div>')
        h.append("</div>")

    infl = res.inflections.get(primary.lang) or []
    if infl:
        h.append("<h2><span class=tick>&#9670;</span>&nbsp; Forms</h2>")
        for i in infl:
            h.append(f'<div class="infl"><span class="il">{_e(i.label)}</span>'
                     f'&nbsp;&nbsp;<span class="iv">{_link(i.form, primary.lang)}</span></div>')

    # --- Translations, one section per other language ---------------------
    for lg in ("sv", "zh", "en"):
        if lg == primary.lang:
            continue
        items, seen = [], set()
        # Translations recorded on the entry itself are authoritative.
        for g in res.direct.get(lg, []):
            if g not in seen:
                seen.add(g)
                items.append((g, lg, None, [], None))
        rels = res.related.get(lg) or []
        indirect = primary.lang != "en" and lg != "en"
        for r in rels:
            if r.word in seen or len(items) >= 14:
                continue
            seen.add(r.word)
            items.append((r.word, lg, r.reading, r.glosses,
                          r.via if indirect else None))
        if not items:
            continue
        note = None
        if indirect and not res.direct.get(lg):
            note = "Matched through English — no direct Chinese–Swedish source exists."
        _section(h, LANG_NAME[lg], items, note)

    # Related words in the query's own language (synonyms reached via English).
    same = [r for r in (res.related.get(primary.lang) or []) if r.score > 0.25][:8]
    if same:
        _section(h, f"Related {LANG_NAME[primary.lang]}",
                 [(r.word, primary.lang, r.reading, r.glosses, None) for r in same])

    for lg, exs in res.examples.items():
        if not exs:
            continue
        label = "Examples" if lg == primary.lang else f"Examples ({LANG_NAME[lg]})"
        h.append(f"<h2><span class=tick>&#9670;</span>&nbsp; {label}</h2>")
        forms = {primary.word} | {i.form for i in
                                  (res.inflections.get(primary.lang) or [])}
        if lg != primary.lang:
            forms = {w for w in (res.direct.get(lg) or [])[:2]}
        for ex in exs:
            h.append('<div class="ex">')
            h.append(f'<div class="exs">{_highlight(ex.text, forms)}</div>')
            if ex.trans:
                h.append(f'<div class="ext">{_e(ex.trans)}</div>')
            h.append("</div>")

    ety = [e for e in res.etymologies if e.text]
    if ety:
        h.append("<h2><span class=tick>&#9670;</span>&nbsp; Etymology</h2>")
        for e in ety:
            cls = "zhw" if e.lang == "zh" else ""
            pos = f'&nbsp;<span class="pos">{_e(e.pos)}</span>' if e.pos else ""
            ipa = f'&nbsp;<span class="rd">{_e(e.ipa)}</span>' if e.ipa else ""
            h.append(f'<div class="ety"><div class="etyh"><span class="{cls}">'
                     f'{_e(e.word)}</span>&nbsp;<span class="badge">{LANG_NAME[e.lang]}</span>'
                     f'{pos}{ipa}</div>'
                     f'<div class="etyt">{_e(e.text)}</div></div>')
    else:
        h.append("<h2><span class=tick>&#9670;</span>&nbsp; Etymology</h2>"
                 '<div class="note">No etymology recorded for this word.</div>')
    return "".join(h)


def render_card(card, res, theme, revealed: bool) -> str:
    """Flashcard face. Front is the word alone; back adds meaning + etymology."""
    t = theme
    h = [f"<style>{css(t)}</style>"]
    cls = "bigzh" if card.lang == "zh" else "big"
    h.append(f'<div class="ctr"><span class="{cls}">{_e(card.word)}</span>')
    h.append(f'<div><span class="badge">{LANG_NAME[card.lang]}</span></div></div>')
    if not revealed:
        h.append('<div class="ctr note">Space to reveal</div>')
        return "".join(h)
    if res is not None and res.found:
        body = render(res, theme)
        h.append(body.split("</style>", 1)[1])
    if card.note:
        h.append(f'<h2><span class=tick>&#9670;</span>&nbsp; Your note</h2><div class="note">{_e(card.note)}</div>')
    return "".join(h)
