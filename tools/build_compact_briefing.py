"""Build the compact-encoding test: fonts and briefing, both in place."""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
from pwtr import arabic, briefing, compact, fonts              # noqa: E402
from pwtr.project import Project                               # noqa: E402

TTF = r'C:\Users\HP\Desktop\MGSPW\SCE-PS3-NR-R-JPN.TTF'
FILL = 1.0

project = Project.load(sys.argv[1])
out_dir = Path(sys.argv[2])
game = project.game

# ---- the plan: one place, shared with the font dialog ----
# The installer is the only thing that knows how wide a glyph is drawn, and
# a mark cannot be centred without that -- so measure first, store it, and
# only then plan, because the plan has to see the mark variants.
measured = ([chr(c) for c in range(0xFE70, 0xFEFD)]
            + [chr(m) for m in sorted(arabic.MARKS)])
widths = fonts.metrics(TTF, measured, 67, FILL)
project.manifest.setdefault('font', {})['widths'] = {
    ch: box[2] - box[0] for ch, box in widths['boxes'].items()}

plan = project.compact_plan(('briefing',))
mapping, pool = plan['mapping'], plan['pool']
print('single-byte slots: %d, punctuation only -- no letter or digit moves'
      % len(pool))
print('   the Arabic draws %d of them, so those relocate too: %s'
      % (len(plan['relocate']), ''.join(chr(c) for c in sorted(plan['relocate']))))
print('   cost: %d of %d characters (%.2f%%) across the %d line(s) that stay '
      'English' % (plan['spoilt'], plan['whole'], plan['cost'], plan['kept']))
print('\nglyphs planned: %d' % len(mapping))
print('   %d at one byte, %d at two'
      % (plan['singles'], len(mapping) - plan['singles']))
by_slot = {ord(v): k for k, v in mapping.items()}
print('   one-byte slots went to: %s'
      % ' '.join('%s->%s' % (chr(c), by_slot[c]) for c in pool if c in by_slot))

entries = [e for e in project.read('briefing')['entries'] if e.get('target')]
shaped = {(e['record'], e['index']): arabic.strip_harakat(arabic.shape(e['target']))
          for e in entries}

# ---- fonts: install every planned glyph at its new code point ----
print('\ninstalling the glyphs at their new code points...')
#: Forms this face draws sitting flat where Arabic wants them under the line.
#:
#: The jeem/hah/khah family in its joining forms has no descent at all (-3),
#: and the noon bowl only 10 or 11 against the 16 of a yeh and the 19 of a
#: meem -- shallow enough to read as though the letter had been lifted off
#: the line.  Four pixels is as far as any of them go before the entry and
#: exit strokes stop meeting their neighbours and the word visibly breaks.
NUDGE = {form: 4 for form in ("ﺟﺠﺣﺤﺧﺨ"      # jeem, hah, khah: initial
                              "ﻥﻦ")}    # and medial; noon: isolated,
                                        # final

#: How much of the cell's height the script may take.  Arabic wants 80 pixels
#: of a 67 pixel cell -- alef with hamza alone is 60 of them -- so it is
#: scaled down whatever happens; 1.0 is simply as little as it can be scaled,
#: and leaves nothing spare below for a downward nudge on a deep descender.

# The glyph to draw is the original form; the code point is the new one.
# The punctuation is in here too -- it sits above the engine's lookup ceiling
# exactly like a presentation form, so it is relocated rather than drawn at
# itself, which is what left a box where every comma should be.
variants = arabic.mark_variants()
draw, overlay = {}, {}
for form, slot in mapping.items():
    if form in variants:
        mark, bearing = variants[form]
        draw[slot] = mark            # the glyph is the mark itself
        overlay[slot] = bearing      # drawn over the letter, not beside it
    else:
        draw[slot] = form
result = fonts.install_subtitle_face(
    game, out_dir, TTF, sorted(draw.keys()), remap=draw, nudge=NUDGE,
    fill=FILL, overlay=overlay)
for face in result['faces']:
    print('   %-16s painted %s of %d at %s pt  %s'
          % (face['file'], face.get('painted'), result['letters'],
             face.get('size', '?'), face.get('error', '')))
    lost = face.get('clipped') or {}
    if lost:
        print('      FILL is too high: %d form(s) lose ink, worst %s at %d px'
              % (len(lost), max(lost, key=lost.get), max(lost.values())))
        print('      %s' % ' '.join('%s(%d)' % kv for kv in
                                    sorted(lost.items(), key=lambda kv: -kv[1])[:12]))

# ---- the briefing, through the project's own build ----
# Not a second implementation: build_briefing holds back the lines whose
# cipher run crosses a page boundary, and a copy of the logic here would
# forget to.  So store the plan the way the dialog does and call it.
project.manifest.setdefault('font', {})['compact'] = {
    form: ord(slot) for form, slot in mapping.items()}
report = project.build_briefing(print)

built_path = project.output / 'MLG' / 'disc0_rel' / briefing.NAME
data = briefing.load(project.stock_briefing())
built = briefing.load(built_path, briefing.NAME)
print('\nfile length unchanged: %s' % (len(built) == len(data)))
stock, back = briefing.parse(data), briefing.parse(built)
print('records moved: %d'
      % sum(1 for a, b in zip(stock, back) if a.offset != b.offset))
print('fon_off changed: %d'
      % sum(1 for a, b in zip(stock, back) if a.fon_off != b.fon_off))

destination = out_dir / 'MLG' / 'disc0_rel' / briefing.NAME
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_bytes(built_path.read_bytes())
(out_dir / 'mapping.json').write_text(
    json.dumps({k: ord(v) for k, v in mapping.items()},
               ensure_ascii=False, indent=1), encoding='utf-8')
print('\nwritten to %s' % out_dir)
