"""Visual theme.

The brief was a restrained Norse feeling — lagom. So the reference points are
Nordic *materials* rather than Viking iconography: birch and linen for the
light palette, iron and charcoal for the dark one, and a muted fjord blue as
the single accent. There is no knotwork, no blackletter, no dragons.

The only overt gestures are the thorn (Þ) in the wordmark, which the name
carries anyway, and a short accent tick at the head of each section rule —
a quiet geometric mark rather than an ornament.

Everything else is spacing, weight and restraint.
"""
from __future__ import annotations

PALETTES = {
    "light": {
        "bg":       "#f7f5f0",   # unbleached linen
        "surface":  "#fffefb",
        "raised":   "#ffffff",
        "fg":       "#1b1917",
        "muted":    "#6d6459",
        "faint":    "#9b9285",
        "line":     "#e3ded4",
        "line_soft":"#eeeae2",
        "accent":   "#2f5d78",   # fjord
        "accent_fg":"#ffffff",
        "accent_dim":"#5d87a0",
        "chip":     "#eeeae1",
        "sel":      "#d9e5ed",
        "warn":     "#a4521f",
        "good":     "#3d6b46",
    },
    "dark": {
        "bg":       "#191817",
        "surface":  "#201f1c",
        "raised":   "#262421",
        "fg":       "#e9e4db",
        "muted":    "#9b9285",
        "faint":    "#71695f",
        "line":     "#312d28",
        "line_soft":"#282521",
        "accent":   "#8fb6cd",   # pale ice
        "accent_fg":"#14181b",
        "accent_dim":"#5f8299",
        "chip":     "#2a2723",
        "sel":      "#2c3b45",
        "warn":     "#d08b5a",
        "good":     "#82ad8c",
    },
}

# Text sits on a warm neutral, so the type stack favours humanist faces.
UI_FAMILIES = ('"Inter","Segoe UI","Noto Sans","DejaVu Sans",system-ui,sans-serif')
CJK_FAMILIES = ('"Noto Sans CJK SC","Microsoft YaHei","PingFang SC",'
                '"Source Han Sans SC","WenQuanYi Micro Hei","Droid Sans Fallback"')


def palette(dark: bool) -> dict:
    return dict(PALETTES["dark" if dark else "light"])


