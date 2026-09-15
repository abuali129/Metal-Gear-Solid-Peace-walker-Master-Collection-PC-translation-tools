r"""Peace Walker (PC, Master Collection) .PDT / .DAT container reader.

Layer cake, outermost first:

  1. MASTER COLLECTION   MT19937 keyed by the file NAME  -> pwcrypt.decrypt
  2. PSP CONTAINER       one of TWO ciphers (see below)
  3. PER-ENTITY          zlib, each entity separately

Layer 2 comes in two flavours and the header tells you which:

  HashKey2 == HashKey3 == 0   ->  XOR every dword of the body with
                                  (HashKey1 & 0xFF) repeated 4x
  otherwise                   ->  Hash(k1,k2,k3) + the LCG in
                                  DecryptionUtility.Decrypt, a running
                                  stream that continues across every read

Header (after layer 1, offsets in the file):

  0x00 u32 HashKey1        0x10 u32 magic 0x007A7E9C   <- proof of a good decrypt
  0x04 u32 HashKey2        0x14 u32 version 0x00010000
  0x08 u32 HashKey3        0x18 u16 EntityCount
  0x0C u32 ?               0x20 u32 LookupStart
                           0x28 Entity[count] {u32 size, u32 hash, u32 pos}
                LookupStart: LookupBlock[count] {u32 key, u32 index, u32, u32}
"""
from __future__ import annotations
import struct, zlib

M = 0xFFFFFFFF
MAGIC = 0x007A7E9C
VERSION = 0x00010000
LCG_A = 48828125


class Hash:
    """The PSP stream state, built from the three header keys."""
    __slots__ = ('Low', 'High')

    def __init__(self, h0, h1, h2):
        h0 = (h0 ^ h1) & M
        self.Low = (h0 * h2) & M
        self.High = (h0 | ((h0 ^ 0x6576) << 16)) & M


def _lcg(buf, off, length, h):
    """DecryptionUtility.Decrypt - in place, advances h.High."""
    off = (off + 3) & ~3
    length &= ~3
    n2 = h.High
    for p in range(off, off + length, 4):
        nxt = (h.Low + n2 * LCG_A) & M
        struct.pack_into('<I', buf, p,
                         (struct.unpack_from('<I', buf, p)[0] ^ n2) & M)
        n2 = nxt
    h.High = n2


class ConstXor:
    """The degenerate cipher: a fixed dword, stream position irrelevant."""
    __slots__ = ('word',)

    def __init__(self, k1):
        self.word = (k1 & 0xFF) * 0x01010101


def _const(buf, off, length, c):
    off = (off + 3) & ~3
    length &= ~3
    for p in range(off, off + length, 4):
        struct.pack_into('<I', buf, p,
                         (struct.unpack_from('<I', buf, p)[0] ^ c.word) & M)


def make_cipher(k1, k2, k3):
    """Pick the layer-2 cipher from the header keys."""
    if k2 == 0 and k3 == 0:
        return ConstXor(k1), _const
    return Hash(k1, k2, k3), _lcg


def read_header(dec):
    """dec = the layer-1-decrypted bytes (>= 0x28).  Returns a dict."""
    k1, k2, k3, unk = struct.unpack_from('<4I', dec, 0)
    st, dc = make_cipher(k1, k2, k3)
    body = bytearray(dec[12:0x28])
    dc(body, 0, len(body), st)
    magic, ver = struct.unpack_from('<2I', body, 4)
    count = struct.unpack_from('<H', body, 12)[0]
    lookup = struct.unpack_from('<I', body, 20)[0]
    return dict(k1=k1, k2=k2, k3=k3, unk=unk, magic=magic, version=ver,
                count=count, lookup=lookup, ok=(magic == MAGIC),
                state=st, dec=dc, tail=body)


def read_tables(dec, hdr):
    """Entity + lookup tables.  Continues the SAME stream, so this must run
    straight after read_header on the same header object."""
    st, dc, n = hdr['state'], hdr['dec'], hdr['count']
    ents = bytearray(dec[0x28:0x28 + n * 12])
    dc(ents, 0, len(ents), st)
    look = bytearray(dec[hdr['lookup']:hdr['lookup'] + n * 16])
    dc(look, 0, len(look), st)
    out = []
    for i in range(n):
        size, h, pos = struct.unpack_from('<3I', ents, i * 12)
        key, idx, u1, u2 = struct.unpack_from('<4I', look, i * 16)
        out.append(dict(size=size, hash=h, pos=pos, key=key, index=idx,
                        u1=u1, u2=u2))
    return out


def unpack_entity(raw, ent, hdr, name):
    """Decrypt + inflate one entity.  `raw` is the file's RAW bytes (layer 1
    NOT removed) and `name` is the container's file name.

    THE THING THAT TOOK LONGEST TO SEE: the Master Collection MT stream
    **restarts** at the beginning of every entity.  The header and the two
    tables are read back-to-back from offset 0 through one RNG, so a single
    positional stream decrypts them - but each entity is fetched by its own
    seek+read, and that read gets a fresh RNG (index 5 again).  Decrypting the
    whole file as one stream therefore yields perfect tables and garbage
    payloads, which is exactly the symptom that stalls people here.

    Layer 2 then runs with a FRESH cipher state per entity - the state is
    copied, never carried forward - and the plaintext is `u32
    uncompressed_size` followed by a zlib stream.
    """
    import pwcrypt
    seg = pwcrypt.decrypt(bytes(raw[ent['pos']:ent['pos'] + ent['size']]), name)
    blob = bytearray(seg)
    st, dc = fresh_cipher(hdr), hdr['dec']
    dc(blob, 0, len(blob), st)
    raw_len = struct.unpack_from('<I', blob, 0)[0]
    return zlib.decompress(bytes(blob[4:])), raw_len


def fresh_cipher(hdr):
    """A layer-2 state reset to its initial value - what each entity gets."""
    return make_cipher(hdr['k1'], hdr['k2'], hdr['k3'])[0]
