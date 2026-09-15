"""The texture workbench: see what you have edited, and put it in the game.

The loop this exists for is short -- paint a DDS in whatever editor you like,
look at it beside the game's own version, build, install.  So the window is
built around that: a list of the pictures with their state, the two versions
side by side, and one button that writes.

The things it refuses to let you get wrong are the ones that cost a wasted
round trip.  All of them are size: the game addresses a texture by its offset
inside the package, so a picture saved at a different resolution, in a
different format, or simply longer than the one it replaces would push every
later texture out of place.  Those are named and refused here, where the
message can still point at the picture, rather than in the packer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from pwtr.project import find_game
from pwtr.textures import TextureError, TextureSet

__all__ = ["TextureBench", "last_opened", "remember", "remembered"]

#: The last edit folder opened.  Both ways in -- the standalone launcher and
#: the Textures menu of the translation workbench -- read and write this, so
#: whichever you use next opens where you left off.
RECENT = Path.home() / ".pwtex.json"


def remembered() -> dict:
    try:
        return json.loads(RECENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(folder, game) -> None:
    try:
        RECENT.write_text(json.dumps({"folder": str(folder),
                                      "game": str(game)}, indent=1),
                          encoding="utf-8")
    except OSError:
        pass


def last_opened() -> TextureSet | None:
    """Re-open the folder from last time, or None if there is not one."""
    last = remembered()
    folder, game = last.get("folder"), last.get("game")
    if not folder or not game:
        return None
    try:
        return TextureSet(folder, game)
    except (TextureError, OSError):
        return None


class TextureBench(QMainWindow):
    def __init__(self, textures: TextureSet | None = None):
        super().__init__()
        self.textures = textures
        self.setWindowTitle("Peace Walker textures")
        self.resize(1180, 760)
        self._rows: list = []
        self._build()
        if textures is not None:
            self.reload()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        self._act(file_menu, "Unpack the game's textures...", self.unpack,
                  QKeySequence.New)
        self._act(file_menu, "Open an unpacked folder...", self.open_folder,
                  QKeySequence.Open)
        self._act(file_menu, "Refresh", self.reload, QKeySequence.Refresh)
        file_menu.addSeparator()
        self._act(file_menu, "Open this picture in the editor",
                  self.open_external, QKeySequence("Ctrl+E"))
        self._act(file_menu, "Show the folder", self.show_folder)
        file_menu.addSeparator()
        self._act(file_menu, "Quit", self.close, QKeySequence.Quit)

        game_menu = menu.addMenu("&Game")
        self._act(game_menu, "Check before building", self.check,
                  QKeySequence("Ctrl+K"))
        self._act(game_menu, "Build the packages...", self.build,
                  QKeySequence("Ctrl+B"))

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 6)

        header = QHBoxLayout()
        self._where = QLabel("No unpacked folder open")
        self._where.setProperty("heading", True)
        self._state = QLabel("")
        self._state.setProperty("muted", True)
        header.addWidget(self._where)
        header.addStretch(1)
        header.addWidget(self._state)
        outer.addLayout(header)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 6, 0)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter by name, or type 'edited'")
        self._filter.textChanged.connect(self._refilter)
        left_box.addWidget(self._filter)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.currentItemChanged.connect(lambda *_: self._show())
        left_box.addWidget(self._list, 1)
        split.addWidget(left)

        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(6, 0, 0, 0)

        pictures = QHBoxLayout()
        self._before_box, self._before = self._panel("The game's version")
        self._after_box, self._after = self._panel("Yours")
        pictures.addLayout(self._before_box)
        pictures.addLayout(self._after_box)
        right_box.addLayout(pictures, 1)

        self._detail = QLabel("")
        self._detail.setProperty("muted", True)
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_box.addWidget(self._detail)

        buttons = QHBoxLayout()
        for label, slot in (("Check", self.check),
                            ("Build the packages", self.build),
                            ("Open in editor", self.open_external),
                            ("Show the folder", self.show_folder)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        right_box.addLayout(buttons)
        split.addWidget(right)

        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        outer.addWidget(split, 1)
        self._status = self.statusBar()

    def _panel(self, title: str):
        box = QVBoxLayout()
        caption = QLabel(title)
        caption.setProperty("muted", True)
        caption.setAlignment(Qt.AlignHCenter)
        view = QLabel()
        view.setAlignment(Qt.AlignCenter)
        view.setMinimumSize(QSize(260, 200))
        # A mid grey ground.  The font sheets are white on transparent and
        # look like a blank page on anything else.
        view.setStyleSheet("background:#6e6e6e;border:1px solid #3a3a3a;")
        box.addWidget(caption)
        box.addWidget(view, 1)
        return box, view

    def _act(self, menu, label: str, slot, shortcut=None) -> QAction:
        action = QAction(label, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    # -- loading -----------------------------------------------------------

    def unpack(self) -> None:
        """Get the pictures out of the game, for when there is nothing to open.

        The workbench edits an unpacked set; this is where the first one comes
        from.
        """
        from pwtr.qt.unpackset import UnpackDialog

        game = self.textures.game if self.textures else find_game()
        dialog = UnpackDialog(str(game or ""), self)
        dialog.unpacked.connect(self._adopt)
        dialog.exec()

    def _adopt(self, folder: str, game: str) -> None:
        try:
            self.textures = TextureSet(folder, game)
        except (TextureError, OSError) as error:
            QMessageBox.critical(self, "Textures", str(error))
            return
        remember(folder, game)
        self.reload()

    def open_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Open an unpacked texture folder",
            str(self.textures.root if self.textures else Path.home()))
        if not chosen:
            return

        game = self.textures.game if self.textures else find_game()
        if game is None or not Path(game).is_dir():
            picked = QFileDialog.getExistingDirectory(
                self, "Where is the game? (MGS_PW\\mgspw)")
            if not picked:
                return
            game = picked
        self._adopt(chosen, str(game))

    def reload(self) -> None:
        if self.textures is None:
            return
        try:
            self.textures = TextureSet(self.textures.root, self.textures.game)
        except (TextureError, OSError) as error:
            QMessageBox.critical(self, "Textures", str(error))
            return
        self._where.setText(str(self.textures.root))
        self._refilter()

    def _refilter(self) -> None:
        self._list.clear()
        self._rows = []
        if self.textures is None:
            self._sync()
            return

        needle = self._filter.text().strip().casefold()
        only_edited = needle == "edited"
        for texture in self.textures:
            edited = texture.edited()
            if only_edited and not edited:
                continue
            if needle and not only_edited and needle not in texture.key.casefold():
                continue
            item = QListWidgetItem(
                f"{texture.key}    {'edited' if edited else ''}".rstrip())
            if edited:
                from pwtr.qt import theme
                item.setForeground(theme.accent_brush())
            self._list.addItem(item)
            self._rows.append(texture)

        if self._rows:
            self._list.setCurrentRow(0)
        else:
            self._show()
        self._sync()

    def _current(self):
        row = self._list.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    # -- showing one picture ----------------------------------------------

    def _show(self) -> None:
        texture = self._current()
        if texture is None:
            self._before.clear()
            self._after.clear()
            self._detail.setText("")
            return

        stock = texture.original()
        self._put(self._before, texture.image(stock) if stock else None)
        self._put(self._after, texture.image())

        problem = texture.complaint() if texture.edited() else None
        detail = (f"{texture.package.name}  --  texture {texture.index}, "
                  f"{texture.width}x{texture.height} {texture.fourcc}\n"
                  f"{texture.path}")
        if problem:
            detail += f"\n\nThis will be refused: it {problem}."
        self._detail.setText(detail)
        self._detail.setProperty("warn", bool(problem))
        self._detail.style().unpolish(self._detail)
        self._detail.style().polish(self._detail)

    def _put(self, view: QLabel, rgba) -> None:
        if rgba is None:
            view.setText("cannot decode")
            return
        height, width = rgba.shape[0], rgba.shape[1]
        data = rgba.tobytes()
        # Held on the label: QImage does not copy the buffer, and a numpy
        # array that goes out of scope here takes the picture with it.
        view._buffer = data
        image = QImage(data, width, height, width * 4, QImage.Format_RGBA8888)
        view.setPixmap(QPixmap.fromImage(image).scaled(
            view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event) -> None:      # noqa: N802
        super().resizeEvent(event)
        self._show()

    # -- the outside editor ------------------------------------------------

    def open_external(self) -> None:
        texture = self._current()
        if texture is not None:
            _open(texture.path)

    def show_folder(self) -> None:
        texture = self._current()
        if texture is not None:
            _open(texture.path.parent)
        elif self.textures is not None:
            _open(self.textures.root)

    # -- building ----------------------------------------------------------

    def check(self) -> bool:
        """Say what would be refused, before anything is written."""
        if self.textures is None:
            return False
        edits = self.textures.edits()
        if not edits:
            QMessageBox.information(
                self, "Check",
                "Nothing is edited yet.\n\nA picture counts as edited when it "
                "differs from the one the game ships, so an untouched dump "
                "shows as clean.")
            return False

        complaints = self.textures.complaints()
        if complaints:
            QMessageBox.warning(
                self, "These would be refused",
                f"{len(edits)} edited picture(s), of which "
                f"{len(complaints)} cannot go back:\n\n"
                + "\n".join(f"  {t.key}: it {why}" for t, why in complaints[:12])
                + ("\n  ..." if len(complaints) > 12 else "")
                + "\n\nThe game addresses textures by offset inside the "
                  "package, so a replacement has to keep the original's size, "
                  "format and byte length.")
            return False

        QMessageBox.information(
            self, "Check",
            f"{len(edits)} edited picture(s), all the right size and format.")
        return True

    def build(self) -> None:
        if self.textures is None:
            return
        if not self.check():
            return

        out = QFileDialog.getExistingDirectory(
            self, "Where should the patched packages go?",
            str(self.textures.root.parent))
        if not out:
            return

        notes: list[str] = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.textures.build(out, notes.append)
        except Exception as error:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Could not build", str(error))
            return
        QApplication.restoreOverrideCursor()

        message = (f"{result['edited']} edited texture(s) into "
                   f"{result['packages']} package(s).\n\n"
                   + "\n".join(notes)
                   + f"\n\nWritten under:\n{result['out']}")
        if result["warnings"]:
            message += "\n\nRefused:\n" + "\n".join(result["warnings"])
        QMessageBox.information(self, "Built", message)

    # -- chrome ------------------------------------------------------------

    def _sync(self) -> None:
        if self.textures is None:
            self._state.setText("")
            return
        total = len(self.textures.all())
        edited = len(self.textures.edits())
        shown = len(self._rows)
        state = f"{edited} edited of {total}"
        if shown != total:
            state += f"   ({shown} shown)"
        self._state.setText(state)


def _open(path: Path) -> None:
    """Show a file or folder in the file manager, on whichever system this is."""
    try:
        if sys.platform == "win32":
            os.startfile(path)                       # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass
