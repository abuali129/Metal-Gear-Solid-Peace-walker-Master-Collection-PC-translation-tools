r"""Every font in Peace Walker, and where it hides.

The fonts are textures, as they are in MGS4, and there is not one of them -
there are hundreds, spread across four different containers:

    Text\*.txp, loading\*.txp   ~159 glyph sheets over 8 packages
    FONT\*.xpr                  2 XPR2 faces - the SUBTITLE font
    002aba34.DAT  (SLOT)        215 glyph sheets inside .slot elements
    009645fa.PDT  (STAGEDAT)    db_font.txp / fontprint.txp

## The duplication that matters

`Text\008299c5.txp`, `0082988a.txp`, `005318e4.txp` and `005318e5.txp` are four
different files of exactly 15 228 928 bytes, and **texture 10 is byte-identical
in all four** (sha1 dc5f725f...).  They are the same UI face shipped once per
language package.  Patching one and installing it is the classic way to change
nothing on screen: the game loads whichever package the current language
selects.  So `install_menu` writes **every** copy, and the catalogue groups
sheets by content hash so a duplicate can never be missed again.

## Roles

The GUI needs to offer a different typeface per role, because these are
genuinely different faces:

    menu       the caps-only UI grid, 28x48 cells - see pwfontatlas, the one
               whose byte mapping is fully decoded
    subtitle   FONT\*.xpr, proportional, charmap indexed by real Unicode
    other      every remaining sheet: detected grid, dump/import only, because
               the byte mapping for those has not been decoded and guessing it
               would paint glyphs the engine never asks for
"""

from __future__ import annotations

import glob
import hashlib
import os

import numpy as np

import pwcrypt
import pwfontatlas as FA
import pwtex
import txp_unpack

MENU, SUBTITLE, OTHER = 'menu', 'subtitle', 'other'

# the packages that carry the decoded caps face, and the texture inside them
MENU_TXP = ('008299c5.txp', '0082988a.txp', '005318e4.txp', '005318e5.txp')
MENU_TEXTURE = 10


class Sheet:
    """One font texture, wherever it lives."""

    __slots__ = ('role', 'source', 'path', 'container', 'index', 'w', 'h',
                 'fmt', 'cell_h', 'rows', 'az', 'sha', 'extra')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def label(self) -> str:
        base = os.path.basename(self.path) if self.path else self.container
        return '%s#%s' % (base, self.index)

    def row(self) -> str:
        return '\t'.join(str(v) for v in
                         (self.role, self.source, self.label, self.w, self.h,
                          self.cell_h or '', self.rows or '', self.az or '',
                          self.sha or ''))


HEADER = 'role\tsource\tsheet\tw\th\tcell_h\trows\taz\tsha1'


def _scan_txp_bytes(d: bytes, path: str, source: str, container: str,
                    max_px: int = 4 << 20):
    out = []
    for idx, (k, off, w, h, fcc, n, fmt) in enumerate(txp_unpack.textures(d)):
        if w * h > max_px:
            continue
        blob = d[off + 128:off + 128 + n]
        try:
            rgba = pwtex.decode_bc(blob, w, h, fmt)
        except Exception:
            continue
        hit = pwtex.looks_like_atlas(pwtex.ink_map(rgba), loose=True)
        if not hit:
            continue
        role = MENU if (os.path.basename(path) in MENU_TXP
                        and idx == MENU_TEXTURE) else OTHER
        out.append(Sheet(role=role, source=source, path=path,
                         container=container, index=idx, w=w, h=h, fmt=fmt,
                         cell_h=hit[0], rows=hit[1], az=hit[2],
                         sha=hashlib.sha1(blob).hexdigest()[:16],
                         extra={'dds_off': off}))
    return out


def scan_txp(game: str, progress=None) -> list:
    out = []
    files = sorted(glob.glob(os.path.join(game, '**', '*.txp'), recursive=True))
    for i, p in enumerate(files):
        name = os.path.basename(p)
        try:
            d = pwcrypt.decrypt(open(p, 'rb').read(), name)
        except Exception:
            continue
        out += _scan_txp_bytes(d, p, 'txp', name)
        if progress:
            progress(i + 1, len(files))
    return out


