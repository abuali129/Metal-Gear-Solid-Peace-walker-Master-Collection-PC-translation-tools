r"""Metal Gear Solid: Peace Walker (PC, Master Collection Vol.2) - .olang reader/writer.

`.olang` is the game's LANGUAGE TABLE: a six-language string pool addressed by
24-bit name hashes.  Every file on disk is wrapped in the Master Collection's
outer MT19937 cipher (see pwcrypt.py); `read()` peels it for you.


LAYOUT  (everything little-endian, all offsets are absolute from file start
         except string offsets, which are relative to BodyOffset)

  0x00  u32  magic          0x00584252  =  "RBX\0"
  0x04  u32  strcode        the file's own name as hex (00327f6a -> 0x00327F6A)
  0x08  u32  reserved       always 0
  0x0C  u32  GroupCount<<16 low half always 0; high half = number of groups
  0x10  u32  GroupOffset    always 0x20 (the group table follows the header)
  0x14  u32  EntityOffset   = GroupOffset  + GroupCount  * 8
  0x18  u32  RefOffset      = EntityOffset + EntityCount * 8
  0x1C  u32  BodyOffset     = RefOffset    + RefCount    * 12

  Only the four offsets are stored; the three counts are recovered from them
  (GroupCount also sits in word 3, which is how you bootstrap the chain).

  GROUP TABLE   GroupCount x 8 bytes, sorted by Key, binary-searched
      u32 Key            24-bit strcode of the group / message-set name
      u16 EntityIndex    first entity of this group
      u16 EntityCount    how many entities

  ENTITY TABLE  EntityCount x 8 bytes, sorted by Key *within each group*
      u32 Key            24-bit strcode of the message name
      u16 RefIndex       first reference of this entity
      u16 RefCount       always 6 * SlotCount  (6 = the six languages)

  REFERENCE TABLE  RefCount x 12 bytes.  Inside one entity the references are
  grouped BY LANGUAGE first, then by slot:   ref[RefIndex + lang*slots + slot]
      u32 Lang           strcode of the language code: see LANGS below
      u32 Offset         byte offset into the body of a NUL-terminated string
      u32 Style          0x0402 normal text, 0x0001 the caps-only UI font
                         (in Style 1 strings the accented letters are already
                          remapped onto ASCII slots: "hQUIPE" = "EQUIPE")

  BODY          the string pool: every distinct referenced string, UTF-8,
                NUL-terminated, each entry padded to a 2-byte boundary, laid
                out in ascending offset order with NO slack and NO unreferenced
                bytes.  The file ends at the end of the last string.
                Offset 0 is normally the empty string.

  Verified on all 17 shipped .olang files: the two index tables partition their
  targets exactly, every reference offset lands on a real string start, and the
  body is byte-for-byte the concatenation described above.


THE KEY HASH  (recovered from the game's own name-resolution code)

      h = 0
      for c in name:                 # stops at '.'
          h = ((h << 5) | (h >> 19)) + ord(c)
          h &= 0xFFFFFF              # 24-bit rotate-left-5, then add

  strcode("en") == 0x0D0E, which is exactly the Lang value of the first
  reference in every entity - that is how the language codes were identified.

  In-game text refers to another entry with the tag <I=group_*entity>, e.g.
  <I=charaedit_lang_*suit_equip_help>: the part before "_*" is the group name,
  the part after "*" is the entity name.  All 39 such tags in the shipped files
  hash to a real group key and a real entity key, which proves the hash and the
  meaning of the two tables.  <I=...> without a "*" is a button glyph (ATK, CAN,
  DEC, L2, R2, x, square, triangle), not a lookup.


WHICH FILE HOLDS WHAT

  The exe hard-codes the pairing (around 0x98F3D3):
      ./JPN/Text/007e2f18.olang  <-  ./MLG/Text/009c9ea4.olang
                                   + ./EXLANG/Text/00c7f1dc.olang + 00c7f1dd
      ./JPN/Text/00225520.olang  <-  ./MLG/Text/00d9bfd4.olang
                                   + ./EXLANG/Text/0077040c.olang

  MLG\Text  carries all six languages.  EXLANG\Text fills ONLY the sp column,
  and what it puts there is BRAZILIAN PORTUGUESE - MLG's own sp column holds the
  real Spanish.  Measured on 400 sampled strings of 00c7f1dc: 262 Portuguese
  markers against 100 Spanish, and lines like "nao eramos inimigos desde o
  inicio?" settle it.  So EXLANG is how the Master Collection adds a language
  the PSP original never shipped: an overlay that occupies an existing slot -
  which is exactly the route a Ukrainian build could take.
  en/fr/ge/it/jp are the empty string in all three files; 0077040c has
  a table byte-identical to 00d9bfd4's, and 00c7f1dc + 00c7f1dd are disjoint and
  between them cover 100% of 009c9ea4 and 96% of every MLG key.

      009c9ea4   the main game text: menus, Mother Base, briefings, item and
                 weapon names, tutorials, achievement blurbs (1043 EN strings)
      005184e3   mission/Extra Ops framing, Monster Hunter crossover, dev menus
      00d345a5   long item + weapon descriptions (avg 151 chars, the longest)
      00d9bfd4   key-help / button-prompt lines, dense in <I=...> tags
      00cb1fb7   online lobby search filters and gamer-card UI
      0066e64e   control-scheme button labels ("Aim Weapon/Attack") - 487
                 entities but only 59 distinct EN strings
      00d0c740   cutscene and briefing-tape dialogue, plus online error text
      00cd740b   CQC / roll tutorial prompts (v906_*_gam_inst_* groups)
      0072f326   save-data and storage-device messages
      0043da6e   Xbox LIVE connection errors        0005ee2f  options screens
      0060e2f2   matchmaking + voice-chat options   00327f6a  host-search errors
      00c6a046   save/system-data prompts (also raw ids: NOW_SAVING, FAIL_SAVE)


LIMITS WHEN REPACKING

  Strings may grow freely - offsets are u32 and the tables are rebuilt.  But
  Group.EntityIndex, Group.EntityCount, Entity.RefIndex and Entity.RefCount are
  all u16: EXLANG\00c7f1dc.olang already sits at RefIndex 60348 of 65535, so new
  ENTITIES cannot be added there.  Editing text is unconstrained.


USAGE

    import olang
    f = olang.read(r'...\MLG\Text\00327f6a.olang')   # decrypts if needed
    f.entries()                    # (group, entity, slot, lang, text)
    f.pool[12] = 'new text'        # edit the pool in place
    open(out, 'wb').write(f.build())          # plain
    open(out, 'wb').write(f.build_encrypted('00327f6a.olang'))

    python olang.py roundtrip      # decrypt every shipped file, rebuild, diff
    python olang.py export         # write export\olang_strings.txt
"""

