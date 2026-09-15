r"""The Master Collection SLOT container - `002aba34.DAT` + `002aba34.KEY`.

This is the PSP `SLOT.DAT`/`SLOT.KEY` pair after Konami re-wrapped it for PC.
It holds the half of the script that is not in the shipped `.olang` files, so
it is the container that matters for translation.

THREE LAYERS, and each one has a trap in it:

  1. Master Collection MT19937 (`pwcrypt`), keyed by the *file name the game
     knows* - `002aba34.DAT` / `002aba34.KEY`, never the local path.  For the
     KEY it is one pass over the whole file.  For the DAT the stream
     **RESTARTS AT EVERY BLOCK**, exactly as it restarts per entity in a
     `.PDT` (see pdt.unpack_entity).  Decrypting the DAT as one continuous
     500 MB stream gives a perfect first block and garbage after it.

  2. An affine word cipher over the block:

         word ^= acc ;  acc = acc * 0x02E90EDD + low

     seeded from the KEY's 12-byte header.  The state restarts at every
     block, so one keystream serves all 2137 of them.  This is the same
     recurrence the MGS4 engine uses for its stage pages - see the MGS4
     project's `codecstream.py`; the constant 0x02E90EDD and the 0x6576 salt
     twist are shared between the two games.

  3. zlib, behind a 16-byte block header
     `{u32 0x00100004, u32 0, u32 csize, u32 usize}`.

## The KEY record, and the field everyone gets wrong

20 bytes, little-endian:

    u32 rawStart   (tag << 20) | startPage
    u32 rawEnd     (pageCount << 20) | endPage
    u32 hash       block IDENTITY - see below
    u32 tag        resource type, == rawStart >> 20
    u32 pageCount  pages of CONTENT

`pageCount` is not the block's footprint.  `endPage - startPage` is.  Measured
on the stock MLG container: `startPage..endPage` tiles the whole file - 132 947
pages, **zero holes and zero overlaps**, ending exactly at the DAT's last page -
while `startPage..startPage+pageCount` appears to leave 13 447 pages (~52 MB)
unused.  Those pages are **not free space**; they are the zero padding inside
each block's own allocation.  Treating them as a free list and relocating other
blocks into them writes one block inside another's span, and the KEY's records
stop tiling - which matters because the game binary-searches them by page.

So: the budget for rewriting a block in place is `endPage - startPage`, which is
*more* room than `pageCount` for 389 of the 2 137 blocks, and there is no free
list at all.  A block that outgrows its footprint can only go after the last
page.

## The hash is an identity, not a checksum

Verified here, not assumed: over 401 stock blocks the record hash matches
CRC32 of the inflated data 0 times and CRC32 of the zlib bytes 0 times.  The
game resolves resources by it.  **Never recompute it** - stale hash plus fresh
bytes works, a "fixed" hash loses models and textures.
"""

from __future__ import annotations

import json
import os
import struct
import zlib

try:
    import numpy as np
except ImportError:                                    # pragma: no cover
    np = None

import pwcrypt

M = 0xFFFFFFFF
PAGE = 0x1000
KEY_HEADER = 12

#: The four u32 in front of a block's deflate stream.
BLOCK_HEADER = 16

#: Whether the slow encoder may be used.  On where it is installed: it is only
#: reached for a block zlib could not pack, which is a handful per build, and
#: the alternative is leaving that block in English.  ``PWTR_ZOPFLI=0`` turns
#: it off and restores the behaviour from before it was wired in.
USE_ZOPFLI = os.environ.get('PWTR_ZOPFLI', '1') != '0'

#: Whether this python's zlib is really zlib-ng.  It compresses a few per cent
#: worse, which is the difference between a block fitting its footprint and
#: not -- measured on three of them, see :func:`build_block_smallest`.
ZLIB_IS_NG = 'zlib-ng' in zlib.ZLIB_RUNTIME_VERSION
KEY_RECORD = 20
BLOCK_MAGIC = 0x00100004
MUL = 0x02E90EDD
SALT_TWIST = 0x6576

