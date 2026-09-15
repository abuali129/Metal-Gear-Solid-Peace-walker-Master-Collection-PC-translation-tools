r"""Decrypt/encrypt Metal Gear Solid: Peace Walker (PC, Master Collection Vol.2) files.

    python pwcrypt.py decrypt <file> [out]
    python pwcrypt.py encrypt <file> [out]   same cipher, for putting files back
    python pwcrypt.py all                    decrypt every known type into decrypted\
    python pwcrypt.py strings <file>         decrypt and print the text
    python pwcrypt.py verify [dir]           round-trip EVERY .olang/.txp/.xpr
    python pwcrypt.py selftest               numpy fast path == reference slow path

The Master Collection wraps the original PSP files in TWO layers:

  OUTER   MT19937 keyed by a hash of the FILE NAME, `*dword ^= mt_next() ^
          0xB9D3018F`.  This is the only layer `.olang`, `.txp` and `.xpr` use.
  INNER   an LCG stream (`state = state*48828125 + low`) whose three seed words
          sit at the start of the file.  Save files and .PDT use it; see pdt.py.

Everything below was read out of the unpacked exe (SteamStub removed):

    FUN_14010F440   name hash      h = h*0x2356F + (int8)c*0x1D35
                                   start after the last / : or \, skip one
                                   leading '_', stop at '.'
    FUN_14010EFF0   MT seed        1998 sgenrand: mt[k] = (s & 0xFFFF0000) |
                                   ((s = s*69069+1) >> 16), twice per word,
                                   THEN a full twist + temper, so a block is
                                   ready and the index is 0
    FUN_14010ED80   skip(0x14)     index = 5   <- the stream starts at word 5
    FUN_14010F240   block          standard MT19937 twist and tempering
    FUN_14010F5B0   apply          *dword ^= mt_next() ^ 0xB9D3018F

TAIL BYTES.  A file whose length is not a multiple of 4 (most .olang are
length % 4 == 2) has 1-3 bytes past the last whole dword.  They are NOT left
alone: one further keystream word is drawn and the leftover bytes are XORed
with its bytes from the LOW byte upward, i.e.

    tail[j] ^= ((mt_next() ^ 0xB9D3018F) >> (8*j)) & 0xFF

That is exactly what the dword path would have done had the file been padded,
so the cipher is just "XOR the file with a byte stream", truncated.

SYMMETRY.  The keystream depends only on the file name, never on the data, so
encrypt and decrypt are the same operation.  `encrypt` is an alias kept for
callers that want to say what they mean.

SPEED.  The keystream is produced with numpy: the 624-word twist is done with
four slice operations (the lag between a written word and the word that reads
it back is 227, so the block splits at 227 / 454 / 623) and the tempering is
applied a whole 32 MB chunk at a time.  Measured 60 MB/s: the 547 MB
`0058ced6.pdt` decrypts in 9 s.  The old `mt_stream()` would have had to build
a python list of 136 789 504 ints (~4.5 GB) for that one file and then walk it
a dword at a time.  It is kept anyway, unchanged, because it is the oracle the
fast path is checked against - see `selftest`.

MEMORY.  `crypt()` takes and returns whole `bytes`, so it holds the input, the
output buffer and the returned copy at once - measured 1.68 GB of working set
on the 547 MB `0058ced6.pdt`.  `crypt_file()` / `roundtrip_file()` / `Stream`
go 32 MB at a time and stay flat: 232 MB for the same file, and that figure
does not grow with the file.  Every CLI command uses the streaming path.

Proof it is right: every `.olang` decrypts to a header whose first word is
`0x00584252` = "RBX" and whose second word equals the file's own name in hex
(`00327f6a.olang` -> `0x00327F6A`), and whose entry table runs exactly from the
offset at 0x10 to the offset at 0x14.  Proof the TAIL is right, which no round
trip can show because it is symmetric either way: all 11 odd-length `.olang`
then end on a complete UTF-8 character followed by its NUL terminator
(`...です。\0`, `...square>\0`).  Leave the tail alone and you get `...です` +
two junk bytes; drop the `0xB9D3018F` and the last byte is always 0x01.
"""

from __future__ import annotations

import os
import re
import struct
import sys

import pwpaths
import time

try:
    import numpy as np
except ImportError:                                   # pragma: no cover
    np = None

M = 0xFFFFFFFF
MAGIC_RBX = 0x00584252
SKIP = 5                      # FUN_14010ED80(rng, 0x14) leaves the index at 5
XOR = 0xB9D3018F

