"""Trilex - offline English / Svenska / 中文 dictionary. PySide6 GUI."""
from __future__ import annotations

import csv, faulthandler, getpass, os, sys, time, traceback
from datetime import date
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject,
                            QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon,
                           QImage, QKeySequence, QPainter, QPixmap, QShortcut,
                           QTextDocument)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSizePolicy, QSplitter,
                               QStyledItemDelegate, QSystemTrayIcon, QTabWidget,
                               QTableWidget, QTableWidgetItem, QTextBrowser,
                               QVBoxLayout, QWidget, QInputDialog,
                               QProgressBar, QGroupBox)

from . import db, packs, render, srs, sync, theme

# QtMultimedia depends on a platform backend (GStreamer on Linux, Media
# Foundation on Windows). If it is missing the app must still run - just
# without sound - so the import is optional.
try:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    AUDIO_OK = True
except Exception:                                        # pragma: no cover
    AUDIO_OK = False
from .search import LANG_NAME, Dictionary

APP = "Trilex"
CJK_FONTS = ["Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC",
             "Source Han Sans SC", "WenQuanYi Micro Hei", "Droid Sans Fallback"]
# One running copy per user: a second launch just brings the first forward.
INSTANCE_KEY = f"thriauga-{getpass.getuser()}"


class _SingleInstance(QObject):
    """Local socket that a second launch knocks on instead of starting up.

    The server greets with its pid so that, on Windows, the newcomer can grant
    it the right to take the foreground (AllowSetForegroundWindow); without
    that the restored window only flashes on the taskbar.
    """

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        QLocalServer.removeServer(INSTANCE_KEY)          # stale unix socket
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept)
        self.server.listen(INSTANCE_KEY)

    def _accept(self):
        sock = self.server.nextPendingConnection()
        if not sock:
            return
        sock.write(f"{os.getpid()}\n".encode())
        sock.flush()
        sock.readyRead.connect(lambda: self._read(sock))
        sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock):
        if b"show" in bytes(sock.readAll()):
            self.window.bring_to_front()

    @staticmethod
    def already_running() -> bool:
        """Hand over to a running instance, if any."""
        sock = QLocalSocket()
        sock.connectToServer(INSTANCE_KEY)
        if not sock.waitForConnected(400):
            return False
        if sock.waitForReadyRead(400):
            try:
                pid = int(bytes(sock.readLine()).strip() or 0)
                if pid and sys.platform == "win32":
                    import ctypes
                    ctypes.windll.user32.AllowSetForegroundWindow(pid)
            except Exception:
                pass
        sock.write(b"show\n")
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return True


def theme_from(pal, size=10):
    """Render palette, following the OS light/dark preference."""
    dark = pal.window().color().lightness() < 128
    t = theme.palette(dark)
    t["size"] = size
    t["border"] = t["line"]          # alias kept so render.py reads naturally
    t["dark"] = dark
    return t


def app_icon() -> QIcon:
    """Wordmark: the thorn the app is named for. A letter, not an ornament."""
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.PALETTES["light"]["accent"]))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 8, 240, 240, 52, 52)
    p.setPen(QColor("#f7f5f0"))
    f = QFont("Inter", 132, QFont.DemiBold)
    p.setFont(f)
    p.drawText(pm.rect().adjusted(0, -6, 0, -6), Qt.AlignCenter, "\u00de")
    p.end()
    return QIcon(pm)


class AudioPlayer:
    """Plays pronunciation clips straight out of the database.

    The clip is handed to QMediaPlayer as an in-memory device rather than a
    temporary file, so nothing is written to disk. The QBuffer must stay
    referenced for as long as it plays, or it is collected mid-playback and
    the sound cuts off.
    """

    def __init__(self, dic):
        self.dic = dic
        self.available = AUDIO_OK
        self._buf = None
        if AUDIO_OK:
            self._out = QAudioOutput()
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._out)
            self._out.setVolume(0.9)

    def play(self, lang, word) -> bool:
        if not self.available:
            return False
        try:
            blob = self.dic.audio_blob(lang, word)
        except Exception:
            blob = None
        if not blob:
            return False
        try:
            self._player.stop()
            buf = QBuffer()
            buf.setData(QByteArray(bytes(blob)))
            buf.open(QIODevice.ReadOnly)
            self._buf = buf
            self._player.setSourceDevice(buf, QUrl("clip.mp3"))
            self._player.play()
            return True
        except Exception:
            return False


class DictBrowser(QTextBrowser):
    """QTextBrowser that resolves img:<lang>:<word> against the database.

    Illustrations live as WebP blobs in an attached media pack rather than as
    files, so they are served here on demand instead of written to disk.
    """

    def __init__(self, dic, parent=None):
        super().__init__(parent)
        self.dic = dic
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.NoFrame)

    def loadResource(self, kind, url: QUrl):
        if kind == QTextDocument.ImageResource:
            name = url.toString()
            if name.startswith("img:"):
                try:
                    lang, word = name[4:].split(":", 1)
                    blob = self.dic.image_blob(lang, word)
                except Exception:
                    blob = None
                if blob:
                    img = QImage()
                    img.loadFromData(bytes(blob))
                    return img
        return super().loadResource(kind, url)


