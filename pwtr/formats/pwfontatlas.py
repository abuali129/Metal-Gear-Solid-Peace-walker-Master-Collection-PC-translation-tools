r"""The Peace Walker UI font atlas: how a byte becomes a glyph, and how to
put new glyphs in.

## The grid, measured and then confirmed against real game text

`Text\008299c5.txp`, texture 10 - 512x512 DXT5 - is the caps-only UI face
(`.olang` Style 0x0001).  A byte selects a cell:

    cell = byte - 0x20
    row  = cell // 16          y = row * 48
    col  = cell % 16           x = 484 if col == 0 else (col - 1) * 28

The `col == 0` case is the whole reason this looked unreadable: the first cell
of each row sits alone at the RIGHT edge of the sheet and the other fifteen run
from x=0.  Row 3 is the proof - its far-right glyph is `P` (0x50) while the
left block starts at `Q` (0x51).

It was then confirmed against strings the game actually ships, which is what
makes it fact rather than a fit.  This face has no lowercase, so the accented
letters are stored in the lowercase byte range, and the shipped text is already
written that way:

    hQUIPE SEULE            0x68 -> E-acute      (French "EQUIPE")
    CONFIGURACIpN           0x70 -> O-acute      (Spanish)
    START DRvCKEN           0x76 -> U-umlaut     (German)
    MODALITa                0x61 -> A-grave      (Italian)

All four land exactly where the formula puts them.

## What is free

Rows 0-5 are full, row 6 holds three glyphs, and **rows 7, 8 and 9 are
completely empty**: bytes 0x90..0xBF, **48 cells**.  Row 10 starts at y=480 and
the sheet ends at 512, so it is only 32 px of a 48 px cell - treat it as
unusable.

48 cells against 33 letters of the Ukrainian uppercase alphabet.  It fits, with
room left over.

## Why that matters more than it sounds

The engine masks a code point to ONE BYTE before looking up a glyph - proven
character-for-character on screen, see the project memory.  That is normally the
thing that blocks Cyrillic.  Here it is the thing that saves us: `U+0490` and
`U+FF90` and a bare `0x90` all reach the same cell, so a translation can be
stored as code points whose low byte lands in 0x90..0xBF and the engine will
render whatever we painted there.  No executable patch is required.

The tool is expected to hide that: translate in real Ukrainian, map to the
smuggled code points when packing, and map back when unpacking.
"""

from __future__ import annotations

import os

import numpy as np

import pwbc
import pwtex

CELL_W, CELL_H = 28, 48
GLYPH_W = 20                 # ink width the stock face uses inside a 28px cell
COLS = 16
FIRST_BYTE = 0x20
FAR_X = 484                  # where column 0 lives

# rows 7..9 - the empty band, and the only place new glyphs can go
FREE_FIRST, FREE_LAST = 0x90, 0xBF

UA_UPPER = 'АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ'


def cell_of(byte: int):
    """byte -> (x, y) of its cell in the sheet, or None if out of the grid."""
    c = byte - FIRST_BYTE
    if c < 0:
        return None
    row, col = divmod(c, COLS)
    x = FAR_X if col == 0 else (col - 1) * CELL_W
    return x, row * CELL_H


def free_bytes(count: int = 0):
    out = list(range(FREE_FIRST, FREE_LAST + 1))
    return out[:count] if count else out


def plan(alphabet: str = UA_UPPER) -> dict:
    """letter -> byte, assigning the empty cells in order."""
    slots = free_bytes()
    if len(alphabet) > len(slots):
        raise ValueError('%d letters do not fit in %d free cells'
                         % (len(alphabet), len(slots)))
    return {ch: slots[i] for i, ch in enumerate(alphabet)}


# --------------------------------------------------------------------------
# reading and writing the sheet
# --------------------------------------------------------------------------

