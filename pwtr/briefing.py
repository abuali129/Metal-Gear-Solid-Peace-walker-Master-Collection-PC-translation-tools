"""BRIEFING.DAT -- the briefing tapes, and the last unread body of text.

``MLG/disc0_rel/0076531d.DAT`` is ``BRIEFING.DAT``: ``strcode("BRIEFING")`` is
``0x76531D``, and the record layout matches the PS3 file field for field.
It holds **2 761 records** across six languages; 383 of them are English and
carry **4 987 translatable lines** -- half the size of the story script, and
the last large body of text in the game that nothing else reads.

## Why it reads as noise

The file is encrypted **one 4 KB page at a time**, each page with a fresh
keystream.  That is the same "the stream restarts on every read" behaviour that
hides the ``.PDT`` payloads, except here the unit is the page rather than the
entity, because the engine reads each briefing as a page-aligned window.

Decrypt it as one stream -- the obvious thing, and what a whole-file
``pwcrypt.decrypt`` does -- and record 0 comes out clean while the remaining
4 MB is garbage.  It looks exactly like a container whose payload is compressed
under an unknown scheme.  It is not; it is one keystream per page:

    one keystream, whole file        4 records found
    fresh keystream per 4 KB page    2 761 records found

## Record layout

Records are 16-byte aligned and start with ``oEbN``::

    +0x00  4  magic       "oEbN"
    +0x04  8  padding     FF FF FF FF x2
    +0x0C  4  fon_off     to the audio section, from the record start
    +0x10  4  const       always 0x14
    +0x14  4  u5          text_base = record + 0x0C + u5
    +0x18  4  fon_off - 4 always mirrors fon_off
    +0x1C  .  offsets     nstr x u32 from text_base, nstr = (u5 - 0x10) / 4
    text_base  the strings, NUL-terminated UTF-8
    then       padding and the FON audio block, kept verbatim

**One record is one language.**  Record 0 is the Japanese of a conversation and
record 0x944B0 is the English of the same one, with its own line breaks and its
own string count.  So the tab shows English records and leaves the rest alone.

## Growing, and what is actually known about it

On PS3 a record may not grow: briefings are sought to positions worked out at
runtime, so one that moved stops playing.  **Whether the PC build inherited
that is not established.**  What is established about the PC exe, from reading
it, is this much:

* ``BRIEFING.DAT`` is opened by name -- the literal string is in the binary --
  and resolved through the same 24-bit hash the containers use;
* the record magic ``oEbN`` appears **nowhere** in the exe, so the engine does
  not scan the file for records: it goes straight to a position;
* no table of record positions is in the exe either -- no run of the real
  offsets survives in any encoding -- so the positions are computed at runtime,
  exactly as on PS3;
* the cipher is ``crypt(buffer, length, name_hash)`` with one continuous
  keystream per call, which is why the file decrypts per page: the engine
  reads it one page at a time.

The computation itself has not been found.  So :func:`build` writes **in
place** -- the text inside its original byte count, ``fon_off``, ``u5`` and the
suffix untouched, the file length unchanged -- and a translation that does not
fit is reported and skipped, never truncated.

That is safe and nearly useless: with a real Arabic translation loaded, 26 of
383 records fit.  :func:`relayout` is the other half of the answer, and it
exists to be **tested** rather than shipped -- see its docstring.

There is no third option.  The suffix after a record's text is voice-cue data,
not padding: 4 276 spare bytes across all 383 English records, against the
223 843 the Arabic needs.  Nothing grows without moving.

Format and the in-place rebuild are ported from the PS3 toolkit's
``briefing.py`` (`C:\\MGSPWclaude\\Tools`); the per-page cipher is what this
build adds.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import NamedTuple

from pwtr.formats import pwcrypt

#: The file name the cipher is keyed by, and the hash it is stored under.
NAME = "0076531d.DAT"

MAGIC = b"oEbN"
HDR_CONST = 0x14
PAD_U32 = 0xFFFFFFFF
FON_MAGIC = b"FON\x00"

#: The unit the keystream restarts on.  This is the whole trick.
PAGE = 0x1000


class BriefingError(Exception):
    pass


class Record(NamedTuple):
    index: int
    offset: int             # absolute, in the decrypted file
    fon_off: int
    u5: int
    str_offsets: list[int]  # from text_base; repeats mean a shared string
    strings: list[bytes]    # raw UTF-8, no terminator
    body_size: int          # the byte budget, terminators included
    suffix: bytes           # padding and the FON block, kept verbatim
    #: Indices whose string has no terminator before the next record.  Their
    #: entry in ``strings`` is empty and its real text, if there is any, comes
    #: from :func:`mend`.  A record with any of these is read-only: its extent
    #: is unknown, so every byte figure derived from it is a guess.
    unterminated: tuple[int, ...] = ()

    @property
    def language(self) -> str:
        return language_of(self.strings)

    @property
    def rebuildable(self) -> bool:
        """Can this record be written back at all?

        The test is whether its **own** strings repack into their own budget.
        Most records pass trivially.  A few hundred do not: their offset table
        declares more entries than there is text, and the surplus entries point
        into the audio block, so a "string" read from one is a run of binary
        that happens to end at a NUL.  Repacking those would write that binary
        back as text and lose the cue data.

        Those records are reported and never touched.  Nothing is guessed at:
        if the record cannot be reproduced from its own contents, this build
        has no business editing it.

        A record with an unterminated string is refused here outright.  Its
        text runs past where this file can read, so its true length is not
        known, and repacking would lay the body out from a size that is a
        guess -- writing over whatever really follows.
        """
        return not self.unterminated and fits(self, {})[0]

    @property
    def used(self) -> int:
        """Bytes the current strings occupy, counting each offset once."""
        seen, total = set(), 0
        for offset, text in zip(self.str_offsets, self.strings):
            if offset not in seen:
                seen.add(offset)
                total += len(text) + 1
        return total


# -- the page cipher --------------------------------------------------------

def load(path: str | Path, name: str | None = None) -> bytes:
    """Decrypt the whole file, one page at a time.

    ``name`` keys the cipher.  It defaults to the file's own name, which is
    right for the game's copy and wrong for every copy of it: rename the file
    to ``BRIEFING_test.DAT`` and the whole thing decrypts to noise, silently,
    with no magic and no records.  Pass the name the game knows when reading
    anything that is not sitting in the game folder under its own name.
    """
    path = Path(path)
    name = name or path.name
    raw = path.read_bytes()
    return b"".join(pwcrypt.decrypt(raw[o:o + PAGE], name)
                    for o in range(0, len(raw), PAGE))


def save(data: bytes, path: str | Path, name: str = NAME) -> Path:
    """Re-encrypt page by page and write.

    ``name`` keys the cipher and must be the name the game knows, not whatever
    the output file happens to be called.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = b"".join(pwcrypt.crypt(data[o:o + PAGE], name)
                   for o in range(0, len(data), PAGE))
    path.write_bytes(out)
    return path


