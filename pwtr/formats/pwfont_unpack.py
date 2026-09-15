r"""Unpack the Metal Gear Solid: Peace Walker (PC) FONT/*.xpr files completely.

    python pwfont_unpack.py            unpacks both fonts from the game
    python pwfont_unpack.py <out_dir>

For each font it writes, under FONT_UNPACKED\<stem>_<TAG>\:

    <stem>.xpr.bin      the decrypted XPR2 container (pwcrypt layer removed)
    FontTexture.raw     the raw 8-bit alpha atlas, width*height bytes
    FontData.bin        the raw USER block (charmap + glyph records)
    atlas.png           the atlas, rendered black-on-white so it is visible
    atlas_preview.png   the same at 1/4 size, for a quick look
    sheet_all.png       a contact sheet of every mapped glyph, 32 per row
    glyphs.tsv          one row per glyph record
    charmap.tsv         one row per mapped code point
    coverage.txt        what exists, per Unicode block
    glyphs\U+XXXX.png   one PNG per mapped code point

Nothing is ever written inside the game directory.
"""
from __future__ import annotations

import os
import sys
import unicodedata

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pwpaths
import pwcrypt
import pwfont

GAME = pwpaths.find_game() or ''
FONT_DIR = os.path.join(GAME, 'FONT')
OUT_ROOT = os.path.join(pwpaths.default_output(), 'FONT_UNPACKED')

TAG = {'0007ccd8': 'JP', '000ebbe8': 'LATIN'}


def uname(c):
    try:
        return unicodedata.name(chr(c))
    except ValueError:
        return ''


def unpack(src, out_root):
    stem = os.path.splitext(os.path.basename(src))[0]
    raw = open(src, 'rb').read()
    dec = pwcrypt.decrypt(raw, os.path.basename(src))
    if dec[:4] != b'XPR2':
        raise SystemExit('%s: decrypted to %r, not XPR2' % (stem, dec[:4]))

    out = os.path.join(out_root, '%s_%s' % (stem, TAG.get(stem, 'FONT')))
    os.makedirs(os.path.join(out, 'glyphs'), exist_ok=True)
    open(os.path.join(out, stem + '.xpr.bin'), 'wb').write(dec)

    f = pwfont.XprFont(dec)
    a = f.atlas()
    open(os.path.join(out, 'FontTexture.raw'), 'wb').write(a.tobytes())
    u = f.res['USER']
    open(os.path.join(out, 'FontData.bin'), 'wb').write(
        dec[u['off']:u['off'] + u['size']])

    # the atlas is white-on-transparent alpha; invert so it is readable
    vis = Image.fromarray(255 - a, 'L')
    vis.save(os.path.join(out, 'atlas.png'))
    vis.resize((f.width // 4, f.height // 4), Image.LANCZOS).save(
        os.path.join(out, 'atlas_preview.png'))

    cov = f.coverage()
    codes = [c for c, g in cov]

    with open(os.path.join(out, 'glyphs.tsv'), 'w', encoding='utf-8') as fh:
        fh.write('glyph\tx0\ty0\tx1\ty1\tbearingX\twidth\tadvance\tlast\tcodes\n')
        owner = {}
        for c, g in cov:
            owner.setdefault(g, []).append(c)
        for i, gl in enumerate(f.glyphs):
            cs = ' '.join('U+%04X' % c for c in owner.get(i, ()))
            fh.write('%d\t%s\t%s\n' % (i, '\t'.join(str(v) for v in gl), cs))

    with open(os.path.join(out, 'charmap.tsv'), 'w', encoding='utf-8') as fh:
        fh.write('code\tchar\tglyph\tw\tadvance\tname\n')
        for c, g in cov:
            gl = f.glyphs[g]
            ch = chr(c) if c >= 0x20 and not unicodedata.category(chr(c)).startswith('C') else ''
            fh.write('U+%04X\t%s\t%d\t%d\t%d\t%s\n' % (c, ch, g, gl[5], gl[6], uname(c)))

    with open(os.path.join(out, 'coverage.txt'), 'w', encoding='utf-8') as fh:
        fh.write('%s  atlas %dx%d fmt=%d tiled=%d  %d glyph records, '
                 '%d mapped code points, cell height %.0f\n\n'
                 % (stem, f.width, f.height, f.fmt, f.tiled,
                    f.n_glyphs, len(cov), f.cell_h))
        for lo, hi, name in pwfont.BLOCKS:
            n = sum(1 for c, _ in cov if lo <= c <= hi)
            if n:
                fh.write('  %-28s U+%04X..U+%04X  %d\n' % (name, lo, hi, n))
        cyr = [c for c, _ in cov if 0x0400 <= c <= 0x052F]
        fh.write('\nCYRILLIC code points present: %d  %s\n'
                 % (len(cyr), ' '.join('U+%04X' % c for c in cyr)))
        used_rows = max((f.glyphs[g][3] for _, g in cov), default=0)
        fh.write('glyph rects stop at y=%d, so %d atlas rows (%d lines of %d) are empty\n'
                 % (used_rows, f.height - used_rows,
                    (f.height - used_rows) // max(int(f.cell_h), 1), int(f.cell_h)))
        fh.write('charmap slots still zero: %d of %d\n'
                 % (f.last_code + 1 - len(cov), f.last_code + 1))

    if codes:
        f.sheet(codes).save(os.path.join(out, 'sheet_all.png'))
    for c, g in cov:
        tile = f.crop(g)
        if tile.size == 0:
            continue
        Image.fromarray(255 - tile, 'L').save(
            os.path.join(out, 'glyphs', 'U+%04X.png' % c))

    print('%-10s -> %s' % (stem, out))
    print('            atlas %dx%d, %d glyphs, %d mapped codes, %d glyph PNGs'
          % (f.width, f.height, f.n_glyphs, len(cov), len(cov)))
    return f, cov


def main():
    out_root = sys.argv[1] if len(sys.argv) > 1 else OUT_ROOT
    os.makedirs(out_root, exist_ok=True)
    srcs = sorted(os.path.join(FONT_DIR, n) for n in os.listdir(FONT_DIR)
                  if n.lower().endswith('.xpr'))
    if not srcs:
        raise SystemExit('no .xpr in ' + FONT_DIR)
    for s in srcs:
        unpack(s, out_root)


if __name__ == '__main__':
    main()
