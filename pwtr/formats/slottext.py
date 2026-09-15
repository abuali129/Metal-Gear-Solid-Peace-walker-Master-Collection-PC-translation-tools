r"""Text in and out of the SLOT container.

This is where the missing half of Peace Walker's script lives.  The shipped
`MLG\Text\*.olang` files hold the peripheral text - options screens, lobby UI,
online errors - roughly 1 600 English strings.  The rest of the game, the part
that made the export look empty, is inside `002aba34.DAT` as `olang` elements
of `.slot` blocks: **4 458 olang files, 145 groups, 52 396 distinct strings**,
including the subtitle line that started the whole hunt,
"Hey, Boss, wanna go a round?", which appears nowhere else in the install.

The chain is `slotdat` -> `slotitem` -> `olang`:

    SLOT.DAT block  ->  .slot element container  ->  olang element  ->  strings

Export writes the same `### N — KEY · ORIGINAL` blocks the rest of the project
uses, so one translation file format covers every container.  The KEY is
`slot/<page>/<element>/<group>/<entity>/<slot>`, which is enough to put a
string back exactly where it came from.

Line breaks are folded to `{lf}` so one string is always one line: game strings
contain real newlines, and a raw newline in the export would be indistinguishable
from the block separator.
"""

from __future__ import annotations

import os
import re

import olang
import slotdat
import slotitem

BLOCK_RE = re.compile(r'^### (?P<n>\d+) — (?P<key>\S+) · (?P<orig>.*)$', re.M)


def fold(s: str) -> str:
    return (s.replace('\r\n', '{lf}').replace('\r', '{cr}')
             .replace('\n', '{lf}').replace('\t', '{tab}'))


def unfold(s: str) -> str:
    return (s.replace('{lf}', '\n').replace('{cr}', '\r')
             .replace('{tab}', '\t'))


def _iter_olang(dat_path: str, key_path: str, progress=None):
    """Yield (record, element_index, OlangFile) for every olang in the DAT."""
    _hdr, (high, low), recs = slotdat.load_key(key_path)
    stream = slotdat.WordStream(high, low)
    with open(dat_path, 'rb') as dat:
        for n, rec in enumerate(recs):
            try:
                els, _head, _t = slotitem.parse(slotdat.read_block(dat, rec, stream))
            except Exception:
                continue
            for ei, el in enumerate(els):
                if el.ext != 'olang':
                    continue
                try:
                    yield rec, ei, olang.OlangFile.parse(el.data)
                except Exception:
                    continue
            if progress and n % 100 == 0:
                progress(n, len(recs))


def export(dat_path: str, key_path: str, out_path: str,
           en_only: bool = True, progress=None) -> dict:
    """Write every SLOT string to a translation file.

    The key is `slot/<page>/<element>/<ref>`.  A reference index addresses one
    language slot of one entity, which is the finest thing the format has and
    the only address that survives a rebuild - pool indices do not, because the
    pool is deduplicated.
    """
    blocks, n, dupes = [], 0, 0
    langs = {'en'} if en_only else set(olang.LANG_ORDER)
    seen_text = set()

    for rec, ei, f in _iter_olang(dat_path, key_path, progress):
        for group, entity, slot, lang, ref_index, text in f.entries():
            if lang not in langs or not text.strip():
                continue
            # the same English line is repeated across scenes; one block per
            # distinct string keeps the file translatable, and every reference
            # that shares it is listed on the header line
            if text in seen_text:
                dupes += 1
                continue
            seen_text.add(text)
            key = 'slot/%05X/%d/%d' % (rec.start, ei, ref_index)
            n += 1
            blocks.append('### %d — %s · %s\n%s\n' % (n, key, fold(text), fold(text)))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('# Peace Walker - SLOT.DAT text (%d strings)\n'
                 '# Edit the line UNDER each ### header. Leave it identical to\n'
                 '# leave the string alone. {lf} is a line break, {tab} a tab.\n\n' % n)
        fh.write('\n'.join(blocks))
    return {'strings': n, 'file': out_path}


def parse_translation(path: str) -> dict:
    """-> {original text: translated text}, skipping untranslated blocks.

    Matching on the ORIGINAL rather than on the key is deliberate.  The same
    English line occurs in many scenes, and a key names only the first one; if
    the translation were applied by key, every other occurrence would stay
    English.  Matching by text translates all of them from one edited block,
    which is also what keeps repeated lines consistent.
    """
    text = open(path, encoding='utf-8-sig').read().replace('\r\n', '\n')
    out, marks = {}, list(BLOCK_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip('\n')
        if not body.strip() or body == m.group('orig'):
            continue
        out[unfold(m.group('orig'))] = unfold(body)
    return out


def apply(dat_path: str, key_path: str, txt_path: str, out_dir: str,
          progress=None) -> dict:
    """Put a translation back and write a patched DAT + KEY.

    Only blocks whose text actually changed are rewritten, and the KEY is
    copied byte for byte - every block identity hash and every page number
    stays stock.  A block whose recompressed payload no longer fits its own
    footprint is REPORTED AND SKIPPED, never relocated: the container is fully
    tiled, so there is nowhere safe to relocate it to (see slotdat).
    """
    edits = parse_translation(txt_path)
    if not edits:
        return {'strings': 0, 'blocks': 0, 'note': 'nothing translated'}

    _hdr, (high, low), recs = slotdat.load_key(key_path)
    stream = slotdat.WordStream(high, low)

    os.makedirs(out_dir, exist_ok=True)
    out_dat = os.path.join(out_dir, slotdat.DAT_NAME)
    with open(dat_path, 'rb') as src, open(out_dat, 'wb') as dst:
        while True:
            chunk = src.read(1 << 24)
            if not chunk:
                break
            dst.write(chunk)

    touched = applied = overflow = 0
    over_list = []
    with open(dat_path, 'rb') as src, open(out_dat, 'r+b') as dst:
        for n, rec in enumerate(recs):
            try:
                els, head, _t = slotitem.parse(slotdat.read_block(src, rec, stream))
            except Exception:
                continue
            changed = False
            for el in els:
                if el.ext != 'olang':
                    continue
                try:
                    f = olang.OlangFile.parse(el.data)
                except Exception:
                    continue
                hits = 0
                for ref in f.refs:
                    new = edits.get(f.pool[ref.text])
                    if new is None:
                        continue
                    f.set_ref(ref, new)
                    hits += 1
                if hits:
                    f.compact_pool()          # or the body carries orphans
                    el.data = f.build() + el.data[f.length:]   # keep the slack
                    applied += hits
                    changed = True
            if not changed:
                continue
            payload = slotdat.build_block(slotitem.build(els, head))
            if slotdat.pages_for(payload) > rec.footprint:
                overflow += 1
                over_list.append({'page': '%05X' % rec.start,
                                  'need': slotdat.pages_for(payload),
                                  'budget': rec.footprint})
                continue
            slotdat.write_block(dst, rec, payload, stream)
            touched += 1
            if progress and n % 50 == 0:
                progress(n + 1, len(recs))

    with open(key_path, 'rb') as src, \
            open(os.path.join(out_dir, slotdat.KEY_NAME), 'wb') as dst:
        dst.write(src.read())

    return {'strings': applied, 'blocks': touched, 'overflow': overflow,
            'overflow_list': over_list, 'out_dir': out_dir}
