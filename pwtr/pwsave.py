r"""The PC save file, and the staff names inside it.

A soldier's codename is written into the save when he is recruited, not looked
up from :mod:`pwtr.staff` each time the roster is drawn.  So translating the
table renames nobody already on Mother Base -- it only seeds new arrivals.  The
305 already there have their names in ``STW...``, and this is how to reach
them.

## The container

``mgspw_savedata_win/<steam id>/ww/STW000000<checksum>01``, 325 968 bytes.

Two regions are encrypted, each with a 32-bit LCG keystream XORed over it a
word at a time::

    key   = ((mixed ^ 0x6576) << 16) | mixed        where mixed = a ^ b
    inc   = mixed * c
    ...
    word ^= key
    key   = (key * 48828125 + inc) & 0xFFFFFFFF

``a``, ``b`` and ``c`` are three u32 read from an unencrypted header at
``index * 4`` -- offsets +8, +12 and +28 -- each XORed with its own constant.
The header index is not stored anywhere obvious, so it is found by trying: the
region begins with ``oEbN`` once the key is right.  48828125 is 5^11, the same
multiplier Kojima Productions used for save scrambling since MGS2.

The keystream is a function of the header alone, so the transform is its own
inverse and a byte changed in the plaintext moves exactly one byte of the file.
That is what makes a rename safe: nothing else shifts.

## The roster

350 records of 160 bytes at 0x1FAC0.  The codename is 16 bytes at +32,
NUL-padded -- **the same 15-usable-byte field the table has**, so a name that
fits :mod:`pwtr.staff` fits here, and one that does not fit neither.

## The name

The file is named after its own checksum: ``0xFFFF`` XORed with every
little-endian u16 of the finished, re-encrypted file, printed as four
lowercase hex digits.  Write a save under its old name and the game ignores it.

The layout was worked out from a PeaceWalkerSoldierEditor save and the two
files either side of one rename; the constants are the game's, not that tool's.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

MASK = 0xFFFFFFFF
#: 5^11 -- the multiplier of the LCG the keystream comes from.
MULTIPLIER = 48828125

SAVE_SIZE = 325968
REGION_OFFSET, REGION_SIZE = 64, 231408
#: What the first region reads once it is decrypted.
MAGIC = b"oEbN"

ROSTER_BASE = 129664
RECORD_SIZE = 160
RECORD_COUNT = 350
NAME_OFFSET = 32
NAME_SIZE = 16
#: Fifteen bytes and a terminator, exactly as in ``staff.ohd``.
BUDGET = NAME_SIZE - 1


#: ``(start, end, where the CRC32 of that range is kept)``.  All three live in
#: the unencrypted header, and the roster falls in the third.
CRC_RANGES = ((68, 115136, 56), (115136, 129472, 60), (129472, 231464, 48))


class SaveError(Exception):
    pass


def _u32(data, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _state(data, index: int) -> tuple[int, int]:
    """``(key, increment)`` for a header index."""
    base = index * 4
    a = _u32(data, base + 8) ^ 321379955
    b = _u32(data, base + 12) ^ 762434156
    c = _u32(data, base + 28) ^ 3159224226
    mixed = (a ^ b) & MASK
    key = ((((mixed ^ 25974) << 16) & MASK) | mixed) & MASK
    return key, (mixed * c) & MASK


def transform(buffer: bytearray, index: int) -> None:
    """XOR the keystream over the first region, in place.

    Its own inverse: run it on a decrypted buffer to get the file back.
    """
    key, increment = _state(buffer, index)
    for offset in range(REGION_OFFSET, REGION_OFFSET + REGION_SIZE, 4):
        struct.pack_into("<I", buffer, offset, _u32(buffer, offset) ^ key)
        key = (key * MULTIPLIER + increment) & MASK


def _signed_sum(data, start: int, end: int) -> int:
    """Sum a range with every byte read as signed, wrapped to 32 bits."""
    return sum(b - 256 if b >= 128 else b for b in data[start:end]) & MASK


def reseal(plain: bytearray) -> None:
    """Recompute the integrity fields the game checks, in the plaintext.

    Three CRC32s cover the body, and two 64-bit words fold together a slot
    number, a pair of header words, and running sums over three fixed spans.
    Miss any of them and the save is refused as corrupt -- which is the whole
    reason a renamed soldier needs more than the rename.

    Must run before :func:`transform` re-encrypts: these live in the
    plaintext, even the ones that end up under the keystream.
    """
    slot_number = _u32(plain, 376)
    header_a, header_b = _u32(plain, 352), _u32(plain, 356)
    check = ((((header_b ^ header_a) & MASK) << 32) | slot_number) \
        ^ 270582939876
    struct.pack_into("<Q", plain, 360, check)

    block_a = _signed_sum(plain, 48508, 58108)
    block_b = _signed_sum(plain, 58108, 70268)
    shorts = sum(struct.unpack_from("<7h", plain, 82180)) & MASK
    check = ((shorts << 32) | ((block_a ^ block_b) & MASK)) ^ 880468295804
    struct.pack_into("<Q", plain, 368, check)

    for start, end, stored_at in CRC_RANGES:
        struct.pack_into("<I", plain, stored_at,
                         zlib.crc32(bytes(plain[start:end])) & MASK)


def checksum(data) -> int:
    """The number the file is named after."""
    value = 0xFFFF
    for offset in range(0, len(data) & ~1, 2):
        value ^= struct.unpack_from("<H", data, offset)[0]
    return value


def filename(data) -> str:
    return "STW000000%04x01" % checksum(data)


class Save:
    """One decrypted save, and the roster in it."""

    def __init__(self, plain: bytearray, index: int, source: Path | None = None):
        self.plain, self.index, self.source = plain, index, source

    @classmethod
    def load(cls, path: str | Path) -> "Save":
        path = Path(path)
        raw = path.read_bytes()
        if len(raw) != SAVE_SIZE:
            raise SaveError(f"{path.name} is {len(raw)} bytes, not {SAVE_SIZE}"
                            " -- that is not a PC Peace Walker save")
        for index in range(64):
            plain = bytearray(raw)
            transform(plain, index)
            if bytes(plain[REGION_OFFSET:REGION_OFFSET + 4]) == MAGIC:
                return cls(plain, index, path)
        raise SaveError(f"{path.name} did not decrypt as a PC Peace Walker "
                        f"save -- no header index produced {MAGIC.decode()}")

    def _at(self, slot: int) -> int:
        if not 0 <= slot < RECORD_COUNT:
            raise SaveError(f"there is no slot {slot}")
        return ROSTER_BASE + slot * RECORD_SIZE + NAME_OFFSET

    def name(self, slot: int) -> bytes:
        at = self._at(slot)
        return bytes(self.plain[at:at + NAME_SIZE]).split(b"\0")[0]

    def rename(self, slot: int, raw: bytes) -> None:
        """Write one codename.  ``raw`` is bytes, already rendered."""
        if len(raw) > BUDGET:
            raise SaveError(f"{len(raw)} bytes will not fit the {BUDGET}-byte "
                            f"codename field")
        at = self._at(slot)
        self.plain[at:at + NAME_SIZE] = raw + b"\0" * (NAME_SIZE - len(raw))

    def roster(self):
        """``(slot, name bytes)`` for every soldier who has one."""
        for slot in range(RECORD_COUNT):
            name = self.name(slot)
            if name:
                yield slot, name

    def write(self, out_dir: str | Path, clobber: bool = False) -> Path:
        """Re-encrypt and write, under the name the checksum demands.

        The name is a 16-bit checksum, so two different saves can want the
        same one.  Landing on another playthrough's file would destroy it, so
        that is refused rather than risked.
        """
        buffer = bytearray(self.plain)
        reseal(buffer)
        transform(buffer, self.index)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename(buffer)
        if path.exists() and not clobber and path != self.source:
            raise SaveError(
                f"{path.name} is already there and is a different save -- "
                f"this one wants the same checksum name.  Write it somewhere "
                f"else, or move that file away first.")
        path.write_bytes(bytes(buffer))
        return path