from __future__ import annotations

import os
import re
import struct
import sys

MAGIC = 0x00584252  # "RBX\0"
HEADER_SIZE = 0x20
GROUP_SIZE = 8
ENTITY_SIZE = 8
REF_SIZE = 12

STYLE_TEXT = 0x0402  # proportional font, full accents
STYLE_CAPS = 0x0001  # caps-only bitmap font, accents pre-mangled to ASCII

M32 = 0xFFFFFFFF


# --------------------------------------------------------------------------
# the 24-bit name hash used for every Key in the file
# --------------------------------------------------------------------------

def strcode(name: str) -> int:
    """24-bit rotate-left-5 hash; stops at the first '.' like the game does."""
    h = 0
    for ch in name:
        if ch == '.':
            break
        h = (((h << 5) | (h >> 19)) + ord(ch)) & 0xFFFFFF
    return h


# the six language slots, in the order they appear inside an entity
LANG_ORDER = ['en', 'fr', 'ge', 'it', 'jp', 'sp']
LANG_CODE = {c: strcode(c) for c in LANG_ORDER}          # 'en' -> 0x0D0E
LANG_NAME = {v: k for k, v in LANG_CODE.items()}
LANG_LABEL = {'en': 'English', 'fr': 'French', 'ge': 'German',
              'it': 'Italian', 'jp': 'Japanese', 'sp': 'Spanish'}