N = 624
MT_M = 397
UPPER = 0x80000000
LOWER = 0x7FFFFFFF
MATRIX = 0x9908B0DF
LCG_A = 69069

CHUNK_WORDS = 1 << 23         # 8M words = 32 MB per streaming step


# ---------------------------------------------------------------- name hash

def name_hash(name: str) -> int:
    """FUN_14010F440 - hash of the basename, extension excluded.

    A single leading '_' is dropped first: that is the marker convention for a
    file that has been pulled out and put back, and the game hashes the bare
    stem.  Every stock asset is a bare hex stem, so this changes nothing for
    them."""
    for sep in ('/', '\\', ':'):
        name = name.rsplit(sep, 1)[-1]
    if name.startswith('_'):
        name = name[1:]
    h = 0
    for ch in name:
        if ch == '.':
            break
        c = ord(ch)
        if c > 127:
            c -= 256                      # the code sign-extends the byte
        h = (h * 0x2356F + c * 0x1D35) & M
    return h


# ------------------------------------------------------ MT19937, reference

def _mt_init(seed: int) -> list:
    """FUN_14010EFF0 - the 1998 sgenrand fill, two LCG steps per word."""
    mt = [0] * N
    s = seed & M
    for k in range(N):
        v = s & 0xFFFF0000
        s = (LCG_A * s + 1) & M
        v |= (s & 0xFFFF0000) >> 16
        s = (LCG_A * s + 1) & M
        mt[k] = v
    return mt


def mt_stream(seed: int, count: int) -> list:
    """MT19937, seeded the 1998 way, already twisted once - see FUN_14010EFF0.

    Reference implementation: slow, pure python, no dependencies.  It is the
    oracle the numpy path is verified against; production code uses
    `mt_stream_np`."""
    mt = _mt_init(seed)
    out = []
    while len(out) < count:
        for k in range(N):
            y = (mt[k] & UPPER) | (mt[(k + 1) % N] & LOWER)
            t = mt[(k + MT_M) % N] ^ (y >> 1)
            if y & 1:
                t ^= MATRIX
            mt[k] = t
        for k in range(N):
            y = mt[k]
            y ^= y >> 11
            y ^= (y << 7) & 0x9D2C5680
            y &= M
            y ^= (y << 15) & 0xEFC60000
            y &= M
            y ^= y >> 18
            out.append(y & M)
    return out


# ----------------------------------------------------------- MT19937, fast

_U32 = np.uint32 if np is not None else None


def _temper_np(a):
    """Vectorised MT19937 tempering, in place on a uint32 array."""
    a ^= a >> _U32(11)
    a ^= (a << _U32(7)) & _U32(0x9D2C5680)
    a ^= (a << _U32(15)) & _U32(0xEFC60000)
    a ^= a >> _U32(18)
    return a


def _twist_np(mt):
    """One full 624-word twist, in place, four slices.

    The scalar loop writes mt[k] and a later iteration reads mt[k+227] back,
    so the block cannot be done as one slice.  The lag is 227, which gives the
    split points 0..227, 227..454, 454..623 and the wrap-around word 623."""
    up, lo, mag = _U32(UPPER), _U32(LOWER), _U32(MATRIX)

    # k = 0 .. 226   : reads mt[k+397], all still old
    y = (mt[0:227] & up) | (mt[1:228] & lo)
    mt[0:227] = mt[397:624] ^ (y >> _U32(1)) ^ ((y & _U32(1)) * mag)

    # k = 227 .. 453 : reads mt[k-227] = mt[0..226], written by the slice above
    y = (mt[227:454] & up) | (mt[228:455] & lo)
    mt[227:454] = mt[0:227] ^ (y >> _U32(1)) ^ ((y & _U32(1)) * mag)

    # k = 454 .. 622 : reads mt[k-227] = mt[227..395], written just above
    y = (mt[454:623] & up) | (mt[455:624] & lo)
    mt[454:623] = mt[227:396] ^ (y >> _U32(1)) ^ ((y & _U32(1)) * mag)

    # k = 623        : wraps onto the already-updated mt[0]
    y = (mt[623] & up) | (mt[0] & lo)
    mt[623] = mt[396] ^ (y >> _U32(1)) ^ ((y & _U32(1)) * mag)
    return mt