# --------------------------------------------------------------- autocomplete
class SuggestModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items: list[tuple[str, str]] = []

    def set_items(self, items):
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, ix, role=Qt.DisplayRole):
        if not ix.isValid():
            return None
        word, lang = self.items[ix.row()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            return word
        if role == Qt.UserRole:
            return lang
        return None


class SuggestDelegate(QStyledItemDelegate):
    """Word on the left, language badge right-aligned."""

    def paint(self, painter, opt, ix):
        self.initStyleOption(opt, ix)
        text, lang = opt.text, ix.data(Qt.UserRole) or ""
        opt.text = ""
        opt.widget.style().drawControl(
            opt.widget.style().ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        r = opt.rect.adjusted(9, 0, -9, 0)
        painter.save()
        if opt.state & opt.state.State_Selected:
            painter.setPen(opt.palette.highlightedText().color())
        else:
            painter.setPen(opt.palette.text().color())
        f = QFont(painter.font())
        f.setPointSizeF(f.pointSizeF() + 1.5)
        for fam in CJK_FONTS:
            f.insertSubstitution(f.family(), fam)
        painter.setFont(f)
        painter.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, text)
        c = painter.pen().color()
        c.setAlpha(130)
        painter.setPen(c)
        f.setPointSizeF(f.pointSizeF() - 3)
        painter.setFont(f)
        painter.drawText(r, Qt.AlignVCenter | Qt.AlignRight, LANG_NAME.get(lang, lang))
        painter.restore()

    def sizeHint(self, opt, ix):
        s = super().sizeHint(opt, ix)
        return QSize(s.width(), max(s.height(), 26))


class SearchBox(QLineEdit):
    """Line edit whose Down key opens the suggestion popup."""
    submitted = Signal(str)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.submitted.emit(self.text())
            return
        super().keyPressEvent(ev)


# ------------------------------------------------------------------ dictionary
class SearchPage(QWidget):
    saved = Signal()

    def __init__(self, dic: Dictionary, book: srs.Wordbook, theme_fn, player=None):
        super().__init__()
        self.dic, self.book, self.theme_fn = dic, book, theme_fn
        self.player = player or AudioPlayer(dic)
        self.result = None
        self.history: list[str] = []
        self.hpos = -1

        self.box = SearchBox()
        self.box.setPlaceholderText(
            "Search English, Svenska or 中文  —  pinyin works too (e.g. shuijiao)")
        self.box.setClearButtonEnabled(True)
        f = self.box.font()
        f.setPointSizeF(f.pointSizeF() + 4)
        self.box.setFont(f)
        self.box.setMinimumHeight(38)

        from PySide6.QtWidgets import QCompleter
        self.model = SuggestModel()
        self.comp = QCompleter(self.model, self)
        self.comp.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.comp.setMaxVisibleItems(12)
        self.comp.popup().setItemDelegate(SuggestDelegate(self))
        self.box.setCompleter(self.comp)
        self.comp.activated[str].connect(self.search)

        self.debounce = QTimer(self, singleShot=True, interval=70)
        self.debounce.timeout.connect(self._refresh_suggestions)
        self.box.textEdited.connect(lambda _: self.debounce.start())
        self.box.submitted.connect(self.search)

        self.view = DictBrowser(dic)
        self.view.anchorClicked.connect(self._anchor)

        self.back_b = QPushButton("←")
        self.fwd_b = QPushButton("→")
        for b in (self.back_b, self.fwd_b):
            b.setFixedWidth(38)
            b.setMinimumHeight(38)
            b.setProperty("nav", True)
            b.setToolTip("History")
        self.back_b.clicked.connect(lambda: self._go(-1))
        self.fwd_b.clicked.connect(lambda: self._go(+1))
        self.save_b = QPushButton("Add to wordbook")
        self.save_b.setToolTip("Ctrl+D")
        self.save_b.setMinimumHeight(38)
        self.save_b.clicked.connect(self.save_current)
        self.save_b.setEnabled(False)

        top = QHBoxLayout()
        top.setSpacing(6)
        for w in (self.back_b, self.fwd_b, self.box, self.save_b):
            top.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 6)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        self.welcome()

    def _anchor(self, url):
        s = url.toString()
        if s.startswith("q:"):
            self.search(s[2:])
        elif s.startswith("play:"):
            lang, _, word = s[5:].partition(":")
            self.player.play(lang, word)

    def play_current(self):
        r = self.result
        if r and r.found:
            e = r.entries[0]
            if not self.player.play(e.lang, e.word):
                self.window().statusBar().showMessage(
                    f"No pronunciation recorded for “{e.word}”", 4000)

    # -- suggestions
    def _refresh_suggestions(self):
        txt = self.box.text().strip()
        if not txt:
            self.model.set_items([])
            return
        try:
            items = self.dic.suggest(txt, limit=12)
        except Exception:
            items = []
        self.model.set_items(items)
        if items:
            self.comp.complete()

    # -- navigation
    def _go(self, d):
        n = self.hpos + d
        if 0 <= n < len(self.history):
            self.hpos = n
            self._show(self.history[n])
        self._sync_nav()

    def _sync_nav(self):
        self.back_b.setEnabled(self.hpos > 0)
        self.fwd_b.setEnabled(self.hpos < len(self.history) - 1)

    def search(self, text):
        text = (text or "").strip()
        if not text:
            return
        if not self.history or self.history[self.hpos] != text:
            self.history = self.history[:self.hpos + 1] + [text]
            self.hpos = len(self.history) - 1
        self._show(text)
        self._sync_nav()

    def _show(self, text):
        self.box.blockSignals(True)
        self.box.setText(text)
        self.box.blockSignals(False)
        self.result = self.dic.lookup(text)
        self.view.setHtml(render.render(self.result, self.theme_fn()))
        self.view.verticalScrollBar().setValue(0)
        self.save_b.setEnabled(self.result.found)
        self._sync_save()

    def _sync_save(self):
        if not (self.result and self.result.found):
            return
        e = self.result.entries[0]
        if self.book.has(e.lang, e.word):
            self.save_b.setText("In wordbook")
            self.save_b.setEnabled(False)
        else:
            self.save_b.setText("Add to wordbook")
            self.save_b.setEnabled(True)

    def save_current(self):
        if not (self.result and self.result.found):
            return
        e = self.result.entries[0]
        snap = {
            "word": e.word, "lang": e.lang, "pos": e.pos,
            "reading": e.reading,
            "senses": [(s.lang, s.gloss) for s in e.senses][:8],
            "related": {lg: [(r.word, r.reading, r.glosses[:2])
                             for r in rs[:6]]
                        for lg, rs in self.result.related.items()},
            "etymology": [(x.lang, x.word, x.text)
                          for x in self.result.etymologies if x.text][:3],
        }
        self.book.add(e.lang, e.word, snap)
        self._sync_save()
        self.saved.emit()

    def welcome(self):
        t = self.theme_fn()
        self.view.setHtml(f"""<style>{render.css(t)}</style>
          <div style="padding:26px 6px">
            <div class="hw">Trilex</div>
            <div class="note">English &nbsp;·&nbsp; Svenska &nbsp;·&nbsp;
              <span style="font-family:{render.CJK_STACK}">中文</span>
              &nbsp;—&nbsp; fully offline</div>
            <h2>Try</h2>
            <div class="row"><a href="q:water">water</a>
              <span class="gl">an English word — see Swedish and Chinese at once</span></div>
            <div class="row"><a href="q:vatten">vatten</a>
              <span class="gl">Swedish</span></div>
            <div class="row"><span class="zhw"><a href="q:森林">森林</a></span>
              <span class="gl">Chinese characters</span></div>
            <div class="row"><a href="q:shuijiao">shuijiao</a>
              <span class="gl">pinyin, no tones needed</span></div>
            <div class="row"><a href="q:watter">watter</a>
              <span class="gl">a misspelling — suggestions appear</span></div>
            <h2>Keys</h2>
            <div class="row note">Ctrl+L focus search &nbsp;·&nbsp; Ctrl+D save word
              &nbsp;·&nbsp; Ctrl+1/2/3 switch tabs &nbsp;·&nbsp; Alt+←/→ history</div>
          </div>""")


