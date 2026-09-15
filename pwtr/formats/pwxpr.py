r"""Writing glyphs into an XPR2 font - the subtitle face.

`pwfont.XprFont` reads these; this puts new letters in.

## The three tables

    charmap[code]  -> glyph index, indexed by REAL Unicode, last_code U+FF5E
                      65 375 entries, only ~640 used
    glyphs[gi]     -> (x0, y0, x1, y1, bearing_x, width, advance, _)
                      8 big-endian u16, a rectangle in the atlas
    atlas          -> plain linear 8-bit alpha, width x height, no tiling

## Why we repurpose records instead of adding them

The atlas has room - the 4096x4096 sheet has a 3 497 px empty band at the
bottom, about 3 640 glyph cells - but **every one of its 643 glyph records is
already referenced by the charmap**, so there is no spare record to fill in.
Adding records means growing the USER resource, which moves the texture data
and invalidates every offset in the header: a whole-file rebuild.

There is a cheaper move that needs no resize.  A record's rectangle is just
four numbers, so we can point an existing record at fresh atlas space, paint a
Cyrillic letter there, and aim the charmap at it:

    charmap[U+0410] = gi          # А now resolves to record gi
    glyphs[gi]      = new rect    # which points into the free band

The character that used to own `gi` renders the new letter too.  That is the
cost, and it is the same trade every fan translation of this engine has made -
so pick the donors from a range the target language does not need.

Nothing moves, nothing resizes, and the file stays byte-identical outside the
rectangles we touched and the charmap entries we set.
"""

from __future__ import annotations

import os
import struct

import numpy as np

import pwcrypt
import pwfont

GLYPH_REC = 16          # 8 x u16, big-endian


class XprEditor:
    """A loaded XPR2 that can be modified and written back."""

    def __init__(self, data: bytes, name: str):
        self.name = name
        self.buf = bytearray(data)
        self.f = pwfont.XprFont(bytes(data))
        self.w, self.h = self.f.width, self.f.height
        self.tex_off = self.f.tex_off
        self.cell_h = int(self.f.cell_h)

    # ------------------------------------------------------------- atlas --
    def atlas(self) -> np.ndarray:
        a = np.frombuffer(self.buf, np.uint8, self.w * self.h, self.tex_off)
        return a.reshape(self.h, self.w)

    def write_atlas(self, y: int, x: int, tile: np.ndarray) -> None:
        th, tw = tile.shape
        if y + th > self.h or x + tw > self.w:
            raise ValueError('tile %dx%d at (%d,%d) leaves the atlas'
                             % (tw, th, x, y))
        for r in range(th):
            o = self.tex_off + (y + r) * self.w + x
            self.buf[o:o + tw] = tile[r].tobytes()

    def free_band(self) -> tuple:
        """(first_free_row, height) of the empty band at the bottom."""
        ink = (self.atlas() > 8).sum(axis=1)
        y = self.h
        while y > 0 and ink[y - 1] == 0:
            y -= 1
        return y, self.h - y

    # ------------------------------------------------------------ records --
    def set_glyph(self, gi: int, x0: int, y0: int, x1: int, y1: int,
                  bearing: int, width: int, advance: int) -> None:
        off = self.f.rec_off + gi * GLYPH_REC
        struct.pack_into('>8H', self.buf, off,
                         x0, y0, x1, y1, bearing & 0xFFFF, width, advance, 0)

    def set_charmap(self, code: int, gi: int) -> None:
        if not 0 <= code <= self.f.last_code:
            raise ValueError('U+%04X is past last_code U+%04X'
                             % (code, self.f.last_code))
        off = self.f.user + pwfont.CHARMAP_OFF + 2 * code
        struct.pack_into('>H', self.buf, off, gi)

    def donors(self, count: int, prefer=range(0x2E80, 0x9FFF)) -> list:
        """Glyph indices we can repurpose, chosen from code points a Ukrainian
        build does not need - CJK first, then anything non-ASCII."""
        used = {}
        for code, gi in enumerate(self.f.charmap):
            if gi:
                used.setdefault(gi, code)
        out = [gi for gi, code in used.items() if code in prefer]
        if len(out) < count:
            out += [gi for gi, code in used.items()
                    if code > 0x7F and gi not in out]
        return out[:count]

    # -------------------------------------------------------------- save --
    def save(self, out_path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.',
                    exist_ok=True)
        with open(out_path, 'wb') as fh:
            fh.write(pwcrypt.crypt(bytes(self.buf), self.name))
        return out_path


def load(path: str, name: str | None = None) -> XprEditor:
    name = name or os.path.basename(path)
    raw = open(path, 'rb').read()
    dec = raw if raw[:4] == b'XPR2' else pwcrypt.decrypt(raw, name)
    if dec[:4] != b'XPR2':
        raise ValueError('%s: not XPR2 after decrypting (%r)' % (name, dec[:4]))
    return XprEditor(dec, name)


def install(ed: XprEditor, letters: str, ttf_path: str,
            threshold: int | None = None, progress=None) -> dict:
    """Paint `letters` into the free band and map them to real code points."""
    from PIL import Image, ImageDraw, ImageFont

    y0, band = ed.free_band()
    if band < ed.cell_h:
        return {'painted': 0, 'error': 'no free band in the atlas'}

    gis = ed.donors(len(letters))
    if len(gis) < len(letters):
        return {'painted': 0,
                'error': 'only %d donor glyph records available' % len(gis)}

    # one shared scale, measured over the whole alphabet
    probe = ImageFont.truetype(ttf_path, ed.cell_h)
    boxes = {}
    for ch in letters:
        im = Image.new('L', (ed.cell_h * 4, ed.cell_h * 4), 0)
        ImageDraw.Draw(im).text((ed.cell_h * 2, ed.cell_h * 2), ch,
                                font=probe, fill=255, anchor='mm')
        b = im.getbbox()
        if b:
            boxes[ch] = (b, im)
    if not boxes:
        return {'painted': 0, 'error': 'the font rendered nothing'}
    tall = max(b[3] - b[1] for b, _ in boxes.values())
    scale = (ed.cell_h * 0.72) / tall

    x, y, painted, mapping = 0, y0, 0, {}
    for i, ch in enumerate(letters):
        if ch not in boxes:
            continue
        b, im = boxes[ch]
        g = im.crop(b)
        gw = max(1, int(round(g.size[0] * scale)))
        gh = max(1, int(round(g.size[1] * scale)))
        g = g.resize((gw, gh), Image.LANCZOS)
        cell = Image.new('L', (gw + 2, ed.cell_h), 0)
        cell.paste(g, (1, max(0, int(ed.cell_h * 0.80) - gh)))
        tile = np.array(cell, dtype=np.uint8)
        if threshold is not None:
            tile = np.where(tile >= threshold, tile, 0).astype(np.uint8)

        if x + tile.shape[1] >= ed.w:
            x, y = 0, y + ed.cell_h
        if y + ed.cell_h > ed.h:
            break
        ed.write_atlas(y, x, tile)
        gi = gis[i]
        ed.set_glyph(gi, x, y, x + tile.shape[1], y + ed.cell_h,
                     0, tile.shape[1], tile.shape[1] + 1)
        ed.set_charmap(ord(ch), gi)
        mapping[ch] = gi
        x += tile.shape[1] + 1
        painted += 1
        if progress:
            progress(i + 1, len(letters))
    return {'painted': painted, 'mapping': mapping,
            'band_row': y0, 'band_height': band, 'donors': gis[:painted]}