def stylesheet(p: dict, base_pt: int = 10) -> str:
    """Qt style sheet for the whole application."""
    return f"""
    QWidget {{
        background: {p['bg']};
        color: {p['fg']};
        font-size: {base_pt}pt;
    }}
    QMainWindow, QDialog {{ background: {p['bg']}; }}

    /* --- tabs: flat, with a single accent tick under the active one ----- */
    QTabWidget::pane {{
        border: none;
        border-top: 1px solid {p['line']};
        top: -1px;
    }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: transparent;
        color: {p['muted']};
        padding: 9px 18px 8px 18px;
        margin-right: 2px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 500;
    }}
    QTabBar::tab:hover {{ color: {p['fg']}; }}
    QTabBar::tab:selected {{
        color: {p['fg']};
        border-bottom: 2px solid {p['accent']};
        font-weight: 600;
    }}

    /* --- text entry ---------------------------------------------------- */
    QLineEdit {{
        background: {p['surface']};
        border: 1px solid {p['line']};
        border-radius: 7px;
        padding: 7px 11px;
        selection-background-color: {p['sel']};
        selection-color: {p['fg']};
    }}
    QLineEdit:focus {{ border: 1px solid {p['accent']}; }}
    QLineEdit:disabled {{ color: {p['faint']}; }}

    /* --- buttons: quiet by default, accent only where it leads ---------- */
    QPushButton {{
        background: {p['surface']};
        border: 1px solid {p['line']};
        border-radius: 7px;
        padding: 7px 15px;
        color: {p['fg']};
    }}
    QPushButton:hover {{ background: {p['chip']}; border-color: {p['accent_dim']}; }}
    QPushButton:pressed {{ background: {p['sel']}; }}
    QPushButton:disabled {{ color: {p['faint']}; border-color: {p['line_soft']};
                            background: {p['bg']}; }}
    /* Icon-only navigation buttons: the default 15px side padding leaves no
       room for the glyph inside a fixed narrow width. */
    QPushButton[nav="true"] {{
        padding: 4px 0;
        font-size: {base_pt + 3}pt;
        color: {p['muted']};
    }}
    QPushButton[nav="true"]:hover {{ color: {p['fg']}; }}
    QPushButton[nav="true"]:disabled {{ color: {p['line']}; background: {p['bg']};
                                        border-color: {p['line_soft']}; }}
    QPushButton[accent="true"] {{
        background: {p['accent']};
        color: {p['accent_fg']};
        border: 1px solid {p['accent']};
        font-weight: 600;
    }}
    QPushButton[accent="true"]:hover {{ background: {p['accent_dim']};
                                        border-color: {p['accent_dim']}; }}
    QPushButton[accent="true"]:disabled {{ background: {p['chip']};
                                           color: {p['faint']};
                                           border-color: {p['line']}; }}

    /* --- containers ---------------------------------------------------- */
    QGroupBox {{
        background: {p['surface']};
        border: 1px solid {p['line']};
        border-radius: 9px;
        margin-top: 14px;
        padding: 14px 14px 12px 14px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 5px;
        color: {p['fg']};
    }}

    QTextBrowser {{ background: {p['bg']}; border: none; }}

    /* --- tables -------------------------------------------------------- */
    QTableWidget {{
        background: {p['surface']};
        alternate-background-color: {p['bg']};
        gridline-color: {p['line_soft']};
        border: 1px solid {p['line']};
        border-radius: 8px;
        selection-background-color: {p['sel']};
        selection-color: {p['fg']};
    }}
    QHeaderView::section {{
        background: {p['bg']};
        color: {p['muted']};
        border: none;
        border-bottom: 1px solid {p['line']};
        padding: 7px 9px;
        font-weight: 600;
    }}
    QTableWidget::item {{ padding: 5px 4px; }}

    /* --- menus --------------------------------------------------------- */
    QMenuBar {{ background: {p['bg']}; border-bottom: 1px solid {p['line_soft']}; }}
    QMenuBar::item {{ padding: 6px 11px; background: transparent; }}
    QMenuBar::item:selected {{ background: {p['chip']}; border-radius: 5px; }}
    QMenu {{ background: {p['surface']}; border: 1px solid {p['line']};
             border-radius: 8px; padding: 5px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {p['sel']}; }}
    QMenu::separator {{ height: 1px; background: {p['line_soft']}; margin: 5px 8px; }}

    /* --- completer popup ----------------------------------------------- */
    QListView {{
        background: {p['surface']};
        border: 1px solid {p['line']};
        border-radius: 8px;
        padding: 4px;
        outline: none;
        selection-background-color: {p['sel']};
        selection-color: {p['fg']};
    }}
    QListView::item {{ border-radius: 5px; padding: 3px 6px; }}

    QComboBox {{
        background: {p['surface']}; border: 1px solid {p['line']};
        border-radius: 7px; padding: 5px 10px;
    }}
    QComboBox:hover {{ border-color: {p['accent_dim']}; }}
    QComboBox QAbstractItemView {{
        background: {p['surface']}; border: 1px solid {p['line']};
        selection-background-color: {p['sel']}; selection-color: {p['fg']};
    }}

    /* --- progress ------------------------------------------------------ */
    QProgressBar {{
        background: {p['chip']};
        border: none;
        border-radius: 5px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {p['accent']}; border-radius: 5px; }}

    /* --- scrollbars: slim, no arrows ----------------------------------- */
    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {p['line']}; border-radius: 5px; min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p['accent_dim']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {p['line']}; border-radius: 5px; min-width: 32px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QStatusBar {{ background: {p['bg']}; color: {p['muted']};
                  border-top: 1px solid {p['line_soft']}; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{
        background: {p['fg']}; color: {p['bg']};
        border: none; border-radius: 5px; padding: 5px 8px;
    }}
    QLabel {{ background: transparent; }}
    """