# -------------------------------------------------------------------- wordbook
class WordbookPage(QWidget):
    open_word = Signal(str)
    changed = Signal()

    COLS = ["Word", "Language", "Meaning", "Due", "Recall now", "Reps", "Note"]

    def __init__(self, book: srs.Wordbook):
        super().__init__()
        self.book = book
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._note_edited)
        self.table.itemDoubleClicked.connect(self._dbl)

        self.count = QLabel()
        # Informational only (the status bar repeats it), so it must not set
        # the window's minimum width: let it be clipped when space is short.
        self.count.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.sort = QComboBox()
        self.sort.addItems(["Due first", "Recently added", "Alphabetical", "By language"])
        self.sort.currentIndexChanged.connect(self.reload)
        rm = QPushButton("Remove")
        rm.clicked.connect(self.remove_selected)
        ex = QPushButton("Export CSV…")
        ex.clicked.connect(self.export_csv)
        op = QPushButton("Open in dictionary")
        op.clicked.connect(lambda: self._dbl(self.table.currentItem()))

        bar = QHBoxLayout()
        bar.addWidget(self.count)
        bar.addStretch(1)
        for w in (QLabel("Sort:"), self.sort, op, rm, ex):
            bar.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addLayout(bar)
        lay.addWidget(self.table, 1)
        self._loading = False
        self.reload()

    def _dbl(self, item):
        if item is None:
            return
        w = self.table.item(item.row(), 0)
        if w:
            self.open_word.emit(w.text())

    def reload(self):
        self._loading = True
        order = ["due", "added", "word", "lang"][self.sort.currentIndex()]
        cards = self.book.all(order)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(cards))
        today = date.today()
        for i, c in enumerate(cards):
            snap = c.snapshot or {}
            meaning = "; ".join(g for _, g in (snap.get("senses") or [])[:2]) or "—"
            if c.is_new:
                dtxt = "new"
            else:
                dd = (c.due_date - today).days
                dtxt = "due now" if dd <= 0 else f"in {dd}d"
            vals = [c.word, LANG_NAME.get(c.lang, c.lang), meaning, dtxt,
                    "—" if c.is_new else srs.fmt_retention(c.retention),
                    str(c.reps), c.note]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, c.id)
                if j != 6:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if j == 0:
                    fo = it.font()
                    fo.setPointSizeF(fo.pointSizeF() + 2)
                    fo.setBold(True)
                    it.setFont(fo)
                if j == 3 and dtxt in ("due now", "new"):
                    it.setForeground(QColor("#c64600"))
                if j == 4 and not c.is_new and c.retention < 0.85:
                    it.setForeground(QColor("#c64600"))
                self.table.setItem(i, j, it)
        self.table.setSortingEnabled(True)
        n = self.book.counts()
        self.count.setText(
            f"<b>{n['total']}</b> saved &nbsp;·&nbsp; <b>{n['due']}</b> due today")
        self._loading = False

    def _note_edited(self, item):
        if self._loading or item.column() != 6:
            return
        self.book.set_note(item.data(Qt.UserRole), item.text())
        self.changed.emit()

    def remove_selected(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return
        words = [self.table.item(r, 0).text() for r in sorted(rows)]
        if QMessageBox.question(
                self, "Remove", f"Remove {len(words)} word(s) from the wordbook?\n\n"
                + ", ".join(words[:10]) + ("…" if len(words) > 10 else "")) \
                != QMessageBox.Yes:
            return
        for r in rows:
            self.book.remove(self.table.item(r, 0).data(Qt.UserRole))
        self.reload()
        self.changed.emit()

    def export_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export wordbook", "wordbook.csv",
                                           "CSV (*.csv)")
        if not p:
            return
        with open(p, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["word", "lang", "meaning", "due", "interval_days",
                        "reps", "lapses", "note", "added"])
            for c in self.book.all("added"):
                snap = c.snapshot or {}
                w.writerow([c.word, c.lang,
                            "; ".join(g for _, g in (snap.get("senses") or [])[:3]),
                            c.due, c.interval, c.reps, c.lapses, c.note, c.added_at])
        QMessageBox.information(self, "Exported", f"Wrote {p}")


