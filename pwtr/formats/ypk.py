r"""SLOT.DAT dialogue that is not in a language table: ``.ypk`` and ``.ohd``.

## .ypk -- GTT entities

A ``.ypk`` element is a run of GTT entities, each 16-byte aligned::

    u32 magic "GTT\0"   u32 row count   u32 text start   u32 total length
    row count x 20 bytes: ten int16 fields
    text area

A row is one line *in one language*; a line in six languages is six rows.
Fields 3..9 are seven offsets into the text area that cut it into six
segments, one per language, and a row fills only its own.  Segment 2 (fields
5..6) is English: ``text\0`` and sometimes a few bytes more, which stay.

Measured over all 35 268 stock entities: no offset ever points inside an
English segment, so replacing one and shifting every offset at or past its
end is exact.  The PS3/PSP builder instead set all seven offsets to the
English span, which rebuilds 385 of 462 elements differently with nothing
translated -- it erases the other languages' addressing.

The engine finds entities at their stock offsets (PSP), so an entity is
rebuilt back to its stock total length.  One whose translation does not fit
stays stock -- all of its lines English -- and its lines are reported with
the bytes the entity is over, since they share the room.

## .ohd -- voice-cue subtitles

128-byte records, text at +0x4C: 63 UTF-8 bytes and a NUL, written in place.
"""
from __future__ import annotations

import struct

MAGIC = 0x00545447
HEADER = 16
ROW = 20
EN_START, EN_END = 5, 6
FIRST, LAST = 3, 9

OHD_TEXT = 0x4C
OHD_STRIDE = 0x80
OHD_ROOM = 63


def _align(n: int) -> int:
    return n + (-n) % 16


def entities(data: bytes):
    """``(start, text start, total length, rows)`` for each GTT entity."""
    out, pos = [], 0
    while pos + HEADER <= len(data):
        if struct.unpack_from("<I", data, pos)[0] != MAGIC:
            break
        count, text_start, total = struct.unpack_from("<3I", data, pos + 4)
        if (total < text_start or pos + total > len(data)
                or HEADER + count * ROW > text_start):
            raise ValueError(f"bad GTT entity at 0x{pos:X}")
        rows = [[v & 0xFFFF for v in
                 struct.unpack_from("<10h", data, pos + HEADER + i * ROW)]
                for i in range(count)]
        out.append((pos, text_start, total, rows))
        pos = _align(pos + total)
    return out


def _english(data, pos, text_start, row):
    """``(text bytes, rest of segment)``, or None when the row has no English."""
    a, b = row[EN_START], row[EN_END]
    if b <= a:
        return None
    seg = data[pos + text_start + a:pos + text_start + b]
    cut = seg.find(b"\0")
    if cut < 0:
        return seg, b""
    return seg[:cut], seg[cut + 1:]


def ypk_strings(data: bytes):
    """``(entity index, row index, english)`` for every non-empty English row."""
    try:
        found = entities(data)
    except ValueError:
        return []
    out = []
    for n, (pos, ts, _total, rows) in enumerate(found):
        for r, row in enumerate(rows):
            hit = _english(data, pos, ts, row)
            if hit is None or not hit[0].strip():
                continue
            try:
                out.append((n, r, hit[0].decode("utf-8")))
            except UnicodeDecodeError:
                continue
    return out


