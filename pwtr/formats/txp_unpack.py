r"""Unpack the Metal Gear Solid: Peace Walker (PC) .txp texture packages.

    python txp_unpack.py              every .txp the game ships
    python txp_unpack.py <file.txp>   one of them (encrypted or already plain)

Output: `output\TXP_TEXTURES\<stem>` beside the program
    NN_WxH_FMT.png      every embedded texture, decoded -- edit these
    NN_WxH_FMT.dds      the same texture untouched, only when `dds=True`
    NN_WxH__INK.png     the font-like ones rendered black-on-white
    contents.tsv        index, with the font verdict per texture

PNG is what comes out, because a DDS is not something most editors will
open, and the packer takes the PNG back: every texture in this game is DXT5
with no mipmaps, so re-encoding one lands on exactly the byte count its slot
holds.  The .dds is kept only when asked for -- it is the same pixels, and
writing both doubles a set that already runs to hundreds of megabytes.

FORMAT
======
A .txp in `Text\` or `loading\` is a standalone asset, so the Master Collection
outer cipher has to come off first (pwcrypt).  The ones inside STAGEDAT.PDT are
already plain by the time you have them.

    0x00  u32  flags
    0x04  u32  strcode        NOTE: this is the name of the SET, not of the
                              file - 005302d4/005318e4/005318e5/0082988a/
                              008299c5 all carry 0x0024E502
    0x08  u32  count
    0x0C  u32  count (again)
    0x18  u32  master table offset   (always 0x30)
    0x20  u32  sub table offset      (always 0x30 + 32*count)
    0x30  master[count], 32 bytes each
    ...   then the payload: `count` DDS files, back to back

The master records have not been fully decoded and are not needed to get the
pixels out - every texture carries a real `DDS ` header, so scanning for the
magic and trusting the DDS dimensions is exact and much simpler.

THE FONTS
=========
Peace Walker has THREE font systems, and this is the third one:
  1. FONT\*.xpr           - XPR2, 8-bit atlas + codepoint charmap: SUBTITLES
  2. db_font / fontprint  - inside STAGEDAT.PDT, DDS embedded in a raw blob
  3. these .txp           - DDS atlases, e.g. 008299c5 texture 10

Texture 10 of 008299c5 is the canonical UI sheet: 512x512, **16 columns**,
cell 32x48, `cell = code - 0x20`.  Rows 0-3 cover U+0020..U+005F and row 4
jumps straight to U+00C0 - there are **no lowercase letters at all**, which is
exactly the "caps-only UI font" that .olang marks with Style 0x0001.
"""
from __future__ import annotations

import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
from PIL import Image

import pwpaths
import pwcrypt
import pwtex as D
import pwtex as H
from pwtex import decode_bc

GAME = pwpaths.find_game() or ''
OUT_ROOT = os.path.join(pwpaths.default_output(), 'TXP_TEXTURES')


def textures(d: bytes):
    """Every embedded DDS: (index, offset, w, h, fourcc, payload_len)."""
    out = []
    for k, m in enumerate(re.finditer(b'DDS ', d)):
        i = m.start()
        h, w = struct.unpack_from('<2I', d, i + 12)
        fcc = d[i + 84:i + 88]
        if fcc not in (b'DXT1', b'DXT3', b'DXT5'):
            continue
        fmt = D.DXT1 if fcc == b'DXT1' else D.DXT5
        n = D.level_size(w, h, fmt)
        if i + 128 + n > len(d) or not (0 < w <= 8192 and 0 < h <= 8192):
            continue
        out.append((k, i, w, h, fcc, n, fmt))
    return out


def unpack(path, out_root=OUT_ROOT, dds=False, png=True):
    stem = os.path.splitext(os.path.basename(path))[0]
    raw = open(path, 'rb').read()
    d = raw if raw[:4] == b'DDS ' or b'DDS ' in raw[:4096] else \
        pwcrypt.decrypt(raw, os.path.basename(path))
    if b'DDS ' not in d[:2 * 1024 * 1024]:
        d = raw                                   # already plain after all
    od = os.path.join(out_root, stem)
    os.makedirs(od, exist_ok=True)
    tex = textures(d)
    rows = []
    fonts = 0
    for k, i, w, h, fcc, n, fmt in tex:
        stem_name = '%02d_%dx%d_%s' % (k, w, h, fcc.decode())
        if dds:
            open(os.path.join(od, stem_name + '.dds'), 'wb').write(
                d[i:i + 128 + n])
        rgba = decode_bc(d[i + 128:i + 128 + n], w, h, fmt)
        if png:
            Image.fromarray(rgba, 'RGBA').save(
                os.path.join(od, stem_name + '.png'))
        ink = H.ink_map(rgba)
        got = H.looks_like_atlas(ink) or H.looks_like_atlas(ink, loose=True)
        verdict = ''
        if got:
            verdict = 'font? ch=%d rows=%d az=%d' % got
            fonts += 1
            Image.fromarray(255 - ink.astype(np.uint8), 'L').save(
                os.path.join(od, '%02d_%dx%d__INK.png' % (k, w, h)))
        rows.append((k, i, w, h, fcc.decode(), n, verdict))
    with open(os.path.join(od, 'contents.tsv'), 'w', encoding='utf-8') as fh:
        fh.write('index\toffset\twidth\theight\tformat\tbytes\tverdict\n')
        for r in rows:
            fh.write('%d\t%d\t%d\t%d\t%s\t%d\t%s\n' % r)
    print('%-16s %3d textures, %2d font-like  -> %s' % (stem, len(tex), fonts, od),
          flush=True)
    return rows


def main():
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        unpack(sys.argv[1])
        return
    todo = []
    for sub in ('Text', 'loading'):
        d = os.path.join(GAME, sub)
        if os.path.isdir(d):
            todo += [os.path.join(d, n) for n in sorted(os.listdir(d))
                     if n.lower().endswith('.txp')]
    for p in todo:
        unpack(p)


if __name__ == '__main__':
    main()
