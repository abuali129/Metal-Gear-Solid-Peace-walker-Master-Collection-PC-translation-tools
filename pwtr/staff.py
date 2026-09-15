r"""The Mother Base staff names.

A recruit is called something like ``BLENNY SPARROW``, and neither half of
that is a string in any ``.olang`` pool -- which is why the workbench never
showed them.  Both words are drawn from one 430-entry list of animals held in
``staff.ohd``, a fixed-record binary table sitting inside STAGEDAT next to the
mission text.  The game picks two rows and prints them with a space between.

## The layout

Worked out from the bytes::

    u32 ?                       always 1
    u32 ?                       5 in staff.ohd, 1 in the others
    u32 count                   430, or 44
    then, per record (24 bytes):
        u16 id                  1-based, matches the row
        u16 flag                a species group in staff.ohd, a portrait in
                                the others -- not ours to touch
        u32 ?
        char name[16]           NUL-padded ASCII

Sixteen bytes is the whole budget: **fifteen usable and a terminator**, and
the record cannot grow because the next one starts 24 bytes on.  Written as
raw presentation forms at three bytes a letter that is five letters; through
the project's compact plan a typical animal costs four to six bytes and even
``الحوت الأزرق`` lands on fifteen exactly.  So these fit, but only with a face
installed -- which is the same condition the rest of the text is under.

## Four tables, forty-nine copies

``staff.ohd`` is the animal list.  ``staff_uniq.ohd`` and its ``_f`` and ``_g``
variants hold the same 44 named characters -- SNAKE, AMANDA, HUEY, PAZ,
STRANGELOVE -- differing only in the letter suffixed to the vehicle rows.

Each table is repeated byte for byte across the entities that can show a
roster: 13 carry ``staff.ohd`` (23, 59, 100, 104, 110, 126, 132, 138, 144,
150, 174, 193, 471) and 12 of those carry the three ``_uniq`` tables as well
-- 49 members in all.  Reading one copy is enough; writing has to reach every
one of them, which is why this happens inside the pass :mod:`pwtr.stagedat`
already makes over all 557 entities rather than in a walk of its own.  Do not
take the entity list on trust in code: find them by member name, the way the
build does, because missing one leaves an area still showing English.
"""
from __future__ import annotations

import struct

#: The tables, in the order the workbench shows them.
TABLES = ("staff.ohd", "staff_uniq.ohd", "staff_uniq_f.ohd",
          "staff_uniq_g.ohd")

HEADER = 12
RECORD = 24
NAME_AT = 8
#: The name field, terminator included.  A translation gets ``FIELD - 1``.
FIELD = 16
BUDGET = FIELD - 1


def is_table(name: str) -> bool:
    return name in TABLES


def _count(blob: bytes) -> int | None:
    """How many records, if this really is one of these tables."""
    if len(blob) < HEADER:
        return None
    count = struct.unpack_from("<I", blob, 8)[0]
    if count <= 0 or HEADER + count * RECORD != len(blob):
        return None
    return count


def names(blob: bytes) -> list[str]:
    """Every name in a table, in record order.  Empty if it is not one."""
    count = _count(blob)
    if count is None:
        return []
    out = []
    for index in range(count):
        at = HEADER + index * RECORD + NAME_AT
        out.append(blob[at:at + FIELD].split(b"\0")[0].decode("latin-1"))
    return out


def fits(text: str) -> bool:
    return len(text.encode("utf-8")) <= BUDGET


def rewrite(blob: bytes, translations: dict) -> tuple[bytes | None, list[str]]:
    """-> (the table with its names replaced, complaints)

    ``translations`` is keyed on the English name, so one entry serves all ten
    copies of the table and both halves of every recruit's name.  A name too
    long for the field is left in English and reported: truncating it would
    cut a word in the middle of a ligature, and overrunning it would write
    into the next record's id.

    Returns ``(None, ...)`` when nothing changed, so the caller can leave the
    entity alone.
    """
    count = _count(blob)
    if count is None:
        return None, []
    out = bytearray(blob)
    complaints: list[str] = []
    touched = False
    for index in range(count):
        at = HEADER + index * RECORD + NAME_AT
        english = bytes(out[at:at + FIELD]).split(b"\0")[0].decode("latin-1")
        text = translations.get(english)
        if not text:
            continue
        raw = text.encode("utf-8")
        if len(raw) > BUDGET:
            complaints.append(
                f"{english}: needs {len(raw)} bytes, the field holds {BUDGET}")
            continue
        out[at:at + FIELD] = raw + b"\0" * (FIELD - len(raw))
        touched = True
    return (bytes(out) if touched else None), complaints
