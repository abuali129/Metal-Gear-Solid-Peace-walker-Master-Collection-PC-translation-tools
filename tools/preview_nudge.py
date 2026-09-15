"""Try vertical nudges on chosen forms without rebuilding the face.

Rolling a tile inside its cell is exactly what ``nudge`` does at install
time, so what this shows is what a rebuild would paint.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, '.')
from pwtr import arabic, compact                               # noqa: E402
from pwtr.formats import pwxpr                                 # noqa: E402


def strip(face, atlas, text, shifted, dy):
    tiles = []
    for ch in text:
        code = ord(ch)
        if ch == ' ':
            tiles.append(np.zeros((face.cell_h, face.cell_h // 3), np.uint8))
            continue
        gi = face.f.charmap[code] if code <= face.f.last_code else 0
        x0, y0, x1, y1 = face.f.glyphs[gi][:4]
        tile = atlas[y0:y1, x0:x1].copy()
        if code in shifted and dy:
            tile = np.roll(tile, dy, axis=0)
            tile[:dy] = 0
        tiles.append(tile)
    width = sum(t.shape[1] for t in tiles)
    out = np.zeros((face.cell_h, max(1, width)), np.uint8)
    x = 0
    for tile in tiles:
        out[:, x:x + tile.shape[1]] = tile
        x += tile.shape[1]
    return Image.fromarray(255 - out).resize(
        (out.shape[1] * 2, face.cell_h * 2), Image.LANCZOS)


if __name__ == '__main__':
    face = pwxpr.load(sys.argv[1])
    mapping = {k: chr(v) for k, v in
               json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')).items()}
    out = sys.argv[3]
    forms = sys.argv[4]
    line = Path(sys.argv[5]).read_text(encoding='utf-8').strip()
    steps = [int(n) for n in sys.argv[6].split(',')]

    atlas = face.atlas()
    shifted = {ord(mapping.get(f, f)) for f in forms}
    text = compact.encode(
        arabic.strip_harakat(arabic.shape(line)), mapping)
    rows = [(dy, strip(face, atlas, text, shifted, dy)) for dy in steps]
    w = max(i.width for _, i in rows)
    sheet = Image.new('L', (w + 100, sum(i.height + 10 for _, i in rows)), 255)
    draw, y = ImageDraw.Draw(sheet), 0
    for dy, image in rows:
        sheet.paste(image, (w - image.width, y))
        draw.text((w + 10, y + image.height // 2 - 8), 'nudge %+d' % dy, fill=0)
        y += image.height + 10
    sheet.save(out)
    print('wrote %s' % out)