# The names the engine keys its cipher on.  The files may sit anywhere; these
# strings are part of the key and must not follow the local path.
DAT_NAME = '002aba34.DAT'
KEY_NAME = '002aba34.KEY'


# --------------------------------------------------------------------------
# layer 2
# --------------------------------------------------------------------------

class WordStream:
    """`word ^= acc; acc = acc*MUL + low`, cached.

    Every block restarts from the same seed, so the keystream depends only on
    the offset inside a block and one table serves the whole container.  The
    closed form of the recurrence is

        acc_k = high * MUL^k + low * (MUL^0 + ... + MUL^(k-1))

    which is a cumulative product and a cumulative sum - both of which wrap
    mod 2^32 natively in uint32, so the vector form is exact, not an
    approximation of the scalar loop.
    """

    def __init__(self, high: int, low: int):
        self.high = high & M
        self.low = low & M
        self._ks = None

    def _table(self, n: int):
        if self._ks is not None and len(self._ks) >= n:
            return self._ks
        pw = np.empty(n, dtype=np.uint32)
        pw[0] = 1
        if n > 1:
            pw[1:] = np.cumprod(np.full(n - 1, MUL, dtype=np.uint32))
        run = np.empty(n, dtype=np.uint32)
        run[0] = 0
        if n > 1:
            run[1:] = np.cumsum(pw[:-1], dtype=np.uint32)
        self._ks = (self.high * pw + self.low * run).astype(np.uint32)
        return self._ks

    def apply(self, data: bytes) -> bytes:
        """Symmetric - the same call encrypts and decrypts."""
        n = len(data) // 4
        if not n:
            return data
        if np is not None:
            ks = self._table(n)
            head = (np.frombuffer(data, dtype='<u4', count=n) ^ ks[:n])
            return head.astype('<u4').tobytes() + data[n * 4:]
        out = bytearray(data)
        acc = self.high
        for o in range(0, n * 4, 4):
            w = int.from_bytes(out[o:o + 4], 'little') ^ acc
            out[o:o + 4] = w.to_bytes(4, 'little')
            acc = (acc * MUL + self.low) & M
        return bytes(out)


def seed_from_key(header: bytes):
    """The 12-byte KEY header -> (high, low) for the word stream."""
    k1, k2, k3 = struct.unpack_from('<3I', header, 0)
    h0 = (k1 ^ k2) & M
    high = (h0 | ((h0 ^ SALT_TWIST) << 16)) & M
    low = (h0 * k3) & M
    return high, low


# --------------------------------------------------------------------------
# the KEY
# --------------------------------------------------------------------------

class Record:
    __slots__ = ('index', 'start', 'end', 'hash', 'tag', 'content_pages')

    def __init__(self, index, start, end, hsh, tag, content_pages):
        self.index = index
        self.start = start
        self.end = end
        self.hash = hsh
        self.tag = tag
        self.content_pages = content_pages

    @property
    def footprint(self) -> int:
        """Pages this block actually owns - NOT `content_pages`."""
        return self.end - self.start

    @property
    def offset(self) -> int:
        return self.start * PAGE

    def pack(self) -> bytes:
        return struct.pack('<5I',
                           ((self.tag & 0xFFF) << 20) | (self.start & 0xFFFFF),
                           ((self.content_pages & 0xFFF) << 20) | (self.end & 0xFFFFF),
                           self.hash & M, self.tag & 0xFFF, self.content_pages)

    def __repr__(self):
        return ('<slot %4d page %05X+%-4d (content %d) hash %08X tag %d>'
                % (self.index, self.start, self.footprint,
                   self.content_pages, self.hash, self.tag))


