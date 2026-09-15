"""Shape, encode and lay out plain Arabic against an installed face."""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
from pwtr import arabic, compact                               # noqa: E402
from pwtr.formats import pwxpr                                 # noqa: E402
from tools.preview_face import render                          # noqa: E402

from PIL import Image                                          # noqa: E402

face = pwxpr.load(sys.argv[1])
mapping = {k: chr(v) for k, v in
           json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')).items()}
lines = [l for l in Path(sys.argv[4]).read_text(encoding='utf-8').splitlines()
         if l.strip()]

images = [render(face, compact.encode(
    arabic.strip_harakat(arabic.shape(line)), mapping)) for line in lines]
w = max(i.width for i in images)
sheet = Image.new('L', (w, sum(i.height + 10 for i in images)), 255)
y = 0
for image in images:
    sheet.paste(image, (w - image.width, y))
    y += image.height + 10
sheet.save(sys.argv[3])
print('wrote %s' % sys.argv[3])