# ---------------------------------------------------------------------- review
class ReviewPage(QWidget):
    changed = Signal()

    def __init__(self, dic: Dictionary, book: srs.Wordbook, theme_fn, player=None):
        super().__init__()
        self.dic, self.book, self.theme_fn = dic, book, theme_fn
        self.player = player or AudioPlayer(dic)
        self.queue: list[srs.Card] = []
        self.card = None
        self.revealed = False

        self.head = QLabel()
        self.head.setTextFormat(Qt.RichText)
        self.view = DictBrowser(dic)
        self.view.anchorClicked.connect(self._anchor)

        self.reveal_b = QPushButton("Show answer   (Space)")
        self.reveal_b.setMinimumHeight(40)
        self.reveal_b.clicked.connect(self.reveal)

        self.grades = QWidget()
        gl = QHBoxLayout(self.grades)
        gl.setContentsMargins(0, 0, 0, 0)
        self.gbtn = {}
        for g in (srs.AGAIN, srs.HARD, srs.GOOD, srs.EASY):
            b = QPushButton()
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _=False, g=g: self.grade(g))
            self.gbtn[g] = b
            gl.addWidget(b)
        self.grades.setVisible(False)

        self.start_b = QPushButton("Start review")
        self.start_b.setMinimumHeight(38)
        self.start_b.clicked.connect(self.start)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self.head)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.reveal_b)
        lay.addWidget(self.grades)
        lay.addWidget(self.start_b)

        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._space)
        for i, g in enumerate((srs.AGAIN, srs.HARD, srs.GOOD, srs.EASY), 1):
            QShortcut(QKeySequence(str(i)), self,
                      activated=lambda g=g: self.grade(g) if self.revealed else None)
        self.idle()

    def _anchor(self, url):
        s = url.toString()
        if s.startswith("play:"):
            lang, _, word = s[5:].partition(":")
            self.player.play(lang, word)

    def play_current(self):
        if self.card:
            self.player.play(self.card.lang, self.card.word)

    def _space(self):
        if not self.isVisible():
            return
        if self.card is None:
            self.start()
        elif not self.revealed:
            self.reveal()

    def idle(self):
        n = self.book.counts()
        t = self.theme_fn()
        self.card, self.revealed = None, False
        self.reveal_b.setVisible(False)
        self.grades.setVisible(False)
        self.start_b.setVisible(n["due"] > 0)
        self.head.setText("")
        if n["total"] == 0:
            msg = ("Your wordbook is empty.<br>Search a word and press "
                   "<b>Ctrl+D</b> to start collecting.")
        elif n["due"] == 0:
            nxt = self.book.con.execute(
                "SELECT min(due) d FROM card WHERE due > date('now')").fetchone()["d"]
            when = f"Next review: <b>{nxt}</b>." if nxt else ""
            msg = f"Nothing due today. {when}<br>All {n['total']} words are scheduled."
        else:
            msg = (f"<b>{n['due']}</b> word(s) ready to review"
                   f"<br><span class='note'>{n['new']} new · "
                   f"{n['review']} returning</span>")
        self.view.setHtml(f"<style>{render.css(t)}</style>"
                          f"<div class='ctr'><div style='font-size:{t['size']+4}pt'>"
                          f"{msg}</div></div>")

    def start(self):
        self.queue = self.book.due(limit=200)
        if not self.queue:
            self.idle()
            return
        self.start_b.setVisible(False)
        self.next_card()

    def next_card(self):
        if not self.queue:
            self.idle()
            self.changed.emit()
            return
        self.card = self.queue.pop(0)
        self.revealed = False
        self.reveal_b.setVisible(True)
        self.grades.setVisible(False)
        self._paint()

    def _paint(self):
        left = len(self.queue) + 1
        self.head.setText(
            f"<span style='opacity:.6'>{left} left &nbsp;·&nbsp; "
            f"{'new' if self.card.is_new else 'review'} &nbsp;·&nbsp; "
            f"{'' if self.card.is_new else f'recall now {srs.fmt_retention(self.card.retention)} &nbsp;·&nbsp; '}"
            f"difficulty {self.card.difficulty:.1f}/10</span>")
        res = self.dic.lookup(self.card.word) if self.revealed else None
        self.view.setHtml(render.render_card(self.card, res, self.theme_fn(),
                                             self.revealed))
        self.view.verticalScrollBar().setValue(0)

    def reveal(self):
        if self.card is None:
            return
        self.revealed = True
        self.reveal_b.setVisible(False)
        pv = srs.preview(self.card)
        for g, b in self.gbtn.items():
            b.setText(f"{srs.GRADE_NAME[g]}\n{srs.fmt_interval(pv[g])}   ({g+1})")
        self.grades.setVisible(True)
        self._paint()
        self.play_current()

    def grade(self, g):
        if self.card is None or not self.revealed:
            return
        self.book.grade(self.card, g)
        self.changed.emit()
        self.next_card()