def _rebuild(data, pos, ts, total, rows, new_by_row):
    """``(entity bytes, 0)`` with English replaced, or ``(None, bytes over)``."""
    area_len = total - ts
    area = data[pos + ts:pos + total]
    # The alignment tail after the entity is room too: the next entity is
    # found at the aligned end, which does not move.  Only if it is zeros.
    end = min(_align(pos + total), len(data))
    room = area_len + (end - pos - total
                       if not data[pos + total:end].strip(b"\0") else 0)
    swaps = []                                  # (start, end, replacement)
    for r, new in new_by_row.items():
        row = rows[r]
        _text, rest = _english(data, pos, ts, row)
        swaps.append((row[EN_START], row[EN_END], new + b"\0" + rest))
    swaps.sort()

    def moved(offset):
        return offset + sum(len(rep) - (b - a)
                            for a, b, rep in swaps if b <= offset)

    out, at = bytearray(), 0
    for a, b, rep in swaps:
        out += area[at:a] + rep
        at = b
    out += area[at:]
    # What must survive the cut back to stock length: every offset, and
    # every non-zero byte the stock entity carried.
    offsets = [moved(v) for row in rows for v in row[FIRST:LAST + 1]]
    need = max(offsets + [moved(len(area.rstrip(b"\0")))])
    if need > room or max(offsets) > 0x7FFF:
        return None, need - room
    size = max(area_len, need)
    out = out[:size] + b"\0" * max(0, size - len(out))
    head = bytearray(data[pos:pos + ts])
    struct.pack_into("<I", head, 12, ts + size)
    for i, row in enumerate(rows):
        fields = list(row)
        for k in range(FIRST, LAST + 1):
            fields[k] = moved(row[k])
        struct.pack_into("<10h", head, HEADER + i * ROW,
                         *[v - 0x10000 if v >= 0x8000 else v for v in fields])
    return bytes(head + out), 0


def ypk_rewrite(data: bytes, translations: dict):
    """-> ``(new data, [english written], [(english, needed, room)])``

    Same shape as :func:`pwtr.slotraw.rewrite`, and the same guarantee: the
    element comes back exactly as long as it went in.  For a refused line
    ``needed - room`` is what its *entity* is over.
    """
    try:
        found = entities(data)
    except ValueError:
        return data, [], []
    out = bytearray(data)
    written, refused = [], []
    for pos, ts, total, rows in found:
        new_by_row, english = {}, {}
        for r, row in enumerate(rows):
            hit = _english(data, pos, ts, row)
            if hit is None:
                continue
            try:
                text = hit[0].decode("utf-8")
            except UnicodeDecodeError:
                continue
            target = translations.get(text)
            if target:
                new_by_row[r] = target.encode("utf-8")
                english[r] = text
        if not new_by_row:
            continue
        block, over = _rebuild(data, pos, ts, total, rows, new_by_row)
        if block is None:
            refused += [(t, len(new_by_row[r]) + over, len(new_by_row[r]))
                        for r, t in english.items()]
            continue
        out[pos:pos + len(block)] = block
        written += english.values()
    return bytes(out), written, refused


def ohd_strings(data: bytes):
    """``(record offset, english)`` for every non-empty subtitle."""
    out = []
    for off in range(OHD_TEXT, len(data), OHD_STRIDE):
        raw = data[off:off + OHD_ROOM + 1].split(b"\0", 1)[0]
        if not raw.strip():
            continue
        try:
            out.append((off, raw.decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return out


def ohd_rewrite(data: bytes, translations: dict):
    """In place, 63 bytes a line.  Same return shape as :func:`ypk_rewrite`."""
    out = bytearray(data)
    written, refused = [], []
    for off, english in ohd_strings(data):
        target = translations.get(english)
        if not target:
            continue
        new = target.encode("utf-8")
        room = min(OHD_ROOM, len(data) - off - 1)
        if len(new) > room:
            refused.append((english, len(new), room))
            continue
        out[off:off + room + 1] = new + b"\0" * (room + 1 - len(new))
        written.append(english)
    return bytes(out), written, refused


def strings(ext: str, data: bytes) -> list[str]:
    """Every English line in a ``.ypk`` or ``.ohd`` element, in order."""
    if ext == "ypk":
        return [t for _e, _r, t in ypk_strings(data)]
    if ext == "ohd":
        return [t for _o, t in ohd_strings(data)]
    return []


def rewrite(ext: str, data: bytes, translations: dict):
    """Dispatch on extension; anything else comes back untouched."""
    if ext == "ypk":
        return ypk_rewrite(data, translations)
    if ext == "ohd":
        return ohd_rewrite(data, translations)
    return data, [], []
