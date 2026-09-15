r"""QAR - the sub-archive that Peace Walker entities are packed in.

Found inside .PDT entities (see pdt_unpack.py).  Layout, all little-endian,
worked out from the archives themselves:

    0x00                data, entry after entry, each padded up to 128 bytes
    ...
    <toc>   u32 count
            count x { u32 hash, u32 size }
            count x asciiz name
    EOF-4   u32 toc          <- the last four bytes of the file point at it

Entry i starts at the 128-byte-aligned end of entry i-1, entry 0 at offset 0.
The 128-byte alignment is visible directly in the data: every entry's declared
size rounds up to a 128-byte boundary before the next one begins.

    python qar.py list   <file>
    python qar.py unpack <file> [outdir]
    python qar.py scan   <dir>        find every QAR in a directory and list it
"""
from __future__ import annotations

import os
import struct
import sys


def is_qar(d: bytes) -> bool:
    return parse(d) is not None


def parse(d: bytes):
    """-> [ {name, hash, size, off} ] or None if this is not a QAR."""
    if len(d) < 16:
        return None
    toc = struct.unpack_from('<I', d, len(d) - 4)[0]
    if not (0 < toc <= len(d) - 8):
        return None
    count = struct.unpack_from('<I', d, toc)[0]
    if not (0 < count < 4096):
        return None
    need = toc + 4 + count * 8
    if need > len(d):
        return None
    recs = [struct.unpack_from('<2I', d, toc + 4 + 8 * i) for i in range(count)]
    p = need
    names = []
    for _ in range(count):
        e = d.find(b'\0', p)
        if e < 0 or e > len(d):
            return None
        try:
            names.append(d[p:e].decode('ascii'))
        except UnicodeDecodeError:
            return None
        p = e + 1
    out = []
    off = 0
    for (h, size), name in zip(recs, names):
        if off + size > toc:
            return None
        out.append(dict(name=name, hash=h, size=size, off=off))
        off = (off + size + 127) & ~127
    return out


def unpack(path, outdir=None):
    d = open(path, 'rb').read()
    items = parse(d)
    if items is None:
        raise SystemExit('%s is not a QAR' % path)
    outdir = outdir or os.path.splitext(path)[0] + '_qar'
    os.makedirs(outdir, exist_ok=True)
    for it in items:
        open(os.path.join(outdir, it['name']), 'wb').write(
            d[it['off']:it['off'] + it['size']])
    return outdir, items


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'list':
        items = parse(open(sys.argv[2], 'rb').read())
        if items is None:
            raise SystemExit('not a QAR')
        for it in items:
            print('  %-28s %08X %9d @%d' % (it['name'], it['hash'],
                                            it['size'], it['off']))
    elif cmd == 'unpack':
        out, items = unpack(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        print('%d files -> %s' % (len(items), out))
    elif cmd == 'scan':
        root = sys.argv[2]
        n = 0
        for fn in sorted(os.listdir(root)):
            p = os.path.join(root, fn)
            if not os.path.isfile(p) or fn.endswith('.tsv'):
                continue
            items = parse(open(p, 'rb').read())
            if items:
                n += 1
                print('%s  (%d files)' % (fn, len(items)))
                for it in items:
                    print('    %-28s %9d' % (it['name'], it['size']))
        print('\n%d QAR archives' % n)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
