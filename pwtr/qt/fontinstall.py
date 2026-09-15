"""Put a translation's characters into the game's fonts, and preview them.

The modder chooses the script -- Arabic, which is shaped, or anything drawn as
typed -- and the set of characters the face must carry, with a picker modelled
on the PS3 toolkit's.  Everything below was worked out on Arabic and holds for
any script: the engine only reaches low code points, so whatever the set
holds above U+024F, or the stock face does not draw, is moved to a code point
the plan chooses and painted there.

Peace Walker has two font systems that matter here, and they could not be less
alike:

* the **XPR faces** in ``FONT`` draw the story, the briefings and most of the
  menus.  They are indexed by real Unicode with 65 375 slots -- but the engine
  only looks up the first few hundred of them, so nothing can be left where
  Unicode puts it.  The plan moves every drawn character down into free
  punctuation and Latin Extended, which is also what makes Arabic fit in
  English's byte budget.  That plan is the project's, not this dialog's: the
  text is stored as the code points it chooses, so the face and the build have
  to agree exactly.
* the **caps atlas** has no character map at all.  A byte picks a cell, 63 of
  its 160 cells are empty, and Arabic wants 141 -- so it does not fit, and even
  giving up every Latin letter reaches only 115.  It draws the 80 menu strings
  ``.olang`` marks Style ``0x0001``: BUTTON CONFIG, PRESS START and their like.

## Why there is a preview

Everything about how the letters sit comes down to two numbers, and neither
can be settled by reasoning -- only by looking.  ``fill`` is the size, capped
by geometry: the cell is 67 pixels and the script wants 80 of them, so past a
point the tallest and deepest forms start losing ink and the dialog says which.
``nudge`` moves one form up or down, for the cases where the face itself draws
a letter off the line -- this one gives the jeem/hah/khah family no descent at
all and the noon a bowl only half as deep as a meem's.

The preview is not an impression of the result.  It is drawn by the same code
at the same point size on the same baseline row with the same crop, so what it
shows is what gets painted.

Nothing is written into the game here.  The patched packages land in the
project's ``output/`` folder, and installing them is the same deliberate step
as installing the text.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from pwtr import arabic, compact, fonts
from pwtr.qt.charpicker import CharPicker

__all__ = ["FontInstaller"]

#: Forms this face draws sitting flat where Arabic wants them under the line.
#: The jeem/hah/khah family in its joining forms has no descent at all, and
#: the noon bowl only 10 or 11 pixels against a yeh's 16 and a meem's 19.
#: Four pixels is as far as any of them go before the entry and exit strokes
#: stop meeting their neighbours and the word visibly breaks.
DEFAULT_NUDGE = {form: 4 for form in ("ﺟﺠ"      # jeem  init, med
                                      "ﺣﺤ"      # hah   init, med
                                      "ﺧﺨ"      # khah  init, med
                                      "ﻥﻦ")}    # noon  isol, final

#: What the preview falls back to when the project has nothing translated.
SAMPLE = "كاز، ما حال تلك المنشأة التي أعطانا إياها «البروفيسور»؟"

#: The cell height of the shipped faces.  Both of them, and it is not ours to
#: choose -- it is what the atlas is cut into.
CELL_H = 67


class FontInstaller(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self._plan = None
        self._metrics = None
        self.setWindowTitle("Install characters into the game's fonts")
        # Choosing a script or a set changes the project's manifest in
        # memory, because the plan reads it from there.  Closing without
        # installing puts both back.
        font = project.manifest.get("font", {})
        self._was = (font.get("script"), font.get("charset"))
        self._installed = False
        # Nothing inside may decide how wide this gets: a preview of a long
        # line is a pixmap two thousand pixels across, and a QLabel with a
        # pixmap asks for exactly that much room.  So the window is sized
        # against the screen and the wide things are told to scroll instead.
        screen = QApplication.primaryScreen()
        room = screen.availableGeometry() if screen else None

        # Everything above the buttons scrolls.  Qt works in logical pixels,
        # so at 150% a 1920x1080 display is 1280x720 to lay out in and a
        # dialog that assumed a thousand pixels of height simply runs off the
        # bottom.  Rather than guess a height that suits every scaling, let
        # the body scroll and give the window an explicit small minimum -- an
        # explicit minimum overrides the one the layout computes, which is
        # what otherwise keeps a dialog too big to fit.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        page = QVBoxLayout(body)
        self._body = QScrollArea()
        self._body.setWidget(body)
        self._body.setWidgetResizable(True)
        self._body.setFrameShape(QScrollArea.NoFrame)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self._body, 1)

        page.addWidget(self._charset_group())

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self._sub_ttf = self._picker(grid, 0, "Subtitle face")
        self._menu_ttf = self._picker(grid, 1, "Menu face")
        page.addLayout(grid)

        page.addWidget(self._subtitle_group(), 1)
        page.addWidget(self._atlas_group())

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(110)
        page.addWidget(self._log)

        page.addStretch(1)

        # The buttons stay put: a Close you have to scroll to find is not a
        # Close.
        row = QHBoxLayout()
        row.setContentsMargins(9, 0, 9, 9)
        self._install_button = QPushButton("Install into project output")
        self._install_button.clicked.connect(self.install)
        row.addWidget(self._install_button)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        row.addWidget(box)
        outer.addLayout(row)
        QShortcut(QKeySequence("Esc"), self, self.reject)

        # The preview costs a rasterisation of the whole set, so it waits
        # for typing to stop rather than following every keystroke.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.repreview)

        self.setMinimumSize(480, 300)
        self.resize(min(980, room.width() - 60) if room else 980,
                    min(860, room.height() - 60) if room else 860)

        self._restore()
        self.refresh()

    # -- construction ------------------------------------------------------

    def _picker(self, grid, row: int, label: str) -> QLabel:
        grid.addWidget(QLabel(label), row, 0)
        field = QLabel("none chosen")
        field.setProperty("muted", True)
        field.setToolTip("")
        # A font path is long enough to set the width of the whole dialog,
        # so only the file name is shown and the path lives in the tooltip.
        field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        grid.addWidget(field, row, 1)
        button = QPushButton("Choose a font...")
        button.clicked.connect(lambda: self._choose(field))
        grid.addWidget(button, row, 2)
        grid.setColumnStretch(1, 1)
        return field

    def _charset_group(self) -> QGroupBox:
        group = QGroupBox("Script and characters")
        outer = QVBoxLayout(group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Script"))
        self._script = QComboBox()
        self._script.addItem("Arabic -- shaped, right to left", "arabic")
        self._script.addItem("Other -- drawn as typed, left to right",
                             "plain")
        self._script.setToolTip(
            "Arabic is joined into presentation forms and put in visual "
            "order before it is written, because the engine has no shaper. "
            "Every other script -- Cyrillic, Greek, accented Latin -- is "
            "written exactly as typed.\n\nChanging this changes what every "
            "container writes: rebuild the text after installing.")
        self._script.currentIndexChanged.connect(self._script_changed)
        row.addWidget(self._script, 1)
        choose = QPushButton("Choose characters...")
        choose.clicked.connect(self._choose_characters)
        row.addWidget(choose)
        automatic = QPushButton("Automatic")
        automatic.setToolTip(
            "No chosen set: plan whatever the translation draws that the "
            "engine cannot reach. The way an Arabic project has always "
            "worked.")
        automatic.clicked.connect(lambda: self._set_charset(None))
        row.addWidget(automatic)
        outer.addLayout(row)

        self._charset_note = QLabel("")
        self._charset_note.setWordWrap(True)
        outer.addWidget(self._charset_note)
        return group

    def _subtitle_group(self) -> QGroupBox:
        group = QGroupBox("Subtitle face -- story, briefings, menus")
        outer = QVBoxLayout(group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Plan from"))
        self._scope = QComboBox()
        self._scope.addItem("Briefings only", ("briefing",))
        self._scope.addItem("Briefings, Menus and Missions",
                            ("briefing", "menus", "stage"))
        self._scope.addItem("Everything translated", None)
        self._scope.setToolTip(
            "Which categories the plan reads. A form that earns no code point "
            "here is a form the face will not carry, so this must cover "
            "everything you intend to build -- and rebuilding the text after "
            "changing it, because the plan and the painted glyphs are one "
            "pair.")
        self._scope.setMinimumContentsLength(10)
        self._scope.currentIndexChanged.connect(self._replan)
        row.addWidget(self._scope, 1)

        self._freeze = QCheckBox("Freeze mapping")
        self._freeze.setToolTip(
            "Keep the code points the installed face already uses, and only "
            "repaint the glyphs.\n\n"
            "The plan is worked out from the text, so translating one more "
            "line can move every form to a different code point -- and then "
            "the text has to be rebuilt to match, which is four containers "
            "and a quarter of an hour to see whether a nudge of 2 looks "
            "better than 3.\n\n"
            "Frozen, alif stays wherever it is and nothing already built "
            "goes stale: install the face, look at it in the game, adjust, "
            "install again. Nothing else needs rebuilding.\n\n"
            "Turn it off when you have translated more text and want the "
            "common forms to earn the cheap slots again -- then rebuild "
            "everything once.")
        self._freeze.toggled.connect(self._replan)

        row.addWidget(self._freeze)
        outer.addLayout(row)

        # The second row is how the glyphs are drawn; the first is which code
        # points they go to.  They were one row until it held eleven widgets
        # and the dialog, which does not scroll sideways, simply cut the last
        # two off -- so Freeze mapping was there and invisible.
        row = QHBoxLayout()
        row.addWidget(QLabel("Spend"))
        self._reclaim = QComboBox()
        self._reclaim.addItem("capitals", "capitals")
        self._reclaim.addItem("lower case", "lower")
        self._reclaim.setToolTip(
            "Which 26 letters give up their code point so an Arabic form can "
            "have it for one byte instead of two. The letter is not lost -- "
            "every line this project writes keeps it, at a two-byte code "
            "point. What is lost is that letter in text nobody rendered "
            "through the plan.\n\n"
            "Capitals cost the staff codenames: those are read straight out "
            "of the save and are all upper case, so the whole roster turns "
            "to Arabic gibberish.\n\n"
            "Lower case costs only the lines still untranslated, and gets "
            "two more one-byte slots than capitals do.")
        self._reclaim.currentIndexChanged.connect(self._touch)
        row.addWidget(self._reclaim)
        row.addWidget(QLabel("Size"))
        self._fill = QDoubleSpinBox()
        self._fill.setRange(0.50, 1.40)
        self._fill.setSingleStep(0.01)
        self._fill.setDecimals(2)
        self._fill.setValue(0.97)
        self._fill.setToolTip(
            "How much of the 67 pixel cell the script may take, ascender to "
            "deepest descender. The letters are always scaled down -- Arabic "
            "wants 80 pixels of it -- so this says by how much further. Above "
            "1.0 the tallest and deepest forms start losing ink, and the line "
            "underneath says which.")
        self._fill.valueChanged.connect(self._touch)
        row.addWidget(self._fill)

        row.addWidget(QLabel("Join"))
        self._join = QSpinBox()
        self._join.setRange(0, 255)
        self._join.setSingleStep(16)
        self._join.setValue(128)
        self._join.setToolTip(
            "Trims the soft edge off the sides of a letter, keeping only "
            "columns holding a pixel at least this opaque. Tiles are laid "
            "edge to edge, so a connecting stroke that fades out over its "
            "last column meets one fading in, and the join shows as a "
            "hairline gap -- worst on the medial and final forms whose "
            "tongue runs to the edge. Higher trims more and sets the letters "
            "closer. Zero leaves the edges as the font draws them. Marks are "
            "never trimmed.")
        self._join.valueChanged.connect(self._touch)
        row.addWidget(self._join)

        row.addWidget(QLabel("Overlap"))
        self._tighten = QSpinBox()
        self._tighten.setRange(0, 4)
        self._tighten.setValue(4)
        self._tighten.setToolTip(
            "Pixels taken off the advance, so the next letter is drawn that "
            "much further back and the two overlap. Join fixes the soft edge "
            "the rasteriser leaves in the atlas; this is for the one the "
            "engine leaves, which Join cannot reach -- tiles sit a pixel "
            "apart, and a quad sampled with filtering takes half a texel of "
            "that empty column into each side. If the letters still look a "
            "half pixel apart with Join up, raise this. Marks are "
            "unaffected.")
        self._tighten.valueChanged.connect(self._touch)
        row.addWidget(self._tighten)
        row.addStretch(1)
        outer.addLayout(row)

        self._size = QLabel("")
        self._size.setWordWrap(True)
        outer.addWidget(self._size)

        outer.addWidget(self._heading(
            "Nudge -- every form the face draws, in pixels, down"))
        self._nudge = QTableWidget(0, 3)
        self._nudge.setHorizontalHeaderLabels(["Form", "Letter", "Pixels"])
        self._nudge.setMinimumHeight(200)
        self._nudge.setColumnWidth(0, 60)
        self._nudge.setColumnWidth(1, 190)
        self._nudge.horizontalHeader().setStretchLastSection(True)
        self._nudge.verticalHeader().setVisible(False)
        self._nudge.itemChanged.connect(self._touch)
        outer.addWidget(self._nudge)

        row = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("find a letter -- meem, final, م")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        row.addWidget(QLabel("Find"))
        row.addWidget(self._filter, 1)
        reset = QPushButton("Defaults")
        reset.setToolTip("Back to the two the face is known to draw off the "
                         "line, and zero for everything else.")
        reset.clicked.connect(lambda: self._set_nudge(DEFAULT_NUDGE))
        row.addWidget(reset)
        clear = QPushButton("All zero")
        clear.clicked.connect(lambda: self._set_nudge({}))
        row.addWidget(clear)
        outer.addLayout(row)

        outer.addWidget(self._heading("Preview -- what will be painted"))
        self._probe = QLineEdit()
        self._probe.setToolTip("Type Arabic as you read it; it is shaped for "
                               "the preview the same way the build shapes it.")
        self._probe.textChanged.connect(self._touch)
        outer.addWidget(self._probe)
        self._preview = QLabel("")
        self._preview.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._preview.setStyleSheet("background: #ffffff;")
        # Not resizable: the label is sized to its pixmap by hand.  Left
        # resizable, the scroll area adopts the label's minimum width -- 1702
        # pixels for a line of any length -- and the dialog inherits it.
        self._preview.setMinimumSize(0, 0)
        self._preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._preview)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._scroll.setFixedHeight(160)
        self._scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._scroll.setMinimumWidth(240)
        outer.addWidget(self._scroll)
        return group

    def _atlas_group(self) -> QGroupBox:
        group = QGroupBox("Caps atlas -- the 80 Style 0x0001 menu strings")
        outer = QVBoxLayout(group)
        self._borrow: dict[str, QCheckBox] = {}
        for name, first, last, why in arabic.BORROWABLE:
            box = QCheckBox(f"Give up {name} ({last - first + 1} cells)")
            box.setChecked(name in self.project.borrow)
            box.toggled.connect(self.refresh)
            self._borrow[name] = box
            outer.addWidget(box)
            # A checkbox label does not wrap, so a sentence of reasoning in
            # one would push the dialog off the screen.  It goes below.
            reason = QLabel(why)
            reason.setWordWrap(True)
            reason.setProperty("muted", True)
            reason.setIndent(24)
            outer.addWidget(reason)
        self._capacity = QLabel("")
        self._capacity.setWordWrap(True)
        outer.addWidget(self._capacity)
        return group

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("heading", True)
        label.setWordWrap(True)          # or it sets the dialog's width
        return label

    # -- state -------------------------------------------------------------

    def _restore(self) -> None:
        stored = self.project.manifest.get("font", {})
        self._script.blockSignals(True)
        self._script.setCurrentIndex(
            max(0, self._script.findData(self.project.script)))
        self._script.blockSignals(False)
        self._script_controls()
        self._describe_charset()
        for field, key in ((self._menu_ttf, "ttf"), (self._sub_ttf, "sub_ttf")):
            if stored.get(key):
                self._show_path(field, stored[key])
        self._fill.setValue(float(stored.get("fill", 0.97)))
        self._join.setValue(int(stored.get("join", 128)))
        self._tighten.setValue(int(stored.get("tighten", 4)))
        where = self._reclaim.findData(stored.get("reclaim", "capitals"))
        self._reclaim.setCurrentIndex(max(0, where))
        # The scope decides which forms earn a code point, so opening the
        # dialog on a different one than the installed face was planned from
        # is a silent replan -- a smaller face and every letter moved.
        if "scope" in stored:
            want = stored["scope"]
            want = tuple(want) if want else None
            for index in range(self._scope.count()):
                if self._scope.itemData(index) == want:
                    self._scope.setCurrentIndex(index)
                    break
        # Only offer to freeze what exists: with no installed mapping there
        # is nothing to hold still.
        self._freeze.setEnabled(bool(self.project.compact))
        self._freeze.setChecked(bool(stored.get("freeze"))
                                and bool(self.project.compact))
        self._replan()
        self._set_nudge(stored.get("nudge")
                        or (DEFAULT_NUDGE if self.project.script == "arabic"
                            else {}))
        self._probe.setText(self._sample())

    def _fallback_sample(self) -> str:
        if self.project.script == "arabic":
            return SAMPLE
        chosen = self.project.charset
        letters = [c for c in sorted(chosen or ()) if fonts._is_letter(c)]
        return "".join(letters[:40]) or "The quick brown fox"

    def _sample(self) -> str:
        """A real line out of the project, so the preview shows real work."""
        for name in ("briefing", "story", "stage", "menus"):
            if not self.project.has(name):
                continue
            for entry in self.project.read(name)["entries"]:
                target = (entry.get("target") or "").strip()
                if len(target) > 20:
                    return target.splitlines()[0]
        return self._fallback_sample()

    # -- script and character set -----------------------------------------

    def _script_controls(self) -> None:
        """Join and Overlap only mean something where letters join."""
        joined = self.project.script == "arabic"
        self._join.setEnabled(joined)
        self._tighten.setEnabled(joined)
        self._preview.setAlignment((Qt.AlignRight if joined else Qt.AlignLeft)
                                   | Qt.AlignVCenter)
        self._scroll.setAlignment((Qt.AlignRight if joined else Qt.AlignLeft)
                                  | Qt.AlignVCenter)

    def _unfreeze(self, why: str) -> None:
        """A frozen mapping belongs to the script and set it was planned for.

        Keep it across a change of either and the face gets painted with one
        alphabet at the code points of another -- so a change here always
        replans, and says so.
        """
        if self._freeze.isChecked():
            self._freeze.setChecked(False)
            self.say(f"Freeze mapping turned off: {why}. The text must be "
                     f"rebuilt after installing.")

    def _script_changed(self, *_args) -> None:
        self.project.script = self._script.currentData()
        self._unfreeze("the script changed")
        self._script_controls()
        self._describe_charset()
        self._replan()
        self._set_nudge(self.nudge)

    def _choose_characters(self) -> None:
        stock = self.project.stock_codes()
        reach = {c for c in stock if c <= compact.CEILING}
        chosen = self.project.charset
        picker = CharPicker(
            self,
            selected=(ord(c) for c in chosen) if chosen else (),
            drawn=reach,
            translation=self.project.translation_codes())
        if picker.exec() == QDialog.Accepted:
            self._set_charset([chr(c) for c in picker.codepoints()])

    def _set_charset(self, characters) -> None:
        self.project.charset = characters
        self._unfreeze("the character set changed")
        self._describe_charset()
        self._replan()
        self._set_nudge(self.nudge)

    def _describe_charset(self) -> None:
        chosen = self.project.charset
        if chosen is None:
            text = ("Automatic: every character the translation draws that "
                    "the engine cannot reach is planned and painted.")
            warn = False
        else:
            stock = self.project.stock_codes()
            reach = {c for c in stock if c <= compact.CEILING}
            painted = sum(1 for c in chosen if ord(c) not in stock)
            moved = sum(1 for c in chosen
                        if ord(c) in stock and ord(c) not in reach)
            text = (f"{len(chosen)} character(s) chosen: {painted} to paint, "
                    f"{moved} the face has but the engine cannot reach "
                    f"(moved down), "
                    f"{len(chosen) - painted - moved} already drawn.")
            missing = self.project.uncovered()
            warn = bool(missing)
            if missing:
                sample = " ".join(missing[:24])
                text += (f"\n{len(missing)} character(s) the translation "
                         f"uses are not in the set and will not draw: "
                         f"{sample}" + (" ..." if len(missing) > 24 else ""))
        self._charset_note.setText(text)
        self._charset_note.setProperty("warn", warn)
        self._charset_note.style().unpolish(self._charset_note)
        self._charset_note.style().polish(self._charset_note)

    def done(self, result: int) -> None:
        if not self._installed:
            font = self.project.manifest.setdefault("font", {})
            for key, value in zip(("script", "charset"), self._was):
                if value is None:
                    font.pop(key, None)
                else:
                    font[key] = value
        super().done(result)

    @property
    def borrow(self) -> tuple[str, ...]:
        return tuple(name for name, box in self._borrow.items()
                     if box.isChecked())

    @property
    def nudge(self) -> dict[str, int]:
        out = {}
        for row in range(self._nudge.rowCount()):
            form = self._nudge.item(row, 0)
            pixels = self._nudge.item(row, 2)
            if not form or not form.text():
                continue
            try:
                moved = int(pixels.text()) if pixels else 0
            except ValueError:
                continue
            if moved:                     # zero is "as the font draws it"
                out[form.text()[0]] = moved
        return out

    @staticmethod
    def _letter_of(character: str) -> str:
        """``ARABIC LETTER MEEM FINAL FORM`` -> ``Meem Final``."""
        import unicodedata

        try:
            name = unicodedata.name(character)
        except ValueError:
            return ""
        for cut in ("ARABIC LETTER ", "ARABIC LIGATURE ", "ARABIC ",
                    "CYRILLIC ", "GREEK ", "LATIN "):
            if name.startswith(cut):
                name = name[len(cut):]
                break
        return name.replace(" FORM", "").replace("ISOLATED", "isolated").title()

    def _set_nudge(self, nudge: dict) -> None:
        """List every drawn form, so any of them can be moved.

        The face is the only thing that knows how a letter is cut, and a
        translator looking at a line is the only thing that knows whether it
        sits right; so the table shows the whole set rather than the handful
        somebody thought to add, and a zero simply means "as the font draws
        it".  Only what is not zero gets saved.
        """
        self._nudge.blockSignals(True)
        self._nudge.setRowCount(0)
        forms = set(self.forms()) | set(nudge)
        for form in sorted(forms, key=lambda c: (self._letter_of(c), c)):
            row = self._nudge.rowCount()
            self._nudge.insertRow(row)
            shown = QTableWidgetItem(form)
            shown.setFlags(shown.flags() & ~Qt.ItemIsEditable)
            shown.setTextAlignment(Qt.AlignCenter)
            self._nudge.setItem(row, 0, shown)
            named = QTableWidgetItem(self._letter_of(form)
                                     or "U+%04X" % ord(form))
            named.setFlags(named.flags() & ~Qt.ItemIsEditable)
            self._nudge.setItem(row, 1, named)
            self._nudge.setItem(row, 2,
                                QTableWidgetItem(str(nudge.get(form, 0))))
        self._nudge.blockSignals(False)
        self._apply_filter(self._filter.text() if hasattr(self, "_filter")
                           else "")
        self._touch()

    def _apply_filter(self, text: str) -> None:
        wanted = (text or "").strip().lower()
        for row in range(self._nudge.rowCount()):
            form = self._nudge.item(row, 0)
            named = self._nudge.item(row, 1)
            hit = (not wanted
                   or wanted in (named.text().lower() if named else "")
                   or (form is not None and wanted in form.text()))
            self._nudge.setRowHidden(row, not hit)

    def _touch(self, *_args) -> None:
        self._metrics = None
        self._debounce.start()

    def _replan(self, *_args) -> None:
        self._plan = None
        # Frozen, neither of these decides anything: the code points are the
        # installed face's, and which case was spent is settled history.
        frozen = self._freeze.isChecked()
        self._scope.setEnabled(not frozen)
        if hasattr(self, "_reclaim"):
            self._reclaim.setEnabled(not frozen)
        self._touch()

    def _chosen(self, field: QLabel) -> str | None:
        path = field.toolTip()
        return path if path and Path(path).is_file() else None

    def _show_path(self, field: QLabel, path: str) -> None:
        field.setToolTip(path)
        field.setText(Path(path).name if path else "none chosen")

    def _choose(self, field: QLabel) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a TrueType font", "", "Fonts (*.ttf *.otf)")
        if path:
            self._show_path(field, path)
            self._plan = None
            self._touch()
            self.refresh()

    def say(self, message: str) -> None:
        self._log.appendPlainText(message)
        QApplication.processEvents()

    # -- the plan and the preview -----------------------------------------

    def measure(self) -> None:
        """Record how wide each glyph is drawn, before anything is planned.

        A mark is centred over the letter under it, so the plan cannot choose
        its variants until the widths are known -- and only the face and the
        size decide those.  Measuring first is what lets the harakat exist at
        all.
        """
        ttf = self._chosen(self._sub_ttf)
        if not ttf or self.project.script != "arabic":
            return
        wanted = ([chr(c) for c in range(0xFE70, 0xFEFD)]
                  + [chr(m) for m in sorted(arabic.MARKS)])
        shape = fonts.metrics(ttf, wanted, 67, self._fill.value())
        if shape is None:
            return
        self.project.manifest.setdefault("font", {})["widths"] = {
            ch: box[2] - box[0] for ch, box in shape["boxes"].items()}

    def plan(self) -> dict:
        if self._plan is None:
            self.measure()
            kept = self.project.compact if self._freeze.isChecked() else {}
            if kept:
                # The installed face's own code points, taken as given.  What
                # the text costs is not recomputed with them: those figures
                # describe a plan being chosen, and this one was chosen
                # already -- the number that matters now is that nothing
                # built against it has gone stale.
                self._plan = {
                    "mapping": kept, "pool": [], "relocate": set(),
                    "kept": 0, "spoilt": 0, "whole": 1, "cost": 0.0,
                    "singles": sum(1 for s in kept.values() if ord(s) < 0x80),
                    "frozen": True,
                }
            else:
                self._plan = self.project.compact_plan(
                    self._scope.currentData())
        return self._plan

    def painting(self) -> tuple:
        """``(what to paint, which are marks, what only moves)``.

        The plan **moves** a character as readily as it replaces one: a
        capital gives up its one-byte slot for a two-byte code point, and it
        has to go on looking exactly as it did.  Painting it again from the
        Arabic face does not do that -- it comes back in another typeface at
        the Arabic scale and baseline, which is what put two fonts inside
        ".LIFE".  A moved character is aliased to the glyph the game already
        draws instead, so it is not painted, spends no donor record, and --
        just as importantly -- does not join the set the Arabic size is
        measured against.
        """
        mapping = self.plan()["mapping"]
        variants = arabic.mark_variants()
        stock = self.project.stock_codes()
        draw, overlay, alias = {}, {}, {}
        for form, slot in mapping.items():
            if form in variants:
                mark, bearing = variants[form]
                draw[slot] = mark
                overlay[slot] = bearing
            elif ord(form) in stock:
                # Only the script itself has to be painted.  Everything
                # else the plan moves -- capitals, punctuation, the CJK
                # and fullwidth marks a few lines still carry -- is a
                # character the game already draws, and it keeps its own
                # glyph.  Judging this by the code point rather than by
                # the scope also keeps a kanji out of the set the Arabic
                # size is measured against, where one tall glyph shrinks
                # the whole script.
                alias[slot] = form
            else:
                draw[slot] = form
        return draw, overlay, alias

    def forms(self) -> list[str]:
        """Every character the face will carry -- what decides the size.

        A short preview line measured against itself would be scaled quite
        differently from the same line in a face that also has to hold an alef
        with hamza, so the whole set goes in even when one word comes out.
        """
        return sorted(self.painting()[0].values())

    def repreview(self) -> None:
        ttf = self._chosen(self._sub_ttf)
        if not ttf:
            self._size.setText("Choose a subtitle face to see the size and "
                               "the preview.")
            self._preview.setText("")
            return
        try:
            forms = self.forms()
            if self._metrics is None:
                self._metrics = fonts.metrics(ttf, forms, CELL_H,
                                              self._fill.value(), self.nudge)
            shape = self._metrics
            if shape is None:
                self._size.setText("That font rendered none of the letters.")
                return

            lost = {c: n for c, n in shape["clipped"].items()
                    if fonts._is_letter(c)}
            note = (f"{shape['size']} pt. The script wants {shape['wanted']} "
                    f"pixels of a {CELL_H} pixel cell, so it is drawn at "
                    f"{shape['scale']:.3f} of its nominal size.")
            if lost:
                worst = " ".join(f"{c}({n})" for c, n in
                                 sorted(lost.items(), key=lambda kv: -kv[1])[:8])
                note += (f"  Too large: {len(lost)} form(s) lose ink off the "
                         f"cell -- {worst}")
            else:
                note += "  No letter is clipped."
            self._size.setText(note)
            self._size.setProperty("warn", bool(lost))
            self._size.style().unpolish(self._size)
            self._size.style().polish(self._size)

            probe = self._probe.text() or self._fallback_sample()
            shaped = (arabic.strip_harakat(arabic.shape(probe))
                      if self.project.script == "arabic"
                      else "".join(c for c in probe
                                   if ord(c) not in arabic.CONTROLS))
            image = fonts.render_line(ttf, shaped, CELL_H, self._fill.value(),
                                      self.nudge, characters=forms, scale=2)
        except Exception as error:                       # a bad font, mostly
            self._size.setText(str(error))
            self._preview.setText("")
            return
        if image is None:
            self._preview.setText("")
            return
        image = image.convert("RGB")
        frame = QImage(image.tobytes(), image.width, image.height,
                       image.width * 3, QImage.Format_RGB888).copy()
        self._preview.setPixmap(QPixmap.fromImage(frame))
        self._preview.resize(frame.size())
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.maximum())        # Arabic starts at the right

    def refresh(self) -> None:
        """The atlas budget, which is a different face and a different sum."""
        wanted = self.project.wanted_glyphs("menus")
        report = arabic.capacity(wanted or [chr(c) for c in
                                            range(0xFE70, 0xFEFD)],
                                 self.borrow)
        source = ("Nothing is translated in the Menus tab yet, so this is the "
                  "whole presentation block -- the worst case."
                  if not wanted else
                  f"Measured from the {len(wanted)} distinct glyph shapes the "
                  f"menu translation actually uses.")
        verdict = ("It fits." if report["fits"]
                   else f"It is {report['short']} cell(s) short.")
        self._capacity.setText(
            f"{report['wanted']} glyph shapes into {report['cells']} cells. "
            f"{verdict}  {source}")
        self._capacity.setProperty("warn", not report["fits"])
        self._capacity.style().unpolish(self._capacity)
        self._capacity.style().polish(self._capacity)
        self._install_button.setEnabled(
            bool(self._chosen(self._menu_ttf) or self._chosen(self._sub_ttf)))
        self.repreview()

    # -- installing --------------------------------------------------------

    def install(self) -> None:
        game = self.project.game
        if game is None:
            return
        menu_ttf = self._chosen(self._menu_ttf)
        sub_ttf = self._chosen(self._sub_ttf)
        out = self.project.output
        stored = self.project.manifest.setdefault("font", {})

        missing = self.project.uncovered()
        if missing and QMessageBox.question(
                self, "Characters not in the set",
                f"{len(missing)} character(s) the translation uses are not in "
                f"the chosen set, and the game will not draw them:\n\n"
                f"{' '.join(missing[:60])}\n\nInstall anyway?"
                ) != QMessageBox.Yes:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if sub_ttf:
                plan = self.plan()
                draw, overlay, alias = self.painting()
                if plan.get("frozen"):
                    self.say(f"\nPainting {len(draw)} glyph(s), moving "
                             f"{len(alias)} the face already draws, at the "
                             f"code points the installed face already uses "
                             f"-- mapping frozen, so nothing already built "
                             f"needs rebuilding...")
                else:
                    self.say(f"\nPainting {len(draw)} glyph(s), moving "
                             f"{len(alias)} the face already draws, at the "
                             f"code points the plan chose -- "
                             f"{plan['singles']} of them one byte, costing "
                             f"{plan['cost']:.2f}% of the {plan['kept']} "
                             f"line(s) that stay English...")
                result = fonts.install_subtitle_face(
                    game, out, sub_ttf, sorted(draw) + sorted(alias),
                    remap=draw, nudge=self.nudge,
                    fill=self._fill.value(), overlay=overlay,
                    alias=alias, join=self._join.value(),
                    tighten=self._tighten.value())
                for face in result["faces"]:
                    lost = {c: n for c, n in (face.get("clipped") or {}).items()
                            if fonts._is_letter(c)}
                    self.say(f"  {face['file']}: {face.get('painted', 0)} "
                             f"painted, {face.get('aliased', 0)} moved, at "
                             f"{face.get('size', '?')} pt"
                             + (f", {len(lost)} clipped" if lost else "")
                             + (f" -- {face['error']}" if face.get("error")
                                else ""))
                stored["sub_ttf"] = sub_ttf
                stored["fill"] = self._fill.value()
                stored["join"] = self._join.value()
                stored["tighten"] = self._tighten.value()
                stored["nudge"] = self.nudge
                stored["freeze"] = self._freeze.isChecked()
                if not plan.get("frozen"):
                    scope = self._scope.currentData()
                    stored["scope"] = list(scope) if scope else None
                if not plan.get("frozen"):
                    # A frozen install must not touch these.  The mapping is
                    # what the built text is written in, and `reclaim` is the
                    # question the mapping was the answer to -- writing back
                    # either one would make the manifest describe a plan that
                    # nothing was built against.
                    stored["reclaim"] = self._reclaim.currentData()
                    stored["compact"] = {form: ord(slot) for form, slot
                                         in plan["mapping"].items()}

            if menu_ttf:
                wanted = self.project.wanted_glyphs("menus")
                if not wanted:
                    self.say("\nNo menu line is translated, so there is no "
                             "way to know which shapes the atlas needs; "
                             "skipped.")
                else:
                    cells = arabic.plan(wanted, self.borrow)
                    self.say(f"\nPainting {len(cells)} glyph(s) into the caps "
                             f"atlas...")
                    result = fonts.install_menu_face(
                        game, out, menu_ttf, cells, True)
                    self.say(f"  {result['txp_sheets']} sheet(s) in "
                             f"{len(result['txp_files'])} package(s), and "
                             f"{result['slot_blocks']} SLOT.DAT block(s).")
                    if result["slot_overflow"]:
                        self.say(f"  {len(result['slot_overflow'])} block(s) "
                                 f"no longer fit and were left stock.")
                    self.project.mapping = cells
                    self.project.borrow = self.borrow
                    stored["ttf"] = menu_ttf

            self._installed = True        # script and set are now kept
            self.project.save_manifest()
        except Exception as error:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Could not install the font",
                                 str(error))
            return
        QApplication.restoreOverrideCursor()

        self.say(f"\nWritten to {out}")
        QMessageBox.information(
            self, "Fonts",
            "The patched font packages are in the project's output folder.\n\n"
            f"Script: {self._script.currentText()}.\n\n"
            "Build > Install into the game copies them in, alongside the "
            "text -- and the text has to be rebuilt after this, because it is "
            "stored as the code points this plan just chose.")
