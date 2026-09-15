"""Build the story, leaving named blocks stock.

For bisecting a hang: the block a symptom points at is left in English while
everything else is translated, so a working game says the fault is in there
and a broken one says it is not.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, '.')
sys.path.insert(0, 'pwtr/formats')

import olang                                                   # noqa: E402
import slotdat                                                 # noqa: E402
import slotitem                                                # noqa: E402

from pwtr.project import Project                               # noqa: E402

project = Project.load(sys.argv[1])
out = Path(sys.argv[2])
skip = {int(x, 16) for x in sys.argv[3].split(',')} if len(sys.argv) > 3 else set()

dat, key = project.stock_slot()
_header, (high, low), records = slotdat.load_key(str(key))
stream = slotdat.WordStream(high, low)
translations = {e["source"]: project._render(e["target"], "subtitle")
                for e in project.read("story")["entries"] if e.get("target")}

out.mkdir(parents=True, exist_ok=True)
target = out / "002aba34.DAT"
shutil.copyfile(dat, target)
shutil.copyfile(key, out / "002aba34.KEY")

touched = skipped = placed = 0
with open(dat, "rb") as source, open(target, "r+b") as destination:
    for record in records:
        if record.start in skip:
            skipped += 1
            continue
        try:
            elements, head, _t = slotitem.parse(
                slotdat.read_block(source, record, stream))
        except Exception:
            continue
        hits = 0
        for element in elements:
            if element.ext != "olang":
                continue
            try:
                table = olang.OlangFile.parse(element.data)
            except Exception:
                continue
            n = 0
            for reference in table.refs:
                if olang.LANG_NAME.get(reference.lang) != "en":
                    continue
                target_text = translations.get(table.pool[reference.text])
                if target_text is None:
                    continue
                table.set_ref(reference, target_text)
                n += 1
            if n:
                table.compact_pool()
                element.data = table.build() + element.data[table.length:]
                hits += n
        if not hits:
            continue
        payload = slotdat.build_block_smallest(
            slotitem.build(elements, head))
        if slotdat.pages_for(payload) > record.footprint:
            continue
        slotdat.write_block(destination, record, payload, stream)
        touched += 1
        placed += hits

print("%d block(s) translated, %d left stock by name, %d string(s) placed"
      % (touched, skipped, placed))
print("written to %s" % target)
