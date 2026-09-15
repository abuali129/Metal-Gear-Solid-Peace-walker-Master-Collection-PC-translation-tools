r"""The `.slot` element container - the layer inside a SLOT.DAT block.

A block inflates to one of these.  It is a flat directory of typed elements;
for us the interesting type is `olang`, because the SLOT container holds the
part of the script the shipped `.olang` files do not.

Layout, little-endian, 64-bit fields (the PSP build used 32-bit ones):

    0x00  u32 count           entries + 3 - the three terminator rows below
    0x04  u32 0
    0x08  u64 0x7F000002      table marker
    0x10  u64 data_size       size of the data region
    0x18  count x {u64 key, u64 offset}     offset is relative to data_base
          u64 0x7F000000      end marker
          u64 data_size       repeated
          u64 0               tail
    ...   filler
    data_base = ((count << 4) + 0x1007) & ~0xFFF
    data region: elements back to back, each padded up to a multiple of 16

`key` is `(ext_byte << 24) | strcode24(name)` - the same 24-bit strcode used
for the `.PDT` archive names, and the same extension byte table as the entity
tables.  The name itself is not stored, only its hash, so an element can be
identified by type and matched to a known name but not recovered from nothing.

Round-trip note: the space between the end of one element and the start of the
next is not always zero - the game writes elements into a reused buffer and the
slack keeps whatever was there.  We therefore store each element as its FULL
padded region, so rebuilding an untouched slot reproduces the original byte for
byte.  Trimming to the logical length would look tidier and would corrupt the
comparison that tells us whether a slot changed.
"""

from __future__ import annotations

import os
import struct

MARKER = 0x7F000002
END = 0x7F000000
ALIGN = 16

# Extension byte -> name.  The engine's own table has collisions: mdp, mdc,
# mdl and mdb all hash to 0x13, so the byte cannot tell them apart and the
# last definition wins - exactly as the C# dictionary that built it behaves.
# Keep the collision visible rather than pretending the mapping is injective.
EXTENSIONS = [
    'mdpe', 'qar', 'vrdv', 'vrd', 'mgm', 'mds', 'row', 'spk',
    'cap', 'rat', 'mtfa', 'eqp', 'psq', 'dcd', 'mtst', 'gcx',
    'cvd', 'bgp', 'ohd', 'tri', 'rpd', 'mdp', 'vlm', 'vcpg',
    'kms', 'la2', 'ptcp', 'vcp', 'fcx', 'ola', 'rcm', 'lt2',
    'olang', 'mtsq', 'pcmp', 'vram', 'mdh', 'mmd', 'bin', 'mdpb',
    'img', 'mdc', 'vib', 'zon', 'cddl', 'txp', 'vrdt', 'nav',
    'cmf', 'png', 'la3', 'lst', 'dar', 'ypk', 'rlc', 'mtra',
    'geom', 'cv2', 'prx', 'mtar', 'eft', 'slot', 'mdl', 'mtcm',
    'sep', 'mdb', 'cnf',
]
EXT_BYTES = [
    0x1A, 0xF1, 0x21, 0x20, 0x30, 0x05, 0x6E, 0x1D,
    0x35, 0x6B, 0x0A, 0x64, 0xFF, 0x1B, 0x18, 0x02,
    0x10, 0x38, 0x1E, 0x03, 0x16, 0x13, 0x65, 0x24,
    0x15, 0x5F, 0x33, 0x23, 0x17, 0x6D, 0x6C, 0x06,
    0x5D, 0x09, 0x36, 0x61, 0x04, 0x1F, 0x01, 0x19,
    0x69, 0x13, 0x6A, 0x12, 0x34, 0x14, 0x22, 0x0F,
    0x63, 0x68, 0x5E, 0x66, 0xF0, 0x1C, 0x32, 0x6F,
    0x0C, 0x07, 0x31, 0x08, 0x11, 0x60, 0x13, 0x0B,
    0x37, 0x13, 0xF2,
]
EXT_BY_BYTE = {}
EXT_COLLISIONS = {}
for _i, _b in enumerate(EXT_BYTES):
    EXT_COLLISIONS.setdefault(_b, []).append(EXTENSIONS[_i])
    EXT_BY_BYTE[_b] = EXTENSIONS[_i]        # last wins, as the engine does


def data_base(count: int) -> int:
    return ((count << 4) + 0x1007) & ~0xFFF


