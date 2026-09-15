r"""Metal Gear Solid: Peace Walker (PC, Master Collection Vol.2) - FONT files.

    python pwfont.py info    <decrypted.xpr.bin> ...
    python pwfont.py atlas   <decrypted.xpr.bin> <out.png>
    python pwfont.py sheet   <decrypted.xpr.bin> <out.png>
    python pwfont.py text    <decrypted.xpr.bin> <out.png> <string>
    python pwfont.py cover   <decrypted.xpr.bin>            code point inventory

FORMAT
======
The .xpr in mgspw\FONT is NOT a PSP .PGF and NOT a BWFON.  Peace Walker in the
Master Collection is the HD (Xbox 360 / PS3) build, so its assets are Xbox 360
XPR2 resource bundles - big-endian throughout.  Anything written for the PSP
era's font formats does not apply here.

  file layout
    0x00  'XPR2'
    0x04  u32 headerSize      (counted from 0x0C)
    0x08  u32 dataSize
    0x0C  u32 resourceCount   (== 2 in both font files)
    0x10  resource[resourceCount], 24 bytes each:
              char  type[4]        'TX2D' | 'USER'
              u32   dataOffset     relative to 0x0C
              u32   dataSize
              u32   unused         (0)
              u32   nameOffset     relative to 0x0C -> asciiz
              u32   unused         (0)
          then the asciiz names:  "FontTexture", "FontData"
    0x0C+headerSize   the TX2D texel data (dataSize bytes)

  TX2D payload = a 0x34-byte Xbox-360 D3DTexture header:
      7 x u32   Common, RefCount, Fence, ReadFence, Identifier,
                BaseFlush, MipFlush
      6 x u32   GPUTEXTURE_FETCH_CONSTANT
                d0: type:2 ... pitch:9 (<<22, in 32-texel units), tiled:31
                d1: data_format:6 (2 == FMT_8, one byte per texel), endian:2
                d2: width-1:13, height-1:13, stackdepth:6
      Both fonts: data_format 2, tiled 0  ->  a plain linear 8-bit alpha
      atlas, no mips, no swizzle, no tiling.  Just reshape the data section.

  USER payload ("FontData"), all big-endian:
      +0x00  u32   5            (constant in both files)
      +0x04  f32   67.0         cell / line height; every glyph rect is 67 tall
      +0x08  u32   0
      +0x0C  u32   0
      +0x10  f32   67.0
      +0x14  u16   0xFF5E       highest code in the map (U+FF5E FULLWIDTH ~)
      +0x16  u16   charmap[0 .. 0xFF5E]     65375 entries, code-point indexed
             pad to 8
             glyph[n], 16 bytes each, all u16 big-endian:
                 u0, v0, u1, v1      texel rect in the atlas (v1-v0 == 67)
                 bearingX            pen offset before the bitmap
                 width               == u1-u0
                 advance             pen step
                 0

  CHARACTER MAP.  charmap is indexed by the UCS-2 code point itself and holds
  a glyph index.  Glyph 0 is the .notdef box, so every unmapped code renders
  as tofu.  U+0020 maps to a 1x67 blank with advance 16 - the space.
  No Shift-JIS, no UTF-8, no sequential index table: a flat UCS-2 lookup.

  THE TWO FILES (they are the only .xpr in the whole game)
    FONT/0007ccd8.xpr  16.9 MB  atlas 4096x4096  643 glyphs  -> Japanese
    FONT/000ebbe8.xpr   2.2 MB  atlas 2048x1024  459 glyphs  -> Latin / western
  Same typeface, same rasterisation: the ASCII block is pixel-identical in both
  and the first 63 glyph records are byte-identical.  They are subset fonts -
  only the characters the shipped script actually uses were baked in.

  NO CYRILLIC.  Neither font has a single Cyrillic letter.  The one code point
  in U+0400..U+04FF, U+0400, is a hollow 21x67 placeholder box, not a letter.

  ROOM TO GROW.  Glyph rects stop at y=612 in the JP atlas (3484 empty rows,
  52 more 67px lines) and at y=748 in the Latin one (276 empty rows, 4 lines).
  charmap slots for 64917 code points are still 0, U+0400..U+04FF included, so
  Cyrillic can be added as pure data: paint glyphs into the empty atlas rows,
  append 16-byte records, point charmap[code] at them, re-encrypt with pwcrypt
  (decrypt is its own inverse and round-trips both files byte-exactly).
  The script in .olang is UTF-8, so the codes reaching this table are real
  Unicode - nothing has to be smuggled through a code page.
"""
from __future__ import annotations

import os
import struct
import sys
import unicodedata

import numpy as np
from PIL import Image

CHARMAP_OFF = 0x16          # inside the USER block
LAST_CODE_OFF = 0x14