class Atlas:
    """One texture out of a `.txp`, addressed as font cells."""

    def __init__(self, data: bytes, dds_off: int, w: int, h: int, fmt: int):
        self.data = bytearray(data)
        self.off = dds_off + 128            # past the DDS header
        self.w, self.h, self.fmt = w, h, fmt
        self.size = pwtex.level_size(w, h, fmt)

    @property
    def blob(self) -> bytes:
        return bytes(self.data[self.off:self.off + self.size])

    def image(self) -> np.ndarray:
        return pwtex.decode_bc(self.blob, self.w, self.h, self.fmt)

    def cell(self, byte: int) -> np.ndarray:
        x, y = cell_of(byte)
        return self.image()[y:y + CELL_H, x:x + CELL_W]

    def occupancy(self) -> dict:
        """byte -> ink fraction, for every cell in the grid."""
        img = self.image()[..., 3]
        out = {}
        for b in range(FIRST_BYTE, 0x100):
            p = cell_of(b)
            if not p:
                continue
            x, y = p
            if y + CELL_H > self.h or x + CELL_W > self.w:
                continue
            out[b] = float((img[y:y + CELL_H, x:x + CELL_W] > 8).mean())
        return out

    def put(self, byte: int, alpha: np.ndarray) -> None:
        """Paint one CELL_H x CELL_W alpha glyph, re-encoding only its blocks."""
        x, y = cell_of(byte)
        if y + CELL_H > self.h or x + CELL_W > self.w:
            raise ValueError('byte 0x%02X falls outside the sheet' % byte)
        if alpha.shape != (CELL_H, CELL_W):
            raise ValueError('glyph must be %dx%d, got %s'
                             % (CELL_W, CELL_H, alpha.shape))
        rgba = np.zeros((CELL_H, CELL_W, 4), dtype=np.uint8)
        rgba[..., :3] = 255                      # the face is white
        rgba[..., 3] = alpha
        patched = pwbc.patch_rect(self.blob, self.w, self.h, x, y, rgba, self.fmt)
        self.data[self.off:self.off + self.size] = patched

    def touched_blocks(self, bytes_: list) -> set:
        out = set()
        for b in bytes_:
            x, y = cell_of(b)
            out |= pwbc.blocks_touched(self.w, x, y, CELL_W, CELL_H)
        return out


# --------------------------------------------------------------------------
# glyphs from a TTF
# --------------------------------------------------------------------------

def _draw(ch: str, font) -> 'Image.Image':
    from PIL import Image, ImageDraw
    big = Image.new('L', (CELL_W * 6, CELL_H * 6), 0)
    ImageDraw.Draw(big).text((CELL_W * 3, CELL_H * 3), ch, font=font,
                             fill=255, anchor='mm')
    return big


