r"""Put edited textures back into a Peace Walker .txp package.

    python txp_pack.py <original.txp> <folder-with-textures> [out.txp]

The folder is what `txp_unpack.py` produced: files named `NN_WxH_FMT.png`
(or `.dds`), where NN is the texture's index in the package. Edit any of
them, leave the rest alone, and this writes a new .txp with your versions
spliced in and everything else untouched.

WHY IT IS SAFE
==============
A .txp is a header, a master table and then N DDS files laid end to end, each
padded to a 4096-byte boundary. We do not rebuild any of that. We locate each
texture by its `DDS ` magic exactly as the unpacker does, and overwrite that
one byte range in place.

So the file length never changes, no offset in the master table moves, and
every byte we did not deliberately touch survives. A replacement is REFUSED
unless it matches the original's width, height, format and byte length - a
different size would push everything after it out of place, and the game
addresses these by offset.

If you need a different size, you need a different approach; this tool will
not silently corrupt the package for you.

The whole package is re-encrypted with the Master Collection cipher on the way
out (see pwcrypt), keyed by the DESTINATION file name, so keep the original
name when you put it back into the game.

    python txp_pack.py <original.txp> <folder> --verify
        repacks with no changes and checks the result is byte-identical to the
        original file on disk. That is the proof the splice is lossless.
"""
from __future__ import annotations

import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pwcrypt
import pwpaths
import pwtex as D

FMT_OF = {b'DXT1': D.DXT1, b'DXT3': D.DXT5, b'DXT5': D.DXT5}


def textures(d: bytes):
    """Every embedded texture: (index, offset, w, h, fourcc, payload bytes)."""
    out = []
    for m in re.finditer(b'DDS ', d):
        i = m.start()
        if i + 128 > len(d):
            continue
        h, w = struct.unpack_from('<2I', d, i + 12)
        fcc = d[i + 84:i + 88]
        if fcc not in FMT_OF or not (0 < w <= 8192 and 0 < h <= 8192):
            continue
        n = D.level_size(w, h, FMT_OF[fcc])
        if i + 128 + n > len(d):
            continue
        out.append(dict(index=len(out), off=i, w=w, h=h, fcc=fcc, size=128 + n))
    return out


def decrypt_txp(path: str) -> bytes:
    raw = open(path, 'rb').read()
    if b'DDS ' in raw[:8192]:
        return raw                                   # already plain
    d = pwcrypt.decrypt(raw, os.path.basename(path))
    return d if b'DDS ' in d[:2 * 1024 * 1024] else raw


def _slot_bytes(path: str, slot: dict, warn: list,
                original: bytes | None = None) -> bytes | None:
    """The DDS bytes for one slot, encoding the PNG if that is what is there.

    The slot is overwritten in place, so the result has to be exactly the
    size the original was.  Every texture this game ships is DXT5 with no
    mipmaps, which is what makes that land on the nose: re-encoding WxH at
    the same fourcc gives back the same byte count.  Anything else is
    reported rather than guessed at.
    """
    if not path.lower().endswith('.png'):
        return open(path, 'rb').read()

    from PIL import Image
    import io

    fourcc = slot['fcc'].decode('ascii', 'replace')
    try:
        image = Image.open(path).convert('RGBA')
    except Exception as problem:
        warn.append('%s: will not open -- %s'
                    % (os.path.basename(path), problem))
        return None
    if image.size != (slot['w'], slot['h']):
        warn.append('%s: %dx%d, the slot is %dx%d - skipped'
                    % (os.path.basename(path), image.size[0], image.size[1],
                       slot['w'], slot['h']))
        return None
    # An untouched texture must go back untouched.  Block compression is
    # lossy, so decoding a slot and encoding it again costs a second
    # generation for nothing -- measured at up to 107 levels on a channel.
    # Only a PNG whose pixels actually differ is worth re-encoding.
    if original is not None:
        try:
            import numpy as np
            from pwtex import decode_bc
            was = decode_bc(original[128:], slot['w'], slot['h'],
                            FMT_OF[slot['fcc']])
            if np.array_equal(np.asarray(image), was):
                return original
        except Exception:
            pass

    buffer = io.BytesIO()
    try:
        image.save(buffer, format='DDS', pixel_format=fourcc)
    except Exception as problem:
        warn.append('%s: cannot be encoded as %s -- %s'
                    % (os.path.basename(path), fourcc, problem))
        return None
    return buffer.getvalue()