class XprFont:
    def __init__(self, data: bytes):
        self.d = d = data
        if d[:4] != b'XPR2':
            raise ValueError('not an XPR2 file: %r' % d[:4])
        self.header_size, self.data_size, self.res_count = struct.unpack_from('>3I', d, 4)
        self.res = {}
        for i in range(self.res_count):
            t, off, size, unused, noff, unused2 = struct.unpack_from('>4s5I', d, 0x10 + 24 * i)
            base = 0x0C + noff
            name = d[base:d.index(b'\0', base)].decode('ascii')
            self.res[t.decode('ascii')] = dict(name=name, off=0x0C + off,
                                               size=size, unused=unused)
        self.tex_off = 0x0C + self.header_size

        # --- TX2D / D3DTexture -------------------------------------------
        t = self.res['TX2D']['off']
        self.d3d = struct.unpack_from('>13I', d, t)
        f0, f1, f2 = self.d3d[7], self.d3d[8], self.d3d[9]
        self.pitch = ((f0 >> 22) & 0x1FF) * 32
        self.tiled = (f0 >> 31) & 1
        self.fmt = f1 & 0x3F
        self.endian = (f1 >> 6) & 3
        self.width = (f2 & 0x1FFF) + 1
        self.height = ((f2 >> 13) & 0x1FFF) + 1

        # --- USER / FontData ---------------------------------------------
        u = self.res['USER']['off']
        self.user = u
        self.magic5 = struct.unpack_from('>I', d, u)[0]
        self.cell_h = struct.unpack_from('>f', d, u + 4)[0]
        self.cell_h2 = struct.unpack_from('>f', d, u + 0x10)[0]
        self.last_code = struct.unpack_from('>H', d, u + LAST_CODE_OFF)[0]
        n = self.last_code + 1
        self.charmap = struct.unpack_from('>%dH' % n, d, u + CHARMAP_OFF)
        rec = u + CHARMAP_OFF + 2 * n
        self.rec_off = (rec + 7) & ~7
        end = u + self.res['USER']['size']
        self.n_glyphs = (end - self.rec_off) // 16
        self.glyphs = [struct.unpack_from('>8H', d, self.rec_off + 16 * i)
                       for i in range(self.n_glyphs)]
        self._atlas = None

    # ---------------------------------------------------------------- atlas
    def atlas(self):
        if self._atlas is None:
            if self.fmt != 2:
                raise NotImplementedError('texture format %d' % self.fmt)
            a = np.frombuffer(self.d, np.uint8, self.width * self.height, self.tex_off)
            self._atlas = a.reshape(self.height, self.width)
        return self._atlas

    # --------------------------------------------------------------- lookup
    def glyph_of(self, code: int) -> int:
        if 0 <= code <= self.last_code:
            return self.charmap[code]
        return 0

    def has(self, code: int) -> bool:
        return self.glyph_of(code) != 0

    def coverage(self):
        return [(c, g) for c, g in enumerate(self.charmap) if g]

    def free_glyph_slots(self):
        used = {g for g in self.charmap if g}
        return [i for i in range(1, self.n_glyphs) if i not in used]

    # -------------------------------------------------------------- drawing
    def crop(self, gi: int):
        x0, y0, x1, y1, bx, w, adv, _ = self.glyphs[gi]
        return self.atlas()[y0:y1, x0:x1]

    def render(self, s: str, gap: int = 0) -> Image.Image:
        a = self.atlas()
        h = int(self.cell_h)
        total = sum(self.glyphs[self.glyph_of(ord(c))][6] + gap for c in s) + 8
        out = np.zeros((h, max(total, 1)), np.uint8)
        x = 0
        for ch in s:
            x0, y0, x1, y1, bx, w, adv, _ = self.glyphs[self.glyph_of(ord(ch))]
            tile = a[y0:y1, x0:x1]
            xx = x + bx
            if xx >= 0 and xx + tile.shape[1] <= out.shape[1]:
                dst = out[:tile.shape[0], xx:xx + tile.shape[1]]
                np.maximum(dst, tile, out=dst)
            x += adv + gap
        return Image.fromarray(out)

    def sheet(self, codes, cols=32, cell=None) -> Image.Image:
        a = self.atlas()
        ch = cell or int(self.cell_h)
        cw = max(self.glyphs[self.glyph_of(c)][5] for c in codes) + 2
        rows = (len(codes) + cols - 1) // cols
        out = np.zeros((rows * (ch + 2), cols * cw), np.uint8)
        for i, c in enumerate(codes):
            x0, y0, x1, y1, bx, w, adv, _ = self.glyphs[self.glyph_of(c)]
            tile = a[y0:y1, x0:x1]
            r, k = divmod(i, cols)
            out[r * (ch + 2):r * (ch + 2) + tile.shape[0],
                k * cw:k * cw + tile.shape[1]] = tile
        return Image.fromarray(out)


def load(path) -> XprFont:
    return XprFont(open(path, 'rb').read())