def render_set(chars, ttf_path: str, width: int = GLYPH_W, height: int = CELL_H,
               cap: float = 0.60, threshold: int | None = 128,
               max_width: int = CELL_W - 2) -> dict:
    """char -> alpha array, all drawn at ONE shared scale.

    Scaling each letter independently to fill the ink box is the obvious thing
    and it looks wrong: it is exactly what makes W and I come out the same
    width.  A typeface has a single cap height, so measure the whole set once,
    derive one scale from the tallest letter and the widest, and apply it to
    every glyph - narrow letters then stay narrow, as they should.
    """
    from PIL import Image, ImageFont
    chars = list(chars)
    probe = ImageFont.truetype(ttf_path, CELL_H)
    boxes = {}
    for ch in chars:
        b = _draw(ch, probe).getbbox()
        if b:
            boxes[ch] = b
    if not boxes:
        return {}
    tall = max(b[3] - b[1] for b in boxes.values())
    wide = max(b[2] - b[0] for b in boxes.values())
    scale = min((height * cap) / tall, max_width / wide)

    out = {}
    for ch in chars:
        if ch not in boxes:
            out[ch] = np.zeros((height, CELL_W), dtype=np.uint8)
            continue
        g = _draw(ch, probe).crop(boxes[ch])
        gw = max(1, int(round(g.size[0] * scale)))
        gh = max(1, int(round(g.size[1] * scale)))
        g = g.resize((gw, gh), Image.LANCZOS)
        cell = Image.new('L', (CELL_W, height), 0)
        # left-aligned inside the 20px ink box, sitting on the stock baseline,
        # which is where the shipped glyphs sit
        ox = max(0, (width - gw) // 2)
        oy = max(0, int(height * 0.77) - gh)
        cell.paste(g, (min(ox, CELL_W - gw), oy))
        a = np.array(cell, dtype=np.uint8)
        if threshold is not None:
            a = np.where(a >= threshold, 255, 0).astype(np.uint8)
        out[ch] = a
    return out


def render(text: str, ttf_path: str, **kw) -> np.ndarray:
    """One character, at the scale its own alphabet would give it."""
    return render_set([text], ttf_path, **kw)[text]


def install(atlas: Atlas, mapping: dict, ttf_path: str,
            threshold: int | None = 128, progress=None) -> dict:
    """Paint every letter of `mapping` (char -> byte) into the sheet."""
    glyphs = render_set(mapping.keys(), ttf_path, threshold=threshold)
    done, skipped = 0, []
    items = sorted(mapping.items(), key=lambda kv: kv[1])
    for i, (ch, byte) in enumerate(items):
        try:
            atlas.put(byte, glyphs[ch])
            done += 1
        except Exception as exc:
            skipped.append((ch, '0x%02X' % byte, str(exc)))
        if progress:
            progress(i + 1, len(items))
    return {'painted': done, 'skipped': skipped,
            'blocks': len(atlas.touched_blocks(list(mapping.values())))}


# --------------------------------------------------------------------------
# the containing .txp
# --------------------------------------------------------------------------

FONT_TXP = '008299c5.txp'
FONT_TEXTURE = 10                    # the 512x512 caps sheet


def open_txp(path: str, index: int = FONT_TEXTURE):
    """-> (Atlas, plain_txp_bytes, name).  Decrypts if it needs to."""
    import txp_unpack
    import pwcrypt
    name = os.path.basename(path)
    raw = open(path, 'rb').read()
    plain = raw if raw[:4] == b'DDS ' else pwcrypt.crypt(raw, name)
    ts = txp_unpack.textures(plain)
    if index >= len(ts):
        raise ValueError('%s has %d textures, no #%d' % (name, len(ts), index))
    _k, off, w, h, _fcc, _n, fmt = ts[index]
    return Atlas(plain, off, w, h, fmt), plain, name


def save_txp(atlas: Atlas, out_path: str, name: str) -> str:
    """Re-encrypt and write.  `name` keys the cipher, so it must be the name
    the game knows - not whatever the output file is called."""
    import pwcrypt
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    with open(out_path, 'wb') as fh:
        fh.write(pwcrypt.crypt(bytes(atlas.data), name))
    return out_path


def install_into_game(game_dir: str, out_dir: str, ttf_path: str,
                      alphabet: str = UA_UPPER, threshold: int = 110,
                      progress=None) -> dict:
    """Paint `alphabet` into the free cells and write a patched .txp."""
    src = os.path.join(game_dir, 'Text', FONT_TXP)
    atlas, _plain, name = open_txp(src)
    mapping = plan(alphabet)
    res = install(atlas, mapping, ttf_path, threshold=threshold, progress=progress)
    out = save_txp(atlas, os.path.join(out_dir, 'Text', FONT_TXP), name)
    res['out'] = out
    res['mapping'] = {c: '0x%02X' % b for c, b in mapping.items()}
    return res


# --------------------------------------------------------------------------
# text <-> smuggled code points
# --------------------------------------------------------------------------

def to_font_bytes(text: str, mapping: dict) -> str:
    """Ukrainian -> the code points whose low byte hits our cells."""
    return ''.join(chr(mapping[c]) if c in mapping else c for c in text)


def from_font_bytes(text: str, mapping: dict) -> str:
    back = {chr(b): c for c, b in mapping.items()}
    return ''.join(back.get(c, c) for c in text)
