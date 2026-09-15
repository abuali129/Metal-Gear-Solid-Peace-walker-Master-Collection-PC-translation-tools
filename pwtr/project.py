"""Project model: a folder holding the game's text, its translation and a build.

A project is a plain folder, so it survives without the app::

    <project>/
        project.json      what game folder it came from, and the font plan
        originals/        pristine copies of the .olang tables and the SLOT key
        text/             <category>.json -- source + translation, editable
        output/           rebuilt files, laid out the way the game expects

Translations are stored the way they are typed: logical Arabic, in reading
order.  Shaping to presentation forms -- and, for the menu face, mapping those
onto the atlas cells -- happens only on build, so the project files stay
readable and re-editable.

## Two categories, and why they are not five

Peace Walker keeps its text in two places that have nothing in common:

* **Menus** -- the seventeen shipped ``.olang`` tables under ``MLG/Text`` and
  ``EXLANG/Text``.  20 012 pool strings, of which 1 631 are English; the rest
  are the fr/ge/it/jp/sp columns.  Small, fast, byte-exact to rebuild.
* **Story** -- everything the player actually reads: 4 458 ``.olang`` files
  inside the 2 137 compressed blocks of ``SLOT.DAT``, subtitles included --
  10 074 distinct English lines.  Half a gigabyte to walk (about 20 seconds),
  and it can only be written back block by block, within each block's own
  footprint.

## The 544 MB that is not copied

Every other original is copied into the project.  ``SLOT.DAT`` is not: at
544 MB a copy per project is not a safety measure, it is a disk problem.
Instead the build reads whatever is stock -- the ``.bak`` written at the first
install if there is one, the game's own file if there is not -- so rebuilding
never stacks a translation on top of a previous translation.
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from pwtr import (arabic, briefing, compact, exenames, slotraw, staff,
                  stagedat)
from pwtr import fonts
from pwtr.formats import olang, slotdat, slotitem, ypk

#: Containers a project tracks, in the order the tabs show them.
CATEGORIES = ("menus", "story", "briefing", "stage", "staff",
              "names", "movies")

LABELS = {"menus": "Menus", "story": "Story", "briefing": "Briefing",
          "stage": "Missions", "staff": "Staff", "names": "Speakers",
          "movies": "Movies"}

#: Categories that are not containers of their own.  The staff names live in
#: fixed-record tables inside STAGEDAT, so they are written by the Missions
#: build -- one 487 MB pass, not two, and neither overwriting the other.
RIDES_WITH = {"staff": "stage"}

#: The .olang style that means "draw this with the caps atlas".  Every other
#: style goes to the XPR face, which has a real character map.
CAPS_STYLE = 0x0001

#: The face a category's text reaches by default.  Menus is not a single
#: answer -- it is per line, from the style on the reference -- so the entry's
#: own ``styles`` decides, and this is only the fallback.
FACE = {"menus": "subtitle", "story": "subtitle", "briefing": "subtitle",
        "stage": "subtitle", "staff": "subtitle",
        # The radio bar draws the speaker with the subtitle face: an
        # untranslated "Miller" showed as M and four Arabic letters.
        "names": "subtitle",
        # Burned into the picture by a subtitle renderer, not drawn by the
        # game, so no code point of ours is involved.
        "movies": "none"}


def face_of(entry: dict) -> str:
    """Which face draws this line: ``"ui"`` (the atlas) or ``"subtitle"``."""
    return "ui" if CAPS_STYLE in (entry.get("styles") or []) else "subtitle"

PROJECT_NAME = "project.json"
SOURCE_LANG = "en"

#: Where the shipped language tables live, relative to the game folder.
TEXT_DIRS = {"MLG": Path("MLG") / "Text",
             "EXLANG": Path("EXLANG") / "Text"}

SLOT_DIR = Path("MLG") / "disc0_rel"
SLOT_DAT = "002aba34.DAT"
SLOT_KEY = "002aba34.KEY"

#: BRIEFING.DAT and STAGEDAT.PDT, stored under the hashes of their names.
BRIEFING_DAT = briefing.NAME
STAGEDAT_PDT = stagedat.NAME

ProgressFn = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


class ProjectError(Exception):
    pass


def find_game(hint: str | Path | None = None) -> Path | None:
    """The game folder -- ``.../MGS_PW/mgspw`` -- or None."""
    from pwtr.formats import pwpaths
    found = pwpaths.find_game(str(hint) if hint else None)
    return Path(found) if found else None


#: Beside a file install put into the game where the game had none.  Its
#: presence is the whole record: restore removes the file instead of looking
#: for a ``.bak``, and install never backs that file up.
ADDED_SUFFIX = ".pwtr-added"


def added_marker(path: Path) -> Path:
    return path.with_name(path.name + ADDED_SUFFIX)


def _copy_with_progress(source: Path, destination: Path, done: int,
                        total: int, tick: ProgressFn, label: str) -> int:
    """``shutil.copy2``, reporting as it goes, and never half-written in place.

    The copy goes to a ``.part`` beside the destination and is moved over it
    only once complete.  Copying straight onto a game file means a failure
    part way through -- a full disk, a locked file -- leaves the game holding
    half of a container, which is worse than either the old file or the new.

    Returns the running byte count, for the next file to carry on from.
    """
    part = destination.with_name(destination.name + ".part")
    step = 8 << 20
    with open(source, "rb") as reader, open(part, "wb") as writer:
        while True:
            chunk = reader.read(step)
            if not chunk:
                break
            writer.write(chunk)
            done += len(chunk)
            tick(f"{label}   {done * 100 // max(1, total)}%")
    shutil.copystat(source, part)
    os.replace(part, destination)
    return done


def _stub_others(elements, translations) -> tuple[bool, int]:
    """Empty every reference no one in an Arabic build will read.

    English keeps its text and Japanese is left exactly as it was -- the PS3
    tooling found its entities are shaped differently and stubbing them broke
    things.  The rest point at an empty string, and ``compact_pool`` then
    drops what nothing references any more, which is most of the block.

    Returns whether anything was rewritten at all, and how many references
    were actually emptied.  The two are not the same: a block with no other
    languages in it still comes back rewritten, because re-setting the
    English and compacting the pool drops orphaned strings and can be enough
    on its own.  Reporting that as "emptied the other languages" names a
    cause that was not there -- block 0088B holds English and nothing else.
    """
    from pwtr.formats import olang as _olang

    spare = {"fr", "ge", "it", "sp"}
    emptied = False
    spared = 0
    for element in elements:
        if element.ext != "olang":
            continue
        try:
            table = _olang.OlangFile.parse(element.data)
        except Exception:
            continue
        hits = 0
        for reference in table.refs:
            language = _olang.LANG_NAME.get(reference.lang)
            if language == SOURCE_LANG:
                target = translations.get(table.pool[reference.text])
                if target is not None:
                    table.set_ref(reference, target)
                    hits += 1
            elif language in spare and table.pool[reference.text]:
                table.set_ref(reference, "")
                hits += 1
                spared += 1
        if hits:
            table.compact_pool()
            element.data = table.build() + element.data[table.length:]
            emptied = True
    return emptied, spared


def _raw_rewrite(element, translations: dict):
    """Story text outside the language tables, written without resizing.

    Parsed for ``.ypk`` and ``.ohd``; byte-scanned for the pool strings of an
    ``.olang``, the one place a scan is still the best there is.  Returns the
    :func:`pwtr.slotraw.rewrite` triple either way.
    """
    if element.ext in ("ypk", "ohd"):
        return ypk.rewrite(element.ext, element.data, translations)
    return slotraw.rewrite(element.data, translations)


class Project:
    def __init__(self, root: Path, manifest: dict):
        self.root = Path(root)
        self.manifest = manifest

    # -- layout ------------------------------------------------------------

    @property
    def file(self) -> Path:
        return self.root / PROJECT_NAME

    @property
    def originals(self) -> Path:
        return self.root / "originals"

    @property
    def text(self) -> Path:
        return self.root / "text"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def name(self) -> str:
        return self.manifest.get("name", self.root.name)

    @property
    def game(self) -> Path | None:
        raw = self.manifest.get("game")
        return Path(raw) if raw else None

    def text_of(self, category: str) -> Path:
        return self.text / f"{category}.json"

    def original_tables(self) -> Iterator[tuple[str, Path]]:
        """``(tag, path)`` for every copied ``.olang``, in a fixed order."""
        for tag, relative in TEXT_DIRS.items():
            folder = self.originals / relative
            if folder.is_dir():
                for path in sorted(folder.glob("*.olang")):
                    yield tag, path

    def stock_slot(self) -> tuple[Path, Path] | None:
        """The DAT and KEY to build from: the ``.bak`` pair if one exists.

        Building from an already-installed translation would shape text that
        is already shaped and re-smuggle bytes that are already smuggled, so
        the stock pair is the only safe source.
        """
        if self.game is None:
            return None
        folder = self.game / SLOT_DIR
        dat, key = folder / SLOT_DAT, folder / SLOT_KEY
        backup = dat.with_suffix(dat.suffix + ".bak")
        if backup.exists():
            dat = backup
        key_backup = key.with_suffix(key.suffix + ".bak")
        if key_backup.exists():
            key = key_backup
        return (dat, key) if dat.exists() and key.exists() else None

    # -- opening and creating ---------------------------------------------

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        root = Path(root)
        manifest_file = root / PROJECT_NAME
        if not manifest_file.exists():
            raise ProjectError(f"No {PROJECT_NAME} in {root}")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        return cls(root, manifest)

    @classmethod
    def create(cls, root: str | Path, game: str | Path) -> "Project":
        root, game = Path(root), Path(game)
        if not (game / "MLG").is_dir() or not (game / "FONT").is_dir():
            raise ProjectError(
                f"{game} does not look like the game folder.\n\n"
                f"It should be the mgspw folder, the one holding MLG, "
                f"EXLANG, FONT and Text.")
        root.mkdir(parents=True, exist_ok=True)
        if (root / PROJECT_NAME).exists():
            raise ProjectError(f"{root} already holds a project")

        project = cls(root, {
            "name": root.name,
            "game": str(game),
            "created": datetime.now().isoformat(timespec="seconds"),
            "language": "ar",
            "font": {"borrow": [], "mapping": {}},
        })
        for folder in (project.originals, project.text, project.output):
            folder.mkdir(parents=True, exist_ok=True)
        project.save_manifest()
        return project

    def save_manifest(self) -> None:
        self.manifest["saved"] = datetime.now().isoformat(timespec="seconds")
        temp = self.file.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        temp.replace(self.file)

    # -- the text files ----------------------------------------------------

    def has(self, category: str) -> bool:
        return self.text_of(category).exists()

    def read(self, category: str) -> dict:
        path = self.text_of(category)
        if not path.exists():
            return {"category": category, "entries": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, category: str, doc: dict) -> None:
        self.text.mkdir(parents=True, exist_ok=True)
        path = self.text_of(category)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        temp.replace(path)

    def stats(self, category: str) -> tuple[int, int]:
        """``(translated, total)`` without loading the file into the window."""
        if not self.has(category):
            return 0, 0
        entries = self.read(category)["entries"]
        return sum(1 for e in entries if e.get("target")), len(entries)

    def targets(self) -> dict[str, str]:
        """Every translated line in the project, as source -> target."""
        out: dict[str, str] = {}
        for category in CATEGORIES:
            for entry in self.read(category)["entries"] if self.has(category) else []:
                if entry.get("target"):
                    out[entry["source"]] = entry["target"]
        return out

    # -- filling a new project --------------------------------------------

    def dump(self, progress: ProgressFn = _noop) -> int:
        """Copy the originals in and read the menu text out.

        The story is deliberately not touched here: walking half a gigabyte of
        SLOT.DAT takes about twenty seconds, which is twenty seconds too many
        to spend before the first window appears.  :meth:`extract_story` does
        it when asked, with a progress line.
        """
        if self.game is None:
            raise ProjectError("This project has no game folder")

        copied = 0
        for relative in TEXT_DIRS.values():
            source = self.game / relative
            if not source.is_dir():
                continue
            destination = self.originals / relative
            destination.mkdir(parents=True, exist_ok=True)
            for path in sorted(source.glob("*.olang")):
                progress(f"Copying {path.name}...")
                shutil.copy2(path, destination / path.name)
                copied += 1

        key = self.game / SLOT_DIR / SLOT_KEY
        if key.exists():
            destination = self.originals / SLOT_DIR
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(key, destination / key.name)

        if copied:
            self.extract_menus(progress)
        return copied

    def extract_menus(self, progress: ProgressFn = _noop) -> int:
        """Read the English out of the copied ``.olang`` tables.

        One entry per *pool* string that any English reference points at.  The
        pool is deduplicated by the game itself, so one entry can serve several
        menus -- which is exactly what makes a menu translation consistent.

        The reference's **style** is recorded with it, because it decides which
        font draws the line and therefore whether the line is constrained at
        all.  Style ``0x0001`` is the caps atlas, with 63 free cells; ``0x0402``
        is the XPR face, with a Unicode character map and no such limit.  Of
        the English references, 80 are the first and 2 709 the second.
        """
        keep = {e["source"]: e.get("target", "")
                for e in self.read("menus")["entries"]} if self.has("menus") else {}

        entries = []
        for tag, path in self.original_tables():
            progress(f"Reading {path.name}...")
            table = olang.read(str(path))
            langs = table.pool_langs()
            # A pool entry is shared, so gather every style that reaches it.
            styles: list[set] = [set() for _ in table.pool]
            for reference in table.refs:
                if olang.LANG_NAME.get(reference.lang) == SOURCE_LANG:
                    styles[reference.text].add(reference.style)
            for index, source in enumerate(table.pool):
                if SOURCE_LANG not in langs[index] or not source.strip():
                    continue
                entries.append({
                    "key": f"{tag}/{path.stem}/{index:04d}",
                    "file": f"{tag}/{path.name}",
                    "pool": index,
                    "langs": langs[index],
                    "styles": sorted(styles[index]),
                    "source": source,
                    "target": keep.get(source, ""),
                })

        self.write("menus", {"category": "menus", "entries": entries})
        return len(entries)

    def extract_story(self, progress: ProgressFn = _noop) -> int:
        """Read the English out of SLOT.DAT: 10 074 lines, about 20 seconds.

        One entry per *distinct* string rather than per reference.  The same
        line recurs across scenes, and translating each occurrence separately
        is how a script drifts; a single entry keeps them identical, and the
        build writes it to every reference that carries it.
        """
        pair = self.stock_slot()
        if pair is None:
            raise ProjectError(
                "SLOT.DAT was not found in the game folder:\n\n"
                f"{(self.game or Path()) / SLOT_DIR / SLOT_DAT}")
        dat, key = pair

        keep = {e["source"]: e.get("target", "")
                for e in self.read("story")["entries"]} if self.has("story") else {}

        _header, (high, low), records = slotdat.load_key(str(key))
        stream = slotdat.WordStream(high, low)

        entries, seen = [], set()
        with open(dat, "rb") as handle:
            for number, record in enumerate(records):
                if number % 25 == 0:
                    progress(f"Block {number} of {len(records)} -- "
                             f"{len(entries)} lines")
                try:
                    elements, _head, _t = slotitem.parse(
                        slotdat.read_block(handle, record, stream))
                except Exception:
                    continue                  # a block that is not a container
                for position, element in enumerate(elements):
                    if element.ext != "olang":
                        continue
                    try:
                        table = olang.OlangFile.parse(element.data)
                    except Exception:
                        continue
                    for _g, _e, _slot, lang, ref, source in table.entries():
                        if lang != SOURCE_LANG or not source.strip():
                            continue
                        if source in seen:
                            continue
                        seen.add(source)
                        entries.append({
                            "key": f"slot/{record.start:05X}/{position}/{ref}",
                            "source": source,
                            "target": keep.get(source, ""),
                        })

                # The dialogue that is not in a language table: voice-cue
                # subtitles in .ohd, co-op and tutorial lines in .ypk, and
                # pool strings no English reference points at.  Offered last
                # so a line that is in a table is catalogued as one -- those
                # can grow, and these cannot.
                #
                # .ypk and .ohd are parsed (pwtr.formats.ypk).  They used to
                # be byte-scanned too, and the scan's English vote threw away
                # short lines -- "Careful. They've got reinforcements
                # coming." among 2 134 others.
                for position, element in enumerate(elements):
                    if element.ext == "ypk":
                        for n, r, source in ypk.ypk_strings(element.data):
                            if source in seen:
                                continue
                            seen.add(source)
                            entries.append({
                                "key": f"slot/{record.start:05X}/{position}"
                                       f"/ypk{n}.{r}",
                                "source": source,
                                "target": keep.get(source, ""),
                            })
                        continue
                    if element.ext == "ohd":
                        for offset, source in ypk.ohd_strings(element.data):
                            if source in seen:
                                continue
                            seen.add(source)
                            entries.append({
                                "key": f"slot/{record.start:05X}/{position}"
                                       f"/ohd{offset:05X}",
                                "budget": ypk.OHD_ROOM,
                                "source": source,
                                "target": keep.get(source, ""),
                            })
                        continue
                    for offset, raw in slotraw.strings(element.data):
                        source = raw.decode("utf-8")
                        if source in seen:
                            continue
                        seen.add(source)
                        entries.append({
                            "key": f"slot/{record.start:05X}/{position}"
                                   f"/raw{offset:05X}",
                            "budget": len(raw),
                            "source": source,
                            "target": keep.get(source, ""),
                        })

        self.write("story", {"category": "story", "entries": entries})
        return len(entries)

    def stock_briefing(self) -> Path | None:
        """BRIEFING.DAT to build from: the ``.bak`` if one exists."""
        if self.game is None:
            return None
        path = self.game / SLOT_DIR / BRIEFING_DAT
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            return backup
        return path if path.exists() else None

    def extract_briefing(self, progress: ProgressFn = _noop) -> int:
        """Read the English briefing tapes out of BRIEFING.DAT.

        One entry per distinct *string offset*, not per table index: two
        indices sharing an offset are one string, drawn in both places, so it
        is translated once.

        Each entry carries its record's byte budget, because on this container
        the budget is the whole story -- see :meth:`build_briefing`.
        """
        source = self.stock_briefing()
        if source is None:
            raise ProjectError(
                "BRIEFING.DAT was not found in the game folder:\n\n"
                f"{(self.game or Path()) / SLOT_DIR / BRIEFING_DAT}")

        keep = {}
        if self.has("briefing"):
            keep = {e["source"]: e.get("target", "")
                    for e in self.read("briefing")["entries"]}

        progress("Decrypting BRIEFING.DAT...")
        data = briefing.load(source)
        records = briefing.parse(data)
        # Records whose offset table straddled a page break come back only
        # from the raw file: page-wise, their table is nonsense and parse can
        # do no more than mark them.  They stay read-only either way.
        records = briefing.recover(source.read_bytes(), records, BRIEFING_DAT)
        # Strings the page-wise decryption cut in half, read back at the
        # alignment their own cipher run uses.  They can be shown and
        # translated; writing one needs its run re-encrypted, so the build
        # leaves them alone and says so.
        mended = briefing.mend(source.read_bytes(), records)
        if mended:
            progress(f"{len(mended)} string(s) recovered from across a page "
                     f"boundary")
        progress(f"{len(records)} records; picking out the English ones...")

        entries = []
        for record in records:
            # A record that cannot be repacked can still be written line by
            # line, inside the bytes each line already occupies, so it is
            # extracted like any other.  What it cannot do is lend one line's
            # slack to another, which the entry records.
            # Vote on the mended strings, not the broken ones.  A record
            # whose text ran past a page boundary reads as noise there, and
            # noise decodes into the CJK range -- the same fault that hid the
            # tapes, now hiding five more through the language it picks.
            spoken = briefing.language_of(
                [mended.get((record.offset, i), s)
                 for i, s in enumerate(record.strings)])
            if spoken != "en":
                continue
            seen = set()
            for index, offset in enumerate(record.str_offsets):
                if offset in seen:
                    continue
                seen.add(offset)
                raw = mended.get((record.offset, index))
                if raw is None:
                    raw = (record.strings[index]
                           if index < len(record.strings) else b"")
                try:
                    text = raw.decode("utf-8")
                except (UnicodeDecodeError, IndexError):
                    continue
                if not text.strip():
                    continue
                entries.append({
                    "key": f"bri/{record.offset:06X}/{index:02d}",
                    "record": record.offset,
                    "index": index,
                    "budget": record.body_size,
                    # The mended length, where there is one: the page-wise
                    # copy runs to the wrong NUL and is not the string's
                    # real extent.
                    "limit": len(raw),
                    "in_place": not record.rebuildable,
                    "straddles": (record.offset, index) in mended,
                    "source": text,
                    "target": keep.get(text, ""),
                })

        self.write("briefing", {"category": "briefing", "entries": entries})
        return len(entries)

    def stock_stagedat(self) -> Path | None:
        """STAGEDAT.PDT to build from: the ``.bak`` if one exists."""
        if self.game is None:
            return None
        path = self.game / SLOT_DIR / STAGEDAT_PDT
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            return backup
        return path if path.exists() else None

    def extract_stage(self, progress: ProgressFn = _noop) -> int:
        """Read the mission text out of STAGEDAT.PDT.

        The text is three containers down -- entity, inline archive, ``.olang``
        table -- and most of it repeats across entities, so entries are one per
        distinct string, the way the story is.  The first place a string was
        found becomes its key; the build writes it to all of them.
        """
        source = self.stock_stagedat()
        if source is None:
            raise ProjectError(
                "STAGEDAT.PDT was not found in the game folder:\n\n"
                f"{(self.game or Path()) / SLOT_DIR / STAGEDAT_PDT}")

        # Seeded from the whole project, not just from this tab.  STAGEDAT
        # keeps its own copy of lines that also live in SLOT.DAT and in the
        # briefings -- 4 701 of the 5 071 are already somewhere else -- and a
        # translation of one copy does nothing for the other, because they are
        # different bytes in different containers.  Carrying the answer across
        # is the difference between translating this tab and re-typing it.
        keep = dict(self.targets())
        if self.has("stage"):
            keep.update({e["source"]: e.get("target", "")
                         for e in self.read("stage")["entries"]
                         if e.get("target")})

        progress("Opening STAGEDAT.PDT...")
        container = stagedat.Stagedat(source)

        entries, seen = [], set()
        for index, members in container.archives(progress):
            for member, table in stagedat.tables(members):
                for pool, text in stagedat.english(table):
                    if text in seen:
                        continue
                    seen.add(text)
                    stem = member.name.rsplit(".", 1)[0]
                    entries.append({
                        "key": f"stage/{index:03d}/{stem}/{pool:04d}",
                        "entity": index,
                        "member": member.name,
                        "pool": pool,
                        "source": text,
                        "target": keep.get(text, ""),
                    })

        self.write("stage", {"category": "stage", "entries": entries})
        return len(entries)

    def extract_staff(self, progress: ProgressFn = _noop) -> int:
        """Read the roster's word lists out of STAGEDAT.

        A recruit's name is two rows of ``staff.ohd`` with a space between, so
        there is no "BLENNY SPARROW" anywhere to translate -- there is BLENNY,
        and there is SPARROW, and every pairing the game makes of them follows
        from those.  One entry per row.

        The field is 16 bytes with a terminator, which is a hard wall: the
        next record starts 24 bytes on and the count is in the header, so a
        name cannot borrow a byte from anywhere.
        """
        source = self.stock_stagedat()
        if source is None:
            raise ProjectError(
                "STAGEDAT.PDT was not found in the game folder:\n\n"
                f"{(self.game or Path()) / SLOT_DIR / STAGEDAT_PDT}")
        keep = ({e["source"]: e.get("target", "")
                 for e in self.read("staff")["entries"]}
                if self.has("staff") else {})

        progress(f"Opening {source.name}...")
        found = stagedat.Stagedat(source).staff_tables(progress)

        entries = []
        for table in staff.TABLES:
            for index, name in enumerate(found.get(table, [])):
                if not name.strip():
                    continue
                entries.append({
                    "key": f"staff/{table}/{index:04d}",
                    "table": table,
                    "index": index,
                    "budget": staff.BUDGET,
                    "source": name,
                    "target": keep.get(name, ""),
                })
        self.write("staff", {"category": "staff", "entries": entries})
        return len(entries)

    def save_folder(self) -> Path | None:
        """The game's ``ww`` save folder, if there is exactly one profile.

        Saves sit beside the data, not inside it: ``MGS_PW/mgspw`` is the game
        and ``MGS_PW/mgspw_savedata_win/<steam id>/ww`` holds the saves.
        """
        if self.game is None:
            return None
        root = self.game.parent / "mgspw_savedata_win"
        if not root.is_dir():
            return None
        folders = [p / "ww" for p in sorted(root.iterdir())
                   if (p / "ww").is_dir()]
        return folders[0] if len(folders) == 1 else None

    def rename_save(self, save_path: str | Path, write: bool = False,
                    note: ProgressFn = _noop) -> dict:
        """Put the translated codenames into a save's existing roster.

        The table in STAGEDAT names only *new* recruits -- a codename is
        copied into the save when the soldier is fultoned -- so a base built
        before the translation stays English until its own records are
        rewritten.  This is that pass.

        With ``write`` the new save goes into the folder it came from, under
        the checksum name the game demands, and the one it replaces is moved
        into ``superseded`` beside it: two files holding the same playthrough
        would leave the load menu ambiguous.
        """
        from pwtr import pwsave
        arabic = {e["source"]: e["target"]
                  for e in (self.read("staff")["entries"]
                            if self.has("staff") else [])
                  if e.get("target")}
        if not arabic:
            raise ProjectError("Nothing is translated in the Staff tab yet.")

        save = pwsave.Save.load(save_path)
        renamed, over, untranslated = 0, [], {}
        for slot, raw in save.roster():
            english = raw.decode("latin-1")
            target = arabic.get(english)
            if target is None:
                untranslated[english] = untranslated.get(english, 0) + 1
                continue
            written = self._render(target, FACE["staff"]).encode("utf-8")
            if len(written) > pwsave.BUDGET:
                over.append((english, target, len(written)))
                continue
            save.rename(slot, written)
            renamed += 1
        note(f"{renamed} soldier(s) renamed")

        report = {"renamed": renamed, "over": sorted(set(over)),
                  "left": sum(untranslated.values()),
                  "names": sorted(untranslated), "out": None, "moved": None}
        if not write or not renamed:
            return report

        source = Path(save_path)
        out = save.write(source.parent)
        report["out"] = str(out)
        if out != source:
            attic = source.parent / "superseded"
            attic.mkdir(parents=True, exist_ok=True)
            source.replace(attic / source.name)
            report["moved"] = str(attic / source.name)
        note(f"written -> {out}")
        return report

    def staff_translations(self) -> dict:
        """``{english name: what to write}``, shaped and compacted."""
        if not self.has("staff"):
            return {}
        return {e["source"]: self._render(e["target"], FACE["staff"])
                for e in self.read("staff")["entries"] if e.get("target")}

    # -- the speaker names, in the executable ------------------------------

    def stock_exe(self) -> Path | None:
        """The executable to build from: the ``.bak`` if one exists."""
        if self.game is None:
            return None
        path = self.game / exenames.EXE_NAME
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            return backup
        return path if path.exists() else None

    def _stock_exe_bytes(self) -> bytes:
        source = self.stock_exe()
        if source is None:
            raise ProjectError(
                "The game executable was not found in the game folder:\n\n"
                f"{(self.game or Path()) / exenames.EXE_NAME}")
        return source.read_bytes()

    def _speaker_table(self) -> "exenames.Table":
        """The stock name run, read once: the editor asks on every keystroke."""
        source = self.stock_exe()
        stamp = (source, source.stat().st_mtime) if source else None
        cached = getattr(self, "_speakers", None)
        if cached is None or cached[0] != stamp:
            try:
                table = exenames.read(self._stock_exe_bytes())
            except exenames.NotFound as error:
                raise ProjectError(
                    f"The speaker names could not be found: {error}")
            self._speakers = (stamp, table)
        return self._speakers[1]

    def extract_names(self, progress: ProgressFn = _noop) -> int:
        """Read the radio-bar speaker names out of the executable."""
        progress("Reading the executable...")
        table = self._speaker_table()
        keep = ({e["source"]: e.get("target", "")
                 for e in self.read("names")["entries"]}
                if self.has("names") else {})
        entries = [{
            "key": f"exe/{name.offset:06X}",
            "source": name.text,
            "target": keep.get(name.text, ""),
            "fixed": name.fixed,
        } for name in table.names if exenames.is_text(name)]
        self.write("names", {"category": "names", "entries": entries})
        return len(entries)

    def _speaker_renders(self, entries) -> dict:
        return {e["source"]: self._render(e["target"], FACE["names"])
                for e in entries if e.get("target")}

    def names_room(self, entries: list, entry: dict) -> tuple[int, int]:
        """``(bytes this name needs, bytes it has)`` given the other names.

        Every name but ``unknown`` shares one run, so what one name may spend
        depends on what the rest have taken -- hence the whole list.
        """
        table = self._speaker_table()
        name = next((n for n in table.names if n.text == entry["source"]),
                    None)
        if name is None:
            return 0, 0
        return exenames.room(table, self._speaker_renders(entries), name)

    def _overlong_names(self) -> dict:
        """The names the build would leave English -- by asking the build."""
        if not self.has("names"):
            return {}
        entries = self.read("names")["entries"]
        _out, _written, refused = exenames.patch(
            self._stock_exe_bytes(), self._speaker_renders(entries))
        key_of = {e["source"]: e["key"] for e in entries}
        return {key_of[text]: (need - room, None)
                for text, need, room in refused if text in key_of}

    def build_names(self, note: ProgressFn = _noop) -> dict:
        """Write the translated speaker names into a copy of the executable."""
        if not self.has("names"):
            raise ProjectError("The speaker names have not been extracted yet")
        entries = self.read("names")["entries"]
        rendered = self._speaker_renders(entries)
        if not rendered:
            note("Nothing translated in the speaker names.")
            return {"names": 0, "lines": {}}
        source = self.stock_exe()
        note(f"Patching {source.name}...")
        try:
            out, written, refused = exenames.patch(
                self._stock_exe_bytes(), rendered)
        except exenames.NotFound as error:
            raise ProjectError(
                f"The speaker names could not be found: {error}")
        destination = self.output / exenames.EXE_NAME
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(out)
        note(f"{len(written)} speaker name(s) written.")
        key_of = {e["source"]: e["key"] for e in entries}
        for text, need, room in refused:
            note(f"   {text!r} stays English -- {need - room} byte(s) over")
        return {"names": len(written),
                "lines": {key_of[text]: (need - room, None)
                          for text, need, room in refused if text in key_of}}

    def extract(self, category: str, progress: ProgressFn = _noop) -> int:
        if category == "menus":
            return self.extract_menus(progress)
        if category == "story":
            return self.extract_story(progress)
        if category == "briefing":
            return self.extract_briefing(progress)
        if category == "stage":
            return self.extract_stage(progress)
        if category == "staff":
            return self.extract_staff(progress)
        if category == "names":
            return self.extract_names(progress)
        if category == "movies":
            return self.extract_movies(progress)
        raise ProjectError(f"unknown category {category!r}")

    # -- the font plan -----------------------------------------------------

    @property
    def borrow(self) -> tuple[str, ...]:
        """Which Latin cell ranges the menu face is allowed to give up."""
        return tuple(self.manifest.get("font", {}).get("borrow", []))

    @borrow.setter
    def borrow(self, ranges) -> None:
        self.manifest.setdefault("font", {})["borrow"] = list(ranges)

    @property
    def mapping(self) -> dict:
        """The installed menu face's character -> byte map, if there is one."""
        raw = self.manifest.get("font", {}).get("mapping", {})
        return {ch: int(byte, 16) if isinstance(byte, str) else byte
                for ch, byte in raw.items()}

    @mapping.setter
    def mapping(self, mapping: dict) -> None:
        self.manifest.setdefault("font", {})["mapping"] = {
            ch: f"0x{byte:02X}" for ch, byte in mapping.items()}

    @property
    def reclaim(self) -> str:
        """Which case the compact plan may spend: ``"capitals"`` or ``"lower"``.

        Capitals are the historic default and the cheaper one while the text
        is mostly English.  They stop being cheap once anything drawn from
        outside this toolkit is upper case -- the staff codenames in the save
        are, so reclaiming A-Z makes the whole roster unreadable.  Lower case
        costs only the lines still untranslated.
        """
        return self.manifest.get("font", {}).get("reclaim", "capitals")

    @reclaim.setter
    def reclaim(self, which: str) -> None:
        if which not in ("capitals", "lower"):
            raise ProjectError(f"reclaim is 'capitals' or 'lower', not {which!r}")
        self.manifest.setdefault("font", {})["reclaim"] = which

    #: How a translation becomes glyphs.  ``arabic`` is shaped into joined
    #: presentation forms in visual order; ``plain`` is drawn as typed, left
    #: to right -- Cyrillic, Greek, accented Latin, anything with no shaping.
    SCRIPTS = ("arabic", "plain")

    @property
    def script(self) -> str:
        return self.manifest.get("font", {}).get("script", "arabic")

    @script.setter
    def script(self, which: str) -> None:
        if which not in self.SCRIPTS:
            raise ProjectError(f"script is one of {self.SCRIPTS}, not {which!r}")
        self.manifest.setdefault("font", {})["script"] = which

    @property
    def charset(self) -> set[str] | None:
        """The characters the modder chose for the face to carry.

        ``None`` means none were chosen: everything the translation draws
        above the engine's reach is planned, which is how an Arabic project
        has always worked.  A chosen set is the whole of what gets planned
        and painted -- a character outside it is reported, not guessed at.
        """
        codes = self.manifest.get("font", {}).get("charset")
        return {chr(c) for c in codes} if codes else None

    @charset.setter
    def charset(self, characters) -> None:
        font = self.manifest.setdefault("font", {})
        codes = sorted({ord(c) if isinstance(c, str) else int(c)
                        for c in (characters or ())})
        if codes:
            font["charset"] = codes
        else:
            font.pop("charset", None)

    def stock_codes(self) -> set[int]:
        """Code points the stock subtitle face draws; read once per project."""
        if getattr(self, "_stock_codes", None) is None:
            try:
                self._stock_codes = (fonts.stock_codes(self.game)
                                     if self.game else set())
            except Exception:
                self._stock_codes = set()
        return self._stock_codes

    def translation_codes(self, categories=None) -> collections.Counter:
        """Every code point the translations draw, counted, as they are drawn:
        shaped for Arabic, as typed otherwise, engine markup left out."""
        names = list(categories) if categories else list(CATEGORIES)
        drawn = collections.Counter()
        for name in names:
            if not self.has(name):
                continue
            for entry in self.read(name)["entries"]:
                if entry.get("target"):
                    visible = arabic.PROTECT.sub("", self.shaped(entry["target"]))
                    drawn.update(ord(c) for c in visible)
        return drawn

    def uncovered(self, categories=None) -> list[str]:
        """Characters the translations use that no installed glyph will draw:
        neither in the chosen set nor drawn by the stock face within reach."""
        chosen = self.charset
        if chosen is None:
            return []
        stock = self.stock_codes()
        out = []
        for code in sorted(self.translation_codes(categories)):
            if code in (0x09, 0x0A, 0x0D, 0x20) or chr(code) in chosen:
                continue
            if code <= compact.CEILING and code in stock:
                continue
            if 0xE000 <= code <= 0xF8FF:
                continue            # mark placements; planned with the marks
            out.append(chr(code))
        return out

    def compact_plan(self, categories=None) -> dict:
        """Which code point each drawn character is stored as, and the cost.

        One plan, used by the font installer and by the text build alike --
        they have to agree exactly, because the face draws whatever the text
        says and a mapping computed twice is a mapping that can differ once.

        Punctuation is always reclaimed, and one case range on top of it --
        :attr:`reclaim` says which, and that choice is about what is drawn
        from data this toolkit never renders.  A digit is never taken however
        rare it looks in the English left untranslated, because translations
        keep English names and numbers in them and the same face draws those
        too.  The punctuation the Arabic itself uses is relocated along with
        the presentation forms, so nothing drawn anywhere is left holding a
        literal full stop and every slot is unambiguous.
        """
        names = list(categories) if categories else list(CATEGORIES)
        used = compact.census([])
        surviving = compact.census([])
        drawn = compact.census([])
        shaped, kept = [], 0
        for name in CATEGORIES:
            if not self.has(name):
                continue
            # The slots come from the categories asked for -- a form only
            # earns a cheap one by being common in the text being built.  What
            # they cost does not: a reclaimed full stop is spoilt in every
            # untranslated line in the project, not only in the ones this
            # plan is for, so the damage is always counted over all of them.
            planning = name in names
            for entry in self.read(name)["entries"]:
                if planning:
                    used.update(compact.census([entry["source"]]))
                if entry.get("target"):
                    text = self.shaped(entry["target"])
                    visible = arabic.PROTECT.sub("", text)
                    if planning:
                        drawn.update(compact.census([visible]))
                        shaped.append(visible)
                else:
                    kept += 1
                    surviving.update(compact.census([entry["source"]]))

        pool = compact.free_slots(drawn,
                                  capitals=self.reclaim == "capitals",
                                  lowercase=self.reclaim == "lower")
        relocate = {c for c in pool if drawn[c]}
        # Every letter in the pool gets a two-byte home, used or not, so a
        # name typed tomorrow does not come out in Arabic.
        must = [chr(c) for c in pool if chr(c).isalpha()]
        relocate |= {c for c in pool if chr(c).isalpha()}
        chosen = self.charset
        include, only = frozenset(), None
        if chosen is not None:
            # A chosen character needs a home when the engine cannot reach
            # it where Unicode keeps it, or when the stock face has nothing
            # there to draw.  Its own code point is then not a free two-byte
            # slot for something else.
            stock = self.stock_codes()
            include = frozenset(ch for ch in chosen
                                if ord(ch) > compact.CEILING
                                or ord(ch) not in stock)
            only = frozenset(chosen)
            must += sorted(include - set(must), key=ord)
            used.update({ord(ch): 1 for ch in chosen})
        mapping = compact.plan(shaped, used, pool=pool, relocate=relocate,
                               must=must, include=include, only=only)
        spoilt = sum(surviving[c] for c in pool
                     if c not in compact.SAFE_SINGLE)
        whole = sum(surviving.values()) or 1
        return {"mapping": mapping, "pool": pool, "relocate": relocate,
                "kept": kept, "spoilt": spoilt, "whole": whole,
                "cost": 100 * spoilt / whole,
                "singles": sum(1 for v in mapping.values() if ord(v) < 0x80)}

    @property
    def harakat(self) -> bool:
        """Keep the marks, or strip them?

        They can be kept now.  The shaped text already puts a mark before the
        letter it belongs to, and a glyph painted with a zero advance leaves
        the pen where it is, so the two land on the same x and stack -- which
        is what the game confirmed.  Before that was known the only honest
        option was to drop them.
        """
        return bool(self.manifest.get("harakat", True))

    @property
    def glyph_widths(self) -> dict:
        """How wide each glyph is drawn, written by the font installer.

        Centring a mark needs the width of the letter under it, and only the
        installer knows that -- it depends on the face and the size chosen.
        Empty until a face has been installed, and then the marks can be
        placed.
        """
        return self.manifest.get("font", {}).get("widths") or {}

    def shaped(self, text: str) -> str:
        """A translation as glyphs, marks placed or dropped.

        One place decides it, because the plan and the build must agree: a
        mark the plan never saw has no code point, and a mark the build never
        wrote leaves a gap in the line.
        """
        if self.script == "plain":
            # Drawn as typed.  Only the invisible bidi controls go: the engine
            # draws a glyph for every code point, so one would be a box.
            return "".join(c for c in text if ord(c) not in arabic.CONTROLS)
        shaped = arabic.shape(text)
        widths = self.glyph_widths
        if not self.harakat or not widths:
            return arabic.strip_harakat(shaped)
        return arabic.place_marks(shaped, widths)

    @property
    def compact(self) -> dict[str, str]:
        """``{character drawn: code point it is stored as}``.

        Written by the font installer, because the face and the text have to
        agree: the installer paints each form at the code point named here, so
        the text must store that code point and nothing else.  Empty until a
        face has been installed, and then everything the subtitle face draws
        goes through it.
        """
        stored = self.manifest.get("font", {}).get("compact") or {}
        return {form: chr(code) for form, code in stored.items()}

    def wanted_glyphs(self, category: str | None = None) -> list[str]:
        """Every presentation form the translation actually uses.

        Planned from the text rather than from the alphabet, and only from the
        lines the atlas actually draws.  The face has 63 free cells against
        Arabic's 141 forms, but only 80 English menu references are Style
        0x0001; measuring the whole tab against that budget would report a
        crisis that does not exist.
        """
        categories = [category] if category else list(CATEGORIES)
        shaped = []
        for name in categories:
            if not self.has(name):
                continue
            for entry in self.read(name)["entries"]:
                if entry.get("target") and face_of(entry) == "ui":
                    shaped.append(self.shaped(entry["target"]))
        if self.script == "plain":
            chosen = self.charset
            return sorted({c for text in shaped for c in text
                           if ord(c) > 0x7F
                           and (chosen is None or c in chosen)})
        return arabic.needed(shaped)

    # -- building ----------------------------------------------------------

    def _render(self, text: str, face: str) -> str:
        """A stored translation, as the bytes that face wants."""
        if face == "none":
            # Burned into a picture by a subtitle renderer, which shapes and
            # orders the text itself.  Nothing of ours applies: not the
            # presentation forms, and certainly not the compact code points,
            # which mean something only to a font this project installed.
            return text
        shaped = self.shaped(text)
        if face == "ui":
            mapping = self.mapping
            if mapping:
                shaped = arabic.to_font_bytes(shaped, mapping)
            return shaped
        # The subtitle face no longer draws a presentation form at its own
        # code point -- the engine's character lookup does not reach that far,
        # and three bytes a letter would not fit the containers anyway.  The
        # installer moved every drawn character to a code point of its own
        # choosing, so the text has to be written in those or the face has
        # nothing to draw with.
        plan = self.compact
        return compact.encode(shaped, plan) if plan else shaped

    def render(self, text: str, face: str = "subtitle") -> str:
        """Exactly what a build would write, for anything that has to count it."""
        return self._render(text, face)

    #: What each tab is called in the workbench, for messages that send
    #: somebody there.
    TAB_NAMES = {"menus": "Menus", "story": "Story", "briefing": "Briefing",
                 "stage": "Missions", "staff": "Staff", "names": "Names",
                 "movies": "Movies"}

    def still_english(self, category: str, report, note: ProgressFn = _noop,
                      shown: int = 12) -> int:
        """Say, after a build, which lines the game will still show in English.

        Two reasons, told apart because they are fixed differently:

        * translated, but refused -- too long for the room it must fit in,
          so the build left the English there.  Listed by key, with how far
          over each is, and ``Too long only`` finds them all in the tab;
        * never translated.  Counted only: ``Untranslated only`` finds them.

        A build that says nothing about either reads as "all done", which is
        exactly what nobody should conclude from a report that left lines in
        English.  Returns how many lines were refused.
        """
        tab = self.TAB_NAMES.get(category, category)
        entries = self.read(category)["entries"] if self.has(category) else []
        by_key = {e["key"]: e for e in entries}
        lines = report.get("lines") if isinstance(report, dict) else None
        lines = lines or {}
        refused = [(key, over) for key, over in lines.items()
                   if (by_key.get(key) or {}).get("target")]
        blank = sum(1 for e in entries
                    if e.get("source", "").strip() and not e.get("target"))

        if not refused and not blank:
            note(f"Every {tab} line is translated and fits.")
            return 0

        note("")
        note(f"STILL ENGLISH IN GAME -- {tab}")
        if refused:
            refused.sort(key=lambda kv: -(kv[1][0] if isinstance(kv[1], tuple)
                                          else kv[1]))
            note(f"  {len(refused)} translated line(s) did not fit, so the "
                 f"game shows their English instead:")
            for key, over in refused[:shown]:
                spare, block = over if isinstance(over, tuple) else (over, None)
                english = " ".join(by_key[key]["source"].split())
                where = (f", in block {block:05X}"
                         if isinstance(block, int) else "")
                note(f"    {key}  {spare} byte(s) over{where}  "
                     f"\"{english[:50]}{'...' if len(english) > 50 else ''}\"")
            if len(refused) > shown:
                note(f"    ...and {len(refused) - shown} more")
            note(f"  To fix: in the {tab} tab tick \"Too long only\" (or use "
                 f"Next too long, Ctrl+G), shorten those translations, and "
                 f"build again.")
            if category == "story" and any(
                    isinstance(o, tuple) and isinstance(o[1], int)
                    for _k, o in refused):
                note("  A line \"in block\" shares that block's room with every "
                     "line in it: shortening any translated line in the block "
                     "helps. Type the block number in the filter to see them.")
        if blank:
            note(f"  {blank} line(s) have no translation yet and stay English: "
                 f"tick \"Untranslated only\" in the {tab} tab to find them.")
        return len(refused)

    def build(self, category: str, note: ProgressFn = _noop) -> dict:
        if category == "menus":
            return self.build_menus(note)
        if category == "story":
            return self.build_story(note)
        if category == "briefing":
            return self.build_briefing(note)
        if category in ("stage", "staff"):
            return self.build_stage(note)
        if category == "names":
            return self.build_names(note)
        if category == "movies":
            return self.build_movies(note)
        raise ProjectError(f"unknown category {category!r}")

    def build_stage(self, note: ProgressFn = _noop,
                    progress: ProgressFn = _noop) -> dict:
        """Write the translated mission text into a copy of STAGEDAT.PDT.

        Unlike the briefings, this one may grow: ``pdt_pack`` gives an entity
        that outran its slack a new home at the end of the container and the
        entity table records where it went, so nothing downstream shifts.  The
        cost is a 487 MB write.

        Three different things ship in this one pass -- the mission text, the
        artwork, and the staff name tables -- because all three are written by
        replacing whole entities.  Any two of them built separately would each
        start from the game's own file, and whichever ran last would be the
        only one that survived.
        """
        if not (self.has("stage") or self.has("staff")):
            raise ProjectError("The mission text has not been extracted yet")
        source = self.stock_stagedat()
        if source is None:
            raise ProjectError("STAGEDAT.PDT was not found in the game folder")

        translations = {}
        if self.has("stage"):
            for entry in self.read("stage")["entries"]:
                if entry.get("target"):
                    translations[entry["source"]] = self._render(
                        entry["target"], FACE["stage"])
        names = self.staff_translations()

        # The artwork lives in this container too, and it is rebuilt entity
        # by entity just as the text is -- so it has to go in on the same
        # pass.  Building the two separately means the second one starts
        # from the game's own file and quietly drops the first.
        #
        # `prebuild` is where edited pictures belong: it holds only what is
        # meant to go in, so there is nothing to guess at.  Editing among
        # the extracted originals works, but then every one of the 9 743
        # textures is a candidate, and separating them by timestamp put a
        # thousand files through a decrypt-and-decode each -- minutes of it,
        # to find two.  The unpack tree stays the reference copy.
        from pwtr import stagetex
        textures = self.root / "prebuild" / "STAGEDAT"
        since = None
        if not textures.is_dir():
            textures = self.root / "texture" / "STAGEDAT"
            if textures.is_dir():
                since = stagetex.recent(stagetex.candidates(textures))
            else:
                textures = None

        if not (translations or names or textures):
            note("Nothing translated in the mission text.")
            return {"entities": 0, "strings": 0, "textures": 0, "names": 0}

        note(f"Opening {source.name}...")
        container = stagedat.Stagedat(source)
        destination = self.output / SLOT_DIR / STAGEDAT_PDT

        result = container.build(translations, destination, note, progress,
                                 textures=textures, since=since,
                                 staff_names=names)
        note(f"{result['strings']} string(s) into "
             f"{result['entities']} entity/entities"
             + (f", plus {result['textures']} texture(s)"
                if result.get("textures") else "")
             + (f", plus {result['names']} staff table(s)"
                if result.get("names") else "") + ".")
        return result

    def briefing_budget(self, entries=None) -> dict:
        """``record -> (bytes the translation needs, bytes it has)``.

        The number that decides whether a briefing can ship at all.  Counted
        the way the file counts: every distinct string plus its terminator,
        with an untranslated line still costing its English.
        """
        if entries is None:
            entries = (self.read("briefing")["entries"]
                       if self.has("briefing") else [])
        by_record = {}
        for entry in entries:
            by_record.setdefault(entry["record"], []).append(entry)

        out = {}
        for record, group in by_record.items():
            needed = 0
            for entry in group:
                if entry.get("target"):
                    text = self._render(entry["target"], FACE["briefing"])
                else:
                    text = entry["source"]
                needed += len(text.encode("utf-8")) + 1
            out[record] = (needed, group[0]["budget"])
        return out

    #: Where the block index is kept, so the walk is paid for once.
    BLOCK_INDEX = "story_blocks.json"

    def block_index(self) -> dict:
        """``{source line: [block, ...]}``, as the last story build left it.

        A story entry is one per *distinct string*, and its key names only the
        first block the string turned up in -- so the key reads like a
        location and is not one.  A line sits in five blocks on average, and
        of the 177 in block 195FE not one is filed under its number, so
        "what is in this block" had no answer at all.

        Written by :meth:`build_story`, which reads every block and every
        reference anyway.  There is no separate walk to keep in step with the
        build, and no snapshot to go stale behind it.
        """
        cache = self.root / self.BLOCK_INDEX
        if not cache.is_file():
            return {}
        try:
            with open(cache, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def has_block_index(self) -> bool:
        return (self.root / self.BLOCK_INDEX).is_file()

    def overlong(self, category: str) -> dict:
        """``{entry key: (how far over, which block)}`` -- what will not fit.

        The unit is the container's own.  A briefing line is refused over a
        **byte**, because a record may not grow by one.  A story block is
        refused over a **page**: SLOT.DAT gives each block a footprint in 4 KB
        pages and compresses what goes in it, so the budget is loose and the
        overflow is a property of the block, not of any one line -- every line
        in an overflowing block is reported, because shortening any of them is
        what brings the block back under.

        Menus and Missions have no such limit: their pools grow to whatever
        they are given.

        It is the build that decides, so it is the build that is asked -- the
        same pass, the same order -- because a second opinion on "does this
        fit" is one waiting to disagree with the first.
        """
        if category == "briefing":
            return self._overlong_briefing()
        if category == "story":
            return self._overlong_story()
        if category == "staff":
            return self._overlong_staff()
        if category == "names":
            return self._overlong_names()
        if category == "movies":
            return self._overlong_movies()
        return {}

    def _overlong_movies(self) -> dict:
        """Movie fields whose line count differs from the English.

        Not a byte limit.  A burned-in roll is timed to how many lines it
        carries, so the measure that matters is lines: tested in game, font
        size, equal line counts per field and a single blank line between
        fields are what keep it in sync, and the first and last are copied
        from the subtitle rather than typed.  The figure is signed -- more
        lines than the English is positive -- so the jump can say which way.
        """
        from pwtr import moviesource

        out = {}
        for entry in (self.read("movies")["entries"]
                      if self.has("movies") else []):
            # Only a roll is timed to its line count; a placed card is not.
            if not entry.get("target") or not moviesource.is_roll(entry["key"]):
                continue
            said = len([l for l in entry["target"].splitlines() if l.strip()])
            want = len([l for l in entry["source"].splitlines() if l.strip()])
            if said != want:
                out[entry["key"]] = (said - want, None)
        return out

    def _overlong_staff(self) -> dict:
        """Which staff names will not fit their 15 bytes.

        Cheap, unlike the other two: the budget is a constant and the cost is
        the rendered string's own length, so nothing has to be built to know.
        """
        out = {}
        for entry in (self.read("staff")["entries"]
                      if self.has("staff") else []):
            if not entry.get("target"):
                continue
            need = len(self._render(entry["target"],
                                    FACE["staff"]).encode("utf-8"))
            if need > staff.BUDGET:
                out[entry["key"]] = (need - staff.BUDGET, None)
        return out

    def _overlong_briefing(self) -> dict:
        if not self.has("briefing"):
            return {}
        source = self.stock_briefing()
        if source is None:
            return {}
        edits = {}
        entries = self.read("briefing")["entries"]
        for entry in entries:
            if entry.get("target"):
                edits.setdefault(entry["record"], {})[entry["index"]] =                     self._render(entry["target"],
                                 FACE["briefing"]).encode("utf-8")
        straddling = {(e["record"], e["index"]): e["source"].encode("utf-8")
                      for e in entries if e.get("straddles")}
        _built, report = briefing.build(briefing.load(source), edits, None,
                                        partial=True, straddling=straddling)
        # ``(how far over, where)`` -- a briefing line is over on its own
        # account, so there is no other place to name.
        # Only lines refused for *length*.  A record that cannot be written at
        # all is refused for a different reason, and reporting it here would
        # send "Next too long" to a line it would then say is nought bytes
        # over -- the build log names those instead.
        return {f"bri/{o['offset']:06X}/{o['index']:02d}":
                (o["needed"] - o["budget"], None)
                for o in report["overflow"]
                if "index" in o and not o.get("readonly")}

    def _overlong_story(self) -> dict:
        """Which story lines sit in a block that will not fit its footprint.

        This reads all 2 137 blocks and recompresses the ones that changed,
        which is the only honest way to know -- and it takes minutes, so the
        caller should say so before it starts.
        """
        if not self.has("story"):
            return {}
        pair = self.stock_slot()
        if pair is None:
            return {}
        dat, key = pair
        translations = {e["source"]: self._render(e["target"], FACE["story"])
                        for e in self.read("story")["entries"]
                        if e.get("target")}
        if not translations:
            return {}

        _header, (high, low), records = slotdat.load_key(str(key))
        stream = slotdat.WordStream(high, low)
        over = {}
        #: Raw lines longer than the span they must sit in.  A block problem
        #: and a line problem are different things and are kept apart.
        raw_over = {}
        with open(dat, "rb") as source:
            for record in records:
                try:
                    stock_plain = slotdat.read_block(source, record, stream)
                    elements, head, _t = slotitem.parse(stock_plain)
                except Exception:
                    continue
                # The engine decompresses a block into a buffer sized from
                # the stock block rounded up to a whole page, so the
                # *decompressed* bytes carry a ceiling of their own, quite
                # apart from whether the compressed payload fits the
                # footprint.  One byte over it corrupts whatever follows:
                # a block 32 bytes too big loaded and played fine until the
                # menu that reads it was opened, and then hung.  Compressed
                # size says nothing about this -- the block that hung had
                # ~600 bytes of footprint to spare.
                budget = -(-len(stock_plain) // slotdat.PAGE) * slotdat.PAGE
                changed = False
                sources = set()
                for element in elements:
                    if element.ext != "olang":
                        continue
                    try:
                        table = olang.OlangFile.parse(element.data)
                    except Exception:
                        continue
                    hits = 0
                    for reference in table.refs:
                        # The style says which face draws the line, and
                        # the two do not read the same bytes.  Style 0x0001 is
                        # the caps atlas: a painted grid of 160 cells with no
                        # character map, indexed straight off the byte.  Hand
                        # it a subtitle encoding -- Latin Extended code points
                        # meant for a Unicode charmap -- and it reads past the
                        # end of the grid, inside the menu drawing it.
                        # build_menus has always chosen a face here.  This did
                        # not, and that is what hung the Mission Selector.
                        if reference.style == CAPS_STYLE:
                            continue
                        # English only, exactly as build_story does.  Without
                        # this the check writes Arabic into all six language
                        # slots of a shared pool and every block looks
                        # hundreds of bytes over -- 1 272 lines reported
                        # needing a trim against a build that had none.
                        if olang.LANG_NAME.get(reference.lang) != "en":
                            continue
                        source_text = table.pool[reference.text]
                        target = translations.get(source_text)
                        if target is None:
                            continue
                        sources.add(source_text)
                        table.set_ref(reference, target)
                        hits += 1
                    if hits:
                        table.compact_pool()
                        element.data = (table.build()
                                        + element.data[table.length:])
                        changed = True

                # The same raw pass the build makes, for the same reason the
                # rest of this function exists: a checker that walks the data
                # differently from the writer ends up disagreeing with it.
                for element in elements:
                    fresh, hits, said = _raw_rewrite(element,
                                                     translations)
                    # A raw line too long for its own span is refused by the
                    # build and stays English.  Reporting it here is the only
                    # way "Too long only" can show it: it is not a block that
                    # overflowed, so nothing else in this walk would notice.
                    for text, needed, room in said:
                        if needed - room > raw_over.get(text, 0):
                            raw_over[text] = needed - room
                    if hits:
                        element.data = fresh
                        changed = True
                if not changed:
                    continue
                plain = slotitem.build(elements, head)
                payload = slotdat.build_block(plain)
                if (len(plain) > budget
                        or slotdat.pages_for(payload) > record.footprint):
                    payload = slotdat.build_block_smallest(
                        plain, record.footprint * slotdat.PAGE)
                # Bytes, not pages.  A page is what the container allocates
                # in, but "one page over" is a rounded-up figure that hides
                # whether the block missed by eighty bytes or by four
                # thousand -- and that is the difference between cutting a
                # line and cutting a scene.
                short = max(len(plain) - budget,
                            len(payload) - record.footprint * slotdat.PAGE)
                if short > 0 and _stub_others(elements, translations)[0]:
                    # The build empties the unread languages rather than give
                    # up, so a check that does not would call a line too long
                    # and then watch it be placed.
                    plain = slotitem.build(elements, head)
                    payload = slotdat.build_block_smallest(
                        plain, record.footprint * slotdat.PAGE)
                    short = max(len(plain) - budget,
                                len(payload) - record.footprint * slotdat.PAGE)
                if short > 0:
                    # Keyed by the text it holds, not by its page number:
                    # story entries are one per *distinct string*, so an
                    # entry's key names only the first block that string
                    # appeared in.  The block that is over travels with the
                    # figure, because the key cannot tell you -- a line
                    # catalogued in a block that fits can still be weighing
                    # down a different one.
                    for text in sources:
                        if short > over.get(text, (0, 0))[0]:
                            over[text] = (short, record.start)

        if not over and not raw_over:
            return {}
        return {entry["key"]: (over[entry["source"]]
                               if entry["source"] in over
                               else (raw_over[entry["source"]], None))
                for entry in self.read("story")["entries"]
                if entry.get("target")
                and (entry["source"] in over or entry["source"] in raw_over)}

    def extract_movies(self, progress: ProgressFn = _noop) -> int:
        """The English burned into the three pre-rendered scenes.

        Not read from anywhere: those words are pixels, so the source text is
        a transcription this toolkit carries.  Any translation already sitting
        in the project's ``.ass`` files is picked up, so a team that subtitled
        by hand does not lose it by opening the tab.
        """
        from pwtr import movietext, moviesource

        keep = {e["source"]: e.get("target", "")
                for e in self.read("movies")["entries"]} if self.has("movies") \
            else {}

        entries = []
        for stem, (what, _events) in moviesource.SCENES.items():
            # Whatever the project has subtitled already, block for block.
            existing = []
            for folder in ("Mov", "hqMov"):
                path = self.root / "hardsub" / folder / f"{stem}.ass"
                if path.is_file():
                    try:
                        existing = movietext.parse(path)
                    except Exception:
                        existing = []
                    break
            for event, block, english in moviesource.blocks(stem):
                was = ""
                if (event < len(existing)
                        and block < len(existing[event])):
                    was = existing[event][block]
                entries.append({
                    "key": f"mov/{stem}/{event}/{block:02d}",
                    "scene": what,
                    "movie": stem,
                    "source": english,
                    "target": keep.get(english) or was,
                })
        progress(f"{len(entries)} line(s) in {len(moviesource.SCENES)} scenes")
        self.write("movies", {"category": "movies", "entries": entries})
        return len(entries)

    @property
    def movie_style(self) -> dict:
        """The font and size the movie subtitles are drawn with.

        ``font`` is a family name, ``font_file`` a TTF/OTF to use instead of an
        installed font, and ``scale`` a percentage applied to every size in
        the subtitle -- empty and 100 leave the template's own styling alone.
        """
        style = {"font": "", "font_file": "", "scale": 100}
        style.update(self.manifest.get("movies", {}))
        return style

    @movie_style.setter
    def movie_style(self, style: dict) -> None:
        self.manifest["movies"] = {"font": style.get("font", ""),
                                   "font_file": style.get("font_file", ""),
                                   "scale": int(style.get("scale", 100))}

    def movie_font(self) -> tuple[str | None, Path | None]:
        """``(family to name in the subtitle, folder libass should search)``.

        A font file is named by the family inside it, which is not its file
        name -- libass matches on the family -- so the file is opened to ask.
        """
        style = self.movie_style
        path = Path(style["font_file"]) if style["font_file"] else None
        if path and path.is_file():
            try:
                from PIL import ImageFont
                family = ImageFont.truetype(str(path), 12).getname()[0]
            except Exception:
                family = path.stem
            return family, path.parent
        return (style["font"] or None), None

    def write_movie_subs(self, note: ProgressFn = _noop,
                         folders=("Mov", "hqMov")) -> dict:
        """Turn the translated blocks into ``.ass`` files ready to burn.

        The project's own subtitles are templates and are never written to.
        Each is read for its layout -- styles, override tags, the blank lines
        that place a card -- and a copy with the tab's words and the chosen
        font and size goes to ``hardsub-work/subs``.  Writing back into the
        template would compound a size change on every build, and quietly
        overwrite anything tuned by hand in a subtitle editor.
        """
        from pwtr import movietext, moviesource

        if not self.has("movies"):
            raise ProjectError("The movie text has not been read yet")
        family, _fonts = self.movie_font()
        scale = self.movie_style["scale"] / 100.0
        entries = self.read("movies")["entries"]
        said = {}
        for entry in entries:
            _m, stem, event, block = entry["key"].split("/")
            said.setdefault(stem, {})[(int(event), int(block))] = (
                entry.get("target") or entry["source"])

        written, missing, subs = [], [], {}
        out_root = self.root / "hardsub-work" / "subs"
        for stem in moviesource.SCENES:
            shape = [len(e) for e in moviesource.SCENES[stem][1]]
            events = [[said[stem][(e, b)] for b in range(count)]
                      for e, count in enumerate(shape)]
            for folder in folders:
                path = self.root / "hardsub" / folder / f"{stem}.ass"
                if not path.is_file():
                    missing.append(f"{folder}/{stem}.ass")
                    continue
                fresh = movietext.restyle(movietext.compose(path, events),
                                          family, scale)
                target = out_root / folder / f"{stem}.ass"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(fresh, encoding="utf-8-sig")
                subs[(folder, stem)] = target
                written.append(f"{folder}/{stem}.ass")
                note(f"wrote {folder}/{stem}.ass")
        return {"written": written, "missing": missing, "subs": subs}

    def build_movies(self, note: ProgressFn = _noop, preview: bool = False,
                     folders=("Mov", "hqMov"),
                     tick: ProgressFn = _noop) -> dict:
        """Burn the subtitles into the three movies that carry their text.

        With the Movies tab filled in, its words and the chosen font and size
        are what gets burned; without it, the project's ``.ass`` files are
        burned as they stand.  Line counts that differ from the English are
        warned about in the tab and never stop a build.

        ``preview`` writes plain ``.mp4`` files with their soundtrack to
        ``<project>/preview`` instead -- deliberately outside ``output``,
        which is installed into the game wholesale.
        """
        from pwtr import hardsub

        if self.game is None:
            raise ProjectError("This project has no game folder")
        if not hardsub.subtitles(self.root):
            raise ProjectError(
                "No subtitles to burn. Put them in "
                f"{self.root / 'hardsub'}/Mov and /hqMov, named after the "
                f"movie they belong to -- 004bc514.ass and so on.")
        subs, fonts = None, None
        if self.has("movies"):
            subs = self.write_movie_subs(note, folders)["subs"]
            _family, fonts = self.movie_font()
        out_root = self.root / "preview" if preview else self.output
        try:
            return hardsub.build(self.root, self.game, out_root, note,
                                 subs=subs, fonts_dir=fonts, preview=preview,
                                 folders=folders, tick=tick)
        except hardsub.HardsubError as problem:
            raise ProjectError(str(problem)) from problem

    def build_briefing(self, note: ProgressFn = _noop) -> dict:
        """Write the translated briefings into a copy of BRIEFING.DAT.

        Every record keeps its exact byte length: the engine seeks each
        briefing to a position worked out at runtime, so a record that grew
        would shift the ones after it and those would stop playing.  A record
        line whose translation does not fit is **left in English and
        reported**, never truncated -- and the fill is per line, so a record
        that cannot take all of its Arabic still takes what it can.

        How much fits turns entirely on whether a font has been installed.
        Untouched, an Arabic presentation form costs three bytes in UTF-8
        where English costs one, the records are full to the byte -- 380 of
        the 383 exactly -- and almost nothing goes in.  Once the installer has
        moved the drawn characters down onto one and two byte code points,
        nearly all of it does: 4 921 lines of 4 975.
        """
        if not self.has("briefing"):
            raise ProjectError("The briefing text has not been extracted yet")
        source = self.stock_briefing()
        if source is None:
            raise ProjectError("BRIEFING.DAT was not found in the game folder")

        entries = [e for e in self.read("briefing")["entries"] if e.get("target")]
        if not entries:
            note("Nothing translated in the briefings.")
            return {"records": 0, "strings": 0, "overflow": []}

        if not self.compact:
            note("No font is installed, so every character has to be stored "
                 "as a presentation form at three bytes each against "
                 "English's one. Almost nothing will fit, and almost every "
                 "record below will say so. Install a subtitle face first: "
                 "it chooses the code points, and this build writes them.")

        edits = {}
        # Every recovered string, translated or not.  Its record is pinned to
        # the per-line write by being here at all: a repack lays the region
        # out from the strings it can read, and one it cannot read is one it
        # writes over -- which would destroy the very lines just recovered.
        straddling = {(e["record"], e["index"]): e["source"].encode("utf-8")
                      for e in self.read("briefing")["entries"]
                      if e.get("straddles")}
        for entry in entries:
            rendered = self._render(entry["target"], FACE["briefing"])
            edits.setdefault(entry["record"], {})[entry["index"]] =                 rendered.encode("utf-8")

        note(f"Decrypting {source.name}...")
        data = briefing.load(source)
        built, report = briefing.build(data, edits, note, partial=True,
                                       straddling=straddling,
                                       raw=source.read_bytes(),
                                       name=BRIEFING_DAT)
        if report["rekey"]:
            built = briefing.realign(built, report["rekey"], BRIEFING_DAT)
            note(f"{len(report['rekey'])} line(s) re-keyed to the cipher run "
                 f"they belong to")

        destination = self.output / SLOT_DIR / BRIEFING_DAT
        briefing.save(built, destination, BRIEFING_DAT)

        note(f"{report['records']} record(s) written, "
             f"{report['skipped']} left in English"
             + (f", {report['lines']} line(s) too long to fit."
                if report.get("lines") else "."))
        if report.get("lines"):
            note("Use Next too long (Ctrl+G) in the Briefing tab to walk "
                 "them; each one says how many bytes it is over.")
        return {"records": report["records"], "strings": len(entries),
                "overflow": report["overflow"],
                # Length refusals only -- see _overlong_briefing.
                "lines": {f"bri/{o['offset']:06X}/{o['index']:02d}":
                          (o["needed"] - o["budget"], None)
                          for o in report["overflow"]
                          if "index" in o and not o.get("readonly")}}

    def build_menus(self, note: ProgressFn = _noop) -> dict:
        """Write the translated ``.olang`` tables into ``output/``.

        Each English reference is retargeted with ``set_ref`` rather than by
        assigning into the pool: a pool entry is shared by every reference that
        happened to carry the same string, so writing it in place would rewrite
        the French and German columns too.
        """
        if not self.has("menus"):
            raise ProjectError("There is no menu text in this project yet")

        translations = {e["source"]: e["target"]
                        for e in self.read("menus")["entries"] if e.get("target")}
        if not translations:
            note("Nothing translated in the menus.")
            return {"files": 0, "strings": 0}

        files = applied = 0
        for tag, path in self.original_tables():
            table = olang.read(str(path))
            hits = 0
            for reference in table.refs:
                language = olang.LANG_NAME.get(reference.lang)
                if language != SOURCE_LANG:
                    continue
                target = translations.get(table.pool[reference.text])
                if not target:
                    continue
                # Per reference, not per file: the same string can be drawn by
                # the caps atlas in one menu and the XPR face in another, and
                # only the first needs its bytes smuggled through cells.
                face = "ui" if reference.style == CAPS_STYLE else "subtitle"
                table.set_ref(reference, self._render(target, face))
                hits += 1
            if not hits:
                continue
            table.compact_pool()          # or the body carries orphaned strings
            destination = self.output / TEXT_DIRS[tag] / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            table.write(str(destination), True)
            files += 1
            applied += hits
            note(f"{path.name}: {hits} string(s)")

        note(f"{applied} string(s) into {files} table(s).")
        return {"files": files, "strings": applied}

    @staticmethod
    def _telling(sources) -> str:
        """The line in a block most worth quoting back.

        The longest one: a block full of menu labels sorts "(None)" to the
        front, and an example nobody recognises is no better than the block
        number it was meant to replace.
        """
        return max(sources, key=lambda t: len(t.strip()), default="")

    def build_story(self, note: ProgressFn = _noop,
                    progress: ProgressFn = _noop) -> dict:
        """Splice the translated story into a copy of SLOT.DAT.

        Only blocks whose text actually changed are rewritten, and the KEY is
        copied byte for byte, so every block identity and page number stays
        stock.  A block that no longer fits its own footprint after being
        recompressed is reported and skipped, never relocated: the container
        tiles the whole file, so there is nowhere to relocate it to.
        """
        if not self.has("story"):
            raise ProjectError("The story text has not been extracted yet")
        pair = self.stock_slot()
        if pair is None:
            raise ProjectError("SLOT.DAT was not found in the game folder")
        dat, key = pair

        entries = self.read("story")["entries"]
        translations = {e["source"]: self._render(e["target"], FACE["story"])
                        for e in entries if e.get("target")}
        #: So a block can be reported as lines somebody can go and look at,
        #: rather than as a number with nothing behind it.  A block is not a
        #: key -- the key names the first block a string was found in, and a
        #: line sits in five on average -- so naming one of its lines is the
        #: only handle the workbench can act on.
        key_of = {e["source"]: e["key"] for e in entries}
        if not translations:
            note("Nothing translated in the story.")
            return {"blocks": 0, "strings": 0, "overflow": []}

        _header, (high, low), records = slotdat.load_key(str(key))
        stream = slotdat.WordStream(high, low)

        folder = self.output / SLOT_DIR
        folder.mkdir(parents=True, exist_ok=True)
        out_dat = folder / SLOT_DAT

        note(f"Copying {dat.name}...")
        shutil.copyfile(dat, out_dat)

        touched = applied = 0
        where: dict[str, list] = {}
        overflow: list[str] = []
        #: Of those, the ones no translation can save.
        hopeless: list[str] = []
        emptied: list[str] = []
        blame: dict = {}
        #: Raw strings whose translation is longer than the bytes it must sit
        #: in.  Kept apart from `blame`, which is about a block overflowing:
        #: this one is a single line that will not fit and no other line can
        #: help it.
        raw_over: dict = {}
        with open(dat, "rb") as source, open(out_dat, "r+b") as destination:
            for number, record in enumerate(records):
                if number % 25 == 0:
                    progress(f"Block {number} of {len(records)} -- "
                             f"{applied} string(s) placed")
                try:
                    stock_plain = slotdat.read_block(source, record, stream)
                    elements, head, _t = slotitem.parse(stock_plain)
                except Exception:
                    continue
                # The engine decompresses a block into a buffer sized from
                # the stock block rounded up to a whole page, so the
                # *decompressed* bytes carry a ceiling of their own, quite
                # apart from whether the compressed payload fits the
                # footprint.  One byte over it corrupts whatever follows:
                # a block 32 bytes too big loaded and played fine until the
                # menu that reads it was opened, and then hung.  Compressed
                # size says nothing about this -- the block that hung had
                # ~600 bytes of footprint to spare.
                budget = -(-len(stock_plain) // slotdat.PAGE) * slotdat.PAGE
                changed = False
                sources = set()
                #: Raw lines written into this block.  Kept apart from
                #: `sources`: they name the block, they cannot save it.
                wrote_raw = set()
                #: Which catalogued lines this block holds, translated or
                #: not.  The build already reads every block and every
                #: reference, so the index costs nothing here -- and being
                #: written by the build is what keeps it true, rather than a
                #: snapshot that quietly ages the way a report does.
                present = set()
                for element in elements:
                    if element.ext != "olang":
                        continue
                    try:
                        table = olang.OlangFile.parse(element.data)
                    except Exception:
                        continue
                    hits = 0
                    for reference in table.refs:
                        # English references only.  A SLOT block belongs
                        # to one language, and the pool is shared, so without
                        # this a string the English happens to share with the
                        # Spanish gets Arabic written into the Spanish block
                        # too -- text nobody in this build will read, paid for
                        # out of that block's page budget.
                        if olang.LANG_NAME.get(reference.lang) != SOURCE_LANG:
                            continue
                        # The style says which face draws the line, and
                        # the two do not read the same bytes.  Style 0x0001 is
                        # the caps atlas: a painted grid of 160 cells with no
                        # character map, indexed straight off the byte.  Hand
                        # it a subtitle encoding -- Latin Extended code points
                        # meant for a Unicode charmap -- and it reads past the
                        # end of the grid, inside the menu drawing it.
                        # build_menus has always chosen a face here.  This did
                        # not, and that is what hung the Mission Selector.
                        if reference.style == CAPS_STYLE:
                            continue
                        source_text = table.pool[reference.text]
                        if source_text in key_of:
                            present.add(source_text)
                        target = translations.get(source_text)
                        if target is None:
                            continue
                        sources.add(source_text)
                        table.set_ref(reference, target)
                        hits += 1
                    if hits:
                        table.compact_pool()
                        # The slack after the table is whatever else the
                        # element carried; keeping it is what makes an
                        # untouched rebuild byte-identical.
                        element.data = table.build() + element.data[table.length:]
                        applied += hits
                        changed = True

                # The dialogue that is not in a language table -- voice-cue
                # subtitles, co-op prompts, pool strings nothing references --
                # written inside the bytes it already occupies.  The length
                # never changes, so this cannot push the block past the
                # page-rounded ceiling that hangs the game, and cannot move an
                # offset something else is holding.  A line too long for its
                # own span is refused here and stays English; there is no
                # pooling to fall back on, as a table would have.
                for element in elements:
                    fresh, hits, said = _raw_rewrite(element,
                                                     translations)
                    for text, needed, room in said:
                        if needed - room > raw_over.get(text, 0):
                            raw_over[text] = needed - room
                    if hits:
                        element.data = fresh
                        applied += len(hits)
                        # Named, but never blamed for the block: these are
                        # fixed-length writes, so shortening one frees no
                        # room and telling somebody to trim it would be a
                        # wasted afternoon.
                        wrote_raw.update(hits)
                        present.update(t for t in hits if t in key_of)
                        changed = True
                for text in present:
                    where.setdefault(text, []).append(f"{record.start:05X}")
                if not changed:
                    continue
                plain = slotitem.build(elements, head)
                payload = slotdat.build_block(plain)
                if (len(plain) > budget
                        or slotdat.pages_for(payload) > record.footprint):
                    # The shipped blocks were packed tighter than zlib's
                    # default manages, so a block can come back too big with
                    # nothing translated in it.  Try harder before giving up.
                    payload = slotdat.build_block_smallest(
                        plain, record.footprint * slotdat.PAGE)
                if (len(plain) > budget
                        or slotdat.pages_for(payload) > record.footprint):
                    # Last resort, and only for a block that would
                    # otherwise stay English: empty the languages nobody in
                    # this build will read.  The PS3 tools do the same and
                    # report ~10:1 on the freed text -- with two cautions
                    # learned there, which hold here: leave Japanese alone,
                    # its entities are shaped differently, and a stubbed
                    # cutscene page can take its audio with it.  So it is
                    # never done to a block that already fits, and every
                    # block it touches is named for testing.
                    stubbed, spared = _stub_others(elements, translations)
                    if stubbed:
                        plain = slotitem.build(elements, head)
                        payload = slotdat.build_block_smallest(
                            plain, record.footprint * slotdat.PAGE)
                        if (len(plain) <= budget
                                and slotdat.pages_for(payload)
                                <= record.footprint):
                            slotdat.write_block(destination, record, payload,
                                                stream)
                            touched += 1
                            # Would this block have fitted carrying nothing
                            # but the game's own English?  Some do not: the
                            # shipped file was packed tighter than zlib
                            # manages, so recompressing the stock content
                            # already overruns the footprint.  Where that is
                            # true no amount of trimming can help, and saying
                            # so is the difference between a translator
                            # spending an evening well or wasting one.
                            stock_over = (
                                len(slotdat.build_block_smallest(
                                    stock_plain,
                                    record.footprint * slotdat.PAGE))
                                - record.footprint * slotdat.PAGE)
                            emptied.append({
                                "block": f"{record.start:05X}",
                                "stock_over": stock_over,
                                "raw_only": not sources,
                                "lines": len(sources) + len(wrote_raw),
                                # The longest line in the block, because
                                # the point of the example is to be
                                # recognisable -- sorting the set puts
                                # "(None)" first, which names nothing.
                                "example": key_of.get(
                                    self._telling(sources or wrote_raw)),
                                "text": self._telling(sources or wrote_raw),
                                "spared": spared,
                                "audio": any(e.ext != "olang"
                                             for e in elements)})
                            continue
                    overflow.append(f"{record.start:05X}")
                    # Some blocks cannot be rebuilt at all: the shipped file
                    # was packed tighter than zlib manages, so even the
                    # game's own English overruns the footprint once we
                    # recompress it.  Telling somebody to trim one of those
                    # is telling them to spend an evening on a block that
                    # will refuse them whatever they do.
                    if (len(slotdat.build_block_smallest(
                                    stock_plain,
                                    record.footprint * slotdat.PAGE))
                            > record.footprint * slotdat.PAGE):
                        hopeless.append(f"{record.start:05X}")
                    # Remember which lines are in it.  The check that feeds
                    # "Too long only" does this same walk from scratch, minutes
                    # of it, to learn what the build already knew.
                    short = max(len(plain) - budget,
                            len(payload) - record.footprint * slotdat.PAGE)
                    for text in sources:
                        if short > blame.get(text, (0, 0))[0]:
                            blame[text] = (short, record.start)
                    continue
                slotdat.write_block(destination, record, payload, stream)
                touched += 1

        shutil.copyfile(key, folder / SLOT_KEY)

        try:
            with open(self.root / self.BLOCK_INDEX, "w",
                      encoding="utf-8") as fh:
                json.dump(where, fh)
        except Exception:
            pass

        note(f"{applied} string(s) into {touched} block(s).")
        if emptied:
            note(f"{len(emptied)} block(s) needed a second pass to fit. "
                 f"Type the block number into the Story tab's filter to see "
                 f"every line in it:")
            for hit in emptied:
                how = (f"{hit['spared']} reference(s) in the other languages "
                       f"emptied" if hit["spared"]
                       else "text pool repacked; it holds no other language "
                            "to empty")
                note(f"   {hit['block']} -- {hit['lines']} translated "
                     f"line(s) in it, {how}")
                if hit.get("stock_over", 0) > 0:
                    note(f"        This block does not fit its own footprint "
                         f"even carrying the game's English -- recompressed, "
                         f"the stock text is {hit['stock_over']} byte(s) over. "
                         f"Trimming translations cannot help it.")
                    if slotdat.ZLIB_IS_NG:
                        note("        This python's zlib is zlib-ng, which "
                             "packs a few per cent looser than the real "
                             "thing; blocks that overrun here fit under "
                             "CPython 3.11. Installing zopfli beats both.")
                elif hit.get("raw_only"):
                    note("        Every translated line in it is written into "
                         "the bytes it already occupies, so shortening one "
                         "frees nothing. Trimming cannot help this block.")
                # The key is where the line was *catalogued*, which is the
                # first block it turned up in -- not this one.  A line sits
                # in five blocks on average, so quoting the key beside a
                # different block number reads as a contradiction unless it
                # says which is which.
                if hit["example"]:
                    note(f"        e.g. "
                         + repr(' '.join(hit['text'].split())[:44])
                         + f" -- listed under {hit['example']}, because that "
                           f"is where it was first seen; filter on "
                           f"{hit['block']} to find it here")
            spoken = [h["block"] for h in emptied if h["audio"]]
            quiet = [h["block"] for h in emptied if not h["audio"]]
            if spoken:
                note("   " + ", ".join(spoken)
                     + (" carries" if len(spoken) == 1 else " carry")
                     + " more than text -- play "
                     + ("it" if len(spoken) == 1 else "those")
                     + " to check the audio still lines up."
                     + (("  " + ", ".join(quiet)
                         + (" is" if len(quiet) == 1 else " are")
                         + " text tables only, so there is no sound to lose.")
                        if quiet else ""))
        if overflow:
            note(f"{len(overflow)} block(s) did not fit and were left stock, "
                 f"so every line in them is still English: "
                 + ", ".join(overflow[:8])
                 + ("..." if len(overflow) > 8 else ""))
            fixable = [b for b in overflow if b not in hopeless]
            if fixable:
                note("   " + ", ".join(fixable) + " can be won back: filter "
                     "the Story tab on one of those numbers and shorten its "
                     "longest lines -- the budget belongs to the block, so "
                     "any line in it that loses bytes helps every other one.")
            if hopeless:
                note("   " + ", ".join(hopeless) + " cannot: recompressed, "
                     "even the game's own English overruns the footprint, so "
                     "there is nothing a shorter translation can do.")
            if slotdat.USE_ZOPFLI:
                note("   zopfli is armed and still could not pack these.")
            else:
                note("   zopfli is not installed. It packs tighter than zlib "
                     "and is tried only where zlib misses -- it took every "
                     "one of these back."
                     + ("  This python's zlib is zlib-ng, looser still; "
                        "CPython 3.11 has the real one."
                        if slotdat.ZLIB_IS_NG else ""))

        return {"blocks": touched, "strings": applied, "overflow": overflow,
                "emptied": emptied,
                "lines": {**{entry["key"]: (raw_over[entry["source"]], None)
                             for entry in entries
                             if entry.get("target")
                             and entry["source"] in raw_over},
                          **{entry["key"]: blame[entry["source"]]
                             for entry in entries
                             if entry.get("target")
                             and entry["source"] in blame}}}

    # -- putting it in the game -------------------------------------------

    def built_files(self) -> list[Path]:
        """Everything under ``output/``, as paths relative to it."""
        if not self.output.is_dir():
            return []
        return sorted(path.relative_to(self.output)
                      for path in self.output.rglob("*") if path.is_file())

    #: Already compressed, or media: deflating them costs minutes and saves
    #: nothing -- SLOT's pages are zlib and the movies are H.264 and Vorbis.
    STORE_AS_IS = {".dat", ".pdt", ".xmx", ".xsx", ".key"}

    def bundle(self, destination, include_exe: bool = False,
               note: ProgressFn = _noop, tick: ProgressFn = _noop) -> dict:
        """Zip everything built, laid out as the game folder is, for release.

        The archive's root is the game folder itself -- ``FONT/``, ``MLG/``
        -- so a player extracts it next to the executable and is done.
        Written to ``.part`` first, so a failed or cancelled bundle never
        leaves a zip that looks finished.

        The executable is left out unless asked for: it is the publisher's
        binary, not a data file, and the copy here was patched from whatever
        exe this machine has.
        """
        destination = Path(destination)
        chosen, skipped = [], []
        for relative in self.built_files():
            name = relative.name
            if name.endswith((".part", ".tmp", ".bak", ADDED_SUFFIX)):
                continue
            if name == exenames.EXE_NAME and not include_exe:
                skipped.append(relative)
                continue
            chosen.append(relative)
        if not chosen:
            raise ProjectError("Nothing has been built yet.")

        total = sum((self.output / r).stat().st_size for r in chosen) or 1
        done = 0
        temp = destination.with_name(destination.name + ".part")
        with zipfile.ZipFile(temp, "w", allowZip64=True) as archive:
            for relative in chosen:
                source = self.output / relative
                arcname = relative.as_posix()
                note(f"adding {arcname}")
                info = zipfile.ZipInfo.from_file(source, arcname)
                info.compress_type = (
                    zipfile.ZIP_STORED
                    if source.suffix.lower() in self.STORE_AS_IS
                    else zipfile.ZIP_DEFLATED)
                with open(source, "rb") as reader, \
                        archive.open(info, "w", force_zip64=True) as writer:
                    while True:
                        chunk = reader.read(4 << 20)
                        if not chunk:
                            break
                        writer.write(chunk)
                        done += len(chunk)
                        tick(f"{done * 100 // total}% -- {arcname}")
        os.replace(temp, destination)
        for relative in skipped:
            note(f"left out {relative.as_posix()}")
        return {"files": len(chosen), "path": destination,
                "bytes": destination.stat().st_size,
                "skipped": [r.as_posix() for r in skipped]}

    def install(self, note: ProgressFn = _noop,
                tick: ProgressFn = _noop) -> int:
        """Copy the build into the game, saving each original once as ``.bak``.

        The backup is written only the first time a file is replaced.  Writing
        it again would back up the previous translation, and the way back to
        stock would be gone.

        Progress is counted in bytes across the whole install, backups
        included, because files are nothing like the same size: SLOT.DAT is
        544 MB and a menu table a few kilobytes, so "file 3 of 20" says
        nothing about how long is left.
        """
        if self.game is None:
            raise ProjectError("This project has no game folder")

        plan, total = [], 0
        for relative in self.built_files():
            source = self.output / relative
            destination = self.game / relative
            if not destination.parent.is_dir():
                note(f"skipped {relative} -- {destination.parent} is not there")
                continue
            backup = destination.with_suffix(destination.suffix + ".bak")
            added = added_marker(destination)
            # Only a file the game shipped with is an original.  One this
            # install put there first is marked instead, and never backed up:
            # backing it up on the next install would store the previous
            # translation as ".bak", and restoring would put a translation
            # back where the game had nothing.
            needs_backup = (destination.exists() and not backup.exists()
                            and not added.exists())
            is_new = not destination.exists() and not backup.exists()
            total += source.stat().st_size
            if needs_backup:
                total += destination.stat().st_size
            plan.append((relative, source, destination, backup, needs_backup,
                         is_new, added))

        done, count = 0, 0
        for (relative, source, destination, backup, needs_backup,
             is_new, added) in plan:
            if needs_backup:
                note(f"backing up {relative}")
                done = _copy_with_progress(destination, backup, done, total,
                                           tick, f"backing up {relative}")
            if is_new:
                note(f"adding {relative} -- the game had no such file")
                added.write_bytes(b"")
            note(f"installing {relative}")
            done = _copy_with_progress(source, destination, done, total,
                                       tick, f"installing {relative}")
            count += 1
        return count

    def restore(self, note: ProgressFn = _noop,
                tick: ProgressFn = _noop) -> int:
        """Put the game back as it shipped.

        A file with a ``.bak`` is copied back from it.  A file install added
        -- marked ``.pwtr-added`` -- is removed, because the game had nothing
        there to go back to.  Markers are looked for across the whole game
        folder rather than only among the current build's files, so a file
        added by an earlier build that no longer makes it is removed too.
        """
        if self.game is None:
            raise ProjectError("This project has no game folder")

        count = 0
        removed = set()
        for marker in sorted(self.game.rglob("*" + ADDED_SUFFIX)):
            added = marker.with_name(marker.name[:-len(ADDED_SUFFIX)])
            relative = added.relative_to(self.game)
            if added.exists():
                note(f"removing {relative} -- install added it")
                added.unlink()
                count += 1
            marker.unlink()
            removed.add(added)

        jobs, total = [], 0
        for relative in self.built_files():
            destination = self.game / relative
            if destination in removed:
                continue
            backup = destination.with_suffix(destination.suffix + ".bak")
            source = None
            if backup.exists():
                source, label = backup, f"restoring {relative}"
            elif relative.suffix == ".olang":
                pristine = self.originals / relative
                if pristine.exists():
                    source = pristine
                    label = f"restoring {relative} from the project copy"
            if source is not None:
                jobs.append((source, destination, label))
                total += source.stat().st_size

        done = 0
        for source, destination, label in jobs:
            note(label)
            done = _copy_with_progress(source, destination, done, total,
                                       tick, label)
            count += 1
        return count
