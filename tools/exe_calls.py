"""Who calls this function, and what constants live inside it.

Call sites are found by scanning for the E8/E9 rel32 encodings rather than by
disassembling .text, for the same reason as _refs.py: a linear sweep of 9.5 MB
stops at the first byte that is not an instruction.
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
_, TVA, TVSIZE, TRAW, TRSIZE = TEXT


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
    _, va, vsize, raw, rsize = PDATA
    out = []
    for o in range(raw, raw + min(vsize, rsize), 12):
        begin, end, _u = struct.unpack_from('<3I', d, o)
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


def callers(target_va):
    """Every call/jmp rel32 landing on target_va."""
    target = target_va - IMG
    out = []
    for off in range(TRAW + 1, TRAW + TRSIZE - 4):
        if d[off - 1] not in (0xE8, 0xE9):
            continue
        rel = struct.unpack_from('<i', d, off)[0]
        here = TVA + (off + 4 - TRAW)
        if here + rel == target:
            out.append((IMG + TVA + (off - 1 - TRAW), d[off - 1]))
    return out


def body(start_va, end_va):
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    off = off_of(start_va - IMG)
    size = end_va - start_va
    return list(md.disasm(d[off:off + size], start_va))


if __name__ == '__main__':
    va = int(sys.argv[1], 16)
    fn = func_of(va - IMG)
    if fn:
        print('function 0x%X .. 0x%X  (%d bytes)'
              % (IMG + fn[0], IMG + fn[1], fn[1] - fn[0]))
    print('callers of 0x%X:' % va)
    for site, opcode in callers(va):
        f = func_of(site - IMG)
        print('  0x%X  %s   [in fn 0x%X]'
              % (site, 'call' if opcode == 0xE8 else 'jmp',
                 IMG + f[0] if f else 0))
