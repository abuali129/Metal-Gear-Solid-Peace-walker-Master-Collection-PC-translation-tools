"""Disassemble a window of the game exe around a virtual address."""
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


def dis(va, count=40, back=0):
    off = off_of(va - IMG)
    if off is None:
        print('VA 0x%X is not in any section' % va)
        return
    off -= back
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = False
    start_va = IMG + rva_of(off)
    for i, ins in enumerate(md.disasm(d[off:off + count * 15], start_va)):
        mark = '>>' if ins.address == va else '  '
        print('%s 0x%X  %-24s %s %s' % (mark, ins.address,
                                        ins.bytes.hex(' '), ins.mnemonic,
                                        ins.op_str))
        if i >= count:
            break


if __name__ == '__main__':
    va = int(sys.argv[1], 16)
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    back = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    dis(va, count, back)
