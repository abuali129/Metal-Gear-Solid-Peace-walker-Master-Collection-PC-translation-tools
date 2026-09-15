r"""The speaker names on the radio bar, which live in the executable.

When somebody talks over the radio the bar under the subtitle says who --
``Miller``, ``Paz``, ``Strangelove``.  Those are not in SLOT, STAGEDAT or any
language table: they are a short run of C strings in the exe's ``.rdata``::

    ------  Miller  Paz  Amanda  Chico  Huey  Cécile  Strangelove  Snake  unknown

reached through a table of qword pointers in ``.data``, one per speaker (two
for ``unknown``).  The bar draws them with the subtitle face, which is why an
untranslated ``Miller`` came out as ``M`` and four Arabic letters once the
font plan had reclaimed the lower-case cells.

## Writing

The strings are 8-byte aligned and tight: ``Paz`` has three bytes and
``Strangelove`` eleven.  Rather than squeeze each name into its own slot, the
names are packed one after another from the start of the run and every
pointer is moved to its name's new place.  A pointer in ``.data`` carries a
base relocation, which adds the load delta to whatever value is there, so a
new target inside the image is as good as the old one.

One exception, found by scanning ``.text`` for displacements into the run:
the function at 0x14027DC60 copies ``unknown\0`` by reading its eight bytes as
a qword.  That string therefore stays where it is and is written in place,
seven bytes and a terminator; the packing stops short of it.

The run is found by its bytes and the table by the pointers into it, never by
fixed offsets, so a differently patched exe either works or refuses outright.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

EXE_NAME = "METAL GEAR SOLID PEACE WALKER.exe"

#: The start of the run, and the string the code copies by value.
ANCHOR = b"------\0\0Miller\0\0Paz\0"
FIXED = b"unknown\0"
#: The fixed string is read as one qword.
FIXED_ROOM = 8


@dataclass
class Name:
    offset: int                 # file offset of the stock string
    text: str
    fixed: bool = False         # copied by value: written in place
    pointers: list = field(default_factory=list)


@dataclass
class Table:
    start: int                  # file offset of the packable run
    end: int                    # where the fixed string begins
    names: list


class NotFound(ValueError):
    pass


def _sections(data: bytes):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    first = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]
    image = struct.unpack_from("<Q", data, pe + 24 + 24)[0]
    out = []
    for i in range(count):
        o = first + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, raw = struct.unpack_from("<4I", data, o + 8)
        out.append((name, va, max(vsize, rsize), raw, rsize))
    return image, out


def _va_of(data: bytes, offset: int) -> int:
    image, sections = _sections(data)
    for _name, va, _vsize, raw, rsize in sections:
        if raw <= offset < raw + rsize:
            return image + va + offset - raw
    raise NotFound(f"offset 0x{offset:X} is in no section")


def _offset_of(data: bytes, address: int) -> int | None:
    image, sections = _sections(data)
    rva = address - image
    for _name, va, _vsize, raw, rsize in sections:
        if va <= rva < va + rsize:
            return raw + rva - va
    return None


def read(data: bytes) -> Table:
    """Find the run and its pointer table; raise :class:`NotFound` if either
    is not exactly where the evidence says."""
    start = data.find(ANCHOR)
    if start < 0 or data.find(ANCHOR, start + 1) >= 0:
        raise NotFound("the speaker-name strings are not in this exe")
    end = data.find(FIXED, start)
    if end < 0 or end - start > 0x100:
        raise NotFound("the speaker-name run does not end where expected")

    first = struct.pack("<Q", _va_of(data, start))
    table = data.find(first)
    if table < 0 or data.find(first, table + 1) >= 0:
        raise NotFound("no single pointer table for the speaker names")
    lo, hi = _va_of(data, start), _va_of(data, end)
    names: dict[int, Name] = {}
    at = table
    while True:
        address = struct.unpack_from("<Q", data, at)[0]
        if not lo <= address <= hi:
            break
        offset = _offset_of(data, address)
        if offset not in names:
            raw = data[offset:data.index(b"\0", offset)]
            names[offset] = Name(offset, raw.decode("utf-8"),
                                 fixed=offset == end)
        names[offset].pointers.append(at)
        at += 8
    if not names or end not in names:
        raise NotFound("the pointer table does not reach every speaker name")
    # Every string in the run must be accounted for, or packing would
    # overwrite something reached another way.
    at = start
    while at < end:
        if data[at] == 0:
            at += 1
            continue
        if at not in names:
            raise NotFound(f"a string at 0x{at:X} is not in the table")
        at = data.index(b"\0", at)
    return Table(start, end, sorted(names.values(), key=lambda n: n.offset))


def is_text(name: Name) -> bool:
    """A name, rather than the ``------`` placeholder for nobody."""
    return any(ch.isalpha() for ch in name.text)


def room(table: Table, rendered: dict, name: Name) -> tuple[int, int]:
    """``(bytes this name needs, bytes it has)`` with every other name as
    ``rendered`` gives it (source text where it has no entry)."""
    def cost(n):
        return len(rendered.get(n.text, n.text).encode("utf-8")) + 1

    need = cost(name)
    if name.fixed:
        return need, FIXED_ROOM
    others = sum(cost(n) for n in table.names if not n.fixed and n is not name)
    return need, table.end - table.start - others


def patch(data: bytes, rendered: dict):
    """-> ``(new exe bytes, [names written], [(name, needed, room)])``

    ``rendered`` maps the English name to the bytes-ready translation.  When
    the packed names will not fit, the translations that grew the most go back
    to English one at a time until they do, and are reported.
    """
    table = read(data)
    out = bytearray(data)
    written, refused = [], []

    fixed = next(n for n in table.names if n.fixed)
    target = rendered.get(fixed.text)
    if target:
        new = target.encode("utf-8") + b"\0"
        if len(new) <= FIXED_ROOM:
            out[fixed.offset:fixed.offset + FIXED_ROOM] = (
                new + b"\0" * (FIXED_ROOM - len(new)))
            written.append(fixed.text)
        else:
            refused.append((fixed.text, len(new), FIXED_ROOM))

    packed = [n for n in table.names if not n.fixed]
    chosen = {n.text: rendered[n.text] for n in packed if rendered.get(n.text)}
    span = table.end - table.start

    def size():
        return sum(len(chosen.get(n.text, n.text).encode("utf-8")) + 1
                   for n in packed)

    if not chosen:
        # Nothing to pack: leave the run and its pointers exactly as shipped.
        return bytes(out), written, refused

    while size() > span and chosen:
        worst = max(chosen, key=lambda t: len(chosen[t].encode("utf-8"))
                    - len(t.encode("utf-8")))
        need, have = room(table, chosen, next(n for n in packed
                                              if n.text == worst))
        refused.append((worst, need, have))
        del chosen[worst]

    blob = bytearray()
    for n in packed:
        address = _va_of(data, table.start + len(blob))
        for pointer in n.pointers:
            struct.pack_into("<Q", out, pointer, address)
        blob += chosen.get(n.text, n.text).encode("utf-8") + b"\0"
        if n.text in chosen:
            written.append(n.text)
    out[table.start:table.end] = bytes(blob) + b"\0" * (span - len(blob))
    return bytes(out), written, refused