# -- parsing ----------------------------------------------------------------

def parse(data: bytes) -> list[Record]:
    """Every record in the decrypted file, in file order."""
    offsets = []
    start = 0
    while True:
        found = data.find(MAGIC, start)
        if found < 0:
            break
        offsets.append(found)
        start = found + 4

    # "oEbN" also turns up inside string and audio data, and a false record
    # start silently corrupts every length derived from it -- the suffix runs
    # to the next magic, so one bad offset makes its neighbour unaligned.  A
    # real record is 16-byte aligned and carries four fields whose values are
    # fixed or mirrored; all of them are checked.
    offsets = [o for o in offsets if o % 16 == 0]

    records: list[Record] = []
    for index, offset in enumerate(offsets):
        try:
            pad1, pad2, fon_off, const, u5, mirror = struct.unpack_from(
                "<6I", data, offset + 0x04)
        except struct.error:
            continue
        if pad1 != PAD_U32 or pad2 != PAD_U32 or const != HDR_CONST:
            continue
        if mirror != (fon_off - 4) & 0xFFFFFFFF:
            continue
        count = (u5 - 0x10) // 4
        if count <= 0 or not 0x10 < u5 < 0x4000:
            continue
        text_base = offset + 0x0C + u5
        following = (offsets[index + 1] if index + 1 < len(offsets)
                     else len(data))

        try:
            str_offsets = list(struct.unpack_from(f"<{count}I", data,
                                                  offset + 0x1C))
        except struct.error:
            continue

        # Every string has to terminate inside this record.  A handful do not:
        # their last string has no NUL before the next record starts, so an
        # unbounded search runs on into the neighbour and the record comes out
        # 15 bytes longer than the gap it lives in.  Bounding the search is
        # what catches them, and a record that fails is left alone rather than
        # guessed at -- it is not offered for translation and nothing writes
        # to it.
        if any(text_base + x > following for x in str_offsets):
            # The offset table itself is noise.  These records begin within
            # 0x20 bytes of a page boundary, so the header decrypts correctly
            # and the table straddles the break -- the same fault as a split
            # string, one field earlier.  Nothing here can be trusted, so the
            # record is kept as an empty read-only stub: `build` sees it and
            # refuses to write, and `recover` reads it properly from the raw
            # file, which is the only place the alignment can be fixed.
            records.append(Record(
                index=index, offset=offset, fon_off=fon_off, u5=u5,
                str_offsets=str_offsets, strings=[b""] * count,
                body_size=1, suffix=b"",
                unterminated=tuple(range(count)),
            ))
            continue

        strings, unterminated = [], []
        for index_of, string_offset in enumerate(str_offsets):
            start = text_base + string_offset
            end = data.find(b"\x00", start, following)
            if end < 0:
                # No terminator before the neighbour.  Almost always a run
                # that carried across a page boundary: the text reads to the
                # boundary and turns to noise on it, so the NUL is in the
                # noise.  `mend` reads those back at their own alignment, and
                # it only ever sees records that got this far -- dropping the
                # record here is what kept 126 of them, and 467 English
                # lines, out of the workbench entirely.
                unterminated.append(index_of)
                strings.append(b"")
                continue
            strings.append(data[start:end])

        # The budget is the span from text_base to the end of the last string,
        # taken over distinct offsets: strings can be stored out of index
        # order, and two indices can share one offset.
        seen, body = set(), 0
        for string_offset, text in zip(str_offsets, strings):
            if string_offset not in seen:
                seen.add(string_offset)
                body = max(body, string_offset + len(text) + 1)
        body = body or 1
        if text_base + body > following:
            continue
        records.append(Record(
            index=index, offset=offset, fon_off=fon_off, u5=u5,
            str_offsets=str_offsets, strings=strings, body_size=body,
            # With a string unterminated the body ends somewhere this file
            # cannot see, so there is no honest suffix to keep.  Nothing may
            # be written from it, and `rebuildable` says so.
            suffix=b"" if unterminated else data[text_base + body:following],
            unterminated=tuple(unterminated),
        ))
    return records


# -- which language is this record --------------------------------------

