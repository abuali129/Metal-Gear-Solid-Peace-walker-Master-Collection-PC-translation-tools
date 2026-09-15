"""One dark palette for the whole window, shared with the MGS4 workbench it grew out of."""

from __future__ import annotations

BG = "#1e1e1e"          # window
PANEL = "#252526"       # lists, tables, text
EDGE = "#333338"        # borders and troughs
HEAD = "#2d2d32"        # table headings, tab strip
FG = "#e8e8e8"
MUTED = "#9aa3ad"
ACCENT = "#3a5a7a"
DONE = "#243024"        # translated rows
WARN = "#c8a45c"        # token mismatch

QSS = f"""
QWidget {{
    background: {BG};
    color: {FG};
    font-size: 10pt;
}}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[heading="true"] {{ font-weight: bold; }}
QLabel[warn="true"] {{ color: {WARN}; }}

QMenuBar, QMenu {{ background: {HEAD}; color: {FG}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {ACCENT}; }}
QMenu {{ border: 1px solid {EDGE}; }}

QTabBar::tab {{
    background: #2a2a2f;
    color: {MUTED};
    padding: 7px 18px;
    border: none;
    margin-right: 1px;
}}
QTabBar::tab:selected {{ background: {ACCENT}; color: {FG}; }}
QTabBar::tab:hover:!selected {{ background: #34343a; }}

QListWidget, QTableView, QPlainTextEdit, QLineEdit, QSpinBox, QAbstractScrollArea {{
    background: {PANEL};
    color: {FG};
    border: 1px solid {EDGE};
    selection-background-color: {ACCENT};
    selection-color: {FG};
}}
QPlainTextEdit[readOnly="true"] {{ background: #1c1c20; color: {MUTED}; }}

QHeaderView::section {{
    background: {HEAD};
    color: {FG};
    padding: 5px;
    border: none;
    border-right: 1px solid {EDGE};
}}
QTableView {{ gridline-color: {EDGE}; }}
QTableView::item:selected {{ background: {ACCENT}; }}

QPushButton {{
    background: {HEAD};
    border: 1px solid {EDGE};
    padding: 5px 12px;
}}
QPushButton:hover {{ background: #3a3a41; }}
QPushButton:pressed {{ background: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; background: #232327; }}

QScrollBar:vertical, QScrollBar:horizontal {{ background: {BG}; border: none; }}
QScrollBar::handle {{ background: {HEAD}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:hover {{ background: #3d3d44; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSplitter::handle {{ background: {EDGE}; }}
QStatusBar {{ background: {HEAD}; color: {FG}; }}
QRadioButton, QCheckBox {{ spacing: 5px; }}
QProgressBar {{
    background: {PANEL};
    border: 1px solid {EDGE};
    text-align: center;
    max-height: 14px;
}}
QProgressBar::chunk {{ background: {ACCENT}; }}
"""


def apply(app) -> None:
    """Theme a ``QApplication``."""
    app.setStyleSheet(QSS)


#: Bright enough to pick out of the list at a glance against PANEL.
MARK = "#9dc4e8"


def accent_brush():
    """The accent colour as a brush, for marking rows in a list."""
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(MARK))
