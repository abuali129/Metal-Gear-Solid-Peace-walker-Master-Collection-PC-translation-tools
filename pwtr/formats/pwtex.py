r"""Self-contained DDS / block-compression helpers for the Peace Walker tools.

These routines were originally written for the MGS4 project and lived in
`E:\Localization\MGS4\tools\pctext`.  They are vendored here on purpose: the
Peace Walker tools ship as a standalone release, and an absolute path into a
sibling project would break on anyone else's machine.

Only what the PW pipeline needs is kept:
    level_size / level_sizes   DDS mip arithmetic
    decode_bc                  BC1 (DXT1) and BC3 (DXT5) -> RGBA
    ink_map                    premultiplied luminance, the "is there art here"
                               measure that works for both storage styles
    row_period / cell_grid / gap_score / looks_like_atlas
                               the structural, typeface-blind glyph-grid test

NOTE ON COLUMN COUNT.  The detector assumes **32** columns, which is what MGS4
uses.  Peace Walker's own UI sheets are **16** columns, so they only trip the
`loose=True` path.  Treat a hit as "worth looking at", not as proof.
"""
from __future__ import annotations

import numpy as np

DXT1, DXT5, RGBA32 = 0x09, 0x0B, 0x03
NAMES = {DXT1: 'DXT1', DXT5: 'DXT5', RGBA32: 'RGBA32'}

COLS = 32
AZ = slice(1, 27)               # columns of row 1 holding 'A'..'Z'
MIN_GAP = 1.25                  # lowest real atlas 1.47, highest foliage 1.01


# ------------------------------------------------------------------- dds --

def level_size(w: int, h: int, fmt: int) -> int:
    if fmt in (DXT1, DXT5):
        return (max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
                * (8 if fmt == DXT1 else 16))
    return w * h * 4