# --------------------------------------------------------------------------
# the Master Collection outer cipher (pwcrypt, plus the trailing-byte case)
# --------------------------------------------------------------------------

SKIP = 5
XOR = 0xB9D3018F


def _name_hash(name: str) -> int:
    for sep in ('/', '\\', ':'):
        name = name.rsplit(sep, 1)[-1]
    h = 0
    for ch in name:
        if ch == '.':
            break
        c = ord(ch)
        if c > 127:
            c -= 256
        h = (h * 0x2356F + c * 0x1D35) & M32
    return h


def _mt_stream(seed: int, count: int):
    mt = [0] * 624
    s = seed & M32
    for k in range(624):
        v = s & 0xFFFF0000
        s = (69069 * s + 1) & M32
        v |= (s & 0xFFFF0000) >> 16
        s = (69069 * s + 1) & M32
        mt[k] = v
    out = []
    while len(out) < count:
        for k in range(624):
            y = (mt[k] & 0x80000000) | (mt[(k + 1) % 624] & 0x7FFFFFFF)
            t = mt[(k + 397) % 624] ^ (y >> 1)
            if y & 1:
                t ^= 0x9908B0DF
            mt[k] = t
        for k in range(624):
            y = mt[k]
            y ^= y >> 11
            y ^= (y << 7) & 0x9D2C5680
            y &= M32
            y ^= (y << 15) & 0xEFC60000
            y &= M32
            y ^= y >> 18
            out.append(y & M32)
    return out


def _crypt_local(data: bytes, name: str) -> bytes:
    """Symmetric: encrypt == decrypt.  The 1-3 bytes past the last whole dword
    matter: they are XORed with the matching bytes of the next keystream word.
    Ten of the seventeen .olang files end on a half word, and skipping the tail
    leaves their final NUL - and the last UTF-8 character - as garbage."""
    n, rem = divmod(len(data), 4)
    st = _mt_stream(_name_hash(name), n + SKIP + 8)
    out = bytearray(data)
    for i in range(n):
        e = (st[SKIP + i] ^ XOR) & M32
        v = int.from_bytes(out[i * 4:i * 4 + 4], 'little') ^ e
        out[i * 4:i * 4 + 4] = v.to_bytes(4, 'little')
    if rem:
        kb = ((st[SKIP + n] ^ XOR) & M32).to_bytes(4, 'little')
        for j in range(rem):
            out[n * 4 + j] ^= kb[j]
    return bytes(out)


import pwpaths

try:                                    # pwcrypt has a numpy fast path
    import pwcrypt as _pw
    crypt = _pw.crypt                   # verified byte-identical to _crypt_local
except Exception:                       # stand alone if it is not importable
    crypt = _crypt_local

decrypt = crypt
encrypt = crypt


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

class Group:
    __slots__ = ('key', 'entity_index', 'entity_count')

    def __init__(self, key, entity_index, entity_count):
        self.key, self.entity_index, self.entity_count = key, entity_index, entity_count

    def __repr__(self):
        return 'Group(%06X, %d, %d)' % (self.key, self.entity_index, self.entity_count)


class Entity:
    __slots__ = ('key', 'ref_index', 'ref_count')

    def __init__(self, key, ref_index, ref_count):
        self.key, self.ref_index, self.ref_count = key, ref_index, ref_count

    @property
    def slots(self):
        return self.ref_count // len(LANG_ORDER)

    def __repr__(self):
        return 'Entity(%06X, %d, %d)' % (self.key, self.ref_index, self.ref_count)


class Ref:
    """`text` is an index into OlangFile.pool, not a byte offset - offsets are
    recomputed on write so the pool can change length."""
    __slots__ = ('lang', 'text', 'style')

    def __init__(self, lang, text, style):
        self.lang, self.text, self.style = lang, text, style

    def __repr__(self):
        return 'Ref(%s, #%d, %04X)' % (LANG_NAME.get(self.lang, hex(self.lang)),
                                       self.text, self.style)


