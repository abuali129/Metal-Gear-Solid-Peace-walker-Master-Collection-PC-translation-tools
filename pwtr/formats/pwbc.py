r"""BC1 / BC3 (DXT1 / DXT5) block encoding, and surgical patching.

`pwtex.decode_bc` reads these; this writes them.

## Why patch blocks instead of re-encoding the sheet

Re-encoding a whole texture with our compressor and writing it back would
change every one of its blocks, because no two BC encoders pick the same
endpoints.  The file would still load, but nothing could then be proven: a
diff against stock would be 100% noise and any real corruption would hide in
it.

So `patch_rect` re-encodes only the 4x4 blocks a glyph actually covers and
leaves every other block's original bytes untouched.  The font grid cooperates:
cells are 28x48 at x = 0, 28, 56 ... and y = 0, 48, 96 ..., and every one of
those is a multiple of 4, so a cell boundary is always a block boundary and no
edit ever bleeds into a neighbouring glyph.

## The formats

BC1, 8 bytes per 4x4:  u16 c0, u16 c1 (RGB565), then 16 x 2-bit indices.
    c0 > c1  -> 4 colours, c2 = (2*c0 + c1)/3, c3 = (c0 + 2*c1)/3
    c0 <= c1 -> 3 colours + transparent black
BC3, 16 bytes: an alpha block then a BC1 colour block.
    u8 a0, u8 a1, then 16 x 3-bit indices, little-endian across 6 bytes.
    a0 > a1  -> 8 alphas, evenly spaced
    a0 <= a1 -> 6 alphas plus hard 0 and 255

A font sheet is white glyphs carried entirely by alpha, so the colour half
encodes exactly (both endpoints white, all indices 0) and only the alpha half
is ever lossy - and BC4-style alpha with 8 levels is close to lossless for
anti-aliased type.
"""

from __future__ import annotations

import numpy as np

# Take the format codes from pwtex rather than inventing our own.  They are the
# game's values (0x09 / 0x0B), not 1 and 5, and a private copy that disagrees
# is not a harmless duplicate: `patch_rect` picks its stride from this constant
# while `encode_block` picks its length from the same one, so a mismatch writes
# 16-byte blocks at an 8-byte stride and quietly shreds the texture.
from pwtex import DXT1, DXT5


def _rgb565(rgb):
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _unpack565(v):
    r = ((v >> 11) & 0x1F) * 255 // 31
    g = ((v >> 5) & 0x3F) * 255 // 63
    b = (v & 0x1F) * 255 // 31
    return np.array([r, g, b], dtype=np.int32)


def encode_alpha_block(a: np.ndarray) -> bytes:
    """16 alpha values (4x4, row-major) -> the 8-byte BC3 alpha block."""
    a = a.reshape(-1).astype(np.int32)
    lo, hi = int(a.min()), int(a.max())
    if lo == hi:
        # a flat block: a0 == a1 selects the 6-value mode, where index 6 is a
        # hard 0 and index 7 a hard 255 - so a solid 0 or 255 block is exact,
        # and any other flat value is carried by a0 itself at index 0.
        return bytes([lo, lo, 0, 0, 0, 0, 0, 0])
    a0, a1 = hi, lo                       # a0 > a1 -> 8 evenly spaced values
    ramp = np.array([a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)],
                    dtype=np.int32)
    idx = np.abs(a[:, None] - ramp[None, :]).argmin(axis=1).astype(np.uint64)
    bits = 0
    for i, v in enumerate(idx):
        bits |= int(v) << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, 'little')


def encode_color_block(rgb: np.ndarray, allow_alpha: bool = False) -> bytes:
    """16 RGB triples (4x4, row-major) -> the 8-byte BC1 colour block."""
    px = rgb.reshape(-1, 3).astype(np.int32)
    lum = px @ np.array([299, 587, 114])
    lo_i, hi_i = int(lum.argmin()), int(lum.argmax())
    c0v, c1v = _rgb565(px[hi_i]), _rgb565(px[lo_i])
    if c0v == c1v:
        return c0v.to_bytes(2, 'little') + c1v.to_bytes(2, 'little') + b'\0\0\0\0'
    if c0v < c1v:
        c0v, c1v = c1v, c0v
    c0, c1 = _unpack565(c0v), _unpack565(c1v)
    ramp = np.stack([c0, c1, (2 * c0 + c1) // 3, (c0 + 2 * c1) // 3])
    d = ((px[:, None, :] - ramp[None, :, :]) ** 2).sum(axis=2)
    idx = d.argmin(axis=1)
    bits = 0
    for i, v in enumerate(idx):
        bits |= int(v) << (2 * i)
    return (c0v.to_bytes(2, 'little') + c1v.to_bytes(2, 'little')
            + bits.to_bytes(4, 'little'))


def encode_block(rgba: np.ndarray, fmt: int = DXT5) -> bytes:
    """One 4x4 RGBA block -> 8 (BC1) or 16 (BC3) bytes."""
    color = encode_color_block(rgba[..., :3])
    if fmt == DXT1:
        return color
    return encode_alpha_block(rgba[..., 3]) + color


def encode(rgba: np.ndarray, fmt: int = DXT5) -> bytes:
    """A whole RGBA image -> BC data.  Dimensions must be multiples of 4."""
    h, w = rgba.shape[:2]
    if h % 4 or w % 4:
        raise ValueError('%dx%d is not a multiple of 4' % (w, h))
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            out += encode_block(rgba[by:by + 4, bx:bx + 4], fmt)
    return bytes(out)


def block_index(x: int, y: int, w: int) -> int:
    return (y // 4) * (w // 4) + (x // 4)


def patch_rect(data: bytes, w: int, h: int, x: int, y: int,
               rgba: np.ndarray, fmt: int = DXT5) -> bytes:
    """Re-encode ONLY the blocks covered by `rgba` placed at (x, y).

    Every other block keeps its original bytes, so a diff against the stock
    texture shows exactly the glyphs that were edited and nothing else.
    """
    bh, bw = rgba.shape[:2]
    if x % 4 or y % 4 or bh % 4 or bw % 4:
        raise ValueError('rect %dx%d at (%d,%d) is not 4-aligned' % (bw, bh, x, y))
    stride = 16 if fmt == DXT5 else 8
    out = bytearray(data)
    for ry in range(0, bh, 4):
        for rx in range(0, bw, 4):
            bi = block_index(x + rx, y + ry, w)
            off = bi * stride
            if off + stride > len(out):
                continue
            out[off:off + stride] = encode_block(rgba[ry:ry + 4, rx:rx + 4], fmt)
    return bytes(out)


def blocks_touched(w: int, x: int, y: int, bw: int, bh: int) -> set:
    return {block_index(x + rx, y + ry, w)
            for ry in range(0, bh, 4) for rx in range(0, bw, 4)}
