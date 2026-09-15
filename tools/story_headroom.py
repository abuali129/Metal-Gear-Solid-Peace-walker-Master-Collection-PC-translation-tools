"""How much room each story block has left, and what is still English in it.

A block is refused on two ceilings: the compressed payload against its
footprint in pages, and the DECOMPRESSED block against its stock size rounded
up to a whole page.  The second is the tight one and the one that hangs the
game, so it is what this reports.
"""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'pwtr/formats')
import olang
import slotdat
import slotitem
from pwtr.project import Project, CAPS_STYLE

project = Project.load(sys.argv[1])
built = sys.argv[2] if len(sys.argv) > 2 else \
    'peace1/output/MLG/disc0_rel/002aba34.DAT'
dat, key = project.stock_slot()
_header, (high, low), records = slotdat.load_key(str(key))
stream = slotdat.WordStream(high, low)
PAGE = slotdat.PAGE

entries = project.read('story')['entries']
todo = {e['source'] for e in entries if not e.get('target')}

rows = []
with open(dat, 'rb') as a, open(built, 'rb') as b:
    for n, record in enumerate(records):
        if n % 200 == 0:
            print('  ... block %d of %d' % (n, len(records)), file=sys.stderr)
        try:
            stock = slotdat.read_block(a, record, stream)
            now = slotdat.read_block(b, record, stream)
            elements, _head, _t = slotitem.parse(stock)
        except Exception:
            continue
        left = 0
        for element in elements:
            if element.ext != 'olang':
                continue
            try:
                table = olang.OlangFile.parse(element.data)
            except Exception:
                continue
            for ref in table.refs:
                if ref.style == CAPS_STYLE:
                    continue
                if olang.LANG_NAME.get(ref.lang) != 'en':
                    continue
                if table.pool[ref.text] in todo:
                    left += 1
        if not left:
            continue
        budget = -(-len(stock) // PAGE) * PAGE
        rows.append((budget - len(now), record.start, left,
                     len(stock), len(now)))

rows.sort()
print('%-8s %-8s %-6s %-9s %s'
      % ('block', 'headroom', 'lines', 'stock', 'now'))
for head, start, left, stock_len, now_len in rows:
    print('%-8s %-8d %-6d %-9d %d'
          % ('%05X' % start, head, left, stock_len, now_len))

tight = [r for r in rows if r[0] < 256]
print('\n%d block(s) still hold untranslated English, %d line(s) in all'
      % (len(rows), sum(r[2] for r in rows)))
print('%d block(s) have under 256 bytes of headroom (%d line(s) in them)'
      % (len(tight), sum(r[2] for r in tight)))
print('median headroom %d bytes'
      % (sorted(r[0] for r in rows)[len(rows) // 2] if rows else 0))