def scan_xpr(game: str) -> list:
    import pwfont
    out = []
    for p in sorted(glob.glob(os.path.join(game, 'FONT', '*.xpr'))):
        name = os.path.basename(p)
        try:
            dec = pwcrypt.decrypt(open(p, 'rb').read(), name)
            f = pwfont.XprFont(dec)
        except Exception:
            continue
        used = sum(1 for c in f.charmap if c)
        out.append(Sheet(role=SUBTITLE, source='xpr', path=p, container=name,
                         index=0, w=f.width, h=f.height, fmt=f.fmt,
                         cell_h=int(f.cell_h), rows=None, az=used,
                         sha=hashlib.sha1(dec[:4096]).hexdigest()[:16],
                         extra={'glyphs': f.n_glyphs, 'mapped': used,
                                'last_code': f.last_code}))
    return out


def scan_slot(game: str, progress=None) -> list:
    """The 215 sheets inside SLOT.DAT.  Slow - it decompresses 2137 blocks."""
    import slotdat
    import slotitem
    d = os.path.join(game, 'MLG', 'disc0_rel')
    dat, key = os.path.join(d, '002aba34.DAT'), os.path.join(d, '002aba34.KEY')
    if not os.path.isfile(dat):
        return []
    _hdr, (hi, lo), recs = slotdat.load_key(key)
    stream = slotdat.WordStream(hi, lo)
    out = []
    with open(dat, 'rb') as fh:
        for i, rec in enumerate(recs):
            try:
                els, _h, _t = slotitem.parse(slotdat.read_block(fh, rec, stream))
            except Exception:
                continue
            for el in els:
                if el.ext != 'txp':
                    continue
                out += _scan_txp_bytes(bytes(el.data), dat, 'slot',
                                       'slot:%05X:%06X' % (rec.start, el.code),
                                       max_px=1 << 20)
            if progress:
                progress(i + 1, len(recs))
    for s in out:
        s.index = '%s/%s' % (s.container.split(':', 1)[1], s.index)
    return out


def catalogue(game: str, include_slot: bool = False, progress=None) -> list:
    out = scan_txp(game, progress) + scan_xpr(game)
    if include_slot:
        out += scan_slot(game, progress)
    return out


def duplicate_groups(sheets: list) -> dict:
    """sha1 -> every sheet carrying identical pixels."""
    groups = {}
    for s in sheets:
        if s.sha:
            groups.setdefault(s.sha, []).append(s)
    return {k: v for k, v in groups.items() if len(v) > 1}


def write_catalogue(sheets: list, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, 'fonts_catalogue.tsv')
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(HEADER + '\n')
        for s in sorted(sheets, key=lambda s: (s.role, s.source, str(s.label))):
            fh.write(s.row() + '\n')
    return p


def dump_images(sheets: list, out_dir: str, progress=None) -> int:
    """A readable PNG per sheet, in a folder per source."""
    from PIL import Image
    import pwfont
    n = 0
    for i, s in enumerate(sheets):
        d = os.path.join(out_dir, s.source)
        os.makedirs(d, exist_ok=True)
        try:
            if s.source == 'xpr':
                f = pwfont.XprFont(pwcrypt.decrypt(open(s.path, 'rb').read(),
                                                   s.container))
                img = 255 - f.atlas()
            else:
                if s.source == 'slot':
                    continue                    # needs the block, dumped separately
                raw = pwcrypt.decrypt(open(s.path, 'rb').read(), os.path.basename(s.path))
                off = s.extra['dds_off']
                size = pwtex.level_size(s.w, s.h, s.fmt)
                rgba = pwtex.decode_bc(raw[off + 128:off + 128 + size],
                                       s.w, s.h, s.fmt)
                img = 255 - rgba[..., 3]
            name = '%s_%s.png' % (os.path.splitext(s.container)[0], s.index)
            Image.fromarray(img).save(os.path.join(d, name))
            n += 1
        except Exception:
            pass
        if progress:
            progress(i + 1, len(sheets))
    return n


# --------------------------------------------------------------------------
# installing
# --------------------------------------------------------------------------

def install_menu(game: str, out_dir: str, ttf_path: str,
                 alphabet: str = FA.UA_UPPER, threshold: int = 110,
                 progress=None) -> dict:
    """Paint the caps face - into EVERY package that carries it."""
    mapping = FA.plan(alphabet)
    done = []
    for i, fn in enumerate(MENU_TXP):
        src = os.path.join(game, 'Text', fn)
        if not os.path.isfile(src):
            continue
        atlas, _plain, name = FA.open_txp(src, MENU_TEXTURE)
        r = FA.install(atlas, mapping, ttf_path, threshold=threshold)
        out = FA.save_txp(atlas, os.path.join(out_dir, 'Text', fn), name)
        done.append({'file': fn, 'painted': r['painted'], 'blocks': r['blocks'],
                     'out': out})
        if progress:
            progress(i + 1, len(MENU_TXP))
    return {'packages': done, 'letters': len(mapping),
            'mapping': {c: '0x%02X' % b for c, b in mapping.items()}}


