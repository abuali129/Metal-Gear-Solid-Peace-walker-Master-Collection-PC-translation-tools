"""Put edited STAGEDAT textures back into a new 009645fa.PDT.

    python tools/stage_textures.py <project> [--all]

By default only PNGs touched since the unpack are checked; --all decodes
every texture in the container instead, which is thorough and slow.
"""
import sys
sys.path.insert(0, '.')
from pathlib import Path
from pwtr import stagetex
from pwtr.project import Project

project = Project.load(sys.argv[1])
root = project.root / 'texture' / 'STAGEDAT'
pdt = project.game / 'MLG' / 'disc0_rel' / stagetex.NAME
out = project.output / 'MLG' / 'disc0_rel' / stagetex.NAME

edits = stagetex.candidates(root)
since = None if '--all' in sys.argv else stagetex.recent(edits)
report = stagetex.build(pdt, root, out, note=lambda m: print('   ' + str(m)),
                        since=since)
for line in report['complaints']:
    print('   ! ' + line)
print('%(textures)d texture(s) in %(entities)d entity/entities' % report)