# ----------------------------------------------------------------- sync dialog
class SyncDialog(QDialog):
    """Passwordless sign-in and manual sync.

    Sync is opt-in and covers the wordbook only - the dictionary itself is
    never sent anywhere and works with no network at all.
    """

    def __init__(self, client: sync.SyncClient, book: srs.Wordbook, parent=None):
        super().__init__(parent)
        self.client, self.book = client, book
        self.setWindowTitle("Sync wordbook")
        self.setMinimumWidth(430)

        self.server = QLineEdit(self.client.base_url)
        self.email = QLineEdit(self.client.email or "")
        self.email.setPlaceholderText("you@example.com")
        self.code = QLineEdit()
        self.code.setPlaceholderText("6-digit code from your email")
        self.code.setMaxLength(6)

        self.status = QLabel()
        self.status.setWordWrap(True)

        self.send_b = QPushButton("Send code")
        self.signin_b = QPushButton("Sign in")
        self.sync_b = QPushButton("Sync now")
        self.out_b = QPushButton("Sign out")
        self.send_b.clicked.connect(self.send_code)
        self.signin_b.clicked.connect(self.sign_in)
        self.sync_b.clicked.connect(self.do_sync)
        self.out_b.clicked.connect(self.sign_out)

        form = QFormLayout()
        form.addRow("Server", self.server)
        form.addRow("Email", self.email)
        form.addRow("Code", self.code)

        row = QHBoxLayout()
        for b in (self.send_b, self.signin_b, self.sync_b, self.out_b):
            row.addWidget(b)

        note = QLabel("Only your saved words and review schedule are synced. "
                      "The dictionary stays entirely on this machine.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(row)
        lay.addWidget(self.status)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)
        lay.addWidget(bb)
        self.refresh()

    def refresh(self):
        on = self.client.signed_in
        self.code.setEnabled(not on)
        self.send_b.setEnabled(not on)
        self.signin_b.setEnabled(not on)
        self.email.setEnabled(not on)
        self.server.setEnabled(not on)
        self.sync_b.setEnabled(on)
        self.out_b.setEnabled(on)
        if on:
            last = self.client.get_state("last_sync", "never")
            self.status.setText(
                f"<b>Signed in</b> as {self.client.email}<br>"
                f"Last sync: {last}<br>{self.book.counts()['total']} words locally")
        else:
            self.status.setText("Not signed in. Enter your email to receive a "
                                "one-time code.")

    def _busy(self, fn, *a):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return fn(*a)
        except sync.SyncError as e:
            self.status.setText(f"<span style='color:#c64600'>{e}</span>")
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def send_code(self):
        e = self.email.text().strip()
        if "@" not in e:
            self.status.setText("Enter a valid email address.")
            return
        self.client.base_url = self.server.text().strip().rstrip("/")
        if self._busy(self.client.request_code, e) is not None or True:
            if "color" not in self.status.text():
                self.status.setText(
                    f"If {e} is a valid address, a code is on its way. "
                    "It expires in 10 minutes.")
                self.code.setFocus()

    def sign_in(self):
        r = self._busy(self.client.verify, self.email.text().strip(),
                       self.code.text().strip())
        if r:
            self.refresh()
            self.do_sync()

    def do_sync(self):
        r = self._busy(self.client.sync)
        if r:
            self.status.setText(f"<b>{r.summary()}</b>")
            self.parent() and self.parent()._refresh()

    def sign_out(self):
        if QMessageBox.question(self, "Sign out",
                                "Sign out? Your words stay on this computer.") \
                == QMessageBox.Yes:
            self.client.sign_out()
            self.refresh()


# ---------------------------------------------------------------- first run
class FirstRunDialog(QDialog):
    """Fetches the dictionary on first launch.

    The installers are deliberately tiny; the 707 MB dictionary is downloaded
    once from the server and decompressed as it arrives, so peak disk use is
    the finished database rather than archive plus database.
    """

    def __init__(self, data_dir, base_url=None, parent=None):
        super().__init__(parent)
        self.client = packs.PackClient(base_url, data_dir=data_dir)
        self.info = None
        self.worker = None
        self.ok = False
        self.setWindowTitle("Þríauga — first run")
        self.setMinimumWidth(520)

        self.head = QLabel("<b>Welcome to Þríauga</b>")
        self.body = QLabel("Checking for the dictionary…")
        self.body.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.get_b = QPushButton("Download dictionary")
        self.get_b.setVisible(False)
        self.get_b.setMinimumHeight(38)
        self.get_b.setProperty("accent", True)
        self.get_b.clicked.connect(self.start)
        self.quit_b = QPushButton("Quit")
        self.quit_b.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.quit_b)
        row.addWidget(self.get_b)

        lay = QVBoxLayout(self)
        lay.addWidget(self.head)
        lay.addWidget(self.body)
        lay.addWidget(self.bar)
        lay.addLayout(row)
        QTimer.singleShot(60, self.check)

    def check(self):
        try:
            self.info = self.client.dictionary_info()
        except packs.PackError as e:
            self.body.setText(
                f"<span style='color:#c64600'>{e}</span><br><br>"
                "A working internet connection is needed once, to fetch the "
                "dictionary. After that Þríauga runs entirely offline.")
            return
        if not self.info:
            self.body.setText("The server has no dictionary published yet.")
            return
        mb = self.info["bytes"] / 1048576
        inst = self.info.get("uncompressed", 0) / 1048576
        self.body.setText(
            f"Þríauga needs its dictionary before first use.<br><br>"
            f"<b>Download: {mb:.0f} MB</b> &nbsp;·&nbsp; "
            f"{inst:.0f} MB once installed<br><br>"
            "This happens once. Afterwards the dictionary works with no "
            "network at all. Pictures and pronunciation audio are separate, "
            "optional downloads you can add later.")
        self.get_b.setVisible(True)

    def start(self):
        self.get_b.setEnabled(False)
        self.quit_b.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 100)
        self.body.setText("Downloading… you can leave this running.")
        self.worker = _DictWorker(self.client, self.info)
        self.worker.progress.connect(
            lambda d, t: self.bar.setValue(int(100 * d / max(t, 1))))
        self.worker.done_ok.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _done(self):
        self.ok = True
        self.accept()

    def _failed(self, msg):
        self.bar.setVisible(False)
        self.get_b.setEnabled(True)
        self.quit_b.setEnabled(True)
        self.body.setText(f"<span style='color:#c64600'>{msg}</span><br><br>"
                          "You can try again.")


class _DictWorker(QThread):
    progress = Signal(int, int)
    done_ok = Signal()
    failed = Signal(str)

    def __init__(self, client, info):
        super().__init__()
        self.client, self.info = client, info

    def run(self):
        try:
            self.client.download_dictionary(
                self.info, progress=lambda d, t: self.progress.emit(d, t))
            self.done_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


# ------------------------------------------------------------------ add-ons
class _DownloadWorker(QThread):
    """Downloads one pack off the UI thread."""
    progress = Signal(int, int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, client, pack):
        super().__init__()
        self.client, self.pack = client, pack
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self.client.download(self.pack,
                                 progress=lambda d, t: self.progress.emit(d, t),
                                 cancel=lambda: self._cancel)
            self.finished_ok.emit(self.pack.name)
        except packs.PackError as e:
            self.failed.emit(str(e))
        except Exception as e:                            # pragma: no cover
            self.failed.emit(str(e))


