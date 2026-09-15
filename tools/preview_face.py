"""Lay a string out from an installed face, the way the engine does.

Rectangle straight from the glyph record, advance straight from the record,
no kerning and no shaping -- so what comes out is what the game draws, and a
placement fault shows here without a play session.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, '.')
from pwtr.formats import pwxpr                                 # noqa: E402


def render(face, text, scale=2):
    """Lay the line out from the records, advance and bearing included.

    A mark carries a zero advance and a bearing that pushes it right, so it
    is drawn over the letter that follows rather than beside it.  Honouring
    both here is what makes this a preview rather than a guess.
    """
    atlas = face.atlas()
    placed, pen = [], 0
    for ch in text:
        code = ord(ch)
        gi = face.f.charmap[code] if code <= face.f.last_code else 0
        if ch == ' ':
            pen += face.cell_h // 3
            continue
        if not gi:
            placed.append((pen, np.full((face.cell_h, face.cell_h // 2), 60,
                                        np.uint8)))
            pen += face.cell_h // 2
            continue
        x0, y0, x1, y1, bearing, _w, advance = face.f.glyphs[gi][:7]
        placed.append((pen + bearing, atlas[y0:y1, x0:x1]))
        pen += advance
    width = max((at + t.shape[1] for at, t in placed), default=1)
    out = np.zeros((face.cell_h, max(1, width)), np.uint8)
    for at, tile in placed:
        window = out[:tile.shape[0], at:at + tile.shape[1]]
        np.maximum(window, tile[:, :window.shape[1]], out=window)
    image = Image.fromarray(255 - out).resize(
        (out.shape[1] * scale, out.shape[0] * scale), Image.LANCZOS)
    return image


if __name__ == '__main__':
    face = pwxpr.load(sys.argv[1])
    lines = [l for l in Path(sys.argv[3]).read_text(encoding='utf-8')
             .splitlines() if l.strip()]
    images = [render(face, line) for line in lines]
    w = max(i.width for i in images)
    sheet = Image.new('L', (w, sum(i.height + 8 for i in images)), 255)
    y = 0
    for image in images:
        sheet.paste(image, (w - image.width, y))     # right aligned, as RTL
        y += image.height + 8
    sheet.save(sys.argv[2])
    print('wrote %s  (%dx%d)' % (sys.argv[2], sheet.width, sheet.height))