STD_BAND = frozenset(range(FA.FREE_FIRST, FA.FREE_LAST + 1))


def _probe_caps(rgba) -> dict | None:
    """Is this the caps grid, and which of its cells are empty?

    Tested by DECODING the grid rather than by shape: the ASCII letters and
    digits must be inked at exactly the cells `cell = byte - 0x20` predicts.
    """
    a = rgba[..., 3]

    def ink(b):
        p = FA.cell_of(b)
        if not p:
            return None
        x, y = p
        if y + FA.CELL_H > a.shape[0] or x + FA.CELL_W > a.shape[1]:
            return None
        return float((a[y:y + FA.CELL_H, x:x + FA.CELL_W] > 8).mean())

    lit = sum(1 for b in list(range(0x41, 0x5B)) + list(range(0x31, 0x3A))
              if (ink(b) or 0) > 0.02)
    if lit < 30:
        return None
    free = {b for b in range(0x20, 0x100)
            if (v := ink(b)) is not None and v <= 0.001}
    return {'lit': lit, 'free': free, 'band_free': STD_BAND <= free}


def find_caps_sheets(game: str, include_slot: bool = True, progress=None) -> dict:
    """Every sheet on the caps grid, split by whether it can take the alphabet.

    160 sheets use this grid - 47 in the `.txp` packages and 113 inside
    SLOT.DAT - but only **35 of them have the whole 0x90..0xBF band free**.
    The other 125 already store their own glyphs there, and their free cells do
    not line up with each other at all: the set of bytes free in every one of
    them is EMPTY.  Since the translated text carries one byte per letter, one
    mapping has to work everywhere, so those sheets cannot be patched with it -
    writing there would both destroy real characters and need a different
    mapping per sheet.
    """
    import slotdat
    import slotitem
    ok, blocked = [], []

    def take(d, where, kind, idx, off, w, h, fmt, extra=None):
        if (w, h) != (FA.COLS * 32, 512):
            return
        n = pwtex.level_size(w, h, fmt)
        try:
            rgba = pwtex.decode_bc(d[off + 128:off + 128 + n], w, h, fmt)
        except Exception:
            return
        r = _probe_caps(rgba)
        if not r:
            return
        rec = {'where': where, 'kind': kind, 'index': idx, 'off': off,
               'w': w, 'h': h, 'fmt': fmt, 'free': len(r['free']),
               'sha': hashlib.sha1(d[off + 128:off + 128 + n]).hexdigest()[:12]}
        rec.update(extra or {})
        (ok if r['band_free'] else blocked).append(rec)

    files = sorted(glob.glob(os.path.join(game, '**', '*.txp'), recursive=True))
    for i, p in enumerate(files):
        nm = os.path.basename(p)
        try:
            d = pwcrypt.decrypt(open(p, 'rb').read(), nm)
        except Exception:
            continue
        for idx, (k, off, w, h, fcc, ln, fmt) in enumerate(txp_unpack.textures(d)):
            take(d, nm, 'txp', idx, off, w, h, fmt, {'path': p})
        if progress:
            progress(i + 1, len(files))

    if include_slot:
        D = os.path.join(game, 'MLG', 'disc0_rel')
        dat, key = os.path.join(D, '002aba34.DAT'), os.path.join(D, '002aba34.KEY')
        if os.path.isfile(dat):
            _h, (hi, lo), recs = slotdat.load_key(key)
            stream = slotdat.WordStream(hi, lo)
            with open(dat, 'rb') as fh:
                for i, rec in enumerate(recs):
                    try:
                        els, _hd, _t = slotitem.parse(
                            slotdat.read_block(fh, rec, stream))
                    except Exception:
                        continue
                    for ei, el in enumerate(els):
                        if el.ext != 'txp':
                            continue
                        b = bytes(el.data)
                        for idx, (k, off, w, h, fcc, ln, fmt) in \
                                enumerate(txp_unpack.textures(b)):
                            take(b, 'slot:%05X' % rec.start, 'slot', idx, off,
                                 w, h, fmt, {'page': rec.start, 'elem': ei,
                                             'path': dat})
                    if progress:
                        progress(i + 1, len(recs))
    return {'patchable': ok, 'blocked': blocked}


