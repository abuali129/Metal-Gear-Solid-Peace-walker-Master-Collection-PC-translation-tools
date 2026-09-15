"""Rebuild the story's blocks without translating a word.

If the game hangs on this, the repack is at fault -- the parse, the pool
compaction, the reassembly -- and not the Arabic, because there is no Arabic
in it.  If the game is happy, the write path is sound and the trouble is in
what we put through it.

Every block the real build would touch is rebuilt here with its own text.
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
dat, key = project.stock_slot()
_header, (high, low), records = slotdat.load_key(str(key))
stream = slotdat.WordStream(high, low)
wanted = {e["source"] for e in project.read("story")["entries"]
          if e.get("target")}

out.mkdir(parents=True, exist_ok=True)
target = out / "002aba34.DAT"
print("copying %s..." % dat.name)
shutil.copyfile(dat, target)
shutil.copyfile(key, out / "002aba34.KEY")

touched = refused = 0
with open(dat, "rb") as source, open(target, "r+b") as destination:
    for record in records:
        try:
            elements, head, _t = slotitem.parse(
                slotdat.read_block(source, record, stream))
        except Exception:
            continue
        would = False
        for element in elements:
            if element.ext != "olang":
                continue
            try:
                table = olang.OlangFile.parse(element.data)
            except Exception:
                continue
            hits = 0
            for reference in table.refs:
                if olang.LANG_NAME.get(reference.lang) != "en":
                    continue
                if table.pool[reference.text] in wanted:
                    # rewritten with the very text it already holds
                    table.set_ref(reference, table.pool[reference.text])
                    hits += 1
            if hits:
                table.compact_pool()
                element.data = table.build() + element.data[table.length:]
                would = True
        if not would:
            continue
        payload = slotdat.build_block(slotitem.build(elements, head))
        if slotdat.pages_for(payload) > record.footprint:
            refused += 1
            continue
        slotdat.write_block(destination, record, payload, stream)
        touched += 1

print("%d block(s) rebuilt with their own text, %d would not fit"
      % (touched, refused))
print("written to %s" % out)