def load_key(path: str, encrypted: bool = True):
    """-> (header_bytes, (high, low), [Record]).

    `encrypted` strips the Master Collection layer; pass False for a KEY that
    has already been through it.
    """
    raw = open(path, 'rb').read()
    if encrypted:
        raw = pwcrypt.crypt(raw, KEY_NAME)
    if len(raw) < KEY_HEADER + KEY_RECORD or (len(raw) - KEY_HEADER) % KEY_RECORD:
        raise ValueError('%s: %d bytes is not a 12 + n*20 KEY' % (path, len(raw)))

    n = (len(raw) - KEY_HEADER) // KEY_RECORD
    recs = []
    for i in range(n):
        rs, re_, hsh, tag, pc = struct.unpack_from('<5I', raw, KEY_HEADER + i * KEY_RECORD)
        recs.append(Record(i, rs & 0xFFFFF, re_ & 0xFFFFF, hsh, tag, pc))

    # Validate EVERY record, not a sample: a still-encrypted KEY parses into
    # noise whose page numbers turn into multi-gigabyte reads.
    bad = [r for r in recs
           if r.tag != (r.start | (r.tag << 20)) >> 20
           or r.footprint <= 0 or r.footprint > 0xFFF
           or r.content_pages == 0 or r.content_pages > r.footprint]
    if bad:
        raise ValueError('%s: %d of %d records are nonsense - is this the '
                         'encrypted KEY, or was `encrypted` set wrong?'
                         % (path, len(bad), n))
    return raw[:KEY_HEADER], seed_from_key(raw), recs


def check_layout(recs, dat_size: int = 0) -> dict:
    """Prove the container tiles, and report the real free space (there is
    none in stock, which is the whole point)."""
    spans = sorted((r.start, r.end) for r in recs)
    overlap = sum(1 for a, b in zip(spans, spans[1:]) if b[0] < a[1])
    covered = sum(e - s for s, e in spans)
    last = spans[-1][1] if spans else 1
    holes = (last - 1) - covered
    out = {'blocks': len(recs), 'overlaps': overlap, 'pages': covered,
           'last_page': last, 'holes': holes}
    if dat_size:
        out['tiles_file'] = (last * PAGE == dat_size)
    return out


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------

def read_block(dat, rec: Record, stream: WordStream) -> bytes:
    """Decrypt + inflate one block.  `dat` is an open binary file."""
    dat.seek(rec.offset)
    blob = dat.read(rec.content_pages * PAGE)
    if len(blob) < 16:
        raise ValueError('%r: short read' % rec)
    plain = stream.apply(pwcrypt.crypt(blob, DAT_NAME))
    magic, zero, csize, usize = struct.unpack_from('<4I', plain, 0)
    if magic != BLOCK_MAGIC:
        raise ValueError('%r: block magic %08X' % (rec, magic))
    data = zlib.decompress(bytes(plain[16:16 + csize]))
    if len(data) != usize:
        raise ValueError('%r: inflated %d, header claims %d' % (rec, len(data), usize))
    return data


#: Deflate settings to try when a block will not fit.  All produce ordinary
#: zlib streams, so the game inflates them without knowing the difference --
#: they only differ in how hard the encoder looks.  Z_FILTERED at the largest
#: memLevel wins often enough to matter: a block of 1 374 pages comes back at
#: 1 371, which is the difference between shipping a scene and not.
HARDER = tuple((mem, strategy, wbits)
               for wbits in (15, 14, 13)
               for mem in (9, 8)
               for strategy in (zlib.Z_FILTERED, zlib.Z_DEFAULT_STRATEGY,
                                zlib.Z_RLE))


def build_block(data: bytes, level: int = 9) -> bytes:
    """Plaintext of a block: the 16-byte header plus the zlib stream."""
    z = zlib.compress(data, level)
    return struct.pack('<4I', BLOCK_MAGIC, 0, len(z), len(data)) + z


