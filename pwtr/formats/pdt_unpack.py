r"""Unpack the Metal Gear Solid: Peace Walker (PC) .PDT containers.

    python pdt_unpack.py                 unpack every .PDT the game ships
    python pdt_unpack.py STAGEDAT        just the one whose PSP name matches
    python pdt_unpack.py <file.PDT>      an explicit path

Output goes to `output\PDT_UNPACKED\<PSPNAME>` beside the program - never into
the game directory.  Each entity is written as <index>_<hash>.<ext>, where the
extension is guessed from the payload magic, plus a `contents.tsv` index.

See pdt.py for the container format and the three-layer cipher.
"""
from __future__ import annotations

import os
import re
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pdt
import pwpaths
import pwcrypt
import qar

GAME = pwpaths.find_game() or ''
OUT_ROOT = os.path.join(pwpaths.default_output(), 'PDT_UNPACKED')

# strcode -> the original PSP file name (see project_pdt_container memory)
NAMES = {0x9645FA: 'STAGEDAT.PDT', 0x01112D: 'BGM.PDT',
         0xB2B2A8: 'VOICEBF.PDT', 0xB2B475: 'VOICEPS.PDT',
         0xB2B4B6: 'VOICERT.PDT'}

MAGIC_EXT = [
    (b'\x89PNG', 'png'), (b'DDS ', 'dds'), (b'XPR2', 'xpr'),
    (b'OggS', 'ogg'), (b'RIFF', 'wav'), (b'\x00\x01\x00\x00', 'ttf'),
    (b'oEbN', 'nbeo'), (b'.noc', 'cachelist'), (b'GIMC', 'gim'),
    (b'MIG.', 'gim'), (b'\x7fELF', 'elf'), (b'PGF0', 'pgf'),
    (b'BWFON', 'bwfon'),
]


# --------------------------------------------------------------------------
# Real file names.
#
# Entities are laid out in GROUPS.  Every group opens with a `.nocache`
# manifest - which is itself the group's `cache.dar` - listing the group's
# members.  Two things about that list cost a day to see:
#
#   * the entities are NOT in the manifest's listing order.  They are sorted
#     by a fixed EXTENSION rank (below).  Reading the list top to bottom
#     silently mis-names every file from `.geom` onwards - `.zon` payloads get
#     called `.gcx`, `.nav` payloads get called `.zon`, and so on, while the
#     first and last entries still look right, which is what makes it seem
#     like "part of the files work".
#   * lines beginning with a dot are directives, not names - except `.vram`,
#     which declares TWO further members:  `.vram <stem>.vrd <stem>.vram`.
#     Skipping it leaves those groups two entities short.
#
# Verified on STAGEDAT: 557/557 entities named, and of the 240 whose payload
# magic identifies the type on its own (.nocache/oEbN/NAVX/ZONX), 240 agree.
EXT_RANK = {'dar': 0, 'qar': 1, 'geom': 2, 'vrd': 3, 'vram': 4,
            'gcx': 5, 'nav': 6, 'zon': 7, 'rlc': 8}


