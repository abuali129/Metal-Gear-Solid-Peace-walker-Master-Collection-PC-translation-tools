"""Collect edited STAGEDAT textures into the project's prebuild folder.

    python tools/stage_prebuild.py <project> [--all]

Compares every PNG under `texture/STAGEDAT` with the game's own texture and
copies the ones that differ into `prebuild/STAGEDAT`, keeping the same path.
After that the unpack tree is a reference copy again: only what is in
prebuild gets packed, so nothing has to be guessed at from timestamps.

Without --all only files touched since the unpack are examined, which is far
quicker and finds anything edited in place.
"""
import shutil
import sys
sys.path.insert(0, '.')
from pathlib import Path

from pwtr import stagetex
from pwtr.formats import pdt_pack, qar, txp_pack
from pwtr.project import Project

project = Project.load(sys.argv[1])
source = project.root / 'texture' / 'STAGEDAT'
destination = project.root / 'prebuild' / 'STAGEDAT'
pdt = project.stock_stagedat()

edits = stagetex.candidates(source)
if '--all' not in sys.argv:
    cutoff = stagetex.recent(edits)
    edits = [e for e in edits if e.path.stat().st_mtime > cutoff]
print('%d texture(s) to check' % len(edits))

template = pdt_pack.Template(str(pdt))
by_entity: dict[int, list] = {}
for edit in edits:
    by_entity.setdefault(edit.entity, []).append(edit)

moved = 0
for entity, group in sorted(by_entity.items()):
    data = template.payload(entity)
    if not data:
        continue
    items = qar.parse(data)
    for edit in group:
        if items:
            item = next((i for i in items
                         if Path(i['name']).stem == edit.member), None)
            if item is None:
                continue
            base, span = item['off'], item['size']
        else:
            base, span = 0, len(data)
        slots = stagetex._slots(data[base:base + span])
        if edit.index >= len(slots):
            continue
        off, w, h, fcc, size = slots[edit.index]
        original = data[base + off:base + off + 128 + size]
        problems: list[str] = []
        new = txp_pack._slot_bytes(str(edit.path), {'w': w, 'h': h,
                                                    'fcc': fcc},
                                   problems, original)
        if new is None or new == original:
            continue                       # unchanged, or will not encode
        target = destination / edit.path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(edit.path, target)
        print('   %s' % target.relative_to(project.root))
        moved += 1

print('%d edited texture(s) -> %s' % (moved, destination))