class OlangFile:
    def __init__(self):
        self.magic = MAGIC
        self.strcode = 0
        self.reserved = 0
        self.word3_low = 0          # low half of word 3; 0 in every shipped file
        self.groups: list[Group] = []
        self.entities: list[Entity] = []
        self.refs: list[Ref] = []
        self.pool: list[str] = []   # distinct strings, in body order
        self.name = None            # file name, needed to re-encrypt
        self.length = 0             # the file's OWN byte length, so an
                                    # olang lifted out of a container can
                                    # be trimmed off its padding

    # ---------------------------------------------------------------- read

    @classmethod
    def parse(cls, data: bytes, name: str | None = None) -> 'OlangFile':
        f = cls()
        f.name = name
        (f.magic, f.strcode, f.reserved, word3,
         group_off, ent_off, ref_off, body_off) = struct.unpack_from('<8I', data, 0)
        if f.magic != MAGIC:
            raise ValueError('not an olang: magic %08X (still encrypted?)' % f.magic)
        f.word3_low = word3 & 0xFFFF
        n_group = word3 >> 16

        if group_off != HEADER_SIZE:
            raise ValueError('group table not at 0x20 (%d)' % group_off)
        if ent_off - group_off != n_group * GROUP_SIZE:
            raise ValueError('group count %d disagrees with the offsets' % n_group)
        if (ref_off - ent_off) % ENTITY_SIZE or (body_off - ref_off) % REF_SIZE:
            raise ValueError('table sizes are not whole records')
        n_ent = (ref_off - ent_off) // ENTITY_SIZE
        n_ref = (body_off - ref_off) // REF_SIZE

        for i in range(n_group):
            f.groups.append(Group(*struct.unpack_from('<IHH', data, group_off + i * GROUP_SIZE)))
        for i in range(n_ent):
            f.entities.append(Entity(*struct.unpack_from('<IHH', data, ent_off + i * ENTITY_SIZE)))

        raw_refs = [struct.unpack_from('<3I', data, ref_off + i * REF_SIZE)
                    for i in range(n_ref)]
        body = data[body_off:]

        # the pool: every distinct referenced offset, in ascending order
        offsets = sorted({r[1] for r in raw_refs})
        index_of = {}
        for o in offsets:
            end = body.index(b'\0', o)
            index_of[o] = len(f.pool)
            f.pool.append(body[o:end].decode('utf-8'))
        for lang, off, style in raw_refs:
            f.refs.append(Ref(lang, index_of[off], style))

        # The body must be exactly those strings, NUL-terminated, 2-byte
        # aligned - but it may be FOLLOWED by slack.  An olang lifted out of a
        # container (a SLOT element, a STAGEDAT entity) arrives padded to the
        # container's alignment, so the file's own length has to come from its
        # header plus its pool, never from however many bytes the caller
        # handed us.  Comparing against `data[body_off:]` instead rejects
        # 3 758 of the 4 458 olang files in SLOT.DAT, all of them valid.
        canon = _build_body(f.pool)[0]
        if body[:len(canon)] != canon:
            raise ValueError('body is not a tight 2-byte-aligned string pool')
        f.length = body_off + len(canon)
        return f

    # --------------------------------------------------------------- write

    def build(self) -> bytes:
        body, offset_of = _build_body(self.pool)
        n_group, n_ent, n_ref = len(self.groups), len(self.entities), len(self.refs)
        group_off = HEADER_SIZE
        ent_off = group_off + n_group * GROUP_SIZE
        ref_off = ent_off + n_ent * ENTITY_SIZE
        body_off = ref_off + n_ref * REF_SIZE

        out = bytearray()
        out += struct.pack('<8I', self.magic, self.strcode, self.reserved,
                           (n_group << 16) | self.word3_low,
                           group_off, ent_off, ref_off, body_off)
        for g in self.groups:
            out += struct.pack('<IHH', g.key, g.entity_index, g.entity_count)
        for e in self.entities:
            out += struct.pack('<IHH', e.key, e.ref_index, e.ref_count)
        for r in self.refs:
            out += struct.pack('<3I', r.lang, offset_of[r.text], r.style)
        out += body
        return bytes(out)

    def build_encrypted(self, name: str | None = None) -> bytes:
        name = name or self.name
        if not name:
            raise ValueError('need the file name - it keys the cipher')
        return crypt(self.build(), os.path.basename(name))

    def write(self, path: str, encrypted: bool = False):
        data = self.build_encrypted(os.path.basename(path)) if encrypted else self.build()
        with open(path, 'wb') as fh:
            fh.write(data)

    # -------------------------------------------------------------- access

    def entries(self):
        """Yield (group, entity, slot, lang, ref_index, text) for every
        reference, in table order."""
        gof = {}
        for g in self.groups:
            for i in range(g.entity_index, g.entity_index + g.entity_count):
                gof[i] = g
        for ei, e in enumerate(self.entities):
            # The shipped MLG/EXLANG files group references by language and
            # then by slot, so RefCount is 6*SlotCount.  The olang files inside
            # SLOT.DAT do NOT: every entity there holds exactly ONE reference,
            # which makes SlotCount 1//6 == 0 and made this loop yield nothing
            # at all for 4 458 files.  Walk the references directly and take
            # the language from the reference itself, which is authoritative in
            # both layouts; for the 6-language files this enumerates exactly
            # the same j in the same order as before.
            slots = e.slots
            for k in range(e.ref_count):
                j = e.ref_index + k
                if j >= len(self.refs):
                    break
                r = self.refs[j]
                sl = (k % slots) if slots else k
                yield gof.get(ei), e, sl, LANG_NAME.get(r.lang, '?%X' % r.lang), j, self.pool[r.text]

    def set_ref(self, ref, text: str) -> None:
        """Point one reference at `text`, reusing a pool entry if it exists.

        Never assign into `pool[i]` directly: a pool entry is shared by every
        reference that happened to carry the same string, so editing in place
        silently rewrites other languages and other entities too.
        """
        try:
            ref.text = self.pool.index(text)
        except ValueError:
            self.pool.append(text)
            ref.text = len(self.pool) - 1

    def compact_pool(self) -> int:
        """Drop pool entries nothing references any more; -> how many went.

        Retargeting a reference leaves its old string orphaned, and `build()`
        writes the whole pool - so the body would carry bytes that no offset
        points at.  Re-parsing rebuilds the pool from referenced offsets only
        and the tight-pool check then fails, which is exactly how five edited
        files came back unreadable.  Relative order is preserved, so compacting
        an unedited file is a no-op and its round trip stays byte-exact.
        """
        used = {r.text for r in self.refs}
        keep = [i for i in range(len(self.pool)) if i in used]
        if len(keep) == len(self.pool):
            return 0
        remap = {old: new for new, old in enumerate(keep)}
        dropped = len(self.pool) - len(keep)
        self.pool = [self.pool[i] for i in keep]
        for r in self.refs:
            r.text = remap[r.text]
        return dropped

    def pool_langs(self):
        """pool index -> sorted list of language codes that reference it."""
        out = [set() for _ in self.pool]
        for r in self.refs:
            out[r.text].add(LANG_NAME.get(r.lang, '?%X' % r.lang))
        return [sorted(s, key=lambda c: LANG_ORDER.index(c) if c in LANG_ORDER else 9)
                for s in out]

    def __repr__(self):
        return '<olang %08X %d groups, %d entities, %d refs, %d strings>' % (
            self.strcode, len(self.groups), len(self.entities), len(self.refs), len(self.pool))