def strcode(name: str) -> int:
    """The 24-bit name hash, identical to the one the .PDT tables use."""
    h = 0
    for ch in name:
        if ch == '.':
            break
        h = (((h << 5) | (h >> 19)) + ord(ch)) & 0xFFFFFF
    return h


def ext_of(key: int) -> str:
    return EXT_BY_BYTE.get((key >> 24) & 0xFF, '')


class Element:
    __slots__ = ('key', 'offset', 'data')

    def __init__(self, key, offset, data):
        self.key = key
        self.offset = offset
        self.data = data          # the FULL padded region, see module docstring

    @property
    def ext(self) -> str:
        return ext_of(self.key)

    @property
    def code(self) -> int:
        return self.key & 0xFFFFFF

    def name(self) -> str:
        e = self.ext
        return '%06X%s' % (self.code, ('.' + e) if e else '')

    def __repr__(self):
        return '<%s %d bytes>' % (self.name(), len(self.data))


def parse(blob: bytes):
    """-> (elements, head_bytes, data_size).

    `head_bytes` is everything before the data region, kept verbatim so a
    rebuild can reproduce the filler the engine left there.
    """
    if len(blob) < 0x20:
        raise ValueError('too small (%d bytes)' % len(blob))
    count, zero = struct.unpack_from('<2I', blob, 0)
    if zero:
        raise ValueError('word at 0x04 is %#x, expected 0' % zero)
    marker, total = struct.unpack_from('<2Q', blob, 0x08)
    if marker != MARKER:
        raise ValueError('table marker %#x, expected %#x' % (marker, MARKER))

    base = data_base(count)
    rows, pos = [], 0x18
    while pos + 16 <= len(blob):
        key, val = struct.unpack_from('<2Q', blob, pos)
        pos += 16
        if key == END:
            break
        rows.append((key, val))
    if not rows:
        return [], blob[:base], total

    # an element runs to the next one's offset; the last runs to data_size
    bounds = [v for _k, v in rows] + [total]
    out = []
    for i, (key, off) in enumerate(rows):
        s, e = base + off, base + bounds[i + 1]
        out.append(Element(key, off, blob[s:min(e, len(blob))]))
    return out, blob[:base], total


def build(elements, head: bytes = b'') -> bytes:
    """Rebuild a `.slot` from elements.

    Offsets and the data size are recomputed, so an element may change length.
    Everything else - the filler, the element order - is preserved.
    """
    count = len(elements) + 3
    base = data_base(count)

    body, offsets, cur = bytearray(), [], 0
    for el in elements:
        offsets.append(cur)
        pad = (-len(el.data)) % ALIGN
        body += el.data + b'\x00' * pad
        cur += len(el.data) + pad
    total = len(body)

    out = bytearray(head[:base] if len(head) >= base else b'\x00' * base)
    struct.pack_into('<2I', out, 0, count, 0)
    struct.pack_into('<2Q', out, 0x08, MARKER, total)
    pos = 0x18
    for el, off in zip(elements, offsets):
        struct.pack_into('<2Q', out, pos, el.key, off)
        pos += 16
    struct.pack_into('<3Q', out, pos, END, total, 0)
    return bytes(out) + bytes(body)


def explode(blob: bytes, out_dir: str) -> list:
    """Write every element out, plus a manifest that `implode` reads back."""
    els, head, _total = parse(blob)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, '!header.bin'), 'wb') as fh:
        fh.write(head)
    lines = []
    for i, el in enumerate(els):
        name = '%03d_%s' % (i, el.name())
        with open(os.path.join(out_dir, name), 'wb') as fh:
            fh.write(el.data)
        lines.append('%016X\t%s' % (el.key, name))
    with open(os.path.join(out_dir, '!manifest.tsv'), 'w', encoding='utf-8') as fh:
        fh.write('key\tfile\n')
        fh.write('\n'.join(lines) + ('\n' if lines else ''))
    return els


def implode(src_dir: str) -> bytes:
    """The inverse of `explode`."""
    head = open(os.path.join(src_dir, '!header.bin'), 'rb').read()
    els = []
    with open(os.path.join(src_dir, '!manifest.tsv'), encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            line = line.rstrip('\n')
            if not line:
                continue
            key, name = line.split('\t')
            data = open(os.path.join(src_dir, name), 'rb').read()
            els.append(Element(int(key, 16), 0, data))
    return build(els, head)