_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[a-zà-öø-ÿ']+")

#: Function words, which are the only thing that separates these five
#: reliably.  Accented letters are not: the briefings are set in Costa Rica
#: and Nicaragua, so the English script is full of "Volcan Irazu" and
#: "Cafetal Aroma Encantado", and an accent test files a third of it as
#: Spanish or French.
_STOPWORDS = {
    "en": {"the", "and", "you", "that", "this", "with", "have", "for", "was",
           "not", "but", "your", "what", "they", "will", "from", "are", "its"},
    "fr": {"le", "la", "les", "des", "une", "que", "qui", "pas", "pour", "est",
           "dans", "vous", "nous", "avec", "sur", "plus", "ce", "au"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "wir",
           "sie", "den", "dem", "mit", "auf", "von", "zu", "aber", "sich"},
    "es": {"el", "los", "las", "que", "de", "en", "un", "una", "por", "con",
           "no", "para", "es", "se", "su", "lo", "pero", "del"},
    "it": {"il", "che", "di", "un", "una", "per", "con", "non", "del", "sono",
           "questo", "come", "ma", "gli", "nel", "si", "alla", "dei"},
}


def readable(raw: bytes) -> bool:
    """Is this a string somebody wrote, or a run of bytes ending in a NUL?

    A few hundred records declare more offsets than they have text, and the
    surplus point into the audio block.  Read as a string, one of those is
    binary -- and binary decoded loosely lands all over the CJK range, which
    is enough to make :func:`language_of` call an English tape Japanese.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return bool(text.strip()) and all(c.isprintable() or c in "\n\r\t"
                                      for c in text)


def language_of(strings) -> str:
    """The language of one record: ``jp``, ``en``, ``fr``/``de``/``es``/``it``.

    A record is a single language, so the vote runs over the whole record
    rather than over one line -- a line like "Understood." carries no signal
    at all, and there are thousands of them.

    Only the readable strings get a vote.  Counting the surplus ones put 43
    English tapes in the Japanese pile, which is why a briefing could be read
    in the game and not found anywhere in the editor.
    """
    strings = [s for s in strings if readable(s)] or list(strings)
    text = "\n".join(s.decode("utf-8", "replace") for s in strings)
    if _JAPANESE.search(text):
        return "jp"
    if not _LATIN.search(text):
        return "?"
    words = _WORD.findall(text.lower())
    scores = {code: sum(1 for w in words if w in bag)
              for code, bag in _STOPWORDS.items()}
    best = max(scores, key=lambda code: scores[code])
    return best if scores[best] else "?"


def english(records) -> list[Record]:
    return [r for r in records if r.language == "en"]


# -- rebuilding, in place ---------------------------------------------------

def rebuild(record: Record, replacements: dict[int, bytes]) -> bytes:
    """One record with some strings replaced, at exactly its original length.

    ``replacements`` maps *string index* to new UTF-8 bytes.  Indices sharing a
    text offset share a string, so the first replacement for an offset wins and
    the rest follow it -- which is what the game does too.

    Returns the rebuilt record, or raises :class:`BriefingError` if it does not
    fit.  Nothing is ever truncated: a caller that cannot afford the failure
    should ask :func:`fits` first.
    """
    original = {}
    for offset, text in zip(record.str_offsets, record.strings):
        original.setdefault(offset, text)

    wanted = {}
    for index, offset in enumerate(record.str_offsets):
        if offset in wanted:
            continue
        new = replacements.get(index)
        wanted[offset] = new if new else original[offset]

    order = sorted(set(record.str_offsets))
    position, body = {}, bytearray()
    for offset in order:
        position[offset] = len(body)
        body += wanted[offset] + b"\x00"

    if len(body) > record.body_size:
        raise BriefingError(
            f"record {record.index} needs {len(body)} bytes of text and has "
            f"{record.body_size}")

    body += b"\x00" * (record.body_size - len(body))

    header = (MAGIC
              + struct.pack("<I", PAD_U32)
              + struct.pack("<I", PAD_U32)
              + struct.pack("<I", record.fon_off)      # unchanged
              + struct.pack("<I", HDR_CONST)
              + struct.pack("<I", record.u5)           # unchanged
              + struct.pack("<I", record.fon_off - 4)) # unchanged
    for offset in record.str_offsets:
        header += struct.pack("<I", position[offset])

    rebuilt = bytes(header) + bytes(body) + record.suffix
    if len(rebuilt) % 16:
        raise BriefingError(
            f"record {record.index} rebuilt to {len(rebuilt)} bytes, "
            f"which is not 16-byte aligned")
    return rebuilt


def repack_readable(record: Record, replacements: dict[int, bytes],
                    partial: bool = True, truth=None):
    """Relay the readable part of a record that cannot be repacked whole.

    :func:`overwrite` is safe but mean: each line is held to its own bytes, so
    a line one byte too long is refused while the line above it has thirty to
    spare.  With Arabic averaging 93% of the English it replaces, that slack
    is the difference between a briefing translated and a briefing half
    translated.

    The strings a person wrote sit in one run, and the surplus offsets that
    make the record unrebuildable point past it into the audio.  So the run
    can be relaid on its own: the same pooled budget an ordinary record gets,
    the surplus entries never read and never written, and the audio block
    untouched.  Only the offset table entries for the readable strings move.

    Returns ``({absolute offset: bytes}, [indices refused])``, or ``None`` if
    anything the record does not own begins inside that run -- 34 records of
    478, which fall back to the per-line write.
    """
    text_base = record.offset + 0x0C + record.u5
    # ``truth`` carries the real bytes of the strings whose page-wise copy
    # is broken.  Without them such a string is not "readable", so it would
    # be left out of the layout -- and a string left out of a repack is a
    # string written over.
    truth = truth or {}

    def text_of(index):
        return truth.get(index, record.strings[index])

    real = [i for i in range(len(record.strings))
            if readable(record.strings[i]) or i in truth]
    if not real:
        return None

    start = min(record.str_offsets[i] for i in real)
    end = max(record.str_offsets[i] + len(text_of(i)) + 1
              for i in real)
    outside = {record.str_offsets[i] for i in range(len(record.strings))
               if i not in real}
    if any(start <= offset < end for offset in outside):
        return None
    budget = end - start

    seat = {index: n for n, index in enumerate(real)}
    sub = record._replace(
        str_offsets=[record.str_offsets[i] - start for i in real],
        strings=[text_of(i) for i in real],
        body_size=budget, suffix=b"")
    asked = {seat[i]: t for i, t in replacements.items() if i in seat}
    refused = [i for i in replacements if i not in seat]

    kept = (fill_in_place(sub, asked) if partial
            else (asked if fits(sub, asked)[0] else {}))
    refused += [i for i in replacements
                if i in seat and seat[i] not in kept]

    original = {}
    for offset, text in zip(sub.str_offsets, sub.strings):
        original.setdefault(offset, text)
    wanted = dict(original)
    for index, text in kept.items():
        wanted[sub.str_offsets[index]] = text

    body, moved = bytearray(), {}
    for offset in sorted(original):
        moved[offset] = len(body)
        body += wanted[offset] + b"\x00"
    if len(body) > budget:
        return None
    body += b"\x00" * (budget - len(body))

    writes = {text_base + start: bytes(body)}
    region = (text_base + start, len(body))
    for index in real:
        moved_to = start + moved[record.str_offsets[index] - start]
        writes[record.offset + 0x1C + 4 * index] = struct.pack("<I", moved_to)
    return writes, refused, region


def overwrite(record: Record, replacements: dict[int, bytes],
              room_of=None) -> tuple:
    """Write each string inside its own bytes, moving nothing at all.

    For the records that cannot be repacked.  Their offset table over-declares
    and the surplus entries point into the audio block, so laying the body out
    afresh would write that audio back as text.  But one string can still be
    replaced where it lies: the span from its offset to its terminator belongs
    to it alone, and a shorter replacement followed by NULs leaves every other
    byte in the record exactly as it was.

    The budget is therefore per line, not per record -- a short line cannot
    lend its slack to a long one the way a repack allows.  Anything too long
    for its own span is refused and counted.

    Returns ``({absolute offset: bytes}, [indices refused])``.
    """
    text_base = record.offset + 0x0C + record.u5
    writes, refused = {}, []
    for index, text in sorted(replacements.items()):
        if index >= len(record.strings):
            refused.append(index)
            continue
        # A string whose cipher run crosses a page boundary has no
        # trustworthy length in the record: read page-wise it runs past its
        # own terminator into noise.  Its real extent comes from the mend.
        room = (room_of or {}).get(index, len(record.strings[index]))
        if len(text) > room:
            refused.append(index)
            continue
        # The span is the string plus its terminator; NUL-fill the rest of it
        # so no tail of the old line survives past the end of the new one.
        writes[text_base + record.str_offsets[index]] = (
            text + b"\x00" * (room - len(text) + 1))
    return writes, refused


def fits(record: Record, replacements: dict[int, bytes]) -> tuple[bool, int, int]:
    """``(does it fit, bytes needed, bytes available)`` without building it."""
    original = {}
    for offset, text in zip(record.str_offsets, record.strings):
        original.setdefault(offset, text)
    wanted = {}
    for index, offset in enumerate(record.str_offsets):
        if offset in wanted:
            continue
        new = replacements.get(index)
        wanted[offset] = new if new else original[offset]
    needed = sum(len(wanted[o]) + 1 for o in sorted(set(record.str_offsets)))
    return needed <= record.body_size, needed, record.body_size


def _page_count(start: int, span: int) -> int:
    return ((start & 0xFFF) + span + PAGE - 1) // PAGE


def relayout(data: bytes, edits: dict[int, dict[int, bytes]],
             note=None, page_budget: bool = False) -> tuple[bytes, dict]:
    """Rebuild the whole file, letting records grow and move.

    Two things were established in game, in this order.

    **Records may move.**  The PS3 engine will not have it -- briefings there
    are sought to positions worked out at runtime -- but the PC build plays a
    fully relaid-out file, and an English one plays with no issue at all.  The
    in-place byte cap was never the real constraint here.

    **A record may not outgrow its pages.**  A record that went from one page
    to two lost exactly the part of its voice-cue block that fell past the
    boundary: 282 bytes of 643 survived.  The cues are what say which line
    appears when, so the text advanced as far as they reached and stopped, the
    audio played on as a separate stream, and with no end-of-briefing cue the
    screen never closed.

    What enforces that is still unidentified -- the KEY-backed footprint table
    in the exe belongs to SLOT and the briefing path never touches it -- but
    the constraint is respected here regardless.  With ``page_budget`` set, no
    record is allowed to occupy more pages than it did in stock:

    * first the record is tried where it naturally falls;
    * then **page-aligned**, which usually costs a page fragment and buys a
      whole page of room -- a record that needs two pages starting halfway
      through one needs only one starting at the top;
    * and only if it still will not fit is the translation dropped and the
      record left in English.

    Trying the aligned position *before* giving up is the whole difference
    between translating a third of the records and translating most of them.
    """
    records = parse(data)
    budgets = {}
    for r in records:
        span = (0x1C + 4 * len(r.str_offsets) + r.body_size + len(r.suffix))
        budgets[r.offset] = _page_count(r.offset, span)

    out = bytearray()
    grown = moved = dropped = aligned = 0
    first_moved = None
    cursor = 0

    for record in records:
        # Everything between the last record and this one is copied across
        # untouched.  parse() recognises 2 697 records out of 2 761 magics and
        # deliberately skips the rest; emitting only what it parsed drops those
        # regions on the floor, which shortens the file by a couple of hundred
        # kilobytes of live data and is exactly the kind of corruption that
        # looks like "the engine cannot take a moved record".
        if record.offset > cursor:
            out += data[cursor:record.offset]

        replacements = edits.get(record.offset, {})
        span = (0x1C + 4 * len(record.str_offsets)
                + record.body_size + len(record.suffix))
        cursor = record.offset + span
        rebuilt = None

        if replacements and record.rebuildable:
            original = {}
            for offset, text in zip(record.str_offsets, record.strings):
                original.setdefault(offset, text)
            wanted = {}
            for index, offset in enumerate(record.str_offsets):
                if offset in wanted:
                    continue
                new = replacements.get(index)
                wanted[offset] = new if new else original[offset]

            position, body = {}, bytearray()
            for offset in sorted(set(record.str_offsets)):
                position[offset] = len(body)
                body += wanted[offset] + b"\x00"
            if len(body) > record.body_size:
                grown += 1

            # The body is NOT padded here.  The suffix -- the voice cue block
            # -- follows the text immediately, and fon_off is the record's own
            # pointer to it, so any byte inserted between them has to be
            # accounted for in fon_off or the engine reads cue data from the
            # middle of a sentence.  Keeping the body exact means the only
            # correction needed is this delta.
            delta = len(body) - record.body_size
            fon_off = record.fon_off + delta

            header = (MAGIC
                      + struct.pack("<I", PAD_U32)
                      + struct.pack("<I", PAD_U32)
                      + struct.pack("<I", fon_off)
                      + struct.pack("<I", HDR_CONST)
                      + struct.pack("<I", record.u5)
                      + struct.pack("<I", fon_off - 4))
            for offset in record.str_offsets:
                header += struct.pack("<I", position[offset])
            rebuilt = bytes(header) + bytes(body) + record.suffix

            if page_budget:
                budget = budgets[record.offset]
                if _page_count(len(out), len(rebuilt)) > budget:
                    # try it at the top of a page before giving up on it
                    pad = (PAGE - (len(out) % PAGE)) % PAGE
                    if _page_count(len(out) + pad, len(rebuilt)) <= budget:
                        out += b"\x00" * pad
                        aligned += 1
                    else:
                        rebuilt = None
                        dropped += 1
        if rebuilt is None:
            rebuilt = data[record.offset:record.offset + span]

        if page_budget:
            budget = budgets[record.offset]
            if _page_count(len(out), len(rebuilt)) > budget:
                # a record that only moved can still need an extra page purely
                # because it now starts further into one
                out += b"\x00" * ((PAGE - (len(out) % PAGE)) % PAGE)
                aligned += 1

        if len(out) != record.offset:
            moved += 1
            if first_moved is None:
                first_moved = record.offset
        out += rebuilt
        while len(out) % 16:
            out += b"\x00"

    # and whatever follows the last record
    if cursor < len(data):
        out += data[cursor:]

    report = {"records": len(records), "grown": grown, "moved": moved,
              "dropped": dropped, "aligned": aligned,
              "first_moved": first_moved,
              "before": len(data), "after": len(out)}
    if note:
        note(f"{grown} record(s) grew, {moved} of {len(records)} moved")
        if page_budget:
            note(f"{aligned} page-aligned to stay within budget, "
                 f"{dropped} left in English because they could not")
        if first_moved is not None:
            note(f"the first to move sat at 0x{first_moved:06X}")
        note(f"{len(data)} -> {len(out)} bytes")
    return bytes(out), report


def fill_in_place(record: Record, replacements: dict[int, bytes]
                  ) -> dict[int, bytes]:
    """As many of a record's translations as fit its own byte budget.

    The all-or-nothing version skips a record the moment its full translation
    is too big, which for this container means skipping nearly all of them.
    Taking the lines in order and stopping when the next one would not fit
    gets Arabic into the top of a briefing and leaves the tail in English --
    the record does not grow by a byte, so nothing moves and nothing is
    truncated.
    """
    original = {}
    for offset, text in zip(record.str_offsets, record.strings):
        original.setdefault(offset, text)

    order = sorted(set(record.str_offsets))
    first = {}
    for index, offset in enumerate(record.str_offsets):
        first.setdefault(offset, index)

    keep: dict[int, bytes] = {}
    total = sum(len(original[o]) + 1 for o in order)

    # Repeated until nothing more can be placed.  A single pass is
    # order-dependent and wrong in the direction that matters: an early line is
    # judged against the total before any later line has shrunk it, so it is
    # refused and never looked at again -- even when the record ends up with
    # hundreds of bytes spare.  With a compact encoding most replacements are
    # *shorter* than what they replace, so the room appears as the pass goes on.
    while True:
        placed = False
        for offset in order:
            index = first[offset]
            if index in keep:
                continue
            candidate = replacements.get(index)
            if not candidate:
                continue
            grown = total - len(original[offset]) + len(candidate)
            if grown <= record.body_size:
                keep[index] = candidate
                total = grown
                placed = True
        if not placed:
            break
    return keep


def mend(raw: bytes, records, name: str = None):
    """Recover the strings that page-wise decryption cut in half.

    The cipher is not applied one page at a time.  A run can carry across a
    page boundary, and where it does, decrypting that page on its own is
    wrong: the text reads correctly up to the boundary and turns to noise
    exactly on it.  ``Playa del Alba``'s third line ends mid-word at "palm
    tre" for precisely this reason, and 606 strings across the file do the
    same -- which is also why several hundred records look unrebuildable and
    several dozen English tapes were voted Japanese.

    Decrypting the pages a record spans as a single call gives the run its
    proper alignment and every one of the 606 comes back whole.

    Returns ``{(record offset, string index): bytes}`` for the strings that
    were recovered.  It reads; it does not write.  A recovered string sits at
    a different cipher alignment from the page it lives on, so writing one
    back means re-encrypting its run, and until that is done these are
    extracted and shown but never built.
    """
    import pwcrypt

    name = name or NAME
    mended = {}
    for record in records:
        base = record.offset + 0x0C + record.u5
        first = {}
        for index, offset in enumerate(record.str_offsets):
            first.setdefault(offset, index)
        # Every unreadable string is a candidate, not only the ones with a
        # readable head.  A string that begins *after* the boundary has no
        # readable part at all under the wrong alignment -- which is how the
        # fourth line of a briefing went missing while the third came back.
        # Nothing is guessed: a recovery is kept only if it reads as text.
        broken = [i for i in first.values()
                  if i < len(record.strings)
                  and not readable(record.strings[i])]
        if not broken:
            continue
        low = (record.offset // PAGE) * PAGE
        high = min(len(raw), ((base + record.body_size) // PAGE + 2) * PAGE)
        span = pwcrypt.decrypt(raw[low:high], name)
        for index in broken:
            at = base + record.str_offsets[index] - low
            end = span.find(b"\x00", at)
            text = span[at:end] if end >= 0 else span[at:at + 400]
            if readable(text):
                mended[(record.offset, index)] = text
    return mended


def recover(raw: bytes, records, name: str = None) -> list:
    """Read back the records whose *offset table* fell across a page break.

    :func:`mend` repairs a string that straddles a boundary.  This is the same
    fault one field earlier: these records begin within a few bytes of the
    break, so the header decrypts correctly -- the padding, the constant and
    the mirrored ``fon_off`` all check out -- and the offset table that starts
    at ``+0x1C`` runs into the next page, where the keystream has restarted.
    Read page-wise the table is nonsense, with offsets in the gigabytes, which
    is what :func:`parse` sees when it makes the stub.

    Decrypting the pages the record spans in one call gives the table its
    proper alignment, and the strings behind it come back with it.  53 records
    and 92 lines the PS3 script has and this file appeared not to.

    The result is still **read-only**: the strings sit at an alignment the
    page they live on does not use, so writing one back means re-encrypting
    its run.  Every recovered record keeps its ``unterminated`` marks, which
    is what stops :func:`build` touching it.

    Returns a new list with the stubs replaced; anything already whole is
    passed through untouched.
    """
    name = name or NAME
    out = []
    for record in records:
        count = len(record.str_offsets)
        if not count or set(record.unterminated) != set(range(count)):
            out.append(record)
            continue

        low = (record.offset // PAGE) * PAGE
        high = min(len(raw), (record.offset // PAGE + 4) * PAGE)
        span = pwcrypt.decrypt(raw[low:high], name)
        head = record.offset - low
        try:
            offsets = list(struct.unpack_from(f"<{count}I", span,
                                              head + 0x1C))
        except struct.error:
            out.append(record)
            continue
        base = head + 0x0C + record.u5
        # Only trust a table that is plausible on its own terms: inside the
        # span, and in the order the reader expects.  A wrong alignment
        # produces offsets in the millions, so this is not a close call.
        if not offsets or any(not 0 <= o < (high - low) - base for o in offsets):
            out.append(record)
            continue
        strings = []
        for offset in offsets:
            start = base + offset
            end = span.find(b"\x00", start)
            strings.append(span[start:end] if end >= 0 else b"")
        if not any(readable(s) for s in strings):
            out.append(record)
            continue
        out.append(record._replace(str_offsets=offsets, strings=strings))
    return out


def rewrite_run(raw: bytes, record: Record, replacements: dict[int, bytes],
                following: int, name: str = None):
    """-> ``(run start, this record's new plaintext, [indices refused])``

    The write path for a record the page-wise view cannot represent.  Its
    text is decrypted the way the engine reads it -- the record's pages in
    one call, from the page its own header starts on -- so the offset table,
    every string and every terminator are the real ones.  Replacements go in
    string by string, each inside its own span, exactly as
    :func:`overwrite` does for the ordinary records.

    What comes back is plaintext at the *run's* alignment, so the caller has
    to mask it through :func:`realign` before :func:`save` encrypts page-wise.
    Only the bytes between this record and the next are returned: the decrypt
    window runs to a page edge and covers part of the neighbour, whose own
    alignment is not this one's.
    """
    low = (record.offset // PAGE) * PAGE
    high = min(len(raw), (following // PAGE + 2) * PAGE)
    span = bytearray(pwcrypt.decrypt(raw[low:high], name or NAME))
    base = record.offset - low + 0x0C + record.u5

    refused = []
    for index, text in sorted(replacements.items()):
        if index >= len(record.str_offsets):
            refused.append(index)
            continue
        at = base + record.str_offsets[index]
        end = span.find(b"\x00", at)
        if at >= len(span) or end < 0:
            refused.append(index)
            continue
        room = end - at
        if len(text) > room:
            refused.append(index)
            continue
        span[at:at + room + 1] = text + b"\x00" * (room - len(text) + 1)

    return low, bytes(span[record.offset - low:following - low]), refused


def realign(data: bytes, spans, name: str = None) -> bytes:
    """Re-key the spans whose cipher run does not start at their own page.

    :func:`save` encrypts page by page.  For a string whose run began on an
    earlier page that is the wrong keystream, so the bytes are pre-XORed here
    with the difference between the two:

        mask[x] = ks[x mod PAGE] ^ ks[x - run start]

    Encrypting page-wise afterwards then yields ``plain ^ ks[x - run]``, which
    is what the game will decrypt with.  The mask is its own inverse, so
    reading the file back through :func:`load` and :func:`mend` returns the
    text unchanged.

    Only the spans actually written are passed in.  A span left in English
    must keep its stock bytes, and masking one nobody edited would corrupt
    exactly the line it was meant to protect.
    """
    import pwcrypt

    name = name or NAME
    out = bytearray(data)
    stream = pwcrypt.crypt(b"\x00" * (PAGE * 4), name)
    for start, length, low in spans:
        for x in range(start, start + length):
            out[x] ^= stream[x % PAGE] ^ stream[x - low]
    return bytes(out)


def checked_repack(record: Record, replacements: dict[int, bytes],
                   truth: dict, partial: bool, stream=None):
    """Repack a record holding recovered strings -- but only if it verifies.

    Pinning such a record to the per-line write is safe and costly: no line
    can borrow the slack of another, so a translation one byte over is refused
    while its neighbour has thirty spare.  Where most recovered lines are
    translated that is hundreds of refusals.

    The rules of this container are not known well enough to be sure a repack
    is safe here, so this does not reason about it.  It does the repack,
    reads every string back out of the layout it produced, and accepts the
    result only if all of them are byte-exact.  Anything less and the caller
    writes each line where it already lies.

    Returns ``(writes, refused, region)`` or ``None``.
    """
    import struct as _struct

    attempt = repack_readable(record, replacements, partial, truth)
    if attempt is None:
        return None
    writes, refused, region = attempt

    base = record.offset + 0x0C + record.u5
    start, length = region
    # The body as laid out *is* the plaintext.  The re-keying mask turns
    # plaintext into what the page-wise save must be handed so the game reads
    # it back -- so masking here and then comparing against the text would be
    # comparing a cipher against a plain, which is how this check came to
    # reject every repack it was given.
    body = bytearray(writes[start])

    # What each *offset* is meant to hold.  Several table entries can share
    # one string -- the same line drawn in two places -- and the second must
    # be judged against the text the first one owns, not against its own
    # stale copy of the English.
    intended = {}
    for index, offset in enumerate(record.str_offsets):
        if offset in intended or index >= len(record.strings):
            continue
        want = replacements.get(index)
        if want is None or index in refused:
            want = truth.get(index, record.strings[index])
        intended[offset] = want

    for index, offset in enumerate(record.str_offsets):
        key = record.offset + 0x1C + 4 * index
        if key not in writes:
            continue
        moved = _struct.unpack("<I", writes[key])[0]
        at = base + moved - start
        if not 0 <= at < length:
            return None
        end = body.find(b"\x00", at)
        if end < 0:
            return None
        if bytes(body[at:end]) != intended.get(offset):
            return None
    return writes, refused, region


def build(data: bytes, edits: dict[int, dict[int, bytes]],
          note=None, partial: bool = False,
          straddling=None, raw: bytes = None,
          name: str = None) -> tuple[bytes, dict]:
    """Apply ``{record offset: {string index: bytes}}`` to the whole file.

    Every record keeps its length, so the result is the same size as the input
    and every page boundary stays where it was.  Records whose text no longer
    fits are left stock and listed in the report.

    ``raw`` is the file still encrypted.  Give it and the records whose cipher
    run crosses a page boundary can be written too: they are read and rewritten
    at the run's own alignment and masked back through :func:`realign`.
    Without it those records are recognised but refused, which is the safe
    answer and was the only one before.
    """
    all_records = parse(data)
    if raw is not None:
        all_records = recover(raw, all_records, name)
    records = {r.offset: r for r in all_records}
    in_order = sorted(records)
    out = bytearray(data)
    done = skipped = untouched = dropped = 0
    rekey: list = []
    straddling = straddling or {}
    # A log nobody can scroll to the end of is a log nobody reads.  The first
    # few are shown, the rest are counted -- and every one of them is
    # reachable from the editor, which is where a line gets shortened anyway.
    LOUD = 12
    spoken = [0]
    import pwcrypt
    stream = pwcrypt.crypt(b"\x00" * (PAGE * 4), NAME)
    over: list[dict] = []

    for offset, replacements in sorted(edits.items()):
        record = records.get(offset)
        if record is None:
            continue

        # A record holding a string with no terminator is read-only, and it
        # is not enough that `rebuildable` is False.  That bars the repack;
        # the in-place path below would still write, and for these records
        # in-place is the more dangerous of the two.  Their missing text
        # comes back from `mend`, which reads at a different cipher alignment
        # from the page it lives on, so its length describes bytes this file
        # cannot represent at that address.  Writing one covers what follows:
        # measured, it put a mended line into 0x0A7DC0 and took out nine
        # strings of the voice-cue block behind it.
        if record.unterminated:
            if raw is None:
                for index in replacements:
                    dropped += 1
                    over.append({
                        "offset": offset, "index": index,
                        "readonly": True,
                        "reason": "a string in this record runs past what "
                                  "the file can read, so nothing in it can "
                                  "be written until that run is re-encrypted",
                    })
                if note:
                    note(f"record 0x{offset:06X} left alone -- "
                         f"{len(replacements)} translated line(s) in it "
                         f"cannot be written: it holds text recovered from "
                         f"across a page boundary")
                continue

            # Written at the run's alignment and masked back.  The whole span
            # to the next record goes in as one piece: the boundary the
            # keystream restarts on falls inside it, so there is no smaller
            # unit that is coherent.
            at = in_order.index(offset)
            following = (in_order[at + 1] if at + 1 < len(in_order)
                         else len(data))
            low, fresh, refused = rewrite_run(raw, record, replacements,
                                              following, name)
            if len(fresh) != following - offset:
                # The decrypt window ran off the end of the file, so this is
                # not the whole record.  Writing a short piece would shorten
                # the file and move every record after it.
                for index in replacements:
                    dropped += 1
                    over.append({"offset": offset, "index": index,
                                 "readonly": True,
                                 "reason": "this record runs to the end of "
                                           "the file and cannot be re-keyed"})
                continue
            out[offset:following] = fresh
            rekey.append((offset, following - offset, low))
            if refused:
                for index in refused:
                    dropped += 1
                    over.append({
                        "offset": offset, "index": index,
                        "needed": len(replacements[index]) + 1,
                        "budget": 0,
                        "reason": "line is longer than the bytes it sits in",
                    })
            done += 1
            continue

        # Only rewrite a record that actually changes.  Repacking lays the
        # strings out afresh, which is valid -- the game reads them through the
        # offset table -- but it is not the same bytes, and a record nobody
        # translated has no business being rewritten at all.
        real = {i: t for i, t in replacements.items()
                if i < len(record.strings) and t and t != record.strings[i]}
        if not real:
            untouched += 1
            continue
        replacements = real

        # A record holding a recovered string never repacks, whether it
        # could otherwise or not.  Two reasons, and either alone is enough:
        # the mask that re-keys such a string is computed from its address,
        # so moving it keys it wrong; and a repack lays the region out from
        # the strings it can read, so one it cannot read is one it writes
        # over -- which is how seven rebuildable records quietly destroyed
        # eighteen of the lines this whole exercise recovered.
        pinned = any(rec == offset for rec, _index in straddling)
        if pinned or not record.rebuildable:
            truth = {index: text for (rec, index), text in straddling.items()
                     if rec == offset}
            rooms = {i: len(t) for i, t in truth.items()}
            relaid = None
            if pinned:
                # Pool the record's budget if -- and only if -- reading the
                # result back proves every string in it byte-exact.
                relaid = checked_repack(record, replacements, truth, partial,
                                        stream)
            elif not record.rebuildable:
                relaid = repack_readable(record, replacements, partial)
                if relaid is not None and len(relaid) == 3:
                    relaid = (relaid[0], relaid[1], relaid[2])
            if relaid is not None:
                writes, refused, region = relaid
                if pinned:
                    rekey.append((region[0], region[1],
                                  (record.offset // PAGE) * PAGE))
            else:
                writes, refused = overwrite(record, replacements, rooms)
                for index in replacements:
                    if (offset, index) in straddling and index not in refused:
                        at = (record.offset + 0x0C + record.u5
                              + record.str_offsets[index])
                        if at in writes:
                            rekey.append((at, len(writes[at]),
                                          (record.offset // PAGE) * PAGE))
            # Not `raw`: that is the encrypted file this function was handed,
            # and rebinding it here left every later record's run rewrite
            # reading a decrypt window a few bytes long.
            for where, chunk in writes.items():
                out[where:where + len(chunk)] = chunk
            if writes:
                done += 1
            else:
                untouched += 1
            for index in refused:
                # Named one by one, with the shortfall, so "Next too long"
                # can walk to them like any other line that did not fit.
                room = rooms.get(index, len(record.strings[index])
                                 if index < len(record.strings) else 0)
                dropped += 1
                over.append({"offset": offset, "index": index,
                             "needed": len(replacements[index]) + 1,
                             "budget": room + 1,
                             "reason": "line is longer than the bytes it "
                                       "sits in, and this record cannot "
                                       "repack"})
            if refused:
                spoken[0] += 1
                if note and spoken[0] <= LOUD:
                    note(f"record 0x{offset:06X} written in place; "
                         f"{len(refused)} line(s) did not fit their own span")
                elif note and spoken[0] == LOUD + 1:
                    note("...further records like this are counted in the "
                         "summary rather than listed")
            continue

        if partial:
            # Filling line by line means some lines are left behind, and a
            # line left behind in silence is a translation the writer thinks
            # shipped.  Say which, and by how much: over by one byte is a
            # word shorter, and worth knowing.
            asked = dict(replacements)
            replacements = fill_in_place(record, replacements)
            for index, text in asked.items():
                if index in replacements:
                    continue
                _ok, needed, budget = fits(
                    record, {**replacements, index: text})
                dropped += 1
                over.append({"offset": offset, "index": index,
                             "needed": needed, "budget": budget,
                             "reason": "line does not fit"})
                spoken[0] += 1
                if note and spoken[0] <= LOUD:
                    note(f"record 0x{offset:06X} line {index} is "
                         f"{needed - budget} byte(s) too long -- left in "
                         f"English")
                elif note and spoken[0] == LOUD + 1:
                    note("...further lines like this are counted in the "
                         "summary rather than listed")
            if not replacements:
                untouched += 1
                continue

        ok, needed, budget = fits(record, replacements)
        if not ok:
            skipped += 1
            over.append({"offset": offset, "needed": needed, "budget": budget})
            if note:
                note(f"record 0x{offset:06X} needs {needed} bytes of "
                     f"{budget} -- left in English")
            continue
        rebuilt = rebuild(record, replacements)
        out[offset:offset + len(rebuilt)] = rebuilt
        done += 1

    if len(out) != len(data):
        raise BriefingError("the rebuild changed the file length, which would "
                            "move every record after it")
    return bytes(out), {"records": done, "skipped": skipped,
                        "untouched": untouched, "lines": dropped,
                        "rekey": rekey, "overflow": over}