def _build_body(pool):
    """The string pool exactly as the game stores it: UTF-8 + NUL, padded to a
    2-byte boundary, concatenated in list order."""
    body = bytearray()
    offsets = []
    for s in pool:
        offsets.append(len(body))
        body += s.encode('utf-8')
        body += b'\0'
        if len(body) & 1:
            body += b'\0'
    return bytes(body), offsets


def read(path: str, encrypted: bool | None = None) -> OlangFile:
    """Read a .olang.  `encrypted=None` auto-detects by looking for the magic."""
    name = os.path.basename(path)
    with open(path, 'rb') as fh:
        data = fh.read()
    enc = encrypted
    if enc is None:
        enc = struct.unpack_from('<I', data, 0)[0] != MAGIC
    if enc:
        data = crypt(data, name)
    return OlangFile.parse(data, name)


def write(f: OlangFile, path: str, encrypted: bool = False):
    f.write(path, encrypted)


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

GAME = pwpaths.find_game() or ''
DEC = os.path.join(pwpaths.default_output(), 'decrypted')
EXPORT = os.path.join(pwpaths.default_output(), 'export')
DIRS = [('MLG', os.path.join(GAME, 'MLG', 'Text')),
        ('EXLANG', os.path.join(GAME, 'EXLANG', 'Text'))]