def install_everywhere(game: str, out_dir: str, ttf_path: str,
                       alphabet: str = FA.UA_UPPER, threshold: int = 110,
                       include_slot: bool = True, progress=None) -> dict:
    """Paint the alphabet into EVERY caps sheet whose standard band is free -
    across all `.txp` packages and inside SLOT.DAT - using one mapping."""
    import slotdat
    import slotitem
    found = find_caps_sheets(game, include_slot, progress)
    targets, blocked = found['patchable'], found['blocked']
    mapping = FA.plan(alphabet)
    glyphs = FA.render_set(mapping.keys(), ttf_path, threshold=threshold)

    # ---- the .txp packages: open each file once, patch all its sheets ----
    by_file = {}
    for t in targets:
        if t['kind'] == 'txp':
            by_file.setdefault(t['path'], []).append(t)
    written = []
    for p, group in sorted(by_file.items()):
        nm = os.path.basename(p)
        data = bytearray(pwcrypt.decrypt(open(p, 'rb').read(), nm))
        for t in group:
            atlas = FA.Atlas(data, t['off'], t['w'], t['h'], t['fmt'])
            for ch, byte in mapping.items():
                atlas.put(byte, glyphs[ch])
            data = atlas.data
        out = os.path.join(out_dir, os.path.relpath(p, game))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as fh:
            fh.write(pwcrypt.crypt(bytes(data), nm))
        written.append({'file': nm, 'sheets': len(group), 'out': out})

    # ---- SLOT.DAT: rebuild each touched block, splice into a copy ----
    slot_done, slot_over = 0, []
    slot_targets = [t for t in targets if t['kind'] == 'slot']
    if slot_targets:
        D = os.path.join(game, 'MLG', 'disc0_rel')
        dat, key = os.path.join(D, '002aba34.DAT'), os.path.join(D, '002aba34.KEY')
        _h, (hi, lo), recs = slotdat.load_key(key)
        stream = slotdat.WordStream(hi, lo)
        by_page = {r.start: r for r in recs}
        out_dir_slot = os.path.join(out_dir, 'MLG', 'disc0_rel')
        os.makedirs(out_dir_slot, exist_ok=True)
        out_dat = os.path.join(out_dir_slot, '002aba34.DAT')
        with open(dat, 'rb') as src, open(out_dat, 'wb') as dst:
            while True:
                chunk = src.read(1 << 24)
                if not chunk:
                    break
                dst.write(chunk)
        pages = {}
        for t in slot_targets:
            pages.setdefault(t['page'], []).append(t)
        with open(dat, 'rb') as src, open(out_dat, 'r+b') as dst:
            for page, group in sorted(pages.items()):
                rec = by_page[page]
                els, head, _t = slotitem.parse(slotdat.read_block(src, rec, stream))
                for t in group:
                    el = els[t['elem']]
                    buf = bytearray(el.data)
                    atlas = FA.Atlas(buf, t['off'], t['w'], t['h'], t['fmt'])
                    for ch, byte in mapping.items():
                        atlas.put(byte, glyphs[ch])
                    el.data = bytes(atlas.data)
                payload = slotdat.build_block(slotitem.build(els, head))
                if slotdat.pages_for(payload) > rec.footprint:
                    slot_over.append('%05X' % page)
                    continue
                slotdat.write_block(dst, rec, payload, stream)
                slot_done += 1
        with open(key, 'rb') as src, \
                open(os.path.join(out_dir_slot, '002aba34.KEY'), 'wb') as dst:
            dst.write(src.read())

    return {'mapping': {c: '0x%02X' % b for c, b in mapping.items()},
            'txp_files': written,
            'txp_sheets': sum(w['sheets'] for w in written),
            'slot_blocks': slot_done, 'slot_overflow': slot_over,
            'slot_sheets': len(slot_targets),
            'blocked': blocked, 'blocked_count': len(blocked)}


def install_subtitles(game: str, out_dir: str, ttf_path: str,
                      alphabet: str | None = None, progress=None) -> dict:
    """Paint the XPR subtitle faces at their real Unicode code points."""
    import pwxpr
    letters = alphabet or (FA.UA_UPPER + FA.UA_UPPER.lower())
    done = []
    files = sorted(glob.glob(os.path.join(game, 'FONT', '*.xpr')))
    for i, p in enumerate(files):
        ed = pwxpr.load(p)
        r = pwxpr.install(ed, letters, ttf_path)
        if r.get('painted'):
            r['out'] = ed.save(os.path.join(out_dir, 'FONT', os.path.basename(p)))
        r['file'] = os.path.basename(p)
        done.append(r)
        if progress:
            progress(i + 1, len(files))
    return {'faces': done, 'letters': len(letters)}
