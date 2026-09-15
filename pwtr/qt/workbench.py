"""The translation window.

One tab per container, a table of lines, and an entry box that takes Arabic
the way it is typed -- logical order, ordinary keyboard.  The shaping to
presentation forms, and the mapping of those forms onto the menu face's atlas
cells, happen on build and never in the project files, so a line can always be
re-opened and edited as text.

The layout is the MGS4 workbench's, because the job is the same one: a list you
filter, a line you are on, and the game's own rendering of it beside the
English.  What changed is underneath -- two containers instead of five, and a
preview that has to say when a glyph has nowhere to live.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import (QAction, QColor, QFont, QFontDatabase,
                           QKeySequence, QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog,
    QDialogButtonBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressDialog, QPushButton, QSplitter, QTabBar, QTableView, QVBoxLayout,
    QWidget,
)

from pwtr import arabic
from pwtr.project import (CATEGORIES, LABELS, RIDES_WITH, Project,
                          ProjectError, face_of, find_game)
from pwtr.qt import theme
from pwtr.qt.findreplace import FindReplace, find_in, replace_all

#: Forces a run of text to be drawn left to right regardless of its own
#: direction.  Shaped Arabic is already in the order the game blits it, so the
#: preview has to stop Qt from helpfully reversing it back.
LRO, PDF = "‭", "‬"


def display_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def one_line(text: str) -> str:
    return " / ".join(p for p in display_lines(text) if p.strip()).strip()


class LineModel(QAbstractTableModel):
    """The visible slice of one container's lines."""

    HEADERS = ("Line", "English", "Arabic")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def row_at(self, index: int) -> dict | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def touched(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            left = self.index(index, 0)
            right = self.index(index, self.columnCount() - 1)
            self.dataChanged.emit(left, right)

    def rowCount(self, _parent=QModelIndex()) -> int:      # noqa: N802
        return len(self._rows)

    def columnCount(self, _parent=QModelIndex()) -> int:   # noqa: N802
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        row = self._rows[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            if column == 0:
                return row["key"]
            if column == 1:
                return one_line(row["source"])
            return one_line(row.get("target", ""))

        if role == Qt.ToolTipRole:
            # The columns are narrow and the interesting part is often past
            # the ellipsis -- a mission key is stage/<entity>/<table>/<index>
            # and the table name is the half that gets cut.
            if column == 0:
                return row["key"]
            return row["source"] if column == 1 else row.get("target", "")

        if role == Qt.TextAlignmentRole and column == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == Qt.BackgroundRole and row.get("target"):
            return QColor(theme.DONE)

        return None


class Workbench(QMainWindow):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.setWindowTitle("Peace Walker Arabic Translator")
        self.resize(1280, 760)

        self.project: Project | None = None
        #: category -> list of entry dicts, loaded on demand
        self._data: dict[str, list[dict]] = {}
        #: categories with unsaved edits
        self._dirty: set[str] = set()
        #: model row -> index into the category's full entry list
        self._map: list[int] = []
        self._loading = False
        self._preview_font: str | None = None
        self._finder: FindReplace | None = None
        self._textures = None

        self._build()
        if project is not None:
            self.use(project)
        else:
            self._sync()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        self._act(file_menu, "New project...", self.new_project,
                  QKeySequence.New)
        self._act(file_menu, "Open project...", self.open_project,
                  QKeySequence.Open)
        file_menu.addSeparator()
        self._act(file_menu, "Save translations", self.save, QKeySequence.Save)
        file_menu.addSeparator()
        self._act(file_menu, "Import translations from another build...",
                  self.import_translations)
        file_menu.addSeparator()
        self._act(file_menu, "Unpack the game's textures...",
                  self.unpack_textures)
        file_menu.addSeparator()
        self._act(file_menu, "Quit", self.close, QKeySequence.Quit)

        edit_menu = menu.addMenu("&Edit")
        self._act(edit_menu, "Find / Replace...", self.find_replace,
                  QKeySequence.Find)
        self._act(edit_menu, "Next untranslated line", self.next_blank,
                  QKeySequence("Ctrl+J"))
        self._act(edit_menu, "Filter the list", self.focus_search,
                  QKeySequence("Ctrl+L"))
        edit_menu.addSeparator()
        self._act(edit_menu, "Copy English into Arabic", self.copy_source)
        self._act(edit_menu, "Clear this line", self.clear_translation)

        font_menu = menu.addMenu("&Fonts")
        self._act(font_menu, "Install characters into the game's fonts...",
                  self.install_font)
        self._act(font_menu, "What the menu face can hold...", self.font_plan)

        tex_menu = menu.addMenu("&Textures")
        self._act(tex_menu, "Open the texture workbench...", self.textures,
                  QKeySequence("Ctrl+T"))
        self._act(tex_menu, "Unpack the game's textures...",
                  self.unpack_textures)

        build_menu = menu.addMenu("&Build")
        self._act(build_menu, "Extract everything...", self.extract_all)
        self._act(build_menu, "Build everything (including burning the "
                              "subtitled pre-rendered movies)", self.build_all)
        build_menu.addSeparator()
        self._act(build_menu, "Build this container", self.build_current,
                  QKeySequence("Ctrl+B"))
        self._act(build_menu, "Rename the staff in a save...",
                  self.rename_save)
        build_menu.addSeparator()
        self._act(build_menu, "Pre-rendered subtitle font and size...",
                  self.movie_style)
        self._act(build_menu, "Preview the subtitled pre-rendered movies...",
                  self.preview_movies)
        build_menu.addSeparator()
        self._act(build_menu, "Install into the game...", self.install)
        self._act(build_menu, "Bundle a release zip...", self.bundle_release)
        self._act(build_menu, "Restore the originals...", self.restore)
        build_menu.addSeparator()
        self._act(build_menu, "Re-Extract the menu text again", self.re_extract)
        self._act(build_menu, "Re-Extract the movie text", self.extract_movies)
        self._act(build_menu, "Re-Extract the story text from SLOT.DAT...",
                  self.extract_story)
        self._act(build_menu,
                  "Re-Extract the briefing text from BRIEFING.DAT...",
                  self.extract_briefing)
        self._act(build_menu,
                  "Re-Extract the mission text from STAGEDAT.PDT...",
                  self.extract_stage)
        self._act(build_menu,
                  "Re-Extract the staff names from STAGEDAT.PDT...",
                  self.extract_staff)
        self._act(build_menu,
                  "Re-Extract the radio speaker names from the exe...",
                  self.extract_names)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 4)

        header = QHBoxLayout()
        self._project_label = QLabel("No project open")
        self._project_label.setProperty("heading", True)
        self._state_label = QLabel("")
        self._state_label.setProperty("muted", True)
        header.addWidget(self._project_label)
        header.addStretch(1)
        header.addWidget(self._state_label)
        outer.addLayout(header)

        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        for category in CATEGORIES:
            self._tabs.addTab(LABELS[category])
        self._tabs.currentChanged.connect(self._on_tab)
        outer.addWidget(self._tabs)

        panes = QSplitter(Qt.Horizontal)
        outer.addWidget(panes, 1)

        # -- left: the lines
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 6, 6, 0)

        tools = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search English or Arabic...")
        self._search.textChanged.connect(self._refilter)
        tools.addWidget(self._search, 1)
        self._only_blank = QCheckBox("Untranslated only")
        self._only_blank.toggled.connect(self._refilter)
        tools.addWidget(self._only_blank)
        # Walking two hundred lines one at a time is not triage.  This
        # shows the whole set at once, worst overrun first, so the ones that
        # need a sentence rewritten sit above the ones that need a word.
        self._only_long = QCheckBox("Too long only")
        self._only_long.setToolTip(
            "Only the briefing lines the build cannot fit, worst first. "
            "Working that out costs a build, so the first tick pauses.")
        self._only_long.toggled.connect(self._refilter)
        tools.addWidget(self._only_long)
        left_box.addLayout(tools)

        # Cached, because working it out costs a build -- and remembered
        # against the tab it was worked out for.  Reused across tabs it is
        # not merely stale but nonsense: none of one container's keys exist
        # in another, so every line is reported as needing a trim and not one
        # of them can be found.
        # One answer per container, because working one out costs a build
        # -- and a build hands its own answer over, so the usual way to get
        # this list is to have just built the thing.
        self._overlong_by: dict = {}
        #: {source: [block, ...]} from the last story build, so a block
        #: number can be typed into the filter.  Not shown as a column.
        self._blocks: dict | None = None
        self._model = LineModel(self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(False)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Fixed)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 150)
        self._table.selectionModel().currentRowChanged.connect(self._on_line)
        left_box.addWidget(self._table)
        panes.addWidget(left)

        # -- right: the line being worked on
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(6, 6, 0, 0)

        row = QHBoxLayout()
        row.addWidget(QLabel("As the game draws it"))
        row.addStretch(1)
        self._face_label = QLabel("")
        self._face_label.setProperty("muted", True)
        row.addWidget(self._face_label)
        right_box.addLayout(row)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(110)
        self._preview.setLayoutDirection(Qt.LeftToRight)
        # No wrapping, for a reason that is not cosmetic.  Shaped Arabic is
        # already in visual order, so a soft wrap moves the *start* of the
        # sentence to the second line and the preview reads back to front.
        # The game does not reflow either -- it breaks on the line breaks the
        # string carries and nowhere else -- so scrolling sideways is both
        # truer and readable.
        self._preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        right_box.addWidget(self._preview)

        right_box.addWidget(QLabel("English"))
        self._source = QPlainTextEdit()
        self._source.setReadOnly(True)
        self._source.setMaximumHeight(110)
        right_box.addWidget(self._source)

        row = QHBoxLayout()
        row.addWidget(QLabel("Translation"))
        row.addStretch(1)
        self._warn_label = QLabel("")
        self._warn_label.setProperty("warn", True)
        row.addWidget(self._warn_label)
        right_box.addLayout(row)

        self._entry = QPlainTextEdit()
        self._entry.setLayoutDirection(Qt.RightToLeft)
        self._entry.textChanged.connect(self._on_edit)
        right_box.addWidget(self._entry, 1)

        buttons = QHBoxLayout()
        for label, slot, tip in (
            ("Next blank", self.next_blank, "Ctrl+J"),
            ("Next too long", self.next_overlong, "Ctrl+G"),
            ("Copy English", self.copy_source, ""),
            ("Clear", self.clear_translation, ""),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            if tip:
                button.setToolTip(tip)
            buttons.addWidget(button)
        buttons.addStretch(1)
        right_box.addLayout(buttons)

        panes.addWidget(right)
        panes.setStretchFactor(0, 3)
        panes.setStretchFactor(1, 2)

        self._status = self.statusBar()

    def _act(self, menu, label: str, slot, shortcut=None) -> QAction:
        action = QAction(label, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    # -- project -----------------------------------------------------------

    def new_project(self) -> None:
        if not self._confirm_discard():
            return

        folder = QFileDialog.getExistingDirectory(self, "New project folder")
        if not folder:
            return

        found = find_game()
        start = str(found or Path.home())
        game = QFileDialog.getExistingDirectory(
            self, "The game folder (MGS_PW\\mgspw)", start)
        if not game:
            return

        try:
            project = Project.create(folder, game)
            self._busy("Copying and reading the menu text...")
            copied = project.dump(self._status.showMessage)
        except (ProjectError, OSError) as error:
            self._idle()
            QMessageBox.critical(self, "Could not create project", str(error))
            return
        self._idle()

        if not copied:
            QMessageBox.warning(
                self, "Nothing found",
                "No .olang tables were found in that folder.\n\n"
                "It should be the mgspw folder, the one holding MLG, EXLANG, "
                "FONT and Text.")
        self.use(project)

        QMessageBox.information(
            self, "Project created",
            f"{copied} language table(s) copied, and the menu text is ready.\n\n"
            "The story -- every subtitle and every briefing, about 10 000 "
            "lines -- lives in SLOT.DAT and takes around twenty seconds to "
            "read, so it is not done automatically.\n\n"
            "Build > Extract the story text when you want it.")

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(self, "Open project folder")
        if not folder:
            return
        try:
            self.use(Project.load(folder))
        except (ProjectError, OSError, ValueError) as error:
            QMessageBox.critical(self, "Could not open project", str(error))

    def use(self, project: Project) -> None:
        self.project = project
        self._data.clear()
        self._dirty.clear()
        self._load_preview_font()
        self._project_label.setText(f"{project.name}  --  {project.root}")
        self._pick_first()
        self._sync()

    def _load_preview_font(self) -> None:
        """Draw the preview in the typeface that was installed into the game.

        MGS4 could register the game's own TTF.  Peace Walker has no TTF to
        register -- its faces are painted atlases -- so the nearest true thing
        is the font the glyphs were rendered *from*, which the font dialog
        records on the project.
        """
        self._preview_font = None
        if self.project is None:
            return
        chosen = self.project.manifest.get("font", {}).get("ttf")
        if not chosen or not Path(chosen).is_file():
            return
        handle = QFontDatabase.addApplicationFont(chosen)
        families = QFontDatabase.applicationFontFamilies(handle)
        if families:
            self._preview_font = families[0]

    def _pick_first(self) -> None:
        if self.project is None:
            return
        for index, category in enumerate(CATEGORIES):
            if self.project.has(category):
                self._tabs.setCurrentIndex(index)
                self._on_tab()
                return
        self._on_tab()

    # -- current container -------------------------------------------------

    @property
    def category(self) -> str:
        return CATEGORIES[max(0, self._tabs.currentIndex())]

    def entries_of(self, category: str) -> list[dict]:
        """One container's lines, loaded on first use."""
        if self.project is None:
            return []
        if category not in self._data:
            if not self.project.has(category):
                return []
            try:
                self._data[category] = self.project.read(category)["entries"]
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "Could not read text", str(error))
                return []
        return self._data[category]

    def entries(self) -> list[dict]:
        """The current container's lines."""
        return self.entries_of(self.category)

    def _on_tab(self, _index: int = -1) -> None:
        self._load_blocks()
        self._refilter()
        self._sync()

    def _load_blocks(self) -> None:
        """Read the block index, if the last story build left one.

        There is no column for it: the block a line sits in is not something
        worth a column of its own on every row.  It is here so that typing a
        block number into the filter finds what is *in* that block, which is
        the one question the key cannot answer -- the key names only the
        first block a string was found in.
        """
        if (self._blocks is None and self.project is not None
                and self.project.has_block_index()):
            self._blocks = self.project.block_index()

    def _refilter(self) -> None:
        needle = self._search.text().strip().casefold()
        blanks_only = self._only_blank.isChecked()
        long_only = self._only_long.isChecked()

        over = {}
        if long_only and self.project is not None:
            if self.category not in self._overlong_by:
                if self.category == "story":
                    self._status.showMessage(
                        "Reading all 2137 story blocks and recompressing the "
                        "ones that changed -- this takes a few minutes...", 0)
                    QApplication.processEvents()
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    self._overlong_by[self.category] = (
                        self.project.overlong(self.category))
                finally:
                    QApplication.restoreOverrideCursor()
            over = self._overlong_by[self.category]

        rows, mapping = [], []
        for index, entry in enumerate(self.entries()):
            if blanks_only and entry.get("target"):
                continue
            if long_only and entry["key"] not in over:
                continue
            here = (self._blocks or {}).get(entry["source"], ())
            if needle and not (
                    needle in entry["source"].casefold()
                    or needle in entry.get("target", "").casefold()
                    or any(needle in b.casefold() for b in here)):
                # Typing a block number finds what is *in* it, which the
                # key cannot answer: the key names only the first block a
                # string was found in, and a line sitting in twenty of
                # them is filed under one.
                continue
            rows.append(entry)
            mapping.append(index)

        if long_only:
            # Worst first -- and for the story, grouped by the block that is
            # over, longest line first inside it.  The overflow is the
            # block's, so the lines worth cutting are the long ones sitting
            # together in the same scene, not a shuffle of every container.
            order = sorted(
                range(len(rows)),
                key=lambda n: (-over[rows[n]["key"]][0],
                               over[rows[n]["key"]][1] or 0,
                               -len(rows[n].get("target") or "")))
            rows = [rows[n] for n in order]
            mapping = [mapping[n] for n in order]

        self._map = mapping
        self._model.set_rows(rows)
        if rows:
            self._table.selectRow(0)
        else:
            self._show(None)
        if long_only:
            self._status.showMessage(
                "%d line(s) need trimming, worst first" % len(rows), 8000)

    # -- editing -----------------------------------------------------------

    def _current(self) -> dict | None:
        index = self._table.currentIndex()
        return self._model.row_at(index.row()) if index.isValid() else None

    def _on_line(self, index, _previous=None) -> None:
        self._show(self._model.row_at(index.row()) if index.isValid() else None)

    def _show(self, entry: dict | None) -> None:
        self._loading = True
        try:
            self._source.setPlainText(entry["source"] if entry else "")
            self._entry.setPlainText(entry.get("target", "") if entry else "")
        finally:
            self._loading = False
        self._entry.setEnabled(entry is not None)
        self._repreview()

    def _on_edit(self) -> None:
        if self._loading:
            return
        entry = self._current()
        if entry is None:
            return

        entry["target"] = self._entry.toPlainText()
        self._dirty.add(self.category)
        self._overlong_by.pop(self.category, None)   # the budget just moved
        self._model.touched(self._table.currentIndex().row())
        self._repreview()
        self._sync()

    def _repreview(self) -> None:
        entry = self._current()
        if entry is None:
            self._preview.setPlainText("")
            self._warn_label.setText("")
            self._face_label.setText("")
            return

        # The face is a property of the line, not of the tab: a menu string
        # marked Style 0x0001 goes to the caps atlas and its 63 cells, and
        # every other string goes to the XPR face, which has room for the
        # whole presentation block.
        face = face_of(entry)
        if self.category == "briefing":
            need, budget = self._record_budget(entry)
            self._face_label.setText(
                f"record 0x{entry['record']:06X} -- {need} of {budget} bytes")
            face = "subtitle"
        elif self.category == "movies":
            rows = len([l for l in (entry.get("target") or entry["source"]
                                    ).splitlines() if l.strip()])
            was = len([l for l in entry["source"].splitlines() if l.strip()])
            self._face_label.setText(
                f"{entry['scene']} -- {rows} line(s), the English has {was}")
            face = "subtitle"
        elif self.category == "staff":
            need, budget = self._name_budget(entry)
            self._face_label.setText(
                f"{entry['table']} row {entry['index']} -- "
                f"{need} of {budget} bytes")
            face = "subtitle"
        elif self.category == "names":
            need, budget = self._speaker_budget(entry)
            self._face_label.setText(
                ("its own 8-byte slot" if entry.get("fixed")
                 else "shared name run") + f" -- {need} of {budget} bytes")
            face = "subtitle"
        else:
            self._face_label.setText(
                "caps atlas -- 63 free cells" if face == "ui"
                else "XPR face -- real Unicode, no limit")
        over = (self.category == "briefing"
                and self._record_budget(entry)[0] > entry["budget"])
        if self.category == "staff":
            need, budget = self._name_budget(entry)
            over = need > budget
        if self.category == "names":
            need, budget = self._speaker_budget(entry)
            over = need > budget
        from pwtr import moviesource
        if (self.category == "movies" and entry.get("target")
                and moviesource.is_roll(entry["key"])):
            over = (len([l for l in entry["target"].splitlines() if l.strip()])
                    != len([l for l in entry["source"].splitlines()
                            if l.strip()]))
        self._face_label.setProperty("warn", face == "ui" or over)
        self._face_label.style().unpolish(self._face_label)
        self._face_label.style().polish(self._face_label)

        text = entry.get("target") or entry["source"]
        plain = self.project is not None and self.project.script == "plain"
        self._entry.setLayoutDirection(Qt.LeftToRight if plain
                                       else Qt.RightToLeft)
        try:
            shaped = text if plain else arabic.shape(text)
        except Exception as error:                  # shaping is best effort
            self._preview.setPlainText(f"[cannot shape: {error}]")
            return

        font = QFont(self._preview_font) if self._preview_font else QFont()
        font.setPointSize(14)
        self._preview.setFont(font)
        self._preview.setPlainText(
            "\n".join(LRO + line + PDF for line in display_lines(shaped)))
        self._check(entry, shaped, face)

    def _record_budget(self, entry: dict) -> tuple[int, int]:
        """``(bytes this briefing record needs, bytes it has)``.

        Counted live from the lines on screen, because the budget belongs to
        the whole record: shortening one line is what pays for lengthening
        another, and a translator needs to see that as they type rather than
        at build time.
        """
        record = entry.get("record")
        if record is None:
            return 0, 0
        need = 0
        for other in self.entries_of("briefing"):
            if other.get("record") != record:
                continue
            text = other.get("target") or other["source"]
            if other.get("target") and self.project is not None \
                    and self.project.script == "plain":
                pass
            elif other.get("target"):
                try:
                    text = arabic.shape(text)
                    if self.project is None or self.project.manifest.get(
                            "strip_harakat", True):
                        text = arabic.strip_harakat(text)
                except Exception:
                    pass
            need += len(text.encode("utf-8")) + 1
        return need, entry.get("budget", 0)

    def _name_budget(self, entry: dict) -> tuple[int, int]:
        """``(bytes this staff name needs, the 15 it has)``.

        Counted through the compact plan rather than off the shaped text: a
        name is written the way the build writes it, and without the plan a
        letter is three bytes instead of one, which would call every name in
        the list too long.
        """
        budget = entry.get("budget", 15)
        target = entry.get("target")
        if not target or self.project is None:
            return 0, budget
        try:
            return len(self.project.render(target).encode("utf-8")), budget
        except Exception:
            return 0, budget

    def _speaker_budget(self, entry: dict) -> tuple[int, int]:
        """``(bytes this speaker name needs, bytes left for it)``.

        Counted against the names as typed, not as saved: the run is shared,
        so shortening one name here is room for another straight away.
        """
        if self.project is None:
            return 0, 0
        try:
            return self.project.names_room(self.entries_of("names"), entry)
        except Exception:
            return 0, 0

    def _check(self, entry: dict, shaped: str, face: str) -> None:
        """Two ways a translated line can be wrong before it is ever built."""
        target = entry.get("target", "")
        if not target:
            self._warn_label.setText("")
            return

        problems = []

        # Dropped markup: the engine reads <I=ATK> and <C=FF4040>, and a line
        # that loses one loses a button prompt or a colour.
        want, got = arabic.tokens(entry["source"]), arabic.tokens(target)
        if want != got:
            missing = [t for t in want if t not in got]
            extra = [t for t in got if t not in want]
            if missing:
                problems.append("missing " + " ".join(missing))
            if extra:
                problems.append("extra " + " ".join(extra))

        # A briefing record cannot grow: over budget means the whole record
        # stays English, so it is the line's problem too, not just the file's.
        if self.category == "briefing":
            need, budget = self._record_budget(entry)
            if need > budget:
                problems.append(
                    f"record is {need - budget} byte(s) over -- it will stay "
                    f"in English")

        # A burned-in roll is timed to how many lines it has, so a field that
        # gains or loses one drifts out of step with the video.  Font size and
        # the blank line between fields are copied from the subtitle and
        # cannot be broken here; this is the one rule typing can break.
        from pwtr import moviesource
        if self.category == "movies" and moviesource.is_roll(entry["key"]):
            said = len([l for l in target.splitlines() if l.strip()])
            want = len([l for l in entry["source"].splitlines() if l.strip()])
            if said != want:
                problems.append(
                    f"{said} line(s) where the English has {want} -- keep "
                    f"them equal or the roll drifts out of sync")

        # A staff name is 16 bytes with its terminator and the next record
        # starts straight after, so there is nothing to borrow from.
        if self.category == "staff":
            need, budget = self._name_budget(entry)
            if need > budget:
                problems.append(
                    f"{need - budget} byte(s) over the {budget}-byte field -- "
                    f"this name will stay in English")

        if self.category == "names":
            need, budget = self._speaker_budget(entry)
            if need > budget:
                problems.append(
                    f"{need - budget} byte(s) over -- this name will stay in "
                    f"English")

        # A glyph the menu face has no cell for is drawn as something else
        # entirely, and only shows up in the game.
        if face == "ui" and self.project is not None:
            mapping = self.project.mapping
            if mapping:
                lost = arabic.unmapped(shaped, mapping)
                if lost:
                    problems.append(
                        f"{len(lost)} glyph(s) not in the installed font: "
                        + " ".join(lost[:6]))

        self._warn_label.setText("; ".join(problems))

    def next_blank(self) -> None:
        start = self._table.currentIndex().row() + 1
        for row in list(range(start, self._model.rowCount())) + \
                list(range(0, max(0, start))):
            entry = self._model.row_at(row)
            if entry is not None and not entry.get("target"):
                self._table.selectRow(row)
                self._entry.setFocus()
                return
        self._status.showMessage("No untranslated lines left here", 4000)

    @staticmethod
    def _block_of(entry) -> str:
        """The SLOT block a story line lives in, from its key.

        ``slot/0061B/6/128`` -- the second field is the block's start page,
        which is what the KEY calls it and what the build log prints.
        """
        key = entry.get("key") or ""
        return key.split("/")[1] if key.startswith("slot/") else ""

    def next_overlong(self) -> None:
        """Jump to the next line the build cannot fit, and say by how much.

        A line too long is left in English and the tape plays around it, so
        it is invisible until somebody reads the build log and matches four
        hundred hex offsets against a table.  This walks them instead.
        """
        if self.project is None:
            return
        if self.category not in ("briefing", "story", "staff", "names",
                                 "movies"):
            self._status.showMessage(
                "Menus and Missions have no size limit -- their pools grow "
                "to whatever they are given", 5000)
            return
        # Which basis the figures are on is not a detail: without a plan a
        # letter costs three bytes instead of one or two, and every count
        # here is roughly two and a half times what it will be once a face
        # is installed.  Say so rather than quoting a number that means
        # something else.
        plan = self.project.compact
        if not plan:
            self._status.showMessage(
                "No font is installed, so this counts three bytes a letter "
                "-- install a subtitle face and these figures fall by about "
                "60%", 12000)
        if self.category not in self._overlong_by:
            if self.category == "story":
                self._status.showMessage(
                    "Reading all 2137 story blocks and recompressing the "
                    "ones that changed -- this takes a few minutes...", 0)
                QApplication.processEvents()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._overlong_by[self.category] = (
                    self.project.overlong(self.category))
            finally:
                QApplication.restoreOverrideCursor()
        over = self._overlong_by[self.category]
        if not over:
            self._status.showMessage(
                f"Every translated {LABELS[self.category].lower()} line fits",
                4000)
            return
        if not plan:
            return          # the warning above is the useful message

        start = self._table.currentIndex().row() + 1
        for row in (list(range(start, self._model.rowCount()))
                    + list(range(0, max(0, start)))):
            entry = self._model.row_at(row)
            if entry is None:
                continue
            found = over.get(entry["key"])
            if found is None:
                continue
            spare, block = found
            self._table.selectRow(row)
            self._entry.setFocus()
            one = sum(1 for slot in plan.values() if ord(slot) < 0x80)
            basis = (f"{one} of {len(plan)} glyphs on one byte" if plan
                     else "NO FONT INSTALLED -- three bytes a letter")
            # A briefing line is over by bytes of its own; a story line sits
            # in a block over by pages, which shortening any line in it helps.
            if self.category == "story":
                # Which block, and how much of it is here: the overflow
                # belongs to the block, so the useful question is not "which
                # line is too long" but "how much must come out of this one".
                # The block that is over, which is not necessarily the
                # one this line's key names.
                kin = sum(1 for e in self.entries()
                          if over.get(e.get("key"), (0, None))[1] == block)
                block = "%05X" % block
                # No single page is at fault: the block is compressed
                # whole and then stored in whole pages, so what is over is
                # the block's total.
                where = (f"block {block} is {spare} byte(s) over its budget "
                         f"once compressed; {kin} of its lines are "
                         f"translated and shortening any of them counts")
            elif self.category == "movies":
                where = (f"{abs(spare)} line(s) "
                         f"{'more' if spare > 0 else 'fewer'} than the English "
                         f"-- the roll will drift out of sync")
            else:
                where = f"{spare} byte(s) too long"
            self._status.showMessage(
                f"{where} -- {len(over)} line(s) in all  ({basis})", 15000)
            return
        self._status.showMessage(
            f"{len(over)} line(s) need trimming, none of them here", 4000)

    def copy_source(self) -> None:
        entry = self._current()
        if entry is not None:
            self._entry.setPlainText(entry["source"])

    def clear_translation(self) -> None:
        if self._current() is not None:
            self._entry.setPlainText("")

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    # -- find and replace --------------------------------------------------

    def find_replace(self) -> None:
        if self.project is None:
            return
        if self._finder is None:
            self._finder = FindReplace(self, self)
        self._finder.show()
        self._finder.raise_()
        self._finder.activateWindow()
        self._finder.find_field.setFocus()
        self._finder.find_field.selectAll()

    def say(self, message: str) -> None:
        self._status.showMessage(message, 5000)

    def _positions(self) -> list[tuple[str, int]]:
        """Every line in the project, as ``(category, index)`` in tab order."""
        return [(category, index)
                for category in CATEGORIES
                for index in range(len(self.entries_of(category)))]

    def _here(self) -> tuple[str, int] | None:
        row = self._table.currentIndex().row()
        if 0 <= row < len(self._map):
            return self.category, self._map[row]
        return None

    def _goto(self, category: str, index: int) -> None:
        """Select one line, lifting the list filter if it is hiding it."""
        if category != self.category:
            self._tabs.setCurrentIndex(CATEGORIES.index(category))

        if index not in self._map:
            for widget, clear in ((self._only_long, lambda w: w.setChecked(False)),
                                  (self._search, lambda w: w.clear()),
                                  (self._only_blank,
                                   lambda w: w.setChecked(False))):
                blocked = widget.blockSignals(True)
                clear(widget)
                widget.blockSignals(blocked)
            self._refilter()
            self.say("Filter cleared to show the match")

        if index in self._map:
            row = self._map.index(index)
            self._table.selectRow(row)
            self._table.scrollTo(self._model.index(row, 0))

    def _hit(self, entry: dict, needle: str, match_case: bool,
             search_english: bool) -> bool:
        if find_in(entry.get("target", ""), needle, match_case) >= 0:
            return True
        return search_english and find_in(
            entry["source"], needle, match_case) >= 0

    def find_next(self, needle: str, up: bool = False,
                  match_case: bool = False,
                  search_english: bool = False) -> bool:
        if self.project is None:
            return False

        positions = self._positions()
        here = self._here()
        start = positions.index(here) if here in positions else -1
        order = (positions[:start][::-1] + positions[start + 1:][::-1] if up
                 else positions[start + 1:] + positions[:max(0, start)])

        for category, index in order:
            if self._hit(self.entries_of(category)[index], needle,
                         match_case, search_english):
                self._goto(category, index)
                return True
        return False

    def replace_one(self, needle: str, repl: str, up: bool = False,
                    match_case: bool = False,
                    search_english: bool = False) -> None:
        """Replace this line's first match, then move to the next one."""
        entry = self._current()
        if entry is not None:
            target = entry.get("target", "")
            at = find_in(target, needle, match_case)
            if at >= 0:
                entry["target"] = target[:at] + repl + target[at + len(needle):]
                self._dirty.add(self.category)
                self._model.touched(self._table.currentIndex().row())
                self._show(entry)
                self._sync()
                self.say("Replaced")
        self.find_next(needle, up=up, match_case=match_case,
                       search_english=search_english)

    def _replace_in(self, category: str, needle: str, repl: str,
                    match_case: bool) -> int:
        total = 0
        for entry in self.entries_of(category):
            if not entry.get("target"):
                continue
            new, count = replace_all(entry["target"], needle, repl, match_case)
            if count:
                entry["target"] = new
                total += count
        if total:
            self._dirty.add(category)
        return total

    def _count_in(self, category: str, needle: str, match_case: bool) -> int:
        return sum(
            replace_all(entry["target"], needle, needle, match_case)[1]
            for entry in self.entries_of(category) if entry.get("target"))

    def replace_all_here(self, needle: str, repl: str,
                         match_case: bool) -> int:
        here = self._here()
        count = self._replace_in(self.category, needle, repl, match_case)
        if count:
            self._refilter()
            if here is not None:
                self._goto(*here)
            self._sync()
        return count

    def count_everywhere(self, needle: str,
                         match_case: bool) -> tuple[int, int] | None:
        matches, containers = 0, 0
        for category in CATEGORIES:
            found = self._count_in(category, needle, match_case)
            if found:
                matches += found
                containers += 1
        return (matches, containers) if matches else None

    def replace_all_everywhere(self, needle: str, repl: str,
                               match_case: bool) -> tuple[int, int]:
        here = self._here()
        matches, containers = 0, 0
        for category in CATEGORIES:
            count = self._replace_in(category, needle, repl, match_case)
            if count:
                matches += count
                containers += 1
        if matches:
            self._refilter()
            if here is not None:
                self._goto(*here)
            self._sync()
        return matches, containers

    # -- saving, extracting, building --------------------------------------

    def save(self) -> None:
        if self.project is None or not self._dirty:
            self._status.showMessage("Nothing to save", 3000)
            return
        try:
            for category in sorted(self._dirty):
                doc = self.project.read(category)
                doc["entries"] = self._data[category]
                self.project.write(category, doc)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Could not save", str(error))
            return

        saved = ", ".join(LABELS[c] for c in sorted(self._dirty))
        self._dirty.clear()
        self._status.showMessage(f"Saved {saved}", 4000)
        self._sync()

    def extract_all(self) -> None:
        """Read every text source in one go: a new project's first step.

        One question, one progress window.  A source that cannot be read --
        a file missing from this install, say -- is reported at the end and
        does not stop the others.
        """
        if self.project is None:
            return
        if QMessageBox.question(
                self, "Extract everything",
                "Read the English from every source: menus, SLOT.DAT, "
                "BRIEFING.DAT, STAGEDAT.PDT (missions and staff names), the "
                "radio speaker names in the exe, and the pre-rendered movie "
                "text.\n\nTranslations already entered are kept wherever the "
                "English line is unchanged. SLOT.DAT alone takes about twenty "
                "seconds.") != QMessageBox.Yes:
            return

        self.save()
        from pwtr.qt.jobs import run_job
        project = self.project
        steps = [
            ("menus", lambda tick: project.extract_menus(tick)),
            ("story", lambda tick: project.extract("story", tick)),
            ("briefing", lambda tick: project.extract("briefing", tick)),
            ("stage", lambda tick: project.extract("stage", tick)),
            ("staff", lambda tick: project.extract("staff", tick)),
            ("names", lambda tick: project.extract("names", tick)),
            ("movies", lambda tick: project.extract_movies(tick)),
        ]

        def work(note, tick):
            failed = []
            for number, (category, step) in enumerate(steps, 1):
                label = LABELS[category]
                tick(f"{label}: step {number} of {len(steps)}")
                try:
                    count = step(tick)
                except Exception as error:      # keep going; say so after
                    failed.append(category)
                    note(f"{label}: could not be read -- {error}")
                    continue
                note(f"{label}: {count} line(s)")
            return failed

        failed, error, notes = run_job(self, "Extract everything", work)
        for category in CATEGORIES:
            self._data.pop(category, None)
        self._refilter()
        self._sync()
        if error is not None:
            self._job_failed("Could not extract", error)
            return
        self._report(
            "Extracted",
            "\n".join(notes)
            + ("\n\nNot read: " + ", ".join(LABELS[c] for c in failed)
               if failed else "\n\nEvery source was read."))

    def re_extract(self) -> None:
        if self.project is None:
            return
        if QMessageBox.question(
                self, "Read the menu text again",
                "Read the English again from the copies in originals/.\n\n"
                "Translations already entered are kept wherever the English "
                "line is unchanged.") != QMessageBox.Yes:
            return

        self.save()
        from pwtr.qt.jobs import run_job
        project = self.project
        _count, error, _notes = run_job(
            self, "Read the menu text",
            lambda note, tick: project.extract_menus(tick))
        if error is not None:
            self._job_failed("Could not read the text", error)
            return

        self._data.pop("menus", None)
        self._refilter()
        self._sync()
        self.say("Menu text re-read")

    def _slow_extract(self, category: str, container: str, title: str,
                      done: str) -> None:
        """Read one of the big containers, with something moving on screen.

        Both take long enough to look like a hang -- twenty seconds for
        SLOT.DAT, a few for BRIEFING.DAT -- so neither runs from a menu item
        that simply stops responding.
        """
        if self.project is None:
            return
        if self.project.has(category) and QMessageBox.question(
                self, title,
                f"The {LABELS[category].lower()} text is already in this "
                f"project.\n\nReading it again keeps every translation whose "
                f"English line is unchanged.  Carry on?") != QMessageBox.Yes:
            return

        self.save()
        from pwtr.qt.jobs import run_job
        project = self.project
        # Extraction reports through a single callback, and all of it is
        # progress rather than record -- "Block 250 of 2137" -- so it goes to
        # tick, which moves the bar without filling the log.
        count, error, _notes = run_job(
            self, title,
            lambda note, tick: project.extract(category, tick))
        if error is not None:
            self._job_failed(f"Could not read {container}", error)
            return

        self._data.pop(category, None)
        self._refilter()
        self._sync()
        QMessageBox.information(self, title, done.format(count=count))

    def extract_story(self) -> None:
        self._slow_extract(
            "story", "SLOT.DAT", "Story text",
            "{count} distinct line(s) read from SLOT.DAT.\n\n"
            "Repeated lines share one entry, so translating it once "
            "translates every scene that says it.")

    def extract_briefing(self) -> None:
        self._slow_extract(
            "briefing", "BRIEFING.DAT", "Briefing text",
            "{count} line(s) read from BRIEFING.DAT.\n\n"
            "Every record is packed to the byte and cannot grow, so a "
            "translation only ships if it fits the English it replaces. The "
            "editor shows each record's budget as you type.")

    def extract_stage(self) -> None:
        self._slow_extract(
            "stage", "STAGEDAT.PDT", "Mission text",
            "{count} distinct line(s) read from STAGEDAT.PDT.\n\n"
            "The text sits three containers down -- entity, archive, language "
            "table -- and most of it repeats across entities, so each line "
            "appears once and the build writes it everywhere it occurs.")

    def extract_staff(self) -> None:
        self._slow_extract(
            "staff", "STAGEDAT.PDT", "Staff names",
            "{count} name(s) read from the roster tables.\n\n"
            "A recruit is called two of these with a space between, so "
            "translating BLENNY and SPARROW once covers every pairing the "
            "game makes of them. Each name has 15 bytes and cannot grow.")

    def extract_names(self) -> None:
        from pwtr import exenames
        self._slow_extract(
            "names", exenames.EXE_NAME, "Speaker names",
            "{count} speaker name(s) read from the executable.\n\n"
            "These are the names on the radio bar under a subtitle. All but "
            "'unknown' share one run of bytes, so a longer name borrows from "
            "the others; 'unknown' has 7 bytes of its own. Building writes a "
            "patched copy of the exe, which Install puts in the game.")

    def import_translations(self) -> None:
        """Fill the blanks from a PS3 or PSP translation folder.

        Asked for one folder at a time and applied straight away, so two runs
        -- PS3 then PSP -- leave PS3's wording in place wherever it had one.
        Only blanks are filled; nothing already translated here is touched.
        """
        if self.project is None:
            return
        chosen = QFileDialog.getExistingDirectory(
            self, "The other build's translations folder")
        if not chosen:
            return

        from pwtr import importer

        box = QProgressDialog("Reading the CSVs...", "", 0, 0, self)
        box.setWindowTitle("Import translations")
        box.setCancelButton(None)
        box.setWindowModality(Qt.WindowModal)
        box.show()

        def note(message: str) -> None:
            box.setLabelText(message)
            QApplication.processEvents()

        try:
            source = importer.load(chosen, note)
        except OSError as error:
            box.close()
            QMessageBox.critical(self, "Could not read that folder", str(error))
            return

        if not source["pairs"]:
            box.close()
            QMessageBox.warning(
                self, "Nothing to import",
                f"No usable translations under\n\n{chosen}\n\n"
                f"{source['files']} CSV file(s) were read. They need a "
                f"'source_text' and a 'translation' column.")
            return

        lines = []
        for category in CATEGORIES:
            entries = self.entries_of(category)
            if not entries:
                continue
            note(f"Matching {LABELS[category]}...")
            result = importer.apply(entries, source)
            if result["total"]:
                self._dirty.add(category)
            lines.append(f"{LABELS[category]}: {result['total']} filled, "
                         f"{result['missing']} still blank")
        box.close()

        self._refilter()
        self._sync()
        QMessageBox.information(
            self, "Imported",
            f"{len(source['pairs'])} translated string(s) read from "
            f"{source['files']} file(s); {source['stubs']} stub(s) skipped.\n\n"
            + "\n".join(lines)
            + "\n\nMatched on the English text, because the three builds do "
              "not agree on addresses. Nothing already translated here was "
              "overwritten -- save to keep this.")

    def build_current(self) -> None:
        self._run_build([self.category])

    def build_all(self) -> None:
        if self.project is None:
            return
        self._run_build([c for c in CATEGORIES if self.project.has(c)])

    def _run_build(self, categories: list[str]) -> None:
        if self.project is None or not categories:
            return
        # The staff names are not a container: they are written by the
        # Missions build, along with the mission text and the artwork.  Asking
        # for both would repack STAGEDAT twice -- half a gigabyte each time,
        # to the same file, with the second pass carrying everything the first
        # one did anyway.
        categories = list(dict.fromkeys(
            RIDES_WITH.get(c, c) for c in categories))
        self.save()

        from pwtr.qt.jobs import run_job
        project = self.project
        kept: dict = {}

        def work(note, tick) -> None:
            # Runs on a worker thread: talks to the window only through note
            # and tick, and leaves _keep for afterwards.  The story walks
            # 2 137 blocks and says so every twenty-five, the missions walk
            # 557 entities -- that goes to tick, which moves the bar without
            # burying the four lines that matter in the report.
            for category in categories:
                note(f"-- {LABELS[category]}")
                if category == "story":
                    kept[category] = project.build_story(note, tick)
                elif category == "stage":
                    project.build_stage(note, tick)
                elif category == "movies":
                    kept[category] = project.build_movies(note, tick=tick)
                else:
                    kept[category] = project.build(category, note)
                # Movies have their own line-count warnings, and a scene
                # with a blank field is not "still English" -- it is burned.
                if category != "movies":
                    project.still_english(category, kept.get(category), note)
                    if category == "stage" and project.has("staff"):
                        project.still_english("staff", None, note)

        _result, error, notes = run_job(self, "Build", work)
        if error is not None:
            self._job_failed("Build failed", error)
            return
        for category, got in kept.items():
            self._keep(category, got)
        self._report("Built", "\n".join(notes)
                     + f"\n\nWritten to:\n{self.project.output}")

    def _job_failed(self, title: str, error) -> None:
        """An expected failure as a message; anything else with its traceback."""
        if isinstance(error, (ProjectError, OSError, ValueError)):
            QMessageBox.critical(self, title, str(error))
        else:
            self._report(title, getattr(error, "trace", None) or repr(error))

    def _keep(self, category: str, report) -> None:
        """Take the overflow a build worked out, rather than working it out again.

        The build already knows which lines it could not place -- it decided
        that line by line -- so remembering it here is the difference between
        "Too long only" answering at once and spending minutes rediscovering
        what was just computed.
        """
        if isinstance(report, dict) and "lines" in report:
            self._overlong_by[category] = report["lines"]

    def _report(self, title: str, text: str) -> None:
        """Show a report in something you can read all of.

        A message box sizes itself to its text and then stops: a long build
        runs off the screen with no way to scroll and no way to copy the one
        line you wanted.  The same text, in a window that scrolls, resizes,
        and hands it to the clipboard.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(view, 1)

        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        row.addWidget(copy)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        row.addWidget(buttons)
        layout.addLayout(row)

        screen = QApplication.primaryScreen()
        room = screen.availableGeometry() if screen else None
        dialog.setMinimumSize(420, 260)
        dialog.resize(min(860, room.width() - 80) if room else 860,
                      min(560, room.height() - 80) if room else 560)
        QShortcut(QKeySequence("Esc"), dialog, dialog.reject)
        dialog.exec()

    def extract_movies(self) -> None:
        """Fill the Movies tab with the English burned into the three scenes.

        Nothing is read from the game: those words are pixels.  The source is
        a transcription the toolkit carries, and anything already subtitled in
        this project is picked up so hand-made work is not lost.
        """
        if self.project is None:
            return
        self.save()
        try:
            count = self.project.extract_movies(self._status.showMessage)
        except (ProjectError, OSError, ValueError) as error:
            QMessageBox.critical(self, "Could not read the movie text",
                                 str(error))
            return
        self._data.pop("movies", None)
        self._refilter()
        self._sync()
        QMessageBox.information(
            self, "Movie text",
            f"{count} block(s) across three scenes.\n\n"
            "These are burned into the video, so translating them writes a "
            "subtitle and re-encodes the movie -- Build this container does "
            "both. Keep the line breaks roughly as they are: each line is one "
            "line on screen, and the roll is timed to how many there are.")

    def movie_style(self) -> None:
        """Choose the font and size the movie subtitles are burned with."""
        if self.project is None:
            return
        from PySide6.QtWidgets import QFontComboBox, QSpinBox
        from PySide6.QtGui import QFont as _QFont

        style = self.project.movie_style
        dialog = QDialog(self)
        dialog.setWindowTitle("Movie subtitle font and size")
        layout = QVBoxLayout(dialog)

        use_file = QCheckBox("Use a font file instead of an installed font")
        family = QFontComboBox()
        if style["font"]:
            family.setCurrentFont(_QFont(style["font"]))
        file_label = QLabel(style["font_file"] or "none chosen")
        file_label.setWordWrap(True)
        choose = QPushButton("Choose a font file...")

        def pick() -> None:
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Choose a font", "", "Fonts (*.ttf *.otf *.ttc)")
            if path:
                file_label.setText(path)
                use_file.setChecked(True)

        choose.clicked.connect(pick)
        use_file.setChecked(bool(style["font_file"]))

        def sync() -> None:
            family.setEnabled(not use_file.isChecked())
            choose.setEnabled(use_file.isChecked())
            file_label.setEnabled(use_file.isChecked())

        use_file.toggled.connect(sync)
        sync()

        keep = QCheckBox("Keep each subtitle's own font")
        keep.setChecked(not style["font"] and not style["font_file"])
        keep.toggled.connect(
            lambda on: [w.setEnabled(not on) for w in (use_file, family,
                                                       choose, file_label)]
            or (None if on else sync()))

        size = QSpinBox()
        size.setRange(50, 200)
        size.setSuffix(" %")
        size.setValue(int(style["scale"]))

        row = QHBoxLayout()
        row.addWidget(QLabel("Size"))
        row.addWidget(size)
        row.addStretch(1)

        for widget in (keep, use_file, family, choose, file_label):
            layout.addWidget(widget)
        layout.addLayout(row)
        note = QLabel(
            "Size scales every size in the subtitle together, so the roll, "
            "the cards and both resolutions stay in proportion.\n\n"
            "A different font or size wraps lines differently, which can put "
            "a roll out of step with the video. Preview before you build.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if keep.isChecked():
            for w in (use_file, family, choose, file_label):
                w.setEnabled(False)
        if dialog.exec() != QDialog.Accepted:
            return

        chosen_file = file_label.text() if file_label.text() != "none chosen" else ""
        if keep.isChecked():
            new = {"font": "", "font_file": "", "scale": size.value()}
        elif use_file.isChecked() and chosen_file:
            new = {"font": "", "font_file": chosen_file, "scale": size.value()}
        else:
            new = {"font": family.currentFont().family(), "font_file": "",
                   "scale": size.value()}
        self.project.movie_style = new
        self.project.save_manifest()
        what = (Path(new["font_file"]).name if new["font_file"]
                else new["font"] or "each subtitle's own font")
        self.say(f"Movie subtitles: {what}, {new['scale']}%")

    def preview_movies(self) -> None:
        """Burn a quick copy of the movies, with their soundtrack, to watch.

        Written to <project>/preview, never to output -- output is installed
        into the game wholesale.  Only the 720p set by default, since the
        1080p60 encodes are what make a build slow.
        """
        if self.project is None:
            return
        from pwtr import hardsub
        if not hardsub.subtitles(self.project.root):
            QMessageBox.information(
                self, "Preview",
                "There are no movie subtitles in this project yet -- see "
                "Build > Burn the subtitled movies for where they go.")
            return
        if not hardsub.have_ffmpeg():
            QMessageBox.warning(self, "Preview",
                                "ffmpeg and ffprobe are not on PATH.")
            return
        answer = QMessageBox.question(
            self, "Preview the subtitled movies",
            "Preview the 720p set only?\n\n"
            "Yes is quick. No also renders the 1080p set, which takes "
            "several minutes.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return
        folders = ("Mov",) if answer == QMessageBox.Yes else ("Mov", "hqMov")

        self.save()
        from pwtr.qt.jobs import run_job
        project = self.project
        report, error, _notes = run_job(
            self, "Preview",
            lambda note, tick: project.build_movies(
                note, preview=True, folders=folders, tick=tick))
        if error is not None:
            self._job_failed("Could not render the previews", error)
            return

        folder = self.project.root / "preview"
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if QMessageBox.question(
                self, "Previews ready",
                f"{len(report['made'])} preview(s) in:\n\n{folder}\n\n"
                "Open the folder?") == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def build_movies(self) -> None:
        """Burn the .ass subtitles into the three pre-rendered scenes.

        Slow enough to need saying so -- four 62-second re-encodes, two of
        them 1080p60 -- and it draws on ffmpeg, which is the one thing in this
        program that is not Python.
        """
        if self.project is None:
            return
        from pwtr import hardsub

        found = hardsub.subtitles(self.project.root)
        if not found:
            QMessageBox.information(
                self, "Subtitled movies",
                "Three scenes have their text burned into the picture: the "
                "Kant epigraph and the two halves of the chronology. They are "
                "translated by subtitling them, not by typing here.\n\n"
                "Put the .ass files in:\n\n"
                f"{self.project.root / 'hardsub'}\\Mov\n"
                f"{self.project.root / 'hardsub'}\\hqMov\n\n"
                "named after the movie -- 004bc514.ass, 004bc518.ass, "
                "00568c22.ass. Both folders matter: the game chooses between "
                "them at runtime, and subtitling one set leaves the other in "
                "English.")
            return
        if not hardsub.have_ffmpeg():
            QMessageBox.warning(
                self, "Subtitled movies",
                "ffmpeg and ffprobe are not on PATH.\n\nThey do the drawing "
                "and the re-encoding; everything else here is pure Python.")
            return

        names = ", ".join(sorted({stem for _f, stem in found}))
        if QMessageBox.question(
                self, "Burn the subtitled movies",
                f"{len(found)} movie(s) to build: {names}.\n\n"
                "Each is re-encoded from the game's own copy, so this takes a "
                "few minutes -- the 1080p pair are 62 seconds at 60fps. The "
                "result goes to the project's output and installs with "
                "everything else.") != QMessageBox.Yes:
            return

        from pwtr.qt.jobs import run_job
        self.save()
        project = self.project
        report, error, notes = run_job(
            self, "Subtitled movies",
            lambda note, tick: project.build_movies(note, tick=tick))
        if error is not None:
            self._job_failed("Could not burn the movies", error)
            return
        self._report("Subtitled movies",
                     "\n".join(notes)
                     + (("\n\nSkipped:\n" + "\n".join(report["skipped"]))
                        if report["skipped"] else "")
                     + f"\n\n{len(report['made'])} movie(s) written to:\n"
                     + str(self.project.output))

    def rename_save(self) -> None:
        """Rewrite the codenames of the soldiers already on Mother Base.

        Building STAGEDAT names the *next* recruit, not the 305 already
        there: a codename is copied into the save when the soldier is
        fultoned.  So the roster needs its own pass, and it is the only edit
        in this program that touches a save file.
        """
        if self.project is None:
            return
        if not self.project.has("staff"):
            QMessageBox.warning(
                self, "Staff names",
                "Read the staff names from STAGEDAT.PDT first -- the rename "
                "matches on the English codename, so it needs the tab.")
            return

        start = self.project.save_folder()
        chosen, _ = QFileDialog.getOpenFileName(
            self, "The save to rename in", str(start or ""),
            "Peace Walker saves (STW*);;All files (*)")
        if not chosen:
            return

        # Dry run first.  A save is somebody's playthrough, so what is about
        # to happen is shown before anything is written, not after.
        try:
            self._busy("Reading the save...")
            report = self.project.rename_save(chosen, write=False)
        except (ProjectError, OSError, ValueError) as error:
            self._idle()
            QMessageBox.critical(self, "Could not read the save", str(error))
            return
        self._idle()

        lines = [f"{report['renamed']} soldier(s) would be renamed."]
        if report["over"]:
            lines.append("")
            lines.append("Too long for the 15-byte codename field, so left "
                         "in English:")
            lines += [f"    {english} -> {target} ({need} bytes)"
                      for english, target, need in report["over"][:8]]
        if report["left"]:
            lines.append("")
            lines.append(
                f"{report['left']} soldier(s) keep their English name -- "
                f"{len(report['names'])} name(s) are not translated yet.")
        if not report["renamed"]:
            QMessageBox.information(self, "Nothing to rename",
                                    "\n".join(lines))
            return

        lines.append("")
        lines.append("The new save is written beside this one under the "
                     "checksum name the game requires, and this one is moved "
                     "into a 'superseded' folder so the load menu does not "
                     "show both.  Close the game first.")
        if QMessageBox.question(self, "Rename the staff",
                                "\n".join(lines)) != QMessageBox.Yes:
            return

        try:
            self._busy("Writing the save...")
            report = self.project.rename_save(chosen, write=True)
        except (ProjectError, OSError, ValueError) as error:
            self._idle()
            QMessageBox.critical(self, "Could not write the save", str(error))
            return
        self._idle()
        QMessageBox.information(
            self, "Save written",
            f"{report['renamed']} soldier(s) renamed.\n\n"
            f"Written:\n{report['out']}\n\n"
            + (f"Moved aside:\n{report['moved']}" if report["moved"] else ""))

    def install(self) -> None:
        if self.project is None:
            return
        files = self.project.built_files()
        if not files:
            QMessageBox.warning(self, "Nothing to install",
                                "Build the containers first.")
            return

        if QMessageBox.question(
                self, "Install into the game",
                f"Copy {len(files)} built file(s) into:\n\n{self.project.game}"
                "\n\nThe first time a file is replaced its original is saved "
                "next to it as a .bak.  SLOT.DAT is half a gigabyte, so its "
                "backup takes a moment and a fair amount of "
                "disk.") != QMessageBox.Yes:
            return

        from pwtr.qt.jobs import run_job
        project = self.project
        count, error, notes = run_job(
            self, "Install", lambda note, tick: project.install(note, tick))
        if error is not None:
            self._job_failed("Could not install", error)
            return
        self._report("Installed",
                     "\n".join(notes) or f"{count} file(s) copied.")

    def bundle_release(self) -> None:
        """Zip the build in the game's own layout, for players to copy in."""
        if self.project is None:
            return
        from pwtr import exenames
        files = self.project.built_files()
        if not files:
            QMessageBox.warning(self, "Nothing to bundle",
                                "Build the containers first.")
            return

        include_exe = False
        if any(f.name == exenames.EXE_NAME for f in files):
            include_exe = QMessageBox.question(
                self, "Include the executable?",
                "The build has a patched copy of the game executable -- it "
                "carries the radio speaker names.\n\n"
                "The exe is the publisher's program, not a data file, and this "
                "copy was patched from the exe on this machine. If that exe "
                "has been altered (a DRM-free or cracked build, for instance) "
                "it would ship to everyone who downloads the mod.\n\n"
                "Include it in the zip?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) == QMessageBox.Yes

        suggested = str(self.project.root.parent
                        / f"{self.project.name}-release.zip")
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "Save the release zip", suggested, "Zip archive (*.zip)")
        if not chosen:
            return

        self.save()
        from pwtr.qt.jobs import run_job
        project = self.project
        result, error, notes = run_job(
            self, "Bundle",
            lambda note, tick: project.bundle(chosen, include_exe, note, tick))
        if error is not None:
            self._job_failed("Could not bundle", error)
            return
        self._report(
            "Bundled",
            "\n".join(notes)
            + f"\n\n{result['files']} file(s), "
              f"{result['bytes'] / (1 << 20):.0f} MB:\n{result['path']}\n\n"
              "Unzip into the game folder, next to the executable.")

    def restore(self) -> None:
        if self.project is None:
            return
        if QMessageBox.question(
                self, "Restore the originals",
                f"Put the game's own files back in:\n\n{self.project.game}\n\n"
                "This uses the .bak copies saved at the first install, or the "
                "project's own pristine tables where there is no .bak. Files "
                "the install added, which the game never had, are removed.\n\n"
                "Your translations are not touched -- only what is installed "
                "in the game.") != QMessageBox.Yes:
            return

        from pwtr.qt.jobs import run_job
        project = self.project
        count, error, notes = run_job(
            self, "Restore", lambda note, tick: project.restore(note, tick))
        if error is not None:
            self._job_failed("Could not restore", error)
            return
        self._report(
            "Restore",
            ("\n".join(notes) if notes else "Nothing needed restoring.")
            + (f"\n\n{count} file(s) put back or removed." if count else ""))

    # -- fonts and textures ------------------------------------------------

    def install_font(self) -> None:
        if self.project is None:
            return
        if self.project.game is None:
            QMessageBox.warning(self, "Fonts",
                                "The game folder is not set on this project.")
            return
        from pwtr.qt.fontinstall import FontInstaller
        dialog = FontInstaller(self.project, self)
        dialog.exec()
        self._load_preview_font()
        self._repreview()

    def font_plan(self) -> None:
        """What the translation needs against what the menu face can hold."""
        if self.project is None:
            return
        self.save()
        self._busy("Shaping the translation...")
        wanted = self.project.wanted_glyphs("menus")
        self._idle()

        if not wanted:
            QMessageBox.information(
                self, "The menu face",
                "Nothing is translated in the Menus tab yet, so there is "
                "nothing to fit.\n\nThe face has 48 free cells before any "
                "Latin is given up.")
            return

        report = arabic.capacity(wanted, self.project.borrow)
        lines = [f"The menu translation needs {report['wanted']} distinct "
                 f"glyph shapes.",
                 f"The face has {report['cells']} cell(s) available"
                 + (f" (with {', '.join(report['borrowed'])} given up)."
                    if report['borrowed'] else " in its free band.")]
        if report["fits"]:
            lines.append("\nIt fits.")
        else:
            lines.append(f"\nIt is {report['short']} cell(s) short.")
            if report["would_fit_if"]:
                lines.append("\nGiving up a Latin range would make room:")
                lines += [f"  * {why}" for _name, why in report["would_fit_if"]]
            else:
                lines.append(
                    "\nEven with every Latin range given up it does not fit. "
                    "The subtitle face has no such limit, so the story is "
                    "unaffected -- it is the menus that would have to lose "
                    "some glyph shapes.")
        QMessageBox.information(self, "The menu face", "\n".join(lines))

    def textures(self) -> None:
        """Open the texture side of the project in its own window.

        Textures are a different job from text -- a picture at a time rather
        than a line at a time, with an outside image editor in the middle of
        the loop -- so they get their own window rather than another tab here.
        It is kept on ``self`` so it is not garbage collected on return.
        """
        from pwtr.qt.texturebench import TextureBench, last_opened

        if self._textures is not None:
            self._textures.show()
            self._textures.raise_()
            self._textures.activateWindow()
            return
        self._textures = TextureBench(last_opened())
        self._textures.show()
        if self._textures.textures is None:
            self._textures.open_folder()

    def unpack_textures(self) -> None:
        """The texture side has to start somewhere, and this is where."""
        self.textures()
        self._textures.unpack()

    # -- chrome ------------------------------------------------------------

    def _sync(self) -> None:
        """Refresh the tab labels, the counter and the window title."""
        if self.project is None:
            self._state_label.setText("")
            self._tabs.setEnabled(False)
            return

        self._tabs.setEnabled(True)
        for index, category in enumerate(CATEGORIES):
            rows = self._data.get(category)
            if rows is not None:
                done, total = sum(1 for r in rows if r.get("target")), len(rows)
            else:
                done, total = self.project.stats(category)
            label = LABELS[category]
            self._tabs.setTabText(
                index, f"{label}  {done}/{total}" if total
                else f"{label}  not read yet")

        rows = self.entries()
        done = sum(1 for r in rows if r.get("target"))
        shown = self._model.rowCount()
        state = f"{done} of {len(rows)} translated"
        if shown != len(rows):
            state += f"   ({shown} shown)"
        if self._dirty:
            state += "   * unsaved"
        self._state_label.setText(state)

        star = "*" if self._dirty else ""
        self.setWindowTitle(
            f"{star}Peace Walker Arabic Translator -- {self.project.name}")

    def _busy(self, message: str) -> None:
        # Says what is happening, and nothing else: this is called for
        # installing, restoring, shaping and the save rename, so keying the
        # message off which tab is open announced a story rebuild during all
        # of them.  next_overlong says its own piece where it belongs.
        self._status.showMessage(message)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()

    def _idle(self) -> None:
        QApplication.restoreOverrideCursor()
        self._status.clearMessage()

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved translations",
            "Save the current translations first?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Yes:
            self.save()
        return True

    def closeEvent(self, event) -> None:      # noqa: N802
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    app = QApplication(sys.argv[:1])
    theme.apply(app)

    project = None
    if argv:
        try:
            project = Project.load(argv[0])
        except (ProjectError, OSError, ValueError) as error:
            print(f"Could not open {argv[0]}: {error}", file=sys.stderr)

    window = Workbench(project)
    window.show()
    return app.exec()
