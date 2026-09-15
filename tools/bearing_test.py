"""Does the engine honour a zero advance and a negative bearing?

Arabic marks sit above their letter.  This face has no way to say so: all 643
stock glyph records carry a non-zero advance, so a mark painted as its own
glyph is drawn *beside* the letter, not over it.  The glyph record does have a
bearing field, though, and nothing in the file uses it to pull a glyph back.

So ask the engine.  Four shadda glyphs go into the first briefing tape, each
at its own code point and each with a different advance and bearing, and the
line says which is which.  One play session settles whether the cheap route
exists or whether every marked letter needs a precomposed glyph of its own.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
sys.path.insert(0, 'pwtr/formats')

from PIL import Image, ImageDraw, ImageFont                    # noqa: E402

from pwtr import arabic, briefing, compact, fonts              # noqa: E402
from pwtr.formats import pwxpr                                 # noqa: E402
from pwtr.project import Project                               # noqa: E402

TTF = r'C:\Users\HP\Desktop\MGSPW\SCE-PS3-NR-R-JPN.TTF'
SHADDA = '\u0651'
TAPE = 0x0944B0

project = Project.load(sys.argv[1])
out_dir = Path(sys.argv[2])
plan = project.compact_plan(('briefing',))
mapping = plan['mapping']
project.manifest.setdefault('font', {})['compact'] = {
    form: ord(slot) for form, slot in mapping.items()}

# ---- the ordinary face, then four more glyphs on top ----------------------
draw = {mapping[form]: form for form in mapping}
result = fonts.install_subtitle_face(project.game, out_dir, TTF,
                                     sorted(draw), remap=draw, fill=1.0)
size = result['faces'][0]['size']

used = {ord(v) for v in mapping.values()}
slots = [c for c in range(compact.DOUBLE_FIRST, compact.DOUBLE_LAST + 1)
         if c not in used][:4]

#: (label, advance, bearing).  1 is what the installer does today and is the
#: control -- it should sit beside the letter.  2 asks for no advance at all.
#: 3 and 4 add a pull to the left, half a letter and a whole one.
VARIANTS = [(1, None, 0), (2, 0, 0), (3, 0, 0), (4, 0, 8)]

#: Where the mark sits in the stored text relative to its letter.  With a zero
#: advance the pen does not move, so a mark written *before* its letter is
#: drawn at the same x as the letter -- which stacks them without needing the
#: bearing to pull anything back.  The first round showed why that matters: a
#: negative bearing, written as two's complement, moved the glyph 65 522
#: pixels and took the rest of the line with it, so the field is read
#: unsigned and cannot pull left at all.
BEFORE = {3, 4}

for face in result['faces']:
    path = out_dir / 'FONT' / face['file']
    editor = pwxpr.load(str(path), face['file'])
    top, _band = editor.free_band()
    font = ImageFont.truetype(TTF, size)
    canvas = Image.new('L', (editor.cell_h * 4, editor.cell_h * 4), 0)
    ImageDraw.Draw(canvas).text((editor.cell_h, editor.cell_h * 2), SHADDA,
                                font=font, fill=255, anchor='ls')
    box = canvas.getbbox()
    donors = editor.donors(len(draw) + 8)[len(draw):]
    import numpy as np
    x = 0
    for (label, advance, bearing), record in zip(VARIANTS, donors):
        window = editor.cell_h * 2 - result['faces'][0].get('sit', 50)
        tile = np.array(canvas.crop((box[0], window, box[2],
                                     window + editor.cell_h)), dtype=np.uint8)
        editor.write_atlas(top, x, tile)
        width = box[2] - box[0]
        editor.set_glyph(record, x, top, x + width, top + editor.cell_h,
                         bearing & 0xFFFF, width,
                         width if advance is None else advance)
        editor.set_charmap(slots[label - 1], record)
        x += width + 1
    editor.save(str(path))
    print('%s: four test glyphs at %s'
          % (face['file'], ' '.join('U+%04X' % c for c in slots)))

# ---- the tape says which is which -----------------------------------------
word = arabic.strip_harakat(arabic.shape('بحر'))       # three plain letters
lines = {}
for label, _a, _b in VARIANTS:
    body = compact.encode(word, mapping)
    mark = chr(slots[label - 1])
    if label in BEFORE:
        lines[label] = '%d %s%s%s' % (label, mark, body[0], body[1:])
    else:
        lines[label] = '%d %s%s%s' % (label, body[0], mark, body[1:])

data = briefing.load(project.stock_briefing())
entries = [e for e in project.read('briefing')['entries'] if e.get('target')]
edits = {}
for e in entries:
    edits.setdefault(e['record'], {})[e['index']] = \
        project._render(e['target'], 'subtitle').encode('utf-8')
record = {r.offset: r for r in briefing.parse(data)}[TAPE]
seen, slot_index = set(), 0
for i, off in enumerate(record.str_offsets):
    if off in seen or slot_index >= len(VARIANTS):
        continue
    seen.add(off)
    slot_index += 1
    edits.setdefault(TAPE, {})[i] = lines[slot_index].encode('utf-8')

straddling = {(e['record'], e['index']): e['source'].encode('utf-8')
              for e in project.read('briefing')['entries']
              if e.get('straddles')}
built, report = briefing.build(data, edits, None, partial=True,
                               straddling=straddling)
built = briefing.realign(built, report['rekey'], briefing.NAME)
briefing.save(built, out_dir / 'MLG' / 'disc0_rel' / briefing.NAME,
              briefing.NAME)
(out_dir / 'mapping.json').write_text(
    json.dumps({k: ord(v) for k, v in mapping.items()},
               ensure_ascii=False, indent=1), encoding='utf-8')
print('file length unchanged: %s' % (len(built) == len(data)))
print('the first four lines of the first tape now read 1..4, each with a '
      'shadda after the first letter')