class MTFast:
    """The game's MT19937 as a chunked numpy keystream.

    Construction reproduces FUN_14010EFF0 exactly: fill, then a twist so a
    block is ready with the index at 0."""

    __slots__ = ('mt', 'buf', 'pos')

    def __init__(self, seed: int):
        if np is None:                                # pragma: no cover
            raise RuntimeError('numpy is required for the fast path')
        self.mt = np.array(_mt_init(seed), dtype=np.uint32)
        self.buf = None
        self.pos = N                                  # forces the first twist

    def _refill(self):
        _twist_np(self.mt)
        self.buf = _temper_np(self.mt.copy())
        self.pos = 0

    def words(self, count: int):
        """The next `count` tempered words as a uint32 array."""
        out = np.empty(count, dtype=np.uint32)
        done = 0
        while done < count:
            if self.pos >= N:
                self._refill()
            take = min(N - self.pos, count - done)
            out[done:done + take] = self.buf[self.pos:self.pos + take]
            self.pos += take
            done += take
        return out

    def skip(self, count: int):
        """Discard `count` words - FUN_14010ED80."""
        while count:
            if self.pos >= N:
                self._refill()
            take = min(N - self.pos, count)
            self.pos += take
            count -= take


def mt_stream_np(seed: int, count: int):
    """Same values as `mt_stream`, as a numpy uint32 array."""
    return MTFast(seed).words(count)


# --------------------------------------------------------------- the cipher

class Stream:
    """The name-keyed keystream, applied to a file in order.

    Feed the file in pieces; every piece but the last must be a whole number
    of dwords.  Only the last piece may carry the ragged 1-3 byte tail, and
    once it has, the stream is spent."""

    __slots__ = ('rng', 'spent')

    def __init__(self, name: str):
        self.rng = MTFast(name_hash(name))
        self.rng.skip(SKIP)              # FUN_14010ED80(rng, 0x14)
        self.spent = False

    def feed(self, block) -> bytes:
        if self.spent:
            raise ValueError('the ragged tail was already consumed; a short '
                             'block must be the last one')
        n = len(block)
        nwords = n // 4
        rem = n - nwords * 4
        if rem:
            self.spent = True
        out = bytearray(n)
        off = 0
        done = 0
        while done < nwords:
            take = min(CHUNK_WORDS, nwords - done)
            src = np.frombuffer(block, dtype='<u4', count=take, offset=off)
            ks = self.rng.words(take)
            ks ^= _U32(XOR)
            out[off:off + take * 4] = (src ^ ks).astype('<u4').tobytes()
            off += take * 4
            done += take
        if rem:
            # one more keystream word, leftover bytes from its LOW byte up
            e = int(self.rng.words(1)[0]) ^ XOR
            for j in range(rem):
                out[off + j] = block[off + j] ^ ((e >> (8 * j)) & 0xFF)
        return bytes(out)


def crypt(data: bytes, name: str) -> bytes:
    """XOR `data` with the name-keyed keystream.  Symmetric: this is both the
    decryptor and the encryptor.  Peaks at 2x len(data) - see `crypt_file`."""
    if not data:
        return b''
    return Stream(name).feed(data)


def crypt_file(src: str, dst: str, name: str = None,
               chunk: int = CHUNK_WORDS * 4) -> bytes:
    """Streaming src -> dst.  `name` defaults to the DESTINATION's basename,
    because the name the file is stored under is what keys it.  Returns the
    first 0x200 bytes of the result, which is enough for `identify`."""
    if name is None:
        name = os.path.basename(dst)
    st = Stream(name)
    head = b''
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        while True:
            block = fi.read(chunk)
            if not block:
                break
            piece = st.feed(block)
            if not head:
                head = piece[:0x200]
            fo.write(piece)
    return head


def roundtrip_file(path: str, name: str = None,
                   chunk: int = CHUNK_WORDS * 4):
    """decrypt -> re-encrypt, streaming, comparing against the bytes on disk
    as it goes.  Nothing is written.  Returns (size, head, ok, ndiff)."""
    if name is None:
        name = os.path.basename(path)
    dec_s, enc_s = Stream(name), Stream(name)
    head = b''
    size = ndiff = 0
    with open(path, 'rb') as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            dec = dec_s.feed(block)
            back = enc_s.feed(dec)
            if not head:
                head = dec[:0x200]
            if back != block:
                ndiff += sum(1 for a, b in zip(block, back) if a != b)
            size += len(block)
    return size, head, ndiff == 0, ndiff