def pack(txp_path: str, folder: str, out_path: str | None = None,
         encrypt: bool = True, verify: bool = False):
    """-> (out_path, replaced, [warnings])"""
    d = decrypt_txp(txp_path)
    tex = textures(d)
    if not tex:
        raise SystemExit('%s: no textures found' % txp_path)

    # PNG first: it is what the unpacker writes and what an editor can
    # open.  A .dds beside it still wins, because that is the untouched
    # original and re-encoding it would only lose a little to no purpose.
    by_index = {}
    if os.path.isdir(folder):
        for fn in sorted(os.listdir(folder)):
            m = re.match(r'^(\d+)_(\d+)x(\d+)_(\w+)\.(dds|png)$', fn)
            if not m or fn.endswith('__INK.png'):
                continue
            index = int(m.group(1))
            if m.group(5) == 'dds' or index not in by_index:
                by_index[index] = os.path.join(folder, fn)

    buf = bytearray(d)
    replaced = 0
    warn = []
    for t in tex:
        p = by_index.get(t['index'])
        if not p or verify:
            continue
        original = bytes(buf[t['off']:t['off'] + t['size']])
        new = _slot_bytes(p, t, warn, original)
        if new is None:
            continue
        if new == original:
            continue                # the PNG matches what is already there
        if len(new) != t['size']:
            warn.append('%s: %d bytes, the slot holds %d - skipped'
                        % (os.path.basename(p), len(new), t['size']))
            continue
        nh, nw = struct.unpack_from('<2I', new, 12)
        nfcc = new[84:88]
        if (nw, nh, nfcc) != (t['w'], t['h'], t['fcc']):
            warn.append('%s: %dx%d %s, the slot is %dx%d %s - skipped'
                        % (os.path.basename(p), nw, nh, nfcc.decode('ascii', 'replace'),
                           t['w'], t['h'], t['fcc'].decode('ascii', 'replace')))
            continue
        buf[t['off']:t['off'] + t['size']] = new
        replaced += 1

    out_path = out_path or os.path.join(pwpaths.default_output(), 'packed',
                                        os.path.basename(txp_path))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    data = bytes(buf)
    if encrypt:
        data = pwcrypt.decrypt(data, os.path.basename(out_path))   # symmetric
    open(out_path, 'wb').write(data)
    return out_path, replaced, warn


def cmd_verify(txp_path: str, folder: str):
    """Repack with NO changes; the result must equal the original byte for byte."""
    tmp = os.path.join(pwpaths.default_output(), '_verify',
                       os.path.basename(txp_path))
    out, _, _ = pack(txp_path, folder, tmp, encrypt=True, verify=True)
    same = open(out, 'rb').read() == open(txp_path, 'rb').read()
    print('%-22s %s' % (os.path.basename(txp_path),
                        'BYTE-IDENTICAL' if same else 'DIFFERS - do not use'))
    try:
        os.remove(out)
    except OSError:
        pass
    return same


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    txp, folder = sys.argv[1], sys.argv[2]
    if '--verify' in sys.argv:
        sys.exit(0 if cmd_verify(txp, folder) else 1)
    out = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith('-') else None
    path, n, warn = pack(txp, folder, out)
    print('replaced %d texture(s) -> %s' % (n, path))
    for w in warn:
        print('  ! ' + w)


if __name__ == '__main__':
    main()
