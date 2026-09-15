"""STAGEDAT.PDT -- the mission text, three containers down.

``MLG/disc0_rel/009645fa.PDT`` is ``STAGEDAT.PDT``.  Its 557 entities are not
files: each one is a small archive, and some of those archives carry ``.olang``
tables holding text nothing else in this toolkit reads -- mission objectives,
tutorials, award descriptions, install prompts.  About **1 550 English lines**
that are in neither the menus nor the story.

## Why it was missed

Two layers hid it.  The entities are zlib inside two ciphers, which
:mod:`pwtr.formats.pdt` already handles; and the archives inside them are not
QAR.  A QAR keeps its table of contents at the end of the file, and testing for
one -- which is what the texture unpacker does -- says "not a QAR" and stops.
These are a different, simpler thing with the directory inline.

## The archive layout

Worked out by walking it rather than from any documentation::

    u32 count
    then, per member:
        name        NUL-terminated ASCII, VARIABLE length
        pad         to a 4-byte boundary
        u32 size
        pad         to a 16-byte boundary
        data        `size` bytes
        one NUL     before the next member's name

The variable-length name is the part that catches you out.  Assume a fixed
12-byte name field -- which is what the first two members of every entity look
like, because ``ICON0.png`` and ``ICON0A.png`` happen to pad to exactly that --
and the third member lands one byte early and every length after it is
nonsense.

## Writing back

Members may change size: the whole archive is rebuilt, the entity recompressed,
and the container repacked by :mod:`pwtr.formats.pdt_pack`, which copies every
entity we did not touch verbatim -- still encrypted, never re-deflated -- so an
unedited repack is byte-identical to the original.  An entity that outgrows its
slack is moved to the end rather than shifting its neighbours; the entity table
stores positions outright, so nothing else needs updating.
"""

from __future__ import annotations

import struct
from pathlib import Path

from pwtr.formats import olang, pdt_pack

#: STAGEDAT.PDT, stored under the 24-bit hash of its name.
NAME = "009645fa.PDT"

#: What an embedded language table starts with.
OLANG_MAGIC = b"RBX\x00"


class StagedatError(Exception):
    pass


def _align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


class Member:
    """One file inside one entity's archive."""

    __slots__ = ("name", "offset", "size", "data")

    def __init__(self, name: str, offset: int, size: int, data: bytes):
        self.name, self.offset, self.size, self.data = name, offset, size, data

    @property
    def is_olang(self) -> bool:
        return self.data[:4] == OLANG_MAGIC

    def __repr__(self) -> str:
        return f"<{self.name} {self.size}B at 0x{self.offset:X}>"


def walk(data: bytes) -> list[Member] | None:
    """The archive's members, or None if this entity is not one.

    Returning None rather than raising is deliberate: most of the 557 entities
    are models, effects and audio, and "not an archive" is the normal answer.
    """
    if len(data) < 8:
        return None
    count = struct.unpack_from("<I", data, 0)[0]
    if not 0 < count < 100000:
        return None

    members: list[Member] = []
    position = 4
    for _ in range(count):
        end = data.find(b"\x00", position)
        if end < 0:
            return None
        try:
            name = data[position:end].decode("ascii")
        except UnicodeDecodeError:
            return None
        position = _align(end + 1, 4)
        if position + 4 > len(data):
            return None
        size = struct.unpack_from("<I", data, position)[0]
        position = _align(position + 4, 16)
        if size < 0 or position + size > len(data):
            return None
        members.append(Member(name, position, size,
                              data[position:position + size]))
        position += size + 1                 # the NUL between members
    return members


def rebuild(members: list[Member]) -> bytes:
    """The archive as bytes, laid out the way it was read.

    Sizes may differ from what came in -- that is the point -- so every offset
    is recomputed rather than patched.
    """
    out = bytearray(struct.pack("<I", len(members)))
    for member in members:
        out += member.name.encode("ascii") + b"\x00"
        out += b"\x00" * (_align(len(out), 4) - len(out))
        out += struct.pack("<I", len(member.data))
        out += b"\x00" * (_align(len(out), 16) - len(out))
        out += member.data
        out += b"\x00"
    return bytes(out)


# -- the text inside ---------------------------------------------------------

def tables(members: list[Member]):
    """``(member, OlangFile)`` for every language table in an archive."""
    for member in members:
        if not member.is_olang:
            continue
        try:
            yield member, olang.OlangFile.parse(member.data)
        except Exception:
            continue                          # a table we cannot read is left


def english(table) -> list[tuple[int, str]]:
    """``(pool index, text)`` for every English string in one table."""
    langs = table.pool_langs()
    return [(index, text) for index, text in enumerate(table.pool)
            if "en" in langs[index] and text.strip()]


