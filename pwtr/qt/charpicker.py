"""Choose the characters a translation needs the game's font to carry.

Modelled on the PS3 toolkit's picker: browse by Unicode block or search by
name, character or hex, tick one character or a whole block, and get back the
set.  Two things are different on this engine, and both show in the grid.

* **What the face already draws is greyed.**  The subtitle face has a real
  character map, but the engine only looks up code points up to
  :data:`pwtr.compact.CEILING` (U+024F).  A character the stock face draws at
  or below that line needs nothing; one above it needs moving down even if
  the face has a glyph, so it is *not* greyed.
* **Arabic is picked as it is drawn.**  The engine has no shaper, so an
  Arabic translation is written as presentation forms (U+FE70..U+FEFC) and
  those are what the face must carry -- not the base letters in U+0600.
  The Arabic preset takes the forms, the harakat and the Arabic punctuation.

A set can be saved as a plain list and opened again, so a team can hand its
set to another.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

__all__ = ["CharPicker", "read_list", "write_list", "ARABIC_PRESET"]

#: The scripts a translator is likely to need, as (name, first, last).
BLOCKS = [
    ("Basic Latin", 0x20, 0x7E),
    ("Latin-1 Supplement", 0xA0, 0xFF),
    ("Latin Extended-A", 0x100, 0x17F),
    ("Latin Extended-B", 0x180, 0x24F),
    ("Latin Extended Additional", 0x1E00, 0x1EFF),
    ("IPA Extensions", 0x250, 0x2AF),
    ("Greek and Coptic", 0x370, 0x3FF),
    ("Greek Extended", 0x1F00, 0x1FFF),
    ("Cyrillic", 0x400, 0x4FF),
    ("Cyrillic Supplement", 0x500, 0x52F),
    ("Armenian", 0x530, 0x58F),
    ("Hebrew", 0x590, 0x5FF),
    ("Arabic", 0x600, 0x6FF),
    ("Arabic Presentation Forms-A", 0xFB50, 0xFDFF),
    ("Arabic Presentation Forms-B", 0xFE70, 0xFEFF),
    ("Thai", 0xE00, 0xE7F),
    ("Georgian", 0x10A0, 0x10FF),
    ("Devanagari", 0x900, 0x97F),
    ("General Punctuation", 0x2000, 0x206F),
    ("Currency Symbols", 0x20A0, 0x20CF),
    ("CJK Symbols and Punctuation", 0x3000, 0x303F),
    ("Hiragana", 0x3040, 0x309F),
    ("Katakana", 0x30A0, 0x30FF),
    ("Hangul Syllables (search it)", 0xAC00, 0xD7A3),
    ("CJK Unified (search it)", 0x4E00, 0x9FFF),
    ("Halfwidth and Fullwidth Forms", 0xFF00, 0xFFEF),
]

#: Cells drawn at once; search narrows a block too big to show.
CAP = 1200

def _named(code: int) -> bool:
    try:
        unicodedata.name(chr(code))
        return True
    except ValueError:
        return False


#: What an Arabic translation actually draws once shaped: the joined forms
#: and lam-alef ligatures, the harakat, and the Arabic comma, semicolon and
#: question mark.
ARABIC_PRESET = sorted(
    {c for c in range(0xFE70, 0xFEFD) if _named(c)}
    | set(range(0x064B, 0x0653)) | {0x0670}
    | {0x060C, 0x061B, 0x061F})


def read_list(path: str | Path) -> list[int]:
    """Code points from a saved list: one per line, as ``U+XXXX``, bare hex,
    or the character itself.  Anything after a ``#`` or a tab is a comment."""
    out: list[int] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        token = line.split("#", 1)[0].split("\t", 1)[0].strip()
        if not token:
            continue
        if token.upper().startswith("U+"):
            token = token[2:]
            code = int(token, 16)
        elif len(token) == 1:
            code = ord(token)
        else:
            code = int(token, 16)
        out.append(code)
    return list(dict.fromkeys(out))


