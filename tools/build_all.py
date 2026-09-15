"""Font and text for Briefing, Menus and Missions, planned together.

The plan and the painted glyphs are one pair, and the plan is drawn from the
categories named here -- a form that earns no code point is a form the face
never carries.  Story is deliberately left out: it is the largest container
and the one most likely to need its own decisions, and adding it to the plan
now would reshuffle every code point for the sake of two glyphs.
"""
import sys
from pathlib import Path

sys.path.insert(0, '.')
from pwtr import arabic, compact, fonts                        # noqa: E402
from pwtr.project import Project                               # noqa: E402

#: Fallbacks only.  What the project already carries wins over every
#: one of them: the face and its settings are a judgement made by eye
#: in the dialog, and a script that quietly painted a different font
#: would undo that choice on the next rebuild -- the way the plan and
#: the story container drifted apart once already.
TTF = r'C:\Users\HP\Desktop\MGSPW\SCE-PS3-NR-R-JPN.TTF'
FILL = 0.97
JOIN = 128        # trim the antialiased fringe so joins meet solid
TIGHTEN = 4       # and overlap, for the fade the engine adds after it
NUDGE = {form: 4 for form in "\uFE9F\uFEA0\uFEA3\uFEA4\uFEA7\uFEA8"}
SCOPE = ('briefing', 'menus', 'stage')

project = Project.load(sys.argv[1])

settings = project.manifest.get('font') or {}
TTF = settings.get('sub_ttf') or TTF
FILL = float(settings.get('fill', FILL))
JOIN = int(settings.get('join', JOIN))
TIGHTEN = int(settings.get('tighten', TIGHTEN))
NUDGE = settings.get('nudge') or NUDGE
print('face %s -- size %.2f, join %d, overlap %d'
      % (Path(TTF).name, FILL, JOIN, TIGHTEN))

# measure, so the marks can be centred; then plan, so the face knows what to
# carry; only then paint.
measured = ([chr(c) for c in range(0xFE70, 0xFEFD)]
            + [chr(m) for m in sorted(arabic.MARKS)])
shape = fonts.metrics(TTF, measured, 67, FILL)
project.manifest.setdefault('font', {})['widths'] = {
    ch: box[2] - box[0] for ch, box in shape['boxes'].items()}

frozen = bool(settings.get('freeze')) and project.compact
if frozen:
    # The dialog's Freeze mapping, honoured here too.  A script that quietly
    # replanned would move every code point and leave the containers built
    # against the old ones -- which is the whole thing freezing exists to
    # prevent, and it would happen without a word.
    mapping = project.compact
    print('mapping frozen: %d glyph(s) at the code points already installed'
          % len(mapping))
else:
    plan = project.compact_plan(SCOPE)
    mapping = plan['mapping']
    project.manifest['font']['compact'] = {f: ord(s) for f, s in mapping.items()}
    project.save_manifest()
    print('plan from %s: %d glyphs, %d on one byte, %.2f%% of untranslated '
          'English hurt' % ('+'.join(SCOPE), len(mapping), plan['singles'],
                            plan['cost']))

variants = arabic.mark_variants()
draw, overlay, alias = {}, {}, {}
for form, slot in mapping.items():
    if form in variants:
        mark, bearing = variants[form]
        draw[slot], overlay[slot] = mark, bearing
    elif not fonts._is_arabic(form):
        # Not a form the face lacks -- a character it already draws, moved to
        # a two-byte code point so its one-byte slot can carry Arabic.  It
        # keeps its own glyph; painting it again from the Arabic TTF is what
        # put two different faces inside ".LIFE".
        alias[slot] = form
    else:
        draw[slot] = form
result = fonts.install_subtitle_face(project.game, project.output, TTF,
                                     sorted(draw) + sorted(alias), remap=draw,
                                     nudge=NUDGE, fill=FILL, overlay=overlay,
                                     alias=alias, join=JOIN,
                                     tighten=TIGHTEN)
for face in result['faces']:
    print('   %-16s %s painted, %s aliased, at %s pt'
          % (face['file'], face.get('painted'), face.get('aliased'),
             face.get('size')))

for category in ('briefing', 'menus', 'stage'):
    print('\n-- %s' % category)
    said = []
    report = project.build(category, said.append)
    for line in said[-3:]:
        print('   %s' % line)
    project.still_english(category, report, lambda m: print('   ' + m))