def _zopfli(data: bytes) -> bytes | None:
    """A deflate stream from zopfli, or None if it is not installed.

    Zopfli emits ordinary deflate -- any inflater reads it, the game's
    included -- and spends a hundred times the effort to do it, which is
    where the last few per cent live.  It is optional because it is a
    dependency, and slow enough that nothing should reach for it casually.

    Two packages answer to the name and they do not share an interface.
    ``zopfli`` exposes ``zopfli.zlib.compress``; ``zopflipy`` exposes a
    ``ZopfliCompressor`` at the top level and no ``zopfli.zlib`` at all.  A
    machine can easily have one on one interpreter and the other on another --
    this one does -- and importing only the first is how the fallback came to
    be silently absent on 3.11 while working on 3.14.
    """
    if not USE_ZOPFLI:
        return None
    try:
        from zopfli import zlib as zopfli_zlib          # zopfli
    except ImportError:
        pass
    else:
        try:
            return zopfli_zlib.compress(data)
        except Exception:
            return None
    try:
        import zopfli                                    # zopflipy
        engine = zopfli.ZopfliCompressor(zopfli.ZOPFLI_FORMAT_ZLIB)
        return engine.compress(data) + engine.flush()
    except Exception:
        return None


def build_block_smallest(data: bytes, limit: int = 0) -> bytes:
    """The same block, encoded as tightly as this machine can manage.

    Only worth calling when the ordinary encoding does not fit: it compresses
    the block several times over, which on a five megabyte block is not free.
    Some blocks come back larger than they went in with nothing translated at
    all -- this is what buys those back.

    How many depends on the interpreter, which is not a detail.  CPython 3.14
    bundles **zlib-ng**, which trades ratio for speed, and three blocks that
    reproduce comfortably under the stock zlib 1.2.12 of 3.11 overrun their
    footprint under it::

                    3.11 / zlib 1.2.12      3.14 / zlib-ng 1.3.1
        0007F       265635  fits            266405  over by 165
        003AB       143232  fits            143634  over by 274
        00611        40884  fits             41034  over by  74

    So "this block cannot be rebuilt" can mean nothing more than which python
    is running.  :data:`ZLIB_IS_NG` says which, and zopfli settles it either
    way -- it beats both.

    ``limit`` is the payload size that has to be met, header included.  Pass
    it and zopfli is tried when zlib cannot reach it -- and only then, since
    it costs a minute or two a block where zlib costs a second.  Without it
    the zlib attempts stand, which is what every caller wanted before there
    was anything slower to fall back on.
    """
    best = zlib.compress(data, 9)
    for mem, strategy, wbits in HARDER:
        engine = zlib.compressobj(9, zlib.DEFLATED, wbits, mem, strategy)
        candidate = engine.compress(data) + engine.flush()
        if len(candidate) < len(best):
            best = candidate
    if limit and len(best) + BLOCK_HEADER > limit:
        candidate = _zopfli(data)
        if candidate is not None and len(candidate) < len(best):
            best = candidate
    # A smaller window can pack better than the default and still decodes
    # anywhere: the window size travels in the zlib header, so an inflater
    # with a full 32K window reads a 8K-window stream without being told.
    # Checked rather than assumed, because a block that will not inflate is
    # a scene that will not play.
    if zlib.decompress(best) != data:
        raise ValueError('a tighter encoding did not round-trip')
    return struct.pack('<4I', BLOCK_MAGIC, 0, len(best), len(data)) + best


def write_block(dat, rec: Record, payload: bytes, stream: WordStream,
                pages: int = 0) -> None:
    """Encrypt `payload` into the block at `rec`, zero-padded to `pages`."""
    pages = pages or rec.footprint
    if len(payload) > pages * PAGE:
        raise ValueError('%r: payload %d exceeds %d pages' % (rec, len(payload), pages))
    plain = payload + b'\x00' * (pages * PAGE - len(payload))
    dat.seek(rec.start * PAGE)
    dat.write(stream.apply(pwcrypt.crypt(plain, DAT_NAME)))


