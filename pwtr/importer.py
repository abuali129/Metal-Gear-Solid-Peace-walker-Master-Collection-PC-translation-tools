"""Bring a translation across from the PS3 or PSP build.

Both earlier toolkits store their work as CSV -- a folder of them, one per
container file -- and every one of those files, whatever else it carries, has a
``source_text`` column and a ``translation`` column.  That pair is the whole
interface: this reads them, and matches **on the English**.

## Why matching on content is the only thing that works

The three builds do not agree on addresses.  PS3 keys a slot string by
``entity_key/ref_idx``, the PSP by its own page numbering, and this build by
neither.  Record 364 on PS3 is not record 364 here.  Even the containers differ
-- the PC keeps its briefings in ``BRIEFING.DAT`` under a hashed name, the PSP
in a file with a different magic altogether.

What does agree is the English, because all three shipped the same script.  So
the import ignores every address in the source files and keys purely on the
source string, which also means a line that moved between builds still lands.

## Two things that would otherwise lose most of the matches

**Line endings.**  The PS3 CSVs store ``\\r\\n`` inside the text where the PC
containers store ``\\n``.  Nothing matches until that is normalised -- the PS3
briefings go from **0%** to **95%** on that one substitution alone, which is
exactly the kind of silent zero that reads as "the data is not there".

**Stubs.**  A translation of ``.`` is not a translation.  Both earlier builds
ran into byte budgets they could not grow out of, and a stub was how they freed
space in a full record.  Importing those would fill this project with dots and,
worse, would look like progress.  They are counted and skipped.

Where both builds have a line, the caller decides which wins -- normally PS3
first, since it is the hand-checked one, with the PSP filling what it left.
"""

from __future__ import annotations

import csv
from pathlib import Path

#: A translation made only of these is a placeholder, not a translation.
STUB_CHARACTERS = set(". \t\n\r-_")

#: The columns every studio in the family writes.
SOURCE_COLUMN = "source_text"
TARGET_COLUMN = "translation"

csv.field_size_limit(16 * 1024 * 1024)


def normalise(text: str) -> str:
    """The form both sides are compared in: LF line endings, nothing else.

    Deliberately conservative.  Stripping or collapsing whitespace would match
    more, and would also merge lines the game keeps apart -- several briefing
    strings differ only in a trailing newline.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def is_stub(text: str) -> bool:
    return not text.strip() or set(text) <= STUB_CHARACTERS


def load(root: str | Path, progress=None) -> dict:
    """Every usable ``source -> translation`` pair under a translations folder.

    Returns the map; :func:`report` describes what was in it.  The first
    translation found for a source wins, so a folder walked in a stable order
    imports the same way twice.
    """
    root = Path(root)
    pairs: dict[str, str] = {}
    stubs = files = rows = 0

    for path in sorted(root.rglob("*.csv")):
        files += 1
        if progress and files % 100 == 0:
            progress(f"{files} files, {len(pairs)} strings")
        try:
            with open(path, encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    rows += 1
                    source = row.get(SOURCE_COLUMN)
                    target = row.get(TARGET_COLUMN)
                    if not source or target is None:
                        continue
                    target = target.strip()
                    if not target:
                        continue
                    if is_stub(target):
                        stubs += 1
                        continue
                    pairs.setdefault(normalise(source), normalise(target))
        except (OSError, csv.Error, UnicodeDecodeError):
            continue                    # a file we cannot read is not fatal

    return {"pairs": pairs, "files": files, "rows": rows, "stubs": stubs,
            "root": str(root)}


def apply(entries, *sources, note=None) -> dict:
    """Fill in the blank targets of ``entries`` from ``sources``, in order.

    Only blanks are filled: a line already translated in this project is never
    overwritten, so an import can be run twice, or a second source added later,
    without undoing anything.

    ``sources`` are the maps :func:`load` returned, most trusted first.
    """
    maps = [source["pairs"] if isinstance(source, dict) and "pairs" in source
            else source for source in sources]
    filled = [0] * len(maps)
    already = missing = 0

    for entry in entries:
        if entry.get("target"):
            already += 1
            continue
        key = normalise(entry["source"])
        for index, mapping in enumerate(maps):
            found = mapping.get(key)
            if found:
                entry["target"] = found
                filled[index] += 1
                break
        else:
            missing += 1

    total = sum(filled)
    if note:
        note(f"{total} filled, {missing} left blank, {already} already done")
    return {"filled": filled, "total": total, "missing": missing,
            "already": already}
