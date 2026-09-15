"""Narrow a hang to one element, then one string, inside a single block.

Everything outside the block is translated as usual.  Inside it, only the
elements named on the command line are touched, so each round moves one
variable and the answer halves the search.
"""
import shutil
import os
import sys
from pathlib import Path

sys.path.insert(0, '.')
sys.path.insert(0, 'pwtr/formats')

import olang                                                   # noqa: E402
import slotdat                                                 # noqa: E402
import slotitem                                                # noqa: E402

from pwtr.project import Project, CAPS_STYLE                   # noqa: E402

project = Project.load(sys.argv[1])
out = Path(sys.argv[2])
block = int(sys.argv[3], 16)
wanted = {int(x) for x in sys.argv[4].split(',')} if len(sys.argv) > 4 else set()
#: Optional "first:last" over the translatable references of the chosen
#: elements, so one element can be halved the same way the block was.
span = None
picks = None
if len(sys.argv) > 5:
    if ':' in sys.argv[5]:
        lo, hi = sys.argv[5].split(':')
        span = (int(lo), int(hi))
    else:
        picks = {int(x) for x in sys.argv[5].split(',')}

dat, key = project.stock_slot()
_header, (high, low), records = slotdat.load_key(str(key))
stream = slotdat.WordStream(high, low)
translations = {e["source"]: project._render(e["target"], "subtitle")
                for e in project.read("story")["entries"] if e.get("target")}

out.mkdir(parents=True, exist_ok=True)
target = out / "002aba34.DAT"
shutil.copyfile(dat, target)
shutil.copyfile(key, out / "002aba34.KEY")

touched = placed = inside = 0
with open(dat, "rb") as source, open(target, "r+b") as destination:
    for record in records:
        try:
            elements, head, _t = slotitem.parse(
                slotdat.read_block(source, record, stream))
        except Exception:
            continue
        here = record.start == block
        hits = 0
        index = -1
        for element in elements:
            if element.ext != "olang":
                continue
            index += 1
            if here and index not in wanted:
                continue
            try:
                table = olang.OlangFile.parse(element.data)
            except Exception:
                continue
            n = 0
            seen = -1
            for reference in table.refs:
                if olang.LANG_NAME.get(reference.lang) != "en":
                    continue
                if reference.style == CAPS_STYLE:
                    continue
                text = translations.get(table.pool[reference.text])
                if text is None:
                    continue
                if here and (span is not None or picks is not None):
                    seen += 1
                    if span is not None and not span[0] <= seen < span[1]:
                        continue
                    if picks is not None and seen not in picks:
                        continue
                table.set_ref(reference, text)
                n += 1
            if n:
                if not (here and os.environ.get('NO_COMPACT_POOL') == '1'):
                    table.compact_pool()
                element.data = table.build() + element.data[table.length:]
                hits += n
                if here:
                    inside += n
        if not hits:
            continue
        payload = slotdat.build_block_smallest(slotitem.build(elements, head))
        if slotdat.pages_for(payload) > record.footprint:
            continue
        slotdat.write_block(destination, record, payload, stream)
        touched += 1
        placed += hits
print("%d block(s), %d string(s); inside %05X: %d string(s) in element(s) %s"
      % (touched, placed, block, inside, sorted(wanted)))
print("written to %s" % target)
