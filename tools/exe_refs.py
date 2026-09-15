"""Find code that touches a VA range, and name the function it sits in.

Two things make this reliable on a 9.5 MB .text that capstone cannot sweep
linearly (it stops at the first byte of padding or data):

* references are found by **scanning for the displacement**, not by
  disassembling -- every 4-byte window is tried as a RIP-relative disp32 and
  kept if it lands in the range asked for;
* the function each hit belongs to comes from **.pdata**, the x64 exception
  table, which lists every function's start and end outright.
"""
import bisect
import struct
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_X86, CS_MODE_64

EXE = (r'C:\Program Files (x86)\Steam\steamapps\common'
       r'\MGS_PW\mgspw\METAL GEAR SOLID PEACE WALKER.exe')
IMG = 0x140000000
d = Path(EXE).read_bytes()

_e = struct.unpack_from('<I', d, 0x3C)[0]
_n = struct.unpack_from('<H', d, _e + 6)[0]
_so = _e + 24 + struct.unpack_from('<H', d, _e + 20)[0]
SECS = []
for i in range(_n):
    o = _so + i * 40
    name = d[o:o + 8].rstrip(b'\0').decode('ascii', 'replace')
    vsize, va, rsize, raw = struct.unpack_from('<4I', d, o + 8)
    SECS.append((name, va, vsize, raw, rsize))
TEXT = next(s for s in SECS if s[0] == '.text')
PDATA = next(s for s in SECS if s[0] == '.pdata')


def rva_of(off):
    for name, va, vsize, raw, rsize in SECS:
        if raw <= off < raw + rsize:
            return va + (off - raw)
    return None


def off_of(rva):
    for name, va, vsize, raw, rsize in SECS:
        if va <= rva < va + max(vsize, rsize):
            return raw + (rva - va)
    return None


def functions():
    """(start_rva, end_rva) for every function, from .pdata."""
    _, va, vsize, raw, rsize = PDATA
    out = []
    for o in range(raw, raw + min(vsize, rsize), 12):
        begin, end, _unwind = struct.unpack_from('<3I', d, o)
        if begin and end > begin:
            out.append((begin, end))
    out.sort()
    return out


FUNCS = functions()
STARTS = [f[0] for f in FUNCS]


def func_of(rva):
    i = bisect.bisect_right(STARTS, rva) - 1
    if i >= 0 and FUNCS[i][0] <= rva < FUNCS[i][1]:
        return FUNCS[i]
    return None


def refs(lo_va, hi_va):
    """Every .text offset whose disp32 lands in [lo, hi)."""
    _, tva, tvsize, traw, trsize = TEXT
    lo, hi = lo_va - IMG, hi_va - IMG
    hits = []
    for off in range(traw, traw + trsize - 4):
        disp = struct.unpack_from('<i', d, off)[0]
        if disp == 0:
            continue
        target = tva + (off + 4 - traw) + disp
        if lo <= target < hi:
            hits.append((off, target))
    return hits


def show(lo_va, hi_va):
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    seen = {}
    for off, target in refs(lo_va, hi_va):
        r = rva_of(off)
        fn = func_of(r)
        # decode the instruction that owns this displacement: back up a few
        # bytes and take the one whose length covers the disp
        found = None
        for back in range(3, 10):
            start = off - back
            for ins in md.disasm(d[start:start + 16], IMG + rva_of(start)):
                if ins.address + ins.size > IMG + r and 'rip' in ins.op_str:
                    found = ins
                break
            if found:
                break
        if not found:
            continue
        key = found.address
        if key in seen:
            continue
        seen[key] = True
        where = ('fn 0x%X' % (IMG + fn[0])) if fn else 'no function'
        print('  0x%X  %-6s %-42s -> 0x%X   [%s]'
              % (found.address, found.mnemonic, found.op_str, target, where))
    print('%d distinct instruction(s)' % len(seen))


if __name__ == '__main__':
    lo = int(sys.argv[1], 16)
    hi = int(sys.argv[2], 16) if len(sys.argv) > 2 else lo + 0x40
    print('%d functions in .pdata' % len(FUNCS))
    print('references into 0x%X .. 0x%X:' % (lo, hi))
    show(lo, hi)