def crypt_slow(data: bytes, name: str) -> bytes:
    """`crypt` written against the reference `mt_stream`.  Only used to prove
    the fast path; unusably slow past a few MB."""
    total = len(data)
    nwords = total // 4
    rem = total - nwords * 4
    st = mt_stream(name_hash(name), nwords + SKIP + (1 if rem else 0))
    out = bytearray(data)
    for i in range(nwords):
        e = (st[SKIP + i] ^ XOR) & M
        v = int.from_bytes(out[i * 4:i * 4 + 4], 'little') ^ e
        out[i * 4:i * 4 + 4] = (v & M).to_bytes(4, 'little')
    if rem:
        e = (st[SKIP + nwords] ^ XOR) & M
        for j in range(rem):
            out[nwords * 4 + j] ^= (e >> (8 * j)) & 0xFF
    return bytes(out)


def decrypt(data: bytes, name: str) -> bytes:
    """Peel the outer layer off `data`.  `name` is the file name the game knows
    it by - only the stem matters."""
    return crypt(data, name)


def encrypt(data: bytes, name: str) -> bytes:
    """Put the outer layer back on.  Identical maths to `decrypt`; the cipher
    is a name-keyed XOR stream, so it is its own inverse."""
    return crypt(data, name)


# -------------------------------------------------------------- inspection

def looks_decrypted(d: bytes, name: str) -> bool:
    """True for a plausibly decrypted .olang: 'RBX' plus the file's own
    strcode."""
    if len(d) < 8:
        return False
    magic, strcode = struct.unpack_from('<2I', d, 0)
    stem = os.path.splitext(os.path.basename(name))[0]
    if stem.startswith('_'):
        stem = stem[1:]
    try:
        want = int(stem, 16)
    except ValueError:
        want = None
    return magic == MAGIC_RBX and (want is None or strcode == want)


def _entropy(d: bytes) -> float:
    if not d:
        return 0.0
    if np is not None:
        c = np.bincount(np.frombuffer(d, dtype=np.uint8), minlength=256)
        p = c[c > 0] / len(d)
        return float(-(p * np.log2(p)).sum())
    return 8.0


def identify(d: bytes, name: str, size: int = None) -> str:
    """A one-line verdict on a decrypted blob, per file type.

    The checks are structural, not magic-word spotting: each format states a
    count and a table offset, and the one has to land on the other.  `d` may
    be just the head of the file; pass `size` when it is, so the offsets can
    be range-checked against the real length."""
    if size is None:
        size = len(d)
    ext = os.path.splitext(name)[1].lower()
    if len(d) < 0x30:
        return 'too short'
    w = struct.unpack_from('<12I', d, 0)
    stem = os.path.splitext(os.path.basename(name))[0].lstrip('_')
    try:
        own = int(stem, 16)
    except ValueError:
        own = None

    if ext == '.olang':
        # 0x00 'RBX' | 0x04 own strcode | 0x0C count<<16 | 0x10 table start
        # | 0x14 what follows it - so table start + 8*count must equal it.
        if w[0] != MAGIC_RBX:
            return 'NO RBX (first word 0x%08X)' % w[0]
        cnt = w[3] >> 16
        fits = (w[4] + 8 * cnt == w[5])
        return ('RBX, strcode 0x%08X%s, %d entries, table 0x%X..0x%X %s'
                % (w[1], '' if own is None or w[1] == own else ' (NOT own name!)',
                   cnt, w[4], w[5], 'consistent' if fits else 'INCONSISTENT'))

    if ext == '.txp':
        # 0x00 flags | 0x04 strcode | 0x08 master count | 0x0C sub count
        # 0x18 master table (always 0x30) | 0x20 sub table | 0x28 pixel start
        # master entries are 32 bytes on the PC build (20 on PSP).
        master, sub = w[2], w[3]
        mtab, stab, pix = w[6], w[8], w[10]
        fits = (mtab == 0x30 and stab == mtab + 32 * master
                and 0 < master < 0x10000 and stab < pix <= size)
        who = ('own' if own is not None and w[1] == own
               else 'set 0x%08X' % w[1])
        return ('TXP %s: %d master / %d sub, tables 0x%X/0x%X, pixels 0x%X %s'
                % (who, master, sub, mtab, stab, pix,
                   'consistent' if fits else 'INCONSISTENT'))

    if ext == '.xpr':
        # 'XPR2' container, then four-char section tags with a size each.
        tags = [d[i:i + 4].decode('latin-1')
                for i in (0x00, 0x10, 0x28)]
        names = re.findall(rb'[A-Za-z][A-Za-z0-9_]{3,}', d[:0x200])
        return ('XPR2 container: sections %s, names %s'
                % ('/'.join(tags),
                   b' '.join(names[:4]).decode('latin-1') or '-'))             if tags[0] == 'XPR2' else             ('NOT XPR2 (first word 0x%08X, entropy %.2f)'
             % (w[0], _entropy(d[:1 << 20])))

    return ' '.join('%08X' % x for x in w[:4])


