"""Grow one briefing record and move everything after it.

The container has always been treated as fixed: a record must not grow,
because growing one by sixteen bytes once broke every tape after it.  But the
game ships two briefing files, MLG's and EXLANG's, whose records sit at
different offsets -- they part company at record 42 and drift 24 384 bytes
apart -- and it plays both.  There is no index beside either file.  So the
positions are derived, not compiled in, and the old failure has another
explanation: a record is keyed from its own page start, and moving a record
changes that.  The old rebuild re-encrypted page-wise and uniformly, so
everything downstream decrypted to noise.

This rebuilds the file properly.  Each record's plaintext is taken at its own
alignment, one record is given sixteen more bytes, everything after it moves,
and each record is encrypted again from wherever it now begins.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, '.')
sys.path.insert(0, 'pwtr/formats')

import pwcrypt                                                 # noqa: E402

from pwtr import briefing                                      # noqa: E402
from pwtr.project import Project                               # noqa: E402

PAGE = briefing.PAGE
GROW = 16

project = Project.load(sys.argv[1])
out = Path(sys.argv[2])
raw = Path(project.stock_briefing()).read_bytes()
records = briefing.parse(briefing.load(project.stock_briefing()))
print('%d records, %d bytes' % (len(records), len(raw)))

stream = pwcrypt.crypt(b'\x00' * (PAGE * 16), briefing.NAME)


def plain_of(index):
    """A record's bytes, decrypted at the alignment it actually uses."""
    start = records[index].offset
    end = (records[index + 1].offset if index + 1 < len(records) else len(raw))
    low = (start // PAGE) * PAGE
    return bytes(raw[x] ^ stream[x - low] for x in range(start, end))


# ---- grow an early record, so that most of the file has to move ----------
target = 40
print('growing record %d at 0x%06X by %d bytes; %d records move'
      % (target, records[target].offset, GROW, len(records) - target - 1))

out_bytes = bytearray(raw[:records[0].offset])
placed = []
for index in range(len(records)):
    body = plain_of(index)
    if index == target:
        body += b'\x00' * GROW          # room at the tail, past the FON block
    placed.append((len(out_bytes), body))
    out_bytes += body

# ---- encrypt each record from wherever it now begins ---------------------
final = bytearray(out_bytes)
for start, body in placed:
    low = (start // PAGE) * PAGE
    for n in range(len(body)):
        x = start + n
        final[x] = body[n] ^ stream[x - low]

Path(out).parent.mkdir(parents=True, exist_ok=True)
Path(out).write_bytes(bytes(final))
print('written: %d bytes (%+d)' % (len(final), len(final) - len(raw)))

# ---- read it back the way the game would --------------------------------
good = bad = 0
for start, body in placed:
    low = (start // PAGE) * PAGE
    got = bytes(final[start + n] ^ stream[start + n - low]
                for n in range(min(4, len(body))))
    (good := good + 1) if got == briefing.MAGIC else (bad := bad + 1)
print('records whose header reads back correctly: %d of %d (%d wrong)'
      % (good, len(placed), bad))
print('the record that moved furthest: 0x%06X -> 0x%06X'
      % (records[-1].offset, placed[-1][0]))