def level_sizes(w: int, h: int, fmt: int):
    out = []
    while True:
        out.append(level_size(w, h, fmt))
        if w == 1 and h == 1:
            return out
        w, h = max(1, w // 2), max(1, h // 2)


# ---------------------------------------------------------------- decode --

def _c565(v):
    r = ((v >> 11) & 31).astype(np.uint16)
    g = ((v >> 5) & 63).astype(np.uint16)
    b = (v & 31).astype(np.uint16)
    return (((r * 255 + 15) // 31).astype(np.uint8),
            ((g * 255 + 31) // 63).astype(np.uint8),
            ((b * 255 + 15) // 31).astype(np.uint8))


def decode_bc(data: bytes, w: int, h: int, fmt: int) -> np.ndarray:
    """BC1 (0x09) / BC3 (0x0B) -> (h, w, 4) uint8, vectorised."""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    step = 8 if fmt == DXT1 else 16
    need = bw * bh * step
    buf = np.frombuffer(data[:need].ljust(need, b'\0'), np.uint8)
    blocks = buf.reshape(bw * bh, step)
    col = blocks[:, step - 8:]

    c0 = col[:, 0].astype(np.uint16) | (col[:, 1].astype(np.uint16) << 8)
    c1 = col[:, 2].astype(np.uint16) | (col[:, 3].astype(np.uint16) << 8)
    r0, g0, b0 = _c565(c0)
    r1, g1, b1 = _c565(c1)
    n = len(blocks)
    pal = np.zeros((n, 4, 4), np.uint8)
    pal[:, 0] = np.stack([r0, g0, b0, np.full(n, 255, np.uint8)], 1)
    pal[:, 1] = np.stack([r1, g1, b1, np.full(n, 255, np.uint8)], 1)
    four = (c0 > c1) | (fmt == DXT5)
    for k, (wa, wb) in ((2, (2, 1)), (3, (1, 2))):
        rr = ((wa * r0.astype(np.uint16) + wb * r1) // 3).astype(np.uint8)
        gg = ((wa * g0.astype(np.uint16) + wb * g1) // 3).astype(np.uint8)
        bb = ((wa * b0.astype(np.uint16) + wb * b1) // 3).astype(np.uint8)
        pal[four, k] = np.stack([rr, gg, bb, np.full(n, 255, np.uint8)], 1)[four]
    if (~four).any():
        rr = ((r0.astype(np.uint16) + r1) // 2).astype(np.uint8)
        gg = ((g0.astype(np.uint16) + g1) // 2).astype(np.uint8)
        bb = ((b0.astype(np.uint16) + b1) // 2).astype(np.uint8)
        mid = np.stack([rr, gg, bb, np.full(n, 255, np.uint8)], 1)
        pal[~four, 2] = mid[~four]
        pal[~four, 3] = 0

    idx = (col[:, 4].astype(np.uint32) | (col[:, 5].astype(np.uint32) << 8)
           | (col[:, 6].astype(np.uint32) << 16)
           | (col[:, 7].astype(np.uint32) << 24))
    sel = np.zeros((n, 16), np.uint8)
    for p in range(16):
        sel[:, p] = (idx >> (2 * p)) & 3
    px = pal[np.arange(n)[:, None], sel]

    if fmt == DXT5:
        a0 = blocks[:, 0].astype(np.int32)
        a1 = blocks[:, 1].astype(np.int32)
        bits = np.zeros(n, np.uint64)
        for k in range(6):
            bits |= blocks[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
        atab = np.zeros((n, 8), np.uint8)
        atab[:, 0] = a0
        atab[:, 1] = a1
        gt = a0 > a1
        for i in range(1, 7):
            atab[gt, i + 1] = ((7 - i) * a0[gt] + i * a1[gt]) // 7
        le = ~gt
        for i in range(1, 5):
            atab[le, i + 1] = ((5 - i) * a0[le] + i * a1[le]) // 5
        atab[le, 6] = 0
        atab[le, 7] = 255
        asel = np.zeros((n, 16), np.uint8)
        for p in range(16):
            asel[:, p] = ((bits >> np.uint64(3 * p)) & np.uint64(7)).astype(np.uint8)
        px[:, :, 3] = atab[np.arange(n)[:, None], asel]

    out = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4)
    return np.ascontiguousarray(out.reshape(bh * 4, bw * 4, 4))[:h, :w]


# ------------------------------------------------------------- structure --

def ink_map(rgba):
    """Premultiplied luminance: equals alpha for white-on-transparent sheets
    and luminance for sheets that keep the glyphs in colour with no alpha."""
    lum = (rgba[:, :, :3].astype(np.uint32).sum(axis=2) // 3).astype(np.float32)
    return lum * (rgba[:, :, 3].astype(np.float32) / 255.0)


def row_period(ink, thr):
    """Cell height from the row-ink profile.  Take the SMALLEST period whose
    rows agree - the 2x/3x harmonics often correlate better, so a plain argmax
    returns 72 for a font whose rows are 24 px tall."""
    prof = (ink > 8).mean(axis=1)
    nz = np.nonzero(prof > 0.002)[0]
    if len(nz) < 8:
        return None
    p = prof[:nz[-1] + 1].astype(np.float64)
    for ch in range(6, len(p) // 2 + 1):
        rows = len(p) // ch
        if rows < 2:
            break
        m = p[:rows * ch].reshape(rows, ch)
        m = m - m.mean(axis=1, keepdims=True)
        nrm = np.linalg.norm(m, axis=1)
        if (nrm < 1e-9).any():
            continue
        u = m / nrm[:, None]
        c = u @ u.T
        if (c.sum() - rows) / (rows * (rows - 1)) > thr:
            return ch
    return None


def cell_grid(ink, ch, maxrows=6, cols=COLS):
    h, w = ink.shape
    cw = w // cols
    rows = min(maxrows, h // ch)
    return np.array([[(ink[r * ch:(r + 1) * ch, k * cw:(k + 1) * cw] > 8).mean()
                      for k in range(cols)] for r in range(rows)])


def gap_score(ink, ch, cols=COLS):
    """How much emptier a cell's edges are than its middle.  Foliage masks are
    periodic in both axes and pass every other test; what they lack is the
    gutter a font leaves at the cell boundary."""
    h, w = ink.shape
    cw = w // cols
    rows = min(6, h // ch)
    band = (ink[:rows * ch] > 8).mean(axis=0)
    fold = band[:cols * cw].reshape(cols, cw).mean(axis=0)
    if fold.max() < 1e-6:
        return 0.0
    edge = max(1, cw // 8)
    outer = float(np.concatenate([fold[:edge], fold[-edge:]]).mean())
    inner = float(fold[edge:-edge].mean()) if cw > 2 * edge else float(fold.mean())
    return inner / (outer + 1e-6)


def looks_like_atlas(ink, loose=False, cols=COLS):
    """-> (cell_height, rows, az_hits) if this looks like a glyph grid."""
    ch = row_period(ink, 0.60 if loose else 0.75)
    if ch is None:
        return None
    g = cell_grid(ink, ch, cols=cols)
    if g.shape[0] < 2:
        return None
    az = int((g[1, AZ] > 0.02).sum())
    if loose:
        return (ch, g.shape[0], az) if az >= 20 else None
    if g[0, 0] > 0.06 or az < 26 or gap_score(ink, ch, cols) < MIN_GAP:
        return None
    return ch, g.shape[0], az
