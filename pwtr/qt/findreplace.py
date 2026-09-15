"""Find and replace across the containers.

The string handling lives here; the workbench owns the lines and does the
walking, since it is the thing that knows which container is showing and which
rows the filter is currently hiding.

**Find** runs from the selected line onwards: the rest of the open container,
then each one after it, wrapping back round.  Replacing has two scopes:

* **this container** -- the one on screen, Menus or Story
* **all containers** -- both of them

Both write into memory and mark the container unsaved, so nothing reaches disk
until Save.

Replace only ever writes the **Arabic** column.  A project stores each line's
English next to its translation, and the build finds a line by matching that
English against the game's own string -- so editing it would simply lose the
line.  English
can still be searched -- it is often the only way to find a line -- it just
cannot be rewritten.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton)

__all__ = ["FindReplace", "find_in", "replace_all"]


def find_in(haystack: str, needle: str, match_case: bool) -> int:
    """Offset of ``needle`` in ``haystack``, or -1."""
    if not needle:
        return -1
    if match_case:
        return haystack.find(needle)
    return haystack.lower().find(needle.lower())


def replace_all(text: str, find: str, repl: str,
                match_case: bool) -> tuple[str, int]:
    """Every occurrence replaced; returns the new text and how many.

    Case-insensitive replacement keeps the surrounding text exactly as it was
    -- only the matched runs are swapped -- so casing elsewhere in the line
    survives.
    """
    if not find:
        return text, 0
    if match_case:
        return text.replace(find, repl), text.count(find)

    lowered, target = text.lower(), find.lower()
    out: list[str] = []
    count = 0
    start = 0
    while True:
        hit = lowered.find(target, start)
        if hit < 0:
            break
        out.append(text[start:hit])
        out.append(repl)
        start = hit + len(find)
        count += 1
    out.append(text[start:])
    return "".join(out), count


class FindReplace(QDialog):
    """The Find / Replace box.

    Non-modal on purpose: it stays open beside the editor so a translator can
    keep working between searches, the way the same box behaves in an editor.
    """

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.setWindowTitle("Find / Replace")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(660)

        grid = QGridLayout(self)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("Find what:"), 0, 0)
        self.find_field = QLineEdit()
        self.find_field.returnPressed.connect(self.find_next)
        grid.addWidget(self.find_field, 0, 1)

        grid.addWidget(QLabel("Replace with:"), 1, 0)
        self.replace_field = QLineEdit()
        self.replace_field.setLayoutDirection(Qt.RightToLeft)
        grid.addWidget(self.replace_field, 1, 1)

        options = QHBoxLayout()
        self.match_case = QCheckBox("Match case")
        self.search_up = QCheckBox("Search up")
        self.search_english = QCheckBox("Search English too")
        self.search_english.setChecked(True)
        self.search_english.setToolTip(
            "Find also looks in the English column.\n"
            "Replace always writes the Arabic column -- the English has to "
            "keep matching the original container or the build will refuse it.")
        for box in (self.match_case, self.search_up, self.search_english):
            options.addWidget(box)
        options.addStretch(1)
        grid.addLayout(options, 2, 0, 1, 2)

        self._button(grid, 0, "Find Next", self.find_next, default=True)
        self._button(grid, 1, "Replace", self.replace_one)
        self._button(grid, 2, "Replace All (this container)", self.replace_here)
        self._button(grid, 3, "Replace All (all containers)",
                     self.replace_everywhere)
        self._button(grid, 4, "Close", self.close)

        note = QLabel("Replace writes the Arabic column only.")
        note.setProperty("muted", True)
        grid.addWidget(note, 3, 0, 1, 2)

        grid.setColumnStretch(1, 1)
        grid.setRowStretch(5, 1)

        QShortcut(QKeySequence("Esc"), self, self.close)

    def _button(self, grid, row: int, label: str, slot, default: bool = False):
        button = QPushButton(label)
        button.clicked.connect(slot)
        button.setAutoDefault(False)
        if default:
            button.setDefault(True)
        grid.addWidget(button, row, 2)
        return button

    # -- what the buttons ask for -----------------------------------------

    @property
    def _options(self) -> dict:
        return {
            "match_case": self.match_case.isChecked(),
            "search_english": self.search_english.isChecked(),
        }

    def _needle(self) -> str | None:
        text = self.find_field.text()
        if not text:
            self.find_field.setFocus()
            return None
        return text

    def find_next(self) -> None:
        needle = self._needle()
        if needle is None:
            return
        if not self.host.find_next(needle, up=self.search_up.isChecked(),
                                   **self._options):
            self.host.say(f"No match for {needle!r}")

    def replace_one(self) -> None:
        needle = self._needle()
        if needle is None:
            return
        self.host.replace_one(needle, self.replace_field.text(),
                              up=self.search_up.isChecked(), **self._options)

    def replace_here(self) -> None:
        needle = self._needle()
        if needle is None:
            return
        count = self.host.replace_all_here(
            needle, self.replace_field.text(), self.match_case.isChecked())
        self.host.say(f"{count} replacement(s) in this container"
                      if count else f"No Arabic match for {needle!r} here")

    def replace_everywhere(self) -> None:
        needle = self._needle()
        if needle is None:
            return
        repl = self.replace_field.text()

        preview = self.host.count_everywhere(needle, self.match_case.isChecked())
        if not preview:
            self.host.say(f"No Arabic match for {needle!r}")
            QMessageBox.information(self, "Find / Replace",
                                    f"No Arabic match for {needle!r}.")
            return
        matches, containers = preview

        answer = QMessageBox.question(
            self, "Find / Replace",
            f"Replace {matches} occurrence(s) of {needle!r} with {repl!r}\n"
            f"across {containers} container(s), in the Arabic column?\n\n"
            "Nothing is written to disk until you save.")
        if answer != QMessageBox.Yes:
            return

        matches, containers = self.host.replace_all_everywhere(
            needle, repl, self.match_case.isChecked())
        self.host.say(f"{matches} replacement(s) across "
                      f"{containers} container(s) -- unsaved")
        QMessageBox.information(
            self, "Find / Replace",
            f"{matches} replacement(s) across {containers} container(s).\n\n"
            "Save to write them to the project.")
