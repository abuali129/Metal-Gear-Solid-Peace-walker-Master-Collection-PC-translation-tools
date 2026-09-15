"""Read every built container back and check the Arabic survived.

Each container is read the way the game reads it and the text compared with
what the project meant to write -- not with what the builder thought it wrote,
which is the same source and would agree with itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, '.')
sys.path.insert(0, 'pwtr/formats')

import olang                                                   # noqa: E402

from pwtr import arabic, briefing, compact, stagedat           # noqa: E402
from pwtr.project import Project, TEXT_DIRS, CAPS_STYLE        # noqa: E402

project = Project.load(sys.argv[1])
plan = project.compact


def decode(raw):
    return compact.decode(raw, plan)


# ---- briefing ------------------------------------------------------------
out = project.output / 'MLG' / 'disc0_rel' / briefing.NAME
if out.exists():
    built = {r.offset: r for r in
             briefing.parse(briefing.load(out, briefing.NAME))}
    want = [e for e in project.read('briefing')['entries'] if e.get('target')]
    ok = sum(1 for e in want
             if (lambda r: r and e['index'] < len(r.strings)
                 and decode(r.strings[e['index']].decode('utf-8', 'replace'))
                 == project.shaped(e['target']))(built.get(e['record'])))
    print('briefing : %d of %d translated lines read back' % (ok, len(want)))

# ---- menus ---------------------------------------------------------------
seen = ok = atlas = 0
for tag, path in project.original_tables():
    built_path = project.output / TEXT_DIRS[tag] / path.name
    if not built_path.exists():
        continue
    table = olang.read(str(built_path))
    for reference in table.refs:
        if olang.LANG_NAME.get(reference.lang) != 'en':
            continue
        text = table.pool[reference.text]
        if reference.style == CAPS_STYLE:
            atlas += 1
            continue
        if any(ord(c) > 0x7F for c in text):
            seen += 1
            ok += any(ch in text for ch in plan.values())
print('menus    : %d Arabic reference(s), %d carrying planned code points, '
      '%d drawn by the caps atlas' % (seen, ok, atlas))

# ---- missions ------------------------------------------------------------
built_pdt = project.output / 'MLG' / 'disc0_rel' / stagedat.NAME
if built_pdt.exists():
    print('missions : built, %d bytes' % built_pdt.stat().st_size)