def write_list(path: str | Path, codes: Iterable[int]) -> None:
    """One ``U+XXXX<tab>character<tab>name`` line per code point."""
    lines = ["# Character set for the Peace Walker translation toolkit",
             "# One code point per line; everything after a tab is a note."]
    for code in sorted(set(codes)):
        try:
            name = unicodedata.name(chr(code))
        except ValueError:
            name = ""
        glyph = chr(code) if code >= 0x20 and not unicodedata.category(
            chr(code)).startswith("C") else ""
        lines.append(f"U+{code:04X}\t{glyph}\t{name}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


class CharPicker(QDialog):
    """``selected`` is a set of code points; ``codepoints()`` returns it sorted.

    ``drawn`` is what the stock face already draws where the engine can reach
    it -- shown greyed and not selectable.  ``translation`` is every
    character the project's translations use, offered as a preset.
    """

    def __init__(self, parent=None, selected: Iterable[int] = (),
                 drawn: Iterable[int] = (), translation: Iterable[int] = ()):
        super().__init__(parent)
        self.setWindowTitle("Choose the characters to install")
        self.resize(820, 620)
        self.selected = set(selected)
        self.drawn = set(drawn)
        self.translation = set(translation)
        self._cache: dict = {}
        self._syncing = False

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Tick the characters your translation needs, or tick a block to "
            "take all of it. Greyed characters are already drawn by the game's "
            "face and need nothing. For Arabic, pick the presentation forms "
            "(the Arabic preset does it): the game draws shaped letters, not "
            "the base block.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Search"))
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "a name, a character or hex -- cyrillic small a, é, 0E01 (Enter)")
        self.search.returnPressed.connect(self._repopulate)
        row.addWidget(self.search, 1)
        go = QPushButton("Go")
        go.clicked.connect(self._repopulate)
        row.addWidget(go)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Presets"))
        used = QPushButton("Everything my translation uses")
        used.setToolTip("Every character in the translations that the face "
                        "does not already draw.")
        used.clicked.connect(self._take_translation)
        used.setEnabled(bool(self.translation - self.drawn))
        row.addWidget(used)
        arabic = QPushButton("Arabic letters")
        arabic.clicked.connect(lambda: self._take(ARABIC_PRESET))
        row.addWidget(arabic)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        row.addStretch(1)
        load = QPushButton("Open a list...")
        load.clicked.connect(self._open)
        row.addWidget(load)
        save = QPushButton("Save the list...")
        save.clicked.connect(self._save)
        row.addWidget(save)
        layout.addLayout(row)

        body = QHBoxLayout()
        self.blocks = QListWidget()
        self.blocks.setMaximumWidth(260)
        self.blocks.addItem(self._special("Selected"))
        for name, first, last in BLOCKS:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, (first, last))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.blocks.addItem(item)
        self.blocks.currentRowChanged.connect(self._on_block)
        self.blocks.itemChanged.connect(self._on_block_checked)
        body.addWidget(self.blocks)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        self.scroll.setWidget(self.host)
        body.addWidget(self.scroll, 1)
        layout.addLayout(body, 1)

        self.status = QLabel("")
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.blocks.setCurrentRow(1)

    # -- data ---------------------------------------------------------------

    @staticmethod
    def _special(name: str) -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, None)
        return item

    def _assigned(self, first: int, last: int) -> list[int]:
        key = (first, last)
        if key not in self._cache:
            self._cache[key] = [c for c in range(first, last + 1)
                                if _named(c)]
        return self._cache[key]

    def _search_hits(self, query: str) -> list[int]:
        wanted = query.lower()
        bare = wanted[2:] if wanted.startswith("u+") else wanted
        try:
            as_hex = int(bare, 16)
        except ValueError:
            as_hex = None
        hits = []
        for _name, first, last in BLOCKS:
            for code in self._assigned(first, last):
                name = unicodedata.name(chr(code)).lower()
                if (wanted in name or code == as_hex
                        or (len(query) == 1 and ord(query) == code)):
                    hits.append(code)
                    if len(hits) >= CAP:
                        return hits
        return hits

    # -- presets and lists --------------------------------------------------

    def _take(self, codes) -> None:
        self.selected.update(c for c in codes if c not in self.drawn)
        self._repopulate()

    def _take_translation(self) -> None:
        self._take(c for c in self.translation if c > 0x20)

    def _clear(self) -> None:
        self.selected.clear()
        self._repopulate()

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a character list", "",
            "Character lists (*.txt *.csv);;All files (*)")
        if not path:
            return
        try:
            codes = read_list(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Character list",
                                f"Could not read that list:\n\n{error}")
            return
        self._take(codes)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the character list", "charset.txt",
            "Character lists (*.txt)")
        if path:
            write_list(path, self.selected)

    # -- the block list -----------------------------------------------------

    def _on_block(self, _row) -> None:
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._repopulate()

    def _on_block_checked(self, item) -> None:
        if self._syncing or item.data(Qt.UserRole) is None:
            return
        codes = [c for c in self._assigned(*item.data(Qt.UserRole))
                 if c not in self.drawn]
        if item.checkState() == Qt.Checked:
            self.selected.update(codes)
        else:
            self.selected.difference_update(codes)
        self._repopulate()

    def _sync_block(self) -> None:
        item = self.blocks.currentItem()
        if not item or item.data(Qt.UserRole) is None:
            return
        codes = [c for c in self._assigned(*item.data(Qt.UserRole))
                 if c not in self.drawn]
        have = sum(1 for c in codes if c in self.selected)
        state = (Qt.Unchecked if not have else
                 Qt.Checked if have == len(codes) else Qt.PartiallyChecked)
        self._syncing = True
        item.setCheckState(state)
        self._syncing = False

    # -- the grid -----------------------------------------------------------

    def _repopulate(self) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        query = self.search.text().strip()
        item = self.blocks.currentItem()
        if query:
            codes = self._search_hits(query)
        elif item is not None and item.data(Qt.UserRole) is None:
            codes = sorted(self.selected)
        else:
            codes = self._assigned(*item.data(Qt.UserRole)) if item else []
        shown = codes[:CAP]
        columns = 6
        for index, code in enumerate(shown):
            drawn = code in self.drawn
            box = QCheckBox(f"{chr(code)}  {code:04X}"
                            + ("  in face" if drawn else ""))
            box.setToolTip(unicodedata.name(chr(code), ""))
            box.setChecked(code in self.selected)
            box.setEnabled(not drawn)
            box.toggled.connect(lambda on, c=code: self._toggle(c, on))
            self.grid.addWidget(box, index // columns, index % columns)
        self._sync_block()
        more = (f"  (showing {CAP} of {len(codes)} -- search to narrow)"
                if len(codes) > CAP else "")
        self._update(f"{len(shown)} shown{more}")

    def _toggle(self, code: int, on: bool) -> None:
        if on:
            self.selected.add(code)
        else:
            self.selected.discard(code)
        self._sync_block()
        self._update()

    def _update(self, extra: str = "") -> None:
        missing = len(self.translation - self.drawn - self.selected
                      - {0x20, 0x0A, 0x0D, 0x09})
        text = f"Selected: {len(self.selected)}"
        if self.translation:
            text += (f"   ·   translation characters not covered: {missing}"
                     if missing else "   ·   covers the whole translation")
        if extra:
            text += f"   ·   {extra}"
        self.status.setText(text)

    def codepoints(self) -> list[int]:
        return sorted(self.selected)