def _shipped():
    for tag, d in DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith('.olang'):
                yield tag, fn, os.path.join(d, fn)


def cmd_decrypt():
    n = 0
    for tag, fn, p in _shipped():
        dst = os.path.join(DEC, tag, 'Text', fn)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(p, 'rb') as fh:
            raw = fh.read()
        with open(dst, 'wb') as fh:
            fh.write(crypt(raw, fn))
        n += 1
    print('decrypted %d files -> %s' % (n, DEC))


def cmd_roundtrip():
    ok = fail = 0
    print('%-8s %-15s %9s  %-9s %-9s' % ('dir', 'file', 'bytes', 'plain', 'encrypted'))
    for tag, fn, p in _shipped():
        with open(p, 'rb') as fh:
            raw = fh.read()
        plain = crypt(raw, fn)
        f = OlangFile.parse(plain, fn)
        rebuilt = f.build()
        re_enc = crypt(rebuilt, fn)
        a = rebuilt == plain
        b = re_enc == raw
        ok += a and b
        fail += not (a and b)
        print('%-8s %-15s %9d  %-9s %-9s  %s' % (
            tag, fn, len(plain), 'IDENTICAL' if a else 'DIFFER',
            'IDENTICAL' if b else 'DIFFER', f))
    print('\nround trip: %d passed, %d failed (of %d)' % (ok, fail, ok + fail))
    return fail


def flat(s: str) -> str:
    """Fold a string onto one line for the '### N - KEY - ORIGINAL' header.
    LF becomes '|' and CR becomes '^'.  Neither character occurs in any of the
    20,012 shipped strings, so the fold is reversible."""
    return s.replace('\n', '|').replace('\r', '^')


def unflat(s: str) -> str:
    return s.replace('^', '\r').replace('|', '\n')


HEAD = (
    '# MGS Peace Walker (PC, Master Collection Vol.2) - every string in every .olang\n'
    "# Format: blocks separated by a blank line; '### N' = entry index (don't change!);\n"
    '# key after the em-dash, original after the middle dot, translation on the lines below.\n'
    '# Key = OLANG/<dir>/<file>/<pool index>/<languages that reference the string>.\n'
    "# The pool index is the write-back address: olang.read(f).pool[index] = translation.\n"
    "# In the ORIGINAL, '|' is a real line break and '^' a carriage return.\n"
    '# Only entries whose language list contains "en" need translating; the rest are the\n'
    '# fr/ge/it/jp/sp columns. "en+jp" means Japanese falls back to the English text here.\n'
    '# EXLANG/* is an overlay that adds BRAZILIAN PORTUGUESE by filling the sp slot;\n'
    '# its en/fr/ge/it/jp columns each point at a single empty entry.\n\n')