BLOCKS = [
    (0x0000, 0x007F, 'Basic Latin (ASCII)'),
    (0x0080, 0x00FF, 'Latin-1 Supplement'),
    (0x0100, 0x017F, 'Latin Extended-A'),
    (0x0180, 0x024F, 'Latin Extended-B'),
    (0x0370, 0x03FF, 'Greek'),
    (0x0400, 0x04FF, 'CYRILLIC'),
    (0x0500, 0x052F, 'Cyrillic Supplement'),
    (0x2000, 0x206F, 'General Punctuation'),
    (0x2070, 0x209F, 'Super/Subscripts'),
    (0x20A0, 0x20CF, 'Currency Symbols'),
    (0x2100, 0x214F, 'Letterlike Symbols'),
    (0x2150, 0x218F, 'Number Forms'),
    (0x2190, 0x21FF, 'Arrows'),
    (0x2200, 0x22FF, 'Math Operators'),
    (0x2500, 0x257F, 'Box Drawing'),
    (0x2580, 0x259F, 'Block Elements'),
    (0x25A0, 0x25FF, 'Geometric Shapes'),
    (0x2600, 0x26FF, 'Misc Symbols'),
    (0x2E80, 0x2EFF, 'CJK Radicals Suppl'),
    (0x2F00, 0x2FDF, 'Kangxi Radicals'),
    (0x2FF0, 0x2FFF, 'Ideographic Desc'),
    (0x3000, 0x303F, 'CJK Symbols/Punct'),
    (0x3040, 0x309F, 'HIRAGANA'),
    (0x30A0, 0x30FF, 'KATAKANA'),
    (0x3100, 0x312F, 'Bopomofo'),
    (0x3130, 0x318F, 'Hangul Compat Jamo'),
    (0x3190, 0x319F, 'Kanbun'),
    (0x31F0, 0x31FF, 'Katakana Ext'),
    (0x3200, 0x32FF, 'Enclosed CJK'),
    (0x3300, 0x33FF, 'CJK Compatibility'),
    (0x3400, 0x4DBF, 'CJK Ext A'),
    (0x4E00, 0x9FFF, 'CJK UNIFIED IDEOGRAPHS'),
    (0xAC00, 0xD7AF, 'HANGUL SYLLABLES'),
    (0xF900, 0xFAFF, 'CJK Compat Ideographs'),
    (0xFB00, 0xFB4F, 'Alphabetic Presentation'),
    (0xFE30, 0xFE4F, 'CJK Compat Forms'),
    (0xFF00, 0xFFEF, 'Halfwidth/Fullwidth Forms'),
]


def cmd_info(path):
    f = load(path)
    print('=== %s' % os.path.basename(path))
    print('  XPR2 header %d  data %d  resources %d' % (f.header_size, f.data_size, f.res_count))
    for t, r in f.res.items():
        print('    %s "%s" @0x%X  %d bytes' % (t, r['name'], r['off'], r['size']))
    print('  texture %dx%d  fmt=%d(FMT_8, 8bpp alpha)  pitch=%d  tiled=%d  endian=%d  texels @0x%X'
          % (f.width, f.height, f.fmt, f.pitch, f.tiled, f.endian, f.tex_off))
    print('  cell height %.1f / %.1f' % (f.cell_h, f.cell_h2))
    print('  charmap @0x%X  codes 0..0x%04X (%d entries)'
          % (f.user + CHARMAP_OFF, f.last_code, f.last_code + 1))
    print('  glyph records @0x%X  count %d  (%d bytes)'
          % (f.rec_off, f.n_glyphs, f.n_glyphs * 16))
    cov = f.coverage()
    print('  mapped code points %d  distinct glyph slots referenced %d  unreferenced slots %d'
          % (len(cov), len({g for _, g in cov}), len(f.free_glyph_slots())))
    print('  -- coverage by Unicode block --')
    for lo, hi, name in BLOCKS:
        n = sum(1 for c, g in cov if lo <= c <= hi)
        if n:
            print('    U+%04X..U+%04X  %-28s %5d' % (lo, hi, name, n))
    other = [c for c, g in cov if not any(lo <= c <= hi for lo, hi, _ in BLOCKS)]
    if other:
        print('    (outside the listed blocks: %d)' % len(other))
    cyr = [c for c, g in cov if 0x0400 <= c <= 0x052F]
    print('  CYRILLIC U+0400..U+052F: %d code points%s'
          % (len(cyr), '  -> ' + ' '.join('U+%04X %s' % (c, chr(c)) for c in cyr) if cyr else ''))


def cmd_cover(path):
    f = load(path)
    for c, g in f.coverage():
        try:
            nm = unicodedata.name(chr(c))
        except ValueError:
            nm = '<unnamed>'
        print('U+%04X  glyph %4d  %s  %s' % (c, g, chr(c), nm))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'info'
    if cmd == 'info':
        for p in sys.argv[2:]:
            cmd_info(p)
    elif cmd == 'cover':
        cmd_cover(sys.argv[2])
    elif cmd == 'atlas':
        Image.fromarray(load(sys.argv[2]).atlas()).save(sys.argv[3])
    elif cmd == 'text':
        load(sys.argv[2]).render(sys.argv[4]).save(sys.argv[3])
    elif cmd == 'sheet':
        f = load(sys.argv[2])
        f.sheet([c for c, g in f.coverage()]).save(sys.argv[3])
    else:
        print(__doc__)
