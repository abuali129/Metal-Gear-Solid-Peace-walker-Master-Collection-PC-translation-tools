r"""Rebuild a `.PDT` container - the write half of pdt.py / pdt_unpack.py.

## Why this can be byte-exact, which is not obvious

Both ciphers over an entity are **position-independent**:

  * layer 1 (Master Collection MT19937) restarts at the head of every entity,
    seeded from the container's file NAME only - the entity's offset is not an
    input;
  * layer 2 restarts from a FRESH state per entity, built from the three header
    keys - again no offset.

So an entity's stored bytes depend on its plaintext and nothing else.  Moving an
entity does not change a single byte of it, and an entity we did not touch can
be copied out of the original file **verbatim, still encrypted**, without ever
being decompressed and recompressed.

That matters, because recompression is not reproducible: zlib will not
generally reproduce the exact stream the original packer emitted.  Any packer
that inflates and re-deflates everything therefore cannot rebuild a container
byte-for-byte, and has no way to prove it did not corrupt something.  Copying
untouched entities makes an unedited repack **byte-identical to the original**,
which turns "is the packer correct?" into a test the machine can answer.

## Layout

    0x00                 u32 k1, u32 k2, u32 k3, u32 unk      - PLAINTEXT keys
    0x0C .. 0x28         header tail                          - layer 2
    0x28                 Entity[count] {u32 size, u32 hash, u32 pos}
    LookupStart          Lookup[count] {u32 key, u32 index, u32, u32}
    data_start           entity blobs, each 16-byte aligned

The header tail and both tables are one CONTINUOUS layer-2 stream, in that
order, and the whole head region is one layer-1 stream from offset 0.

The lookup table is copied from the template and never regenerated: its `key`
field is not a name hash (the stock values are small offsets like 0x18 and
0x48 alongside large ones), so a packer that fabricates it writes nonsense the
engine resolves resources by.
"""

from __future__ import annotations

import os
import struct
import zlib

import pdt
import pwcrypt

M = 0xFFFFFFFF

# Entities are laid out on 0x800 boundaries, not 0x10.  Measured on stock
# 009645fa.PDT: all 557 positions are 2048-aligned (270 of them are NOT
# 4096-aligned, so 2048 is the real granularity), the first sits at 0x5000
# rather than at the end of the tables, and the file itself is padded up to a
# 2048 boundary.  Packing tightly at 16 bytes builds a container 687 KB
# smaller than the original - it parses, but it is not what the game writes.
ALIGN = 0x800


def _align(n: int, a: int = ALIGN) -> int:
    return (n + a - 1) & ~(a - 1)


class Template:
    """The original container: keys, tables, and the raw bytes of every entity."""

    def __init__(self, path: str, name: str | None = None):
        self.path = path
        self.name = name or os.path.basename(path)
        self.raw = open(path, 'rb').read()
        dec = pwcrypt.decrypt(self.raw[:0x40000], self.name)
        hdr = pdt.read_header(dec)
        if not hdr['ok']:
            raise ValueError('%s: layer-2 magic missing - not a PDT' % self.name)
        need = hdr['lookup'] + hdr['count'] * 16
        dec = pwcrypt.decrypt(self.raw[:need + 4096], self.name)
        self.hdr = pdt.read_header(dec)
        self.ents = pdt.read_tables(dec, self.hdr)
        self.count = self.hdr['count']
        self.lookup_start = self.hdr['lookup']
        self.size = len(self.raw)
        # the head is one layer-1 stream running to the FIRST entity, padding
        # included - not merely to the end of the lookup table
        self.head_len = (min(e['pos'] for e in self.ents) if self.ents
                         else _align(self.lookup_start + self.count * 16))
        self.data_start = self.head_len

    def raw_blob(self, i: int) -> bytes:
        """One entity exactly as it is stored - both cipher layers intact."""
        e = self.ents[i]
        return self.raw[e['pos']:e['pos'] + e['size']]

    def payload(self, i: int) -> bytes:
        """One entity, decrypted and inflated."""
        return pdt.unpack_entity(self.raw, self.ents[i], self.hdr, self.name)[0]


def encode_entity(data: bytes, hdr, name: str, level: int = 9) -> bytes:
    """Plaintext -> the bytes as stored: zlib, layer 2, layer 1."""
    blob = bytearray(struct.pack('<I', len(data)) + zlib.compress(data, level))
    state, dec = pdt.fresh_cipher(hdr), hdr['dec']
    dec(blob, 0, len(blob), state)          # symmetric
    return pwcrypt.encrypt(bytes(blob), name)