class Stagedat:
    """The container, opened once and read on demand.

    Every entity has to be decrypted and inflated to be looked at, and the file
    is 487 MB, so nothing happens until something asks.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.template = pdt_pack.Template(str(self.path))

    @property
    def count(self) -> int:
        return self.template.count

    def payload(self, index: int) -> bytes | None:
        try:
            return self.template.payload(index)
        except Exception:
            return None

    def archives(self, progress=None):
        """``(index, members)`` for every entity that is an archive."""
        for index in range(self.count):
            if progress and index % 25 == 0:
                progress(f"Entity {index} of {self.count}")
            data = self.payload(index)
            if data is None:
                continue
            members = walk(data)
            if members:
                yield index, members

    def staff_tables(self, progress=None) -> dict:
        """``{table name: [name, ...]}`` -- the roster's word lists.

        The ten copies of each table are identical, so this stops at the first
        entity carrying a full set rather than inflating all 557.
        """
        from pwtr import staff
        found: dict[str, list[str]] = {}
        for index, members in self.archives(progress):
            for member in members:
                if not staff.is_table(member.name) or member.name in found:
                    continue
                rows = staff.names(member.data)
                if rows:
                    found[member.name] = rows
            if len(found) == len(staff.TABLES):
                break
        return found

    def build(self, translations: dict, out_path: str | Path,
              note=None, progress=None, textures=None,
              since: float | None = None, staff_names: dict | None = None
              ) -> dict:
        """Apply ``{english: arabic}`` everywhere it occurs, and repack.

        Matched on the **source text**, not on an address, for the same reason
        the story is: the same line -- "Mission accomplished", every objective
        heading -- is stored in dozens of entities, and translating it by
        address would leave every other copy in English.  One entry
        translates all of them.

        Only entities that actually change are rebuilt; everything else is
        copied across still encrypted, so an empty edit set reproduces the
        original file byte for byte.

        ``textures`` is the STAGEDAT folder of an unpack, and its edited
        pictures go into the same rebuild.  They have to: this container
        holds the mission text *and* the artwork, both are written by
        replacing whole entities, and two separate builds from the stock
        file would each throw away the other's work -- whichever ran last
        would be the one that shipped.
        """
        from pwtr import staff as staff_mod
        payloads, strings, seen = {}, 0, 0
        rosters, roster_said = 0, set()
        for index in range(self.count):
            if progress and index % 25 == 0:
                progress(f"Entity {index} of {self.count} -- "
                         f"{strings} string(s) placed")
            data = self.payload(index)
            if data is None:
                continue
            members = walk(data)
            if not members:
                continue

            changed = False
            for member in members:
                # The staff names are not text in the olang sense: they sit
                # in a fixed-record table beside it.  They ride along here
                # because this container is written entity by entity, and a
                # second pass over the same 557 entities would be a second
                # 487 MB write that threw this one away.
                if staff_names and staff_mod.is_table(member.name):
                    fresh, said = staff_mod.rewrite(member.data, staff_names)
                    for line in said:
                        if line not in roster_said and note:
                            roster_said.add(line)
                            note("  ! " + line)
                    if fresh is not None:
                        member.data = fresh
                        rosters += 1
                        changed = True
                if not member.is_olang:
                    continue
                try:
                    table = olang.OlangFile.parse(member.data)
                except Exception:
                    continue
                hits = 0
                for reference in table.refs:
                    if olang.LANG_NAME.get(reference.lang) != "en":
                        continue
                    seen += 1
                    text = translations.get(table.pool[reference.text])
                    if not text:
                        continue
                    table.set_ref(reference, text)
                    hits += 1
                if not hits:
                    continue
                table.compact_pool()
                # Keep whatever trailed the table inside the member: the
                # length the table reports is its own, and the rest is not
                # ours to drop.
                member.data = table.build() + member.data[table.length:]
                strings += hits
                changed = True

            if changed:
                payloads[index] = rebuild(members)
                if note:
                    note(f"entity {index}: {len(payloads)} rebuilt so far")

        pictures = 0
        if textures:
            from pwtr import stagetex
            written, complaints = stagetex.merge(
                payloads, self.path, textures,
                note or (lambda _m: None), since)
            pictures = len(written)
            for line in complaints:
                if note:
                    note("  ! " + line)

        if not payloads:
            if note:
                note("Nothing matched -- no entity changed.")
            return {"entities": 0, "strings": 0, "textures": 0,
                    "names": 0}

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if note:
            note(f"Repacking {self.count} entities...")
        pdt_pack.build(self.template, payloads, str(out_path), NAME)
        return {"entities": len(payloads), "strings": strings,
                "textures": pictures, "names": rosters,
                "out": str(out_path)}
