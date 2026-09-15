"""Long work off the window's thread, behind a progress dialog that stays alive.

A build used to run on the thread that paints the window.  The dialog could
only redraw when the build happened to report progress, so between reports --
a 544 MB copy, a minute of ffmpeg, a repack -- nothing was painted and Windows
titled the whole program "(Not Responding)".  Nothing was wrong; it looked
broken anyway.

Here the work runs on a worker thread and talks to the dialog only through
signals, which Qt delivers on the window's own thread.  The window keeps
painting, the clock keeps ticking, and the bar moves whenever the work says
how far along it is.

How far along is read from the messages the builds already write, so none of
them had to learn a new way to report:

    "... 37%"                    a percentage, used as it stands
    "Block 250 of 2137 -- ..."   a count, used as a fraction

A message with neither leaves the bar where it was.
"""
from __future__ import annotations

import re
import time
import traceback

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (QDialog, QLabel, QPlainTextEdit, QProgressBar,
                               QVBoxLayout)

PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
COUNT = re.compile(r"(\d+)\s+of\s+(\d+)")


def fraction_of(message: str) -> float | None:
    """How far along a progress message says the work is, if it says."""
    found = PERCENT.search(message)
    if found:
        return max(0.0, min(1.0, float(found.group(1)) / 100.0))
    found = COUNT.search(message)
    if found and int(found.group(2)) > 0:
        return max(0.0, min(1.0, int(found.group(1)) / int(found.group(2))))
    return None


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return (f"{hours}:{minutes:02d}:{secs:02d}" if hours
            else f"{minutes}:{secs:02d}")


class _Worker(QObject):
    noted = Signal(str)
    ticked = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, work):
        super().__init__()
        self._work = work

    @Slot()
    def run(self) -> None:
        try:
            result = self._work(self.noted.emit, self.ticked.emit)
        except BaseException as error:          # reported, never swallowed
            error.trace = traceback.format_exc()
            self.failed.emit(error)
        else:
            self.succeeded.emit(result)


class JobDialog(QDialog):
    """What a long job is doing, how far it has got, and how long it has left."""

    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setMinimumWidth(520)

        self.notes: list[str] = []
        self.running = True
        self.outcome: dict = {}
        self._started = time.monotonic()
        self._fraction: float | None = None
        self._since = None                      # when the bar last started over
        self._thread: QThread | None = None

        layout = QVBoxLayout(self)
        self._step = QLabel("Starting...")
        self._step.setWordWrap(True)
        layout.addWidget(self._step)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)                # moving, until a fraction arrives
        self._bar.setTextVisible(True)
        layout.addWidget(self._bar)

        self._time = QLabel("")
        layout.addWidget(self._time)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)
        self._log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self._log)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_time)
        self._timer.start(500)
        self._refresh_time()

    # -- fed by the worker, delivered on this thread ------------------------

    @Slot(str)
    def note(self, message: str) -> None:
        """Something worth keeping: it goes in the log and the report."""
        self.notes.append(message)
        self._log.appendPlainText(message)
        self._show(message)

    @Slot(str)
    def tick(self, message: str) -> None:
        """Something worth watching, which is not the same thing."""
        self._show(message)

    @Slot(object)
    def succeed(self, result) -> None:
        self._finish("result", result)

    @Slot(object)
    def fail(self, error) -> None:
        self._finish("error", error)

    # -- the rest ------------------------------------------------------------

    def _show(self, message: str) -> None:
        self._step.setText(message)
        fraction = fraction_of(message)
        if fraction is None:
            return
        if self._bar.maximum() == 0:
            self._bar.setRange(0, 1000)
        # A job of several phases starts its count again at each one; time
        # left is estimated from when the current run began, not from the
        # start of the whole job.
        if self._fraction is None or fraction < self._fraction - 0.05:
            self._since = time.monotonic()
        self._fraction = fraction
        self._bar.setValue(int(fraction * 1000))
        self._bar.setFormat(f"{fraction * 100:.0f}%")
        self._refresh_time()

    def _refresh_time(self) -> None:
        elapsed = time.monotonic() - self._started
        text = f"Elapsed {_clock(elapsed)}"
        if self._fraction and self._fraction >= 0.02 and self._since:
            run = time.monotonic() - self._since
            left = run * (1.0 - self._fraction) / self._fraction
            if self._fraction < 1.0:
                text += f"   --   about {_clock(left)} left"
        self._time.setText(text)

    def _finish(self, key: str, value) -> None:
        self.outcome[key] = value
        self.running = False
        self._timer.stop()
        if self._thread is not None:
            self._thread.quit()
        self.accept()

    def reject(self) -> None:                    # Esc
        if not self.running:
            super().reject()

    def closeEvent(self, event) -> None:         # noqa: N802
        if self.running:
            event.ignore()
        else:
            super().closeEvent(event)


def run_job(parent, title: str, work):
    """Run ``work(note, tick)`` on a worker thread behind a live dialog.

    Returns ``(result, error, notes)``: ``error`` is the exception if one was
    raised -- with the traceback on ``error.trace`` -- and ``notes`` everything
    the work asked to be kept.

    ``work`` must not touch the window.  Everything it reports goes through
    ``note`` and ``tick``, which are safe to call from the worker.
    """
    dialog = JobDialog(parent, title)
    thread = QThread(dialog)
    worker = _Worker(work)
    worker.moveToThread(thread)
    dialog._thread = thread

    worker.noted.connect(dialog.note)
    worker.ticked.connect(dialog.tick)
    worker.succeeded.connect(dialog.succeed)
    worker.failed.connect(dialog.fail)
    thread.started.connect(worker.run)

    thread.start()
    dialog.exec()
    thread.wait()
    return (dialog.outcome.get("result"), dialog.outcome.get("error"),
            dialog.notes)