def build(template: Template, payloads: dict, out_path: str,
          out_name: str | None = None, level: int = 9, progress=None) -> dict:
    """Write a new container.

    `payloads` maps entity index -> new plaintext.  Every index not present is
    copied from the template verbatim, still encrypted.
    """
    out_name = out_name or os.path.basename(out_path)
    same_name = (out_name.lower() == template.name.lower())

    # Entities keep their original offsets.  A blob only has to move if it
    # grew past the slack the layout already gives it, and then it goes after
    # the last entity rather than shifting its neighbours - positions are
    # free-form in the entity table, so nothing else needs touching, and an
    # unedited repack reproduces the original layout exactly.
    tail_pos = _align(max(e['pos'] + e['size'] for e in template.ents))
    room = {}
    for i, e in enumerate(template.ents):
        nxt = min((o['pos'] for o in template.ents if o['pos'] > e['pos']),
                  default=tail_pos)
        room[i] = nxt - e['pos']

    blobs, table, moved = [], [], 0
    for i in range(template.count):
        if i in payloads:
            blob = encode_entity(payloads[i], template.hdr, out_name, level)
        elif same_name:
            blob = template.raw_blob(i)      # untouched: copy as-is
        else:
            # the container's NAME keys layer 1, so a rename forces a re-wrap
            blob = pwcrypt.encrypt(
                pwcrypt.decrypt(template.raw_blob(i), template.name), out_name)
        pos = template.ents[i]['pos']
        if len(blob) > room[i]:
            pos = tail_pos
            tail_pos = _align(tail_pos + len(blob))
            moved += 1
        blobs.append((pos, blob))
        table.append((len(blob), template.ents[i]['hash'], pos))
        if progress and i % 50 == 0:
            progress(i, template.count)
    end = _align(max(p + len(b) for p, b in blobs))

    tail = bytearray(template.hdr['tail'])                      # decrypted
    ent_tab = bytearray(template.count * 12)
    for i, (size, hsh, p) in enumerate(table):
        struct.pack_into('<3I', ent_tab, i * 12, size & M, hsh & M, p & M)
    look = bytearray(template.count * 16)
    for i, e in enumerate(template.ents):
        struct.pack_into('<4I', look, i * 16,
                         e['key'] & M, e['index'] & M, e['u1'] & M, e['u2'] & M)

    # one continuous layer-2 stream over tail + entity table + lookup table
    state, dec = pdt.fresh_cipher(template.hdr), template.hdr['dec']
    dec(tail, 0, len(tail), state)
    dec(ent_tab, 0, len(ent_tab), state)
    dec(look, 0, len(look), state)

    # The head is rebuilt ON TOP OF the original's own decrypted bytes, and the
    # container is written as a COPY of the original rather than from nothing.
    # Neither the head padding nor the 556 gaps between entities is zero-filled
    # in a stock file - they hold leftover bytes from whatever buffer the
    # original packer reused - so a from-scratch build cannot reproduce them
    # and can never be proven byte-exact.  Patching a copy can, and does.
    head = bytearray(pwcrypt.decrypt(template.raw[:template.head_len],
                                     template.name))
    struct.pack_into('<4I', head, 0, template.hdr['k1'], template.hdr['k2'],
                     template.hdr['k3'], template.hdr['unk'])
    head[0x0C:0x28] = tail
    head[0x28:0x28 + len(ent_tab)] = ent_tab
    head[template.lookup_start:template.lookup_start + len(look)] = look

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    with open(template.path, 'rb') as src, open(out_path, 'wb') as fh:
        while True:
            chunk = src.read(1 << 24)
            if not chunk:
                break
            fh.write(chunk)
    with open(out_path, 'r+b') as fh:
        if end > template.size:
            fh.truncate(end)
        fh.seek(0)
        fh.write(pwcrypt.encrypt(bytes(head), out_name))
        for i, (p, blob) in enumerate(blobs):
            if i in payloads or p != template.ents[i]['pos']:
                fh.seek(p)
                fh.write(blob)
    return {'entities': template.count, 'rebuilt': len(payloads), 'moved': moved,
            'size': os.path.getsize(out_path), 'out': out_path}


def repack_dir(template_path: str, src_dir: str, out_path: str,
               out_name: str | None = None, level: int = 9,
               progress=None) -> dict:
    """Rebuild from a folder our `pdt_unpack.py` produced.

    Only entities whose bytes actually differ from the original are
    recompressed; everything else is copied, so an untouched folder rebuilds
    the container byte-for-byte.
    """
    tpl = Template(template_path)
    index = _read_contents(src_dir, tpl)
    payloads, changed = {}, 0
    for i, path in index.items():
        data = open(path, 'rb').read()
        if data != tpl.payload(i):
            payloads[i] = data
            changed += 1
        if progress and i % 50 == 0:
            progress(i, tpl.count)
    res = build(tpl, payloads, out_path, out_name, level)
    res['changed'] = changed
    res['found'] = len(index)
    return res


def _read_contents(src_dir: str, tpl: Template) -> dict:
    """index -> file path, from contents.tsv when present, else by prefix."""
    tsv = os.path.join(src_dir, 'contents.tsv')
    out = {}
    if os.path.isfile(tsv):
        with open(tsv, encoding='utf-8') as fh:
            next(fh, None)
            for line in fh:
                parts = line.rstrip('\n').split('\t')
                if len(parts) < 7:
                    continue
                name = parts[6].split('  ')[0]
                if name == 'FAILED':
                    continue
                p = os.path.join(src_dir, name)
                if os.path.isfile(p):
                    out[int(parts[0])] = p
        if out:
            return out
    for fn in os.listdir(src_dir):
        stem = fn.split('_', 1)[0]
        if stem.isdigit():
            p = os.path.join(src_dir, fn)
            if os.path.isfile(p):
                out.setdefault(int(stem), p)
    return out


def verify(template_path: str, out_path: str,
           template_name: str | None = None, out_name: str | None = None) -> dict:
    """Prove a rebuild: same entity count, and every payload inflates equal.

    Pass `out_name` when the file on disk is not called what it will be called
    in the game - the container's NAME keys layer 1, so reading a build under
    the wrong name fails at the magic check, which is a naming mistake and not
    a corrupt file.
    """
    a = Template(template_path, template_name)
    b = Template(out_path, out_name or template_name)
    if a.count != b.count:
        return {'ok': False, 'why': 'entity count %d vs %d' % (a.count, b.count)}
    same = diff = bad = 0
    for i in range(a.count):
        try:
            if a.payload(i) == b.payload(i):
                same += 1
            else:
                diff += 1
        except Exception:
            bad += 1
    return {'ok': diff == 0 and bad == 0, 'entities': a.count,
            'identical': same, 'different': diff, 'unreadable': bad,
            'byte_exact': open(template_path, 'rb').read() == open(out_path, 'rb').read()}