def pages_for(payload: bytes) -> int:
    return -(-len(payload) // PAGE)


# --------------------------------------------------------------------------
# whole-container operations
# --------------------------------------------------------------------------

def unpack(dat_path: str, key_path: str, out_dir: str,
           key_encrypted: bool = True, progress=None) -> dict:
    """Every block to `<startPage>_<hash>.slot`, plus an index."""
    header, (high, low), recs = load_key(key_path, key_encrypted)
    stream = WordStream(high, low)
    os.makedirs(out_dir, exist_ok=True)

    entries, ok, failed = [], 0, []
    with open(dat_path, 'rb') as dat:
        for r in recs:
            try:
                data = read_block(dat, r, stream)
            except Exception as exc:
                failed.append((r.index, str(exc)))
                continue
            name = '%05X_%08X.slot' % (r.start, r.hash)
            with open(os.path.join(out_dir, name), 'wb') as fh:
                fh.write(data)
            entries.append({'file': name, 'index': r.index, 'page': r.start,
                            'end': r.end, 'tag': r.tag, 'hash': r.hash,
                            'content_pages': r.content_pages})
            ok += 1
            if progress and ok % 100 == 0:
                progress(ok, len(recs))

    with open(os.path.join(out_dir, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump({'key_header': list(struct.unpack('<3I', header)),
                   'dat_size': os.path.getsize(dat_path),
                   'entries': entries}, fh, indent=1)
    return {'ok': ok, 'total': len(recs), 'failed': failed,
            'layout': check_layout(recs, os.path.getsize(dat_path))}


def splice(dat_path: str, key_path: str, src_dir: str, out_dir: str,
           key_encrypted: bool = True, dry_run: bool = False,
           progress=None) -> dict:
    """Write back only the blocks whose `.slot` file changed.

    The safe path, and the only one we ship: the stock DAT is copied and the
    changed blocks are laid over their own pages.  The KEY is copied byte for
    byte, so every hash and every page number stays stock.  A block that no
    longer fits its footprint is reported, never silently relocated - there is
    no free list to relocate into (see the module docstring).
    """
    header, (high, low), recs = load_key(key_path, key_encrypted)
    stream = WordStream(high, low)
    by_index = {r.index: r for r in recs}

    meta = json.load(open(os.path.join(src_dir, 'index.json'), encoding='utf-8'))
    unchanged = changed = overflow = 0
    over_list, plan = [], []

    with open(dat_path, 'rb') as dat:
        for n, ent in enumerate(meta['entries']):
            r = by_index[ent['index']]
            path = os.path.join(src_dir, ent['file'])
            if not os.path.isfile(path):
                unchanged += 1
                continue
            data = open(path, 'rb').read()
            if data == read_block(dat, r, stream):
                unchanged += 1
                continue
            payload = build_block(data)
            need = pages_for(payload)
            if need > r.footprint:
                overflow += 1
                over_list.append({'file': ent['file'], 'need': need,
                                  'budget': r.footprint,
                                  'bytes_over': len(payload) - r.footprint * PAGE})
                continue
            plan.append((r, payload))
            changed += 1
            if progress:
                progress(n + 1, len(meta['entries']))

    result = {'unchanged': unchanged, 'changed': changed,
              'overflow': overflow, 'overflow_list': over_list}
    if dry_run:
        return result

    os.makedirs(out_dir, exist_ok=True)
    out_dat = os.path.join(out_dir, os.path.basename(DAT_NAME))
    with open(dat_path, 'rb') as src, open(out_dat, 'wb') as dst:
        while True:
            chunk = src.read(1 << 24)
            if not chunk:
                break
            dst.write(chunk)
    with open(out_dat, 'r+b') as dst:
        for r, payload in plan:
            write_block(dst, r, payload, stream)

    # the KEY is never rebuilt - copy the stock bytes
    with open(key_path, 'rb') as src, \
            open(os.path.join(out_dir, os.path.basename(KEY_NAME)), 'wb') as dst:
        dst.write(src.read())
    result['out_dat'] = out_dat
    return result
