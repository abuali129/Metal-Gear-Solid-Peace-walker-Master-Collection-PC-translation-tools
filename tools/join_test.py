"""Lay glyphs out the way the engine does, at several fringe-trim levels.

The engine advances the pen by the glyph's advance, which the Arabic path
sets equal to the tile width so joining strokes meet.  They only meet
cleanly if the tile ENDS on solid ink: the ink box keeps the antialiased
fade, so two tiles abut fringe-to-fringe and the join reads as a gap.
"""
import sys
sys.path.insert(0, '.')
import numpy as np
from PIL import Image, ImageFont
from pwtr import arabic, fonts

TTF = sys.argv[1]
TEXT = sys.argv[2] if len(sys.argv) > 2 else 'رسالة أعطاها هيوي لسنيك'
OUT = sys.argv[3] if len(sys.argv) > 3 else 'join.png'
CELL, LEVELS = 67, (0, 64, 96, 128, 160)

shaped = arabic.shape(TEXT)
measured = [chr(c) for c in range(0xFE70, 0xFEFD)]
shape = fonts.metrics(TTF, measured, CELL, 1.0)
face = ImageFont.truetype(TTF, shape['size'])
window = shape['pen'] - shape['sit']


def tile_of(ch, trim):
    box, canvas = fonts._ink(face, ch, CELL, shape['pen'])
    if not box:
        return None
    a = np.array(canvas.crop((box[0], window, box[2], window + CELL)),
                 dtype=np.uint8)
    if trim:
        cols = a.max(axis=0)
        lo = 0
        while lo < a.shape[1] - 1 and cols[lo] < trim:
            lo += 1
        hi = a.shape[1]
        while hi - 1 > lo and cols[hi - 1] < trim:
            hi -= 1
        a = a[:, lo:hi]
    return a


rows = []
for trim in LEVELS:
    tiles = [t for t in (tile_of(c, trim) for c in shaped) if t is not None]
    width = sum(t.shape[1] for t in tiles)
    row = np.zeros((CELL, width), np.uint8)
    x = 0
    for t in tiles:
        row[:, x:x + t.shape[1]] = np.maximum(row[:, x:x + t.shape[1]], t)
        x += t.shape[1]          # advance == width, exactly as installed
    rows.append((trim, row))

wide = max(r.shape[1] for _, r in rows)
sheet = np.zeros((CELL * len(rows) + 8 * len(rows), wide), np.uint8)
y = 0
for trim, row in rows:
    sheet[y:y + CELL, :row.shape[1]] = row
    y += CELL + 8
img = Image.fromarray(255 - sheet).convert('L')
img.save(OUT)
print('%s  levels %s (top to bottom), width %s'
      % (OUT, ', '.join(str(t) for t, _ in rows),
         ', '.join(str(r.shape[1]) for _, r in rows)))
