r"""The story text that is not in a language table.

:func:`pwtr.project.Project.extract_story` reads ``.olang`` tables, which is
where the script lives.  It is not where all of it lives.  Measured against
the PS3 translation -- the only independent list of what the game says --
2 399 English lines are in SLOT.DAT and in no tab, and they are ordinary
player-facing dialogue::

    Give 'em the silent treatment!          .ohd    voice-cue subtitles
    Stay focused on the mission, Snake.     .ypk    co-op and tutorial lines
    Abort mission. Come on back.            .olang  pool string, no English ref

Three shapes, one habit: a NUL-terminated string sitting in a fixed field or a
sorted pool, reached by offset rather than through a language table.  The
``.ohd`` ones are 128-byte records -- a 32-byte voice cue, 64 bytes of
subtitle, 32 bytes of parameters -- the same layout :mod:`pwtr.staff` found
for the roster.  The others differ in how they are indexed and not in how they
are stored, so nothing here models a table: it finds strings and writes them
back where they were.

## Why in place, and what that costs

A translation goes inside the bytes its English occupied, NUL-padded to the
end of the span.  Nothing moves, so no offset table needs to be understood --
and, more to the point, the block's *decompressed* size does not change.  That
matters more here than anywhere: the engine sizes its buffer from the stock
block rounded up to a page, and a block one byte over that hangs the game.  A
string that grew would put the whole block at risk to save one line.

The cost is that a line longer than its English is refused and stays English.
The ``.ohd`` fields have slack -- 64 bytes with the longest English at 58 --
while a pool string has none at all.

## What is offered

Only runs that terminate, are at least :data:`SHORTEST` printable characters,
and read as English rather than as another language or an asset name.  The
test is deliberately mean: this is reading bytes nothing has told us are text,
and a false positive is a write into data that was never a string.
"""
from __future__ import annotations

import re

from pwtr import briefing

#: Shorter than this and there is no way to tell a sentence from a file name.
SHORTEST = 10

#: A printable run.  ``\n`` is in it because these strings wrap; the literal
#: two-character ``\n`` some of them use instead is just text.
RUN = re.compile(rb"[\x20-\x7E\n]{%d,}\x00" % SHORTEST)

_WORD = re.compile(r"[a-z']+")


def is_english(text: str) -> bool:
    """Does this read as English, rather than one of the other five?

    The same function-word vote the briefings use.  Accents cannot decide it
    -- the script is full of Costa Rican place names -- and neither can the
    alphabet, because French, German, Spanish and Italian share ours.
    """
    words = set(_WORD.findall(text.lower()))
    if not words:
        return False
    scores = {lang: len(words & bag)
              for lang, bag in briefing._STOPWORDS.items()}
    return scores["en"] > 0 and max(scores, key=scores.get) == "en"


def strings(data: bytes):
    """``(offset, text bytes)`` for every English string in one element.

    The offset is into ``data`` and the text excludes its terminator, so the
    span this may be written into is ``offset .. offset + len(text)``.
    """
    out = []
    for match in RUN.finditer(data):
        raw = match.group()[:-1]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if is_english(text):
            out.append((match.start(), raw))
    return out


def rewrite(data: bytes, translations: dict):
    """-> ``(new data, [english written], [(english, bytes needed, room)])``

    ``translations`` is keyed on the English text, as everywhere else in this
    toolkit, so one entry serves every copy of a line.  The result is always
    the same length as the input.

    The English of what was written comes back, not just a count: a block the
    build changed has to be able to name a line in it, and a block changed
    only here would otherwise report itself as holding nothing.
    """
    out = bytearray(data)
    written, refused = [], []
    for offset, raw in strings(data):
        try:
            english = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        text = translations.get(english)
        if not text:
            continue
        new = text.encode("utf-8")
        if len(new) > len(raw):
            refused.append((english, len(new), len(raw)))
            continue
        out[offset:offset + len(raw)] = new + b"\0" * (len(raw) - len(new))
        written.append(english)
    return bytes(out), written, refused