def cmd_export():
    os.makedirs(EXPORT, exist_ok=True)
    out = os.path.join(EXPORT, 'olang_strings.txt')
    out_en = os.path.join(EXPORT, 'olang_strings.en.txt')
    scope = os.path.join(EXPORT, 'olang.scope.txt')

    blocks, blocks_en, rows = [], [], []
    n = n_en = 0
    for tag, fn, p in _shipped():
        f = read(p)
        langs = f.pool_langs()
        stem = os.path.splitext(fn)[0]
        # which entity keys use each pool entry, for the scope file
        users = [[] for _ in f.pool]
        for g, e, sl, lang, j, txt in f.entries():
            users[f.refs[j].text].append('%06X.%06X[%s%s]' % (
                g.key if g else 0, e.key, lang, '' if e.slots == 1 else '#%d' % sl))
        for i, s in enumerate(f.pool):
            n += 1
            key = 'OLANG/%s/%s/%04d/%s' % (tag, stem, i, '+'.join(langs[i]) or '-')
            one = flat(s)
            blocks.append('### %d  — %s · %s\r\n%s\r\n\r\n' % (n, key, one, one))
            if 'en' in langs[i]:
                n_en += 1
                blocks_en.append('### %d  — %s · %s\r\n%s\r\n\r\n' % (n_en, key, one, one))
            rows.append('%s\t%d\t%s\t%r\r\n' % (key, len(s.encode('utf-8')),
                                                ','.join(sorted(set(users[i]))[:12]), s))

    with open(out, 'w', encoding='utf-8', newline='') as fh:
        fh.write(HEAD.replace('\n', '\r\n'))
        fh.writelines(blocks)
    with open(out_en, 'w', encoding='utf-8', newline='') as fh:
        fh.write(HEAD.replace('\n', '\r\n').replace(
            'every string in every .olang', 'the ENGLISH strings only'))
        fh.writelines(blocks_en)
    with open(scope, 'w', encoding='utf-8', newline='') as fh:
        fh.write('# key\tutf-8 bytes\tgroup.entity[lang] users\traw text\r\n')
        fh.writelines(rows)
    print('%d strings -> %s' % (n, out))
    print('%d english   -> %s' % (n_en, out_en))
    print('%d rows      -> %s' % (len(rows), scope))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'roundtrip'
    if cmd == 'roundtrip':
        sys.exit(1 if cmd_roundtrip() else 0)
    elif cmd == 'decrypt':
        cmd_decrypt()
    elif cmd == 'export':
        cmd_export()
    else:
        print(__doc__)


# --------------------------------------------------------------------------
# importing a translated .txt back into .olang
# --------------------------------------------------------------------------

BLOCK_RE = re.compile(
    r'^### \d+\s+[—-]\s+(?P<key>OLANG/[^\s]+)\s+[·\u00b7]\s?(?P<orig>.*?)$',
    re.M)


def parse_txt(path: str):
    """Read an exported/translated .txt back.

    -> {(tag, stem): {pool_index: translation}}

    A block is `### N — KEY · ORIGINAL` followed by the translation lines and a
    blank line.  A block whose translation is byte-identical to the original,
    or empty, is skipped - so a partially translated file is fine.
    """
    with open(path, 'r', encoding='utf-8') as fh:
        text = fh.read()
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    out = {}
    marks = list(BLOCK_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip('\n')
        # drop a trailing blank line separator only
        parts = m.group('key').split('/')
        if len(parts) < 5:
            continue
        _, tag, stem, idx = parts[0], parts[1], parts[2], parts[3]
        try:
            idx = int(idx)
        except ValueError:
            continue
        orig = m.group('orig')
        if not body.strip():
            continue
        if body == orig:
            continue                      # untranslated, leave the original
        out.setdefault((tag, stem), {})[idx] = unflat(body)
    return out


def apply_txt(txt_path: str, out_dir: str, encrypted: bool = True,
              progress=None):
    """Apply a translated .txt to the shipped .olang files.

    Writes every file that actually changed into `out_dir/<tag>/Text/`, keeping
    the original name so the game's name-keyed cipher still matches.
    Returns (files_written, strings_applied, [warnings]).
    """
    edits = parse_txt(txt_path)
    written = applied = 0
    warn = []
    todo = list(_shipped())
    for n, (tag, fn, p) in enumerate(todo):
        stem = os.path.splitext(fn)[0]
        e = edits.get((tag, stem))
        if progress:
            progress(n + 1, len(todo), fn)
        if not e:
            continue
        f = read(p)
        hit = 0
        for idx, s in e.items():
            if not (0 <= idx < len(f.pool)):
                warn.append('%s/%s: pool index %d out of range (0..%d)'
                            % (tag, stem, idx, len(f.pool) - 1))
                continue
            if f.pool[idx] != s:
                f.pool[idx] = s
                hit += 1
        if not hit:
            continue
        dst = os.path.join(out_dir, tag, 'Text', fn)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        f.write(dst, encrypted)
        written += 1
        applied += hit
    return written, applied, warn
