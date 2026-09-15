"""Getting the pictures out of the game, so there is something to edit.

The workbench opens an *unpacked set*: a folder per ``.txp`` package, holding
the DDS files that package carries.  Making the first one is a different shape
of job from editing it -- it decodes gigabytes, runs for a long time on the
last step, and only the first step is needed at all.  So it gets its own window
rather than a menu item that appears to hang.

Three steps, and they are not a sequence -- they are three different places the
game keeps pictures, in the order you are likely to want them:

1. **The .txp packages** in ``Text`` and ``loading``.  This is where the menu
   art and the UI font sheets live, so it is the one that matters for a
   translation.  A couple of minutes.
2. **The XPR2 subtitle faces** in ``FONT``.  Two of them, seconds.
3. **STAGEDAT.PDT**, followed down through its QAR sub-archives to the
   textures inside them -- thousands of pictures out of 511 MB, and slow.

Each runs in its own process.  Not for tidiness: this is pure Python decoding
BC blocks, it holds the interpreter lock for the whole run, and a window that
shares a process with it stops repainting entirely.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

__all__ = ["UnpackDialog"]

#: The package this dialog drives; run as a module so its imports resolve.
ROOT = Path(__file__).resolve().parents[2]


class UnpackDialog(QDialog):
    """Runs the three unpackers, and says which one you have already done."""

    unpacked = Signal(str, str)          # unpacked folder, game folder

    def __init__(self, game: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unpack the game's textures")
        self.resize(780, 580)
        self.game = game
        self._process: QProcess | None = None
        self._then = None
        self._build()
        self._refresh()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)

        head = QLabel(
            "Everything lands under one working folder, a subfolder per "
            "package. Step 1 is the one a translation needs -- the menu art "
            "and the UI font sheets are in there. Step 3 is 511 MB and takes "
            "a while.")
        head.setWordWrap(True)
        head.setProperty("muted", True)
        outer.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("Game folder"))
        self._game = QLineEdit(self.game)
        row.addWidget(self._game, 1)
        pick = QPushButton("Choose...")
        pick.clicked.connect(lambda: self._pick(self._game, "Where is the game?"))
        row.addWidget(pick)
        outer.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Working folder"))
        self._where = QLineEdit(str(Path.home() / "Peace Walker textures"))
        self._where.textChanged.connect(self._refresh)
        row.addWidget(self._where, 1)
        pick = QPushButton("Choose...")
        pick.clicked.connect(
            lambda: self._pick(self._where, "Where should the pictures go?"))
        row.addWidget(pick)
        outer.addLayout(row)

        self._steps: list = []
        for title, hint, job in (
                ("1.  The .txp packages  (Text and loading)",
                 "Menu art and the UI font sheets. A couple of minutes.",
                 "txp"),
                ("2.  The subtitle faces  (FONT/*.xpr)",
                 "Two XPR2 faces: atlas, glyph PNGs and the character map.",
                 "xpr"),
                ("3.  STAGEDAT.PDT",
                 "Followed down through its sub-archives to the textures "
                 "inside them. 511 MB, and the slow one.",
                 "stage")):
            outer.addWidget(self._step_row(title, hint, job))

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(4000)
        outer.addWidget(self._log, 1)

        bottom = QHBoxLayout()
        self._state = QLabel("")
        self._state.setProperty("muted", True)
        bottom.addWidget(self._state, 1)
        self._open = QPushButton("Open this folder in the workbench")
        self._open.clicked.connect(self._finish)
        bottom.addWidget(self._open)
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self._kill)
        self._stop.setEnabled(False)
        bottom.addWidget(self._stop)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        outer.addLayout(bottom)

    def _step_row(self, title: str, hint: str, job: str) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 2, 0, 2)
        text = QVBoxLayout()
        name = QLabel(title)
        note = QLabel(hint)
        note.setProperty("muted", True)
        note.setWordWrap(True)
        text.addWidget(name)
        text.addWidget(note)
        row.addLayout(text, 1)
        button = QPushButton("Run")
        button.clicked.connect(lambda: self._run(job))
        button.setMinimumWidth(90)
        row.addWidget(button)
        self._steps.append((name, button, job))
        return box

    def _pick(self, field: QLineEdit, title: str) -> None:
        chosen = QFileDialog.getExistingDirectory(self, title, field.text())
        if chosen:
            field.setText(chosen)
            self._refresh()

    # -- where things go ---------------------------------------------------

    @property
    def root(self) -> Path:
        return Path(self._where.text().strip() or ".")

    def _done(self, job: str) -> bool:
        if not self.root.is_dir():
            return False
        if job == "txp":
            return any(self.root.glob("*/contents.tsv"))
        if job == "xpr":
            return any(self.root.glob("*/charmap*")) or \
                any(self.root.glob("*/atlas*"))
        return (self.root / "STAGEDAT").is_dir() or \
            any(self.root.glob("009645fa*"))

    def _refresh(self) -> None:
        busy = self._process is not None
        for name, button, job in self._steps:
            done = self._done(job)
            text = name.text().lstrip("0123456789. ")
            number = job_number(job)
            name.setText(f"{number}.  {'[done]  ' if done else ''}{text}")
            button.setEnabled(not busy)
            button.setText("Run again" if done else "Run")
        self._stop.setEnabled(busy)
        self._open.setEnabled(self._done("txp") and not busy)

    # -- running the unpackers ---------------------------------------------

    def _run(self, job: str) -> None:
        if self._process is not None:
            return
        game = Path(self._game.text().strip())
        if not game.is_dir():
            QMessageBox.warning(self, "Unpack",
                                f"That is not a folder:\n\n{game}")
            return
        self.root.mkdir(parents=True, exist_ok=True)

        self._log.appendPlainText(
            f"\n$ python -m pwtr.unpack_runner {job} \"{game}\" \"{self.root}\"\n")

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(["-m", "pwtr.unpack_runner", job, str(game),
                              str(self.root)])
        process.setWorkingDirectory(str(ROOT))
        process.setProcessChannelMode(QProcess.MergedChannels)
        # The unpackers print as they go, but Python buffers hard when its
        # output is a pipe -- without this the log would sit empty for the
        # whole run and then arrive all at once.
        # Start from the real environment.  A fresh QProcess hands back an
        # EMPTY one -- it means "inherit", not "here is what you have" -- so
        # inserting into that and setting it back replaces the lot, and the
        # child runs with two variables and nothing else.  No APPDATA means
        # Python cannot work out where its user site-packages live, and an
        # unpack died on `No module named numpy` against an interpreter that
        # had numpy installed all along.
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._ended)
        self._process = process
        self._state.setText(f"unpacking {job}...")
        self._refresh()
        process.start()

    def _read(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", "replace")
        self._log.appendPlainText(text.rstrip("\n"))
        for line in reversed(text.strip().splitlines()):
            if line.strip():
                self._state.setText(line.strip()[:110])
                break

    def _ended(self, code: int, _status) -> None:
        self._process = None
        self._state.setText("stopped" if code else "done")
        self._refresh()
        if code:
            self._log.appendPlainText(f"\n[exit {code}]")

    def _kill(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._log.appendPlainText("\n[stopped]")

    def _finish(self) -> None:
        self.unpacked.emit(str(self.root), self._game.text().strip())
        self.accept()

    def reject(self) -> None:
        if self._process is not None:
            if QMessageBox.question(
                    self, "Close",
                    "A step is still running.  Stop it and close?"
            ) != QMessageBox.Yes:
                return
            self._kill()
        super().reject()


def job_number(job: str) -> int:
    return {"txp": 1, "xpr": 2, "stage": 3}[job]