def manifest_names(payload: bytes) -> list:
    """Ordered member names for the group a `.nocache` manifest opens."""
    names = []
    for line in payload.decode('ascii', 'ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('.'):
            part = line.split()
            if part[0] == '.vram':
                names.extend(part[1:3])          # <stem>.vrd <stem>.vram
            continue
        names.append(line)
    return sorted(names, key=lambda f: (
        EXT_RANK.get(os.path.splitext(f)[1].lstrip('.').lower(), 99), f))


def guess_ext(d: bytes) -> str:
    for sig, ext in MAGIC_EXT:
        if d.startswith(sig):
            return ext
    if d[:2] == b'\x1f\x8b':
        return 'gz'
    printable = sum(1 for b in d[:256] if 9 <= b <= 13 or 32 <= b < 127)
    if len(d) >= 16 and printable >= len(d[:256]) * 0.95:
        return 'txt'
    return 'bin'


def unpack(path: str, out_root: str = OUT_ROOT, deep: bool = True) -> str:
    fn = os.path.basename(path)
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = f.read(0x40000)
    hdr = pdt.read_header(pwcrypt.decrypt(head, fn))
    if not hdr['ok']:
        raise SystemExit('%s: layer-2 magic missing - not a PDT' % fn)

    need = hdr['lookup'] + hdr['count'] * 16
    with open(path, 'rb') as f:
        table_raw = f.read(need + 4096)
    dec_tables = pwcrypt.decrypt(table_raw, fn)
    hdr = pdt.read_header(dec_tables)
    ents = pdt.read_tables(dec_tables, hdr)

    stem = NAMES.get(struct.unpack('<I', bytes.fromhex(os.path.splitext(fn)[0]))[0]
                     if False else 0, None)
    try:
        code = int(os.path.splitext(fn)[0], 16)
    except ValueError:
        code = 0
    stem = NAMES.get(code, os.path.splitext(fn)[0])
    out = os.path.join(out_root, os.path.splitext(stem)[0])
    os.makedirs(out, exist_ok=True)

    print('%s -> %s   (%d entities, %.1f MB, cipher %s)'
          % (fn, stem, hdr['count'], size / 1e6, type(hdr['state']).__name__),
          flush=True)

    ok = bad = qars = tex = named = 0
    pending, taken = [], 0
    rows = []
    with open(path, 'rb') as f:
        for i, e in enumerate(ents):
            f.seek(e['pos'])
            blob = f.read(e['size'])
            if len(blob) < e['size']:
                bad += 1
                continue
            seg = pwcrypt.decrypt(blob, fn)          # the stream RESTARTS here
            b = bytearray(seg)
            hdr['dec'](b, 0, len(b), pdt.fresh_cipher(hdr))
            declared = struct.unpack_from('<I', b, 0)[0]
            try:
                data = zlib.decompress(bytes(b[4:]))
            except Exception:
                try:
                    data = zlib.decompressobj().decompress(bytes(b[4:]))
                except Exception:
                    bad += 1
                    rows.append((i, e['hash'], e['pos'], e['size'], 0, 0, 'FAILED'))
                    continue
            if data[:8] == b'.nocache':        # a manifest opens a new group
                pending, taken = manifest_names(data), 0

            real = pending[taken] if taken < len(pending) else None
            if real:
                taken += 1
                stem = '%04d_%s' % (i, os.path.splitext(real)[0])
                ext = os.path.splitext(real)[1].lstrip('.') or guess_ext(data)
                named += 1
            else:
                stem = '%04d_%08X' % (i, e['hash'])
                ext = guess_ext(data)
            name = '%s.%s' % (stem, ext)
            open(os.path.join(out, name), 'wb').write(data)
            ok += 1
            rows.append((i, e['hash'], e['pos'], e['size'], declared, len(data),
                         name if declared == len(data) else name + '  LEN-MISMATCH'))
            if deep:
                n_q, n_t = _dig(data, out, stem)
                qars += n_q
                tex += n_t
            if ok % 50 == 0:
                print('   ... %d/%d' % (ok, hdr['count']), flush=True)

    with open(os.path.join(out, 'contents.tsv'), 'w', encoding='utf-8') as fh:
        fh.write('index\thash\toffset\tcompressed\tdeclared\tinflated\tfile\n')
        for r in rows:
            fh.write('%d\t%08X\t%d\t%d\t%d\t%d\t%s\n' % r)

    print('   inflated %d, failed %d, named from manifests %d'
          % (ok, bad, named), flush=True)
    if deep:
        print('   QAR sub-archives %d, textures %d' % (qars, tex), flush=True)
    print('   -> %s' % out, flush=True)
    return out


def _dig(data: bytes, out: str, stem: str):
    """Follow the chain that actually leads to the pictures.

    An entity is not a texture. The DDS files sit three levels down:

        .PDT entity  ->  QAR sub-archive  ->  .txp member  ->  DDS

    Without this step the unpacker writes 557 opaque blobs and no images at
    all, which is exactly what it used to do.
    """
    items = qar.parse(data)
    if not items:
        return 0, _rip_dds(data, out, stem)
    d = os.path.join(out, stem + '_qar')
    os.makedirs(d, exist_ok=True)
    n_tex = 0
    for it in items:
        member = data[it['off']:it['off'] + it['size']]
        open(os.path.join(d, it['name']), 'wb').write(member)
        n_tex += _rip_dds(member, d, os.path.splitext(it['name'])[0])
    return 1, n_tex


def _rip_dds(blob: bytes, out: str, stem: str, dds: bool = False) -> int:
    """Every embedded texture, decoded to PNG.

    The textures carry a real DDS header, so scanning for the magic is exact -
    no need to decode the .txp master table to find them.

    PNG for the same reason the .txp unpacker writes it: a DDS is not
    something most editors open, and these are here to be looked at.  The
    original block-compressed bytes are written too only when asked for.
    """
    from PIL import Image
    import numpy as np
    from pwtex import decode_bc, DXT1, DXT5

    formats = {b'DXT1': DXT1, b'DXT3': DXT5, b'DXT5': DXT5}
    n = 0
    for m in re.finditer(b'DDS ', blob):
        i = m.start()
        if i + 128 > len(blob):
            continue
        h, w = struct.unpack_from('<2I', blob, i + 12)
        fcc = blob[i + 84:i + 88]
        if fcc not in (b'DXT1', b'DXT3', b'DXT5'):
            continue
        if not (0 < w <= 8192 and 0 < h <= 8192):
            continue
        blk = 8 if fcc == b'DXT1' else 16
        size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * blk
        if i + 128 + size > len(blob):
            continue
        d = os.path.join(out, stem + '_tex')
        os.makedirs(d, exist_ok=True)
        name = '%02d_%dx%d_%s' % (n, w, h, fcc.decode())
        if dds:
            open(os.path.join(d, name + '.dds'), 'wb').write(
                blob[i:i + 128 + size])
        try:
            rgba = decode_bc(blob[i + 128:i + 128 + size], w, h,
                             formats[fcc])
            Image.fromarray(np.asarray(rgba), 'RGBA').save(
                os.path.join(d, name + '.png'))
        except Exception:
            # A texture that will not decode is still worth having, and the
            # bytes are the only honest thing left to give.
            open(os.path.join(d, name + '.dds'), 'wb').write(
                blob[i:i + 128 + size])
        n += 1
    return n


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    todo = []
    if arg and os.path.isfile(arg):
        todo = [arg]
    else:
        for sub in ('EXLANG/disc0_rel', 'MLG/disc0_rel'):
            d = os.path.join(GAME, *sub.split('/'))
            if not os.path.isdir(d):
                continue
            for n in sorted(os.listdir(d)):
                if not n.lower().endswith('.pdt'):
                    continue
                try:
                    code = int(os.path.splitext(n)[0], 16)
                except ValueError:
                    continue
                if arg and arg.upper() not in NAMES.get(code, '').upper():
                    continue
                todo.append(os.path.join(d, n))
            if todo:
                break            # EXLANG and MLG hold the same archives
    if not todo:
        raise SystemExit('nothing to unpack')
    for p in todo:
        unpack(p)


if __name__ == '__main__':
    main()