# ------------------------------------------------------------------- CLI

GAME = pwpaths.find_game() or ''
OUT = os.path.join(pwpaths.default_output(), 'decrypted')
EXTS = ('.olang', '.txp', '.xpr', '.pdt')
ROUNDTRIP_EXTS = ('.olang', '.txp', '.xpr')


def cmd_decrypt(path, out=None):
    """The SOURCE name keys a decrypt.  Streamed, so file size is no object."""
    out = out or os.path.join(OUT, os.path.basename(path) + '.bin')
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    head = crypt_file(path, out, os.path.basename(path))
    print('%s  ->  %s   %s' % (os.path.basename(path), out,
                               identify(head, path, os.path.getsize(out))))
    return head


def cmd_encrypt(path, out=None):
    """Re-encrypt.  The name the result will be STORED under decides the key,
    so `out` (when given) names the key, not the input file.  A '.bin' left
    over from `decrypt` is dropped, and so is a leading '_'."""
    out = out or os.path.join(OUT, os.path.basename(path) + '.enc')
    key_name = os.path.basename(out)
    if key_name.endswith('.bin'):
        key_name = key_name[:-4]
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    crypt_file(path, out, key_name)
    print('%s  ->  %s   (ключ = %s -> 0x%08X)'
          % (os.path.basename(path), out, key_name, name_hash(key_name)))
    return out


def cmd_all():
    n = ok = 0
    for root, _, files in os.walk(GAME):
        for fn in sorted(files):
            if not fn.lower().endswith(EXTS):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(root, GAME)
            dst = os.path.join(OUT, rel, fn + '.bin')
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            head = crypt_file(p, dst, fn)          # streamed, key = source name
            n += 1
            ok += looks_decrypted(head, fn)
    print('розшифровано %d файлів, з них із магією RBX: %d' % (n, ok))
    print('-> %s' % OUT)


def cmd_strings(path):
    d = decrypt(open(path, 'rb').read(), os.path.basename(path))
    rx = re.compile(rb'[\x20-\x7e][\x20-\x7e\x80-\xff]{3,}')
    seen = 0
    for m in rx.finditer(d):
        try:
            s = m.group().decode('utf-8')
        except UnicodeDecodeError:
            continue
        print(s)
        seen += 1
    print('\n-- рядків: %d' % seen, file=sys.stderr)


def cmd_verify(root=None):
    """decrypt -> re-encrypt -> must be byte-identical to the file on disk.

    This is the deliverable that says a repack is safe.  It streams, so it
    costs the same memory on the 80 MB .txp as on the 2 KB .olang, and it
    writes nothing anywhere."""
    root = root or GAME
    bad = []
    total = nbytes = 0
    t0 = time.time()
    print('%-34s %10s %4s  %-9s %s' % ('file', 'bytes', 'r%4', 'round-trip',
                                       'header'))
    for dirpath, _, files in os.walk(root):
        for fn in sorted(files):
            if not fn.lower().endswith(ROUNDTRIP_EXTS):
                continue
            p = os.path.join(dirpath, fn)
            size, head, ok, ndiff = roundtrip_file(p, fn)
            total += 1
            nbytes += size
            if not ok:
                bad.append((p, ndiff))
            print('%-34s %10d %4d  %-9s %s'
                  % (os.path.relpath(p, root), size, size % 4,
                     'OK' if ok else 'MISMATCH', identify(head, fn, size)))
    dt = time.time() - t0
    print('\n%d файлів, %.1f МБ, %s, %.1f s'
          % (total, nbytes / 1e6,
             'усі round-trip байт-у-байт' if not bad
             else '%d ЗЛАМАНИХ' % len(bad), dt))
    for p, ndiff in bad:
        print('  !! %s  (%d різних байтів)' % (p, ndiff))
    return not bad