class AddonsDialog(QDialog):
    """Download optional media packs after installation.

    The base install is text only, so images and pronunciation audio are
    fetched here on demand. Each pack is a standalone SQLite file dropped into
    the data directory and attached immediately - no restart needed.
    """

    def __init__(self, dic, dict_path, parent=None):
        super().__init__(parent)
        self.dic, self.dict_path = dic, dict_path
        self.client = packs.PackClient(data_dir=Path(dict_path).parent)
        self.worker = None
        self.setWindowTitle("Add-ons")
        self.pal = theme.palette(
            self.palette().window().color().lightness() < 128)
        self.setMinimumWidth(580)
        self.resize(580, 420)

        self.status = QLabel("Checking for available add-ons…")
        self.status.setWordWrap(True)
        self.rows_box = QVBoxLayout()
        self.bar = QProgressBar()
        self.bar.setVisible(False)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 18, 20, 16)
        intro = QLabel("Images and pronunciation audio are optional downloads. "
                       "The dictionary itself works fully without them.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{self.pal['muted']};")
        lay.addWidget(intro)
        self.rows_box.setSpacing(12)
        lay.addLayout(self.rows_box)
        lay.addStretch(1)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        QTimer.singleShot(50, self.refresh)

    def _clear_rows(self):
        while self.rows_box.count():
            item = self.rows_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def refresh(self):
        self._clear_rows()
        try:
            available = self.client.available()
        except packs.PackError as e:
            self.status.setText(f"<span style='color:#c64600'>{e}</span>")
            return
        if not available:
            self.status.setText("No add-ons are published on this server.")
            return
        for pack in available:
            self.rows_box.addWidget(self._row(pack))
        self.status.setText("")
        # Grow to fit the rows just added; the dialog opens before the
        # manifest has been fetched, so its first size hint is too small.
        self.layout().activate()
        want = self.sizeHint().height()
        if want > self.height():
            self.resize(self.width(), want)
        self.setMinimumHeight(min(want, 620))

    def _row(self, pack):
        """One pack, laid out vertically.

        The previous version put a word-wrapped QLabel in a horizontal layout,
        where Qt cannot negotiate height-for-width — the second line was simply
        clipped. Stacking the labels and letting the group box size to its
        contents avoids the problem entirely.
        """
        box = QGroupBox(pack.title)
        v = QVBoxLayout(box)
        v.setSpacing(9)

        detail = QLabel(pack.detail)
        detail.setWordWrap(True)
        detail.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v.addWidget(detail)

        row = QHBoxLayout()
        meta = QLabel(f"{pack.rows:,} entries · {pack.size_mb:.0f} MB")
        meta.setStyleSheet(f"color:{self.pal['muted']};")
        row.addWidget(meta)
        row.addStretch(1)

        if self.client.is_installed(pack.name):
            state = QLabel("Installed")
            state.setStyleSheet(f"color:{self.pal['good']}; font-weight:600;")
            row.addWidget(state)
            rm = QPushButton("Remove")
            rm.setMinimumHeight(34)
            rm.clicked.connect(lambda _=False, p=pack: self.remove(p))
            row.addWidget(rm)
        else:
            dl = QPushButton(f"Download  {pack.size_mb:.0f} MB")
            dl.setProperty("accent", True)
            dl.setMinimumWidth(150)
            dl.setMinimumHeight(34)
            dl.clicked.connect(lambda _=False, p=pack: self.download(p))
            row.addWidget(dl)
        v.addLayout(row)
        return box

    def download(self, pack):
        if self.worker and self.worker.isRunning():
            return
        self.bar.setVisible(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status.setText(f"Downloading {pack.title}…")
        self.worker = _DownloadWorker(self.client, pack)
        self.worker.progress.connect(
            lambda d, t: self.bar.setValue(int(100 * d / max(t, 1))))
        self.worker.finished_ok.connect(self._installed)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _installed(self, name):
        self.bar.setVisible(False)
        # Attach straight away so the pack works without restarting.
        ok = db.attach_pack(self.dic.con, name, self.dict_path)
        self.dic.refresh_media()
        self.status.setText(f"<b>Installed.</b> "
                            + ("Ready to use now." if ok else
                               "Restart Trilex to enable it."))
        self.refresh()

    def _failed(self, msg):
        self.bar.setVisible(False)
        self.status.setText(f"<span style='color:#c64600'>{msg}</span>")

    def remove(self, pack):
        if QMessageBox.question(
                self, "Remove add-on",
                f"Delete {pack.title} ({pack.size_mb:.0f} MB)? "
                "You can download it again later.") != QMessageBox.Yes:
            return
        db.detach_pack(self.dic.con, pack.name)
        self.client.remove(pack.name)
        self.dic.refresh_media()
        self.refresh()
        self.status.setText("Removed.")


# ------------------------------------------------------------------ main window
class MainWindow(QMainWindow):
    def __init__(self, dict_path=None, user_path=None):
        super().__init__()
        self.settings = QSettings("trilex", "trilex")
        self.font_size = int(self.settings.value("font_size", 10))

        self.dict_path = Path(dict_path) if dict_path else db.data_dir() / "dict.db"
        self.dcon = db.open_dict(dict_path, create=False)
        self.ucon = db.open_user(user_path)
        self.dic = Dictionary(self.dcon)
        self.book = srs.Wordbook(self.ucon)
        self.sync = sync.SyncClient(self.ucon)

        self.setWindowTitle(APP)
        self.setWindowIcon(app_icon())
        # Remember size and position as they change, not only on a clean
        # close: a crash or a killed process would otherwise forget them.
        self._geo_timer = QTimer(self, singleShot=True, interval=600)
        self._geo_timer.timeout.connect(
            lambda: self.settings.setValue("geometry", self.saveGeometry()))
        self._default_size()

        self.player = AudioPlayer(self.dic)
        self.search_page = SearchPage(self.dic, self.book, self.theme, self.player)
        self.word_page = WordbookPage(self.book)
        self.review_page = ReviewPage(self.dic, self.book, self.theme, self.player)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.search_page, "Dictionary")
        self.tabs.addTab(self.word_page, "Wordbook")
        self.tabs.addTab(self.review_page, "Review")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)

        self.search_page.saved.connect(self._refresh)
        self.word_page.changed.connect(self._refresh)
        self.review_page.changed.connect(self._refresh)
        self.word_page.open_word.connect(self._open_word)

        self.status = self.statusBar()
        self._build_menu()
        self._shortcuts()
        self._refresh()
        self._tray()
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        QTimer.singleShot(150, self.search_page.box.setFocus)
        QTimer.singleShot(400, self._warm)
        QTimer.singleShot(1500, self._auto_sync)

    def theme(self):
        return theme_from(self.palette(), self.font_size)

    def _default_size(self):
        """A comfortable reading width that still leaves room beside it, and
        never larger than the screen it opens on."""
        w, h = 920, 660
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            w, h = min(w, int(avail.width() * 0.9)), min(h, int(avail.height() * 0.9))
        self.resize(w, h)

    def changeEvent(self, ev):
        # Windows users reported the window "closing" when it lost focus; it
        # was in fact being minimised. Log every state and activation change
        # with who holds the foreground, so the log shows what triggered it.
        if ev.type() in (QEvent.WindowStateChange, QEvent.ActivationChange):
            try:
                fg = ""
                if sys.platform == "win32":
                    import ctypes
                    u = ctypes.windll.user32
                    h = u.GetForegroundWindow()
                    pid = ctypes.c_ulong()
                    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    fg = f" fg_hwnd={h} fg_pid={pid.value} own_hwnd={int(self.winId())}"
                old = ev.oldState() if ev.type() == QEvent.WindowStateChange else None
                sys.stderr.write(
                    f"{time.strftime('%H:%M:%S')} {ev.type().name} state={self.windowState()}"
                    f" old={old} active={self.isActiveWindow()}{fg}\n")
            except Exception:
                pass
        super().changeEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.isVisible():
            self._geo_timer.start()

    def moveEvent(self, ev):
        super().moveEvent(ev)
        if self.isVisible():
            self._geo_timer.start()

    def _warm(self):
        """Prime the fuzzy-match pool so the first typo lookup isn't slow."""
        try:
            self.dic.did_you_mean("waterz", limit=1)
        except Exception:
            pass

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("Export wordbook…", self, triggered=self.word_page.export_csv)
        m.addAction(a)
        m.addSeparator()
        m.addAction(QAction("Quit", self, shortcut=QKeySequence.Quit,
                            triggered=self.close))
        m.addSeparator()
        m.addAction(QAction("Sync wordbook…", self, shortcut=QKeySequence("Ctrl+Shift+S"),
                            triggered=self.open_sync))
        m.addAction(QAction("Add-ons (images && audio)…", self,
                            triggered=self.open_addons))
        v = self.menuBar().addMenu("&View")
        v.addAction(QAction("Larger text", self, shortcut=QKeySequence.ZoomIn,
                            triggered=lambda: self._zoom(+1)))
        v.addAction(QAction("Smaller text", self, shortcut=QKeySequence.ZoomOut,
                            triggered=lambda: self._zoom(-1)))
        h = self.menuBar().addMenu("&Help")
        h.addAction(QAction("Sources && licences", self, triggered=self._about))
        h.addAction(QAction("About Trilex", self, triggered=self._about_version))

    def _shortcuts(self):
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+D"), self,
                  activated=self.search_page.save_current)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self._play)
        for i in range(3):
            QShortcut(QKeySequence(f"Ctrl+{i+1}"), self,
                      activated=lambda i=i: self.tabs.setCurrentIndex(i))
        QShortcut(QKeySequence("Alt+Left"), self,
                  activated=lambda: self.search_page._go(-1))
        QShortcut(QKeySequence("Alt+Right"), self,
                  activated=lambda: self.search_page._go(+1))

    def _play(self):
        if self.tabs.currentIndex() == 2:
            self.review_page.play_current()
        else:
            self.search_page.play_current()

    def _focus_search(self):
        self.tabs.setCurrentIndex(0)
        self.search_page.box.setFocus()
        self.search_page.box.selectAll()

    def _zoom(self, d):
        self.font_size = max(7, min(20, self.font_size + d))
        self.settings.setValue("font_size", self.font_size)
        if self.search_page.result:
            self.search_page._show(self.search_page.history[self.search_page.hpos])
        else:
            self.search_page.welcome()
        self.review_page._paint() if self.review_page.card else self.review_page.idle()

    def _tab_changed(self, i):
        if i == 1:
            self.word_page.reload()
        elif i == 2 and self.review_page.card is None:
            self.review_page.idle()

    def _open_word(self, w):
        self.tabs.setCurrentIndex(0)
        self.search_page.search(w)

    def _refresh(self):
        n = self.book.counts()
        self.tabs.setTabText(1, f"Wordbook ({n['total']})" if n["total"] else "Wordbook")
        self.tabs.setTabText(2, f"Review ({n['due']})" if n["due"] else "Review")
        self.status.showMessage(
            f"{n['total']} saved   ·   {n['due']} due today   ·   "
            f"{n['new']} never studied")
        self.search_page._sync_save()

    def _tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        menu.addAction(QAction("Open Trilex", self, triggered=self.bring_to_front))
        menu.addAction(QAction("Review now", self,
                               triggered=lambda: (self.bring_to_front(),
                                                  self.tabs.setCurrentIndex(2))))
        menu.addSeparator()
        menu.addAction(QAction("Quit", self, triggered=QApplication.quit))
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(APP)
        self.tray.activated.connect(
            lambda r: self.bring_to_front()
            if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()
        due = self.book.counts()["due"]
        if due:
            QTimer.singleShot(1200, lambda: self.tray.showMessage(
                f"{due} word{'s' if due != 1 else ''} to review",
                "Open Trilex and press Ctrl+3 to start.", app_icon(), 8000))

    def bring_to_front(self):
        """Restore from minimised or hidden, and take focus."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            try:
                import ctypes
                u = ctypes.windll.user32
                h = int(self.winId())
                if u.IsIconic(h):
                    u.ShowWindow(h, 9)               # SW_RESTORE
                u.SetForegroundWindow(h)
            except Exception:
                pass

    def ensure_taskbar_button(self):
        """Windows: insist on a taskbar button, so a minimised window can
        always be found again. Qt normally gives one, but make it explicit."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            u = ctypes.windll.user32
            h = int(self.winId())
            GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = -20, 0x40000, 0x80
            ex = u.GetWindowLongW(h, GWL_EXSTYLE)
            u.SetWindowLongW(h, GWL_EXSTYLE, (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def open_addons(self):
        AddonsDialog(self.dic, self.dict_path, self).exec()
        if self.search_page.result:
            self.search_page._show(
                self.search_page.history[self.search_page.hpos])

    def open_sync(self):
        SyncDialog(self.sync, self.book, self).exec()
        self.word_page.reload()
        self._refresh()

    def _auto_sync(self):
        """Quiet sync on startup; never blocks or nags if the server is down."""
        if not self.sync.signed_in:
            return
        try:
            r = self.sync.sync()
            if r.applied or r.pushed:
                self.word_page.reload()
                self._refresh()
                self.status.showMessage(f"Sync: {r.summary()}", 6000)
        except sync.SyncError:
            pass

    def _about_version(self):
        from . import __version__
        import PySide6
        try:
            built = self.dcon.execute(
                "SELECT v FROM meta WHERE k='built'").fetchone()
            built = built["v"] if built else None
        except Exception:
            built = None
        try:
            n = self.dcon.execute("SELECT count(*) c FROM entry").fetchone()["c"]
        except Exception:
            n = None
        lines = [f"<b>Þríauga / Trilex {__version__}</b>",
                 "Offline English / Svenska / 中文 dictionary", ""]
        if n:
            lines.append(f"Dictionary: {n:,} entries"
                         + (f", built {built}" if built else ""))
        lines += [f"Data folder: {db.data_dir()}",
                  f"Python {sys.version.split()[0]} · PySide6 {PySide6.__version__}",
                  '<a href="https://github.com/magiccpp/trilex">github.com/magiccpp/trilex</a>']
        QMessageBox.about(self, "About Trilex", "<br>".join(lines))

    def _about(self):
        QMessageBox.information(self, "Sources & licences", (
            "Trilex bundles third-party lexical data:\n\n"
            "• CC-CEDICT (Chinese–English) — CC BY-SA 4.0\n"
            "  mdbg.net/chinese/dictionary?page=cc-cedict\n\n"
            "• Folkets lexikon (Swedish–English) — CC BY-SA 2.5 GENERIC\n"
            "  folkets-lexikon.csc.kth.se\n\n"
            "• Etymologies & IPA from English Wiktionary via wiktextract\n"
            "  (kaikki.org) — CC BY-SA 4.0 / GFDL\n\n"
            "Chinese↔Swedish pairs are derived through English and are\n"
            "labelled 'via English' in results.\n\n"
            "The application runs entirely offline."))

    def closeEvent(self, ev):
        self.settings.setValue("geometry", self.saveGeometry())
        if self.tray:
            self.tray.hide()
        super().closeEvent(ev)


def _log_to_file():
    """Route tracebacks and hard crashes to <data dir>/trilex.log.

    Under pythonw.exe there is no console: sys.stderr is None, so an exception
    raised in a slot or a paint handler would vanish, and a native crash would
    just make the window disappear. The log is the only witness. It is
    truncated when it passes 1 MB so it can never grow without bound.
    """
    try:
        path = db.data_dir() / "trilex.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if path.exists() and path.stat().st_size > 1 << 20 else "a"
        fh = open(path, mode, encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = fh
        if sys.stdout is None:
            sys.stdout = fh
        faulthandler.enable(fh, all_threads=True)

        def hook(t, v, tb):
            traceback.print_exception(t, v, tb, file=fh)
            fh.flush()
        sys.excepthook = hook
        import PySide6
        fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start  "
                 f"python {sys.version.split()[0]}  PySide6 {PySide6.__version__}  "
                 f"{sys.platform}\n")
        return path
    except Exception:
        return None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    _log_to_file()
    if sys.platform == "win32":
        # A stable identity for the taskbar: groups the window under the app's
        # own icon rather than a generic python.exe button.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("magiccpp.Thriauga")
        except Exception:
            pass
    app = QApplication(argv)
    if _SingleInstance.already_running():
        return 0
    app.setApplicationName(APP)
    app.setWindowIcon(app_icon())
    f = app.font()
    for fam in CJK_FONTS:
        f.insertSubstitution(f.family(), fam)
    app.setFont(f)
    dark = app.palette().window().color().lightness() < 128
    app.setStyleSheet(theme.stylesheet(theme.palette(dark), f.pointSize() or 10))

    path = db.data_dir() / "dict.db"
    local = Path(__file__).resolve().parents[1] / "data/dict.db"
    if not path.exists() and local.exists():
        path = local
    if not path.exists():
        # Nothing installed yet: offer to fetch it rather than just failing.
        first = FirstRunDialog(db.data_dir())
        first.exec()
        if not first.ok:
            return 1
        path = db.data_dir() / "dict.db"
    w = MainWindow(dict_path=path)
    w.ensure_taskbar_button()        # before show: the taskbar reads styles then
    w.show()
    w._instance = _SingleInstance(w)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