def cmd_selftest():
    """The numpy path must agree with the pure-python reference, bit for bit."""
    okall = True

    seed = name_hash('00327f6a.olang')
    want = 4096                       # 6.5 blocks - crosses several twists
    ks_slow = mt_stream(seed, want)   # rounds up to whole 624-word blocks
    ks_fast = mt_stream_np(seed, want).tolist()
    same = ks_slow[:want] == ks_fast
    okall &= same
    print('keystream %d words (%d twists): %s'
          % (want, -(-want // N), 'OK' if same else 'MISMATCH'))

    for src, tag in ((os.path.join(GAME, 'MLG', 'Text', '00327f6a.olang'),
                      'olang 2 KB, len%4=2'),
                     (os.path.join(GAME, 'MLG', 'Text', '009c9ea4.olang'),
                      'olang 398 KB'),
                     (os.path.join(GAME, 'Text', '0083be4a.txp'),
                      'txp 83 KB'),
                     (os.path.join(GAME, 'FONT', '000ebbe8.xpr'),
                      'xpr 2.2 MB'),
                     (os.path.join(GAME, 'FONT', '0007ccd8.xpr'),
                      'xpr, first 4 MB + 3')):
        if not os.path.exists(src):
            print('%-24s пропущено (нема файла)' % tag)
            continue
        raw = open(src, 'rb').read()[:(4 << 20) + 3]
        fast = crypt(raw, os.path.basename(src))
        slow = crypt_slow(raw, os.path.basename(src))
        same = fast == slow
        okall &= same
        print('%-24s %8d B  fast==slow: %s' % (tag, len(raw),
                                               'OK' if same else 'MISMATCH'))
    # Chunking must not shift the keystream: many small feeds == one big feed.
    src = os.path.join(GAME, 'MLG', 'Text', '009c9ea4.olang')
    if os.path.exists(src):
        raw = open(src, 'rb').read()
        one = crypt(raw, os.path.basename(src))
        st = Stream(os.path.basename(src))
        parts = []
        pos = 0
        for step in (4, 12, 1024, 65536, 131072):       # all multiples of 4
            if pos >= len(raw):
                break
            parts.append(st.feed(raw[pos:pos + step]))
            pos += step
        parts.append(st.feed(raw[pos:]))                # the ragged tail
        same = b''.join(parts) == one
        okall &= same
        print('chunked feed == one-shot (%d B, uneven blocks): %s'
              % (len(raw), 'OK' if same else 'MISMATCH'))

    # The tail policy cannot be proved by a round trip (it is symmetric either
    # way), so prove it on content: every .olang is a UTF-8 string blob and
    # must end on a complete character followed by its NUL terminator.  Leave
    # the tail alone, or drop the 0xB9D3018F, and this fails on every file.
    n = good = 0
    for dp, _, fs in os.walk(GAME):
        for fn in sorted(fs):
            if not fn.lower().endswith('.olang'):
                continue
            raw = open(os.path.join(dp, fn), 'rb').read()
            if len(raw) % 4 == 0:
                continue
            n += 1
            d = decrypt(raw, fn)
            tail_ok = d.endswith(b'\x00')
            try:
                d[d.rindex(b'\x00', 0, len(d) - 1) + 1:-1].decode('utf-8')
            except (UnicodeDecodeError, ValueError):
                tail_ok = False
            good += tail_ok
    okall &= (n == good)
    print('tail bytes: %d/%d odd-length .olang end on a whole UTF-8 char + NUL'
          % (good, n))

    # the '_' convention must not change the key
    same = name_hash('_00327f6a.olang') == name_hash('00327f6a.olang')
    okall &= same
    print('leading underscore ignored by the hash: %s'
          % ('OK' if same else 'MISMATCH'))

    # edge cases: empty and sub-dword files must not blow up
    for size in (0, 1, 2, 3, 4, 5):
        blob = bytes(range(size))
        same = crypt(crypt(blob, 'x.olang'), 'x.olang') == blob
        okall &= same
        if not same:
            print('edge case %d bytes: MISMATCH' % size)
    print('edge cases 0..5 bytes: round-trip OK')

    print('selftest: %s' % ('усе збігається' if okall else 'Є РОЗБІЖНОСТІ'))
    return okall


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if cmd == 'all':
        cmd_all()
    elif cmd == 'decrypt':
        cmd_decrypt(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == 'encrypt':
        cmd_encrypt(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == 'strings':
        cmd_strings(sys.argv[2])
    elif cmd == 'verify':
        sys.exit(0 if cmd_verify(sys.argv[2] if len(sys.argv) > 2 else None)
                 else 1)
    elif cmd == 'selftest':
        sys.exit(0 if cmd_selftest() else 1)
    else:
        print(__doc__)
