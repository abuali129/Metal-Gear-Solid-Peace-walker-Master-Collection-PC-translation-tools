"""Encoding Arabic so it fits where English was.

The briefing container will not let a record grow by a single byte -- proven in
game, sixteen bytes is enough to break every tape after it -- and Arabic
overruns its records by 1.86x.  Not because Arabic is verbose, but because of
where its glyphs live: Presentation Forms-B sits at U+FE70..U+FEFC, which is
**three bytes per character** in UTF-8 against English's one.

Nothing says the glyphs have to live there.  The XPR character map is indexed
by code point and this toolkit chooses which code point carries which glyph, so
the same sentence can be stored at one or two bytes a letter instead of three:

    3 bytes/glyph   25 of 383 records fit    1.86x over budget
    2 bytes/glyph   30 of 383 records fit    1.30x
    1 byte/glyph   383 of 383 records fit    0.75x

## Which code points are free

Only a code point under 0x80 costs one byte, so there are 128 to go round and
the game's own English has already spoken for about ninety.  Of what is left,
``SAFE_SINGLE`` is the part that is simply free.  The control range is not
usable: painted into the font and stored in the text those bytes **do not
draw**, proven in game -- eighteen of the forty-nine characters in the opening
briefing line went missing and the sentence closed up around them.

Nine free slots is not enough.  They carry 35% of the Arabic characters and
only 3% of the translated lines fit:

    slots   coverage   whole records   lines placed
        9      35.1%      47 of 383       3%
       20      55.5%     220 of 383      51%
       35      74.6%     368 of 383      99%
       44      81.9%     377 of 383     100%

## Reclaiming code points

The rest has to be taken from characters the game does use, and the question
is which ones can be spared.  Not the letters: a translation is full of
English that stays English -- names, ranks, acronyms -- so ``A`` and ``e`` are
drawn thousands of times by the very text we are fitting, and stealing one
corrupts the Arabic as surely as the English.  Not the digits either.

That leaves the punctuation, and it is enough.  Thirty slots, of which the
Arabic itself draws twenty-two -- so those are **relocated too**, the full
stop and the comma travelling to two-byte code points exactly like a
presentation form.  Once they have moved, nothing drawn anywhere holds a
literal ``.``, and the slot is free of ambiguity.

    30 one-byte slots   68% of characters   4 680 of 4 975 lines placed

The cost is 3.9% of the characters in the lines nobody has translated yet,
where a full stop now draws an Arabic letter.  No letter and no digit is
touched, upper or lower.

## Markup is not text

``<I=...>`` and ``<C=...>`` are read by the engine, not drawn by it, so they
must survive byte for byte.  Everything here works outside them.

Two-byte code points are used for everything that does not fit in one.
"""

from __future__ import annotations

import collections

#: Printable single-byte code points the game's own text never uses.
#: 0x40 and 0x7B join the list because every line that contains them is one
#: this project replaces, so nothing survives to be drawn wrongly.
SAFE_SINGLE = [0x5C, 0x5E, 0x60, 0x7C, 0x7D, 0x7E, 0x7F, 0x40, 0x7B]

#: Code points that must keep their own glyph whatever else is reclaimed:
#: the digits, space and the line breaks.  Numbers appear in Arabic text as
#: readily as in English, and a garbled one is a lie rather than a blemish.
KEEP = set(b"0123456789") | {0x20, 0x0A, 0x0D}

#: Characters the engine reads rather than draws, which therefore may not be
#: given to an Arabic form.  ``<=>IC`` build ``<I=...>`` and ``<C=...>``, and a
#: relocated ``<`` leaves the tag unparseable.
#:
#: ``$`` and ``%`` are here for the opposite reason -- not to protect the
#: token, which :data:`pwtr.arabic.PROTECT` does, but to stop one being
#: invented.  The engine scans for ``$`` before a digit and ``%`` before
#: ``sdif``, and Arabic runs beside numerals constantly, so a form sitting on
#: either code point manufactures markers in text that has none: measured over
#: this project's translations, 2 092 of them -- 861 stray ``%d`` alone.
#:
#: ``:/.-`` are a third case: characters the engine *emits*.  A save slot's
#: stamp is built at runtime, never passing through any string this project
#: can translate, so whatever the face draws at those code points is what the
#: player sees -- ``09/03/2026 17:18 325.9KB`` came out with three Arabic
#: letters in it.  Taken from the game's own format strings::
#:
#:     %04d/%02d/%02d %02d:%02d      %02d-%02d-%04d      %.1fKB
#:     %d:%02d:%02d                  %03d/%03d           %2d.%d
#:
#: ``,`` is left in the pool: it appears only in what look like debug and
#: asset names, never in a format the player sees.
MARKUP = set(b"<=>IC$%:/.-")

#: The control range.  **The engine drops these.**  Painted into the font and
#: stored in the text they simply do not draw -- the letters go missing and the
#: sentence closes up around them, which is what the first compact build did
#: to eighteen of the forty-nine characters in its opening line.  Kept only so
#: the reason is recorded; never assigned.
CONTROL_SINGLE: list[int] = []

#: Two-byte room.  Latin Extended-A and B are untouched by the game and sit
#: well clear of the accented letters its own European text needs.
DOUBLE_FIRST, DOUBLE_LAST = 0x0100, 0x024F

#: The highest code point the engine will actually look up.  U+00AB and the
#: whole of Latin Extended draw; U+060C and U+061F, painted into the face with
#: ink in the atlas and a sound charmap entry, come out as empty boxes -- which
#: is what put a box where every comma and question mark should have been.  So
#: the ceiling sits above U+024F and below U+060C, and everything past it, the
#: presentation forms and the Arabic punctuation alike, has to be moved down
#: rather than left where Unicode puts it.
CEILING = DOUBLE_LAST


def census(texts) -> collections.Counter:
    """Every character the game's own text uses, counted."""
    used = collections.Counter()
    for text in texts:
        for ch in text:
            used[ord(ch)] += 1
    return used


def free_single(used) -> list[int]:
    """Single-byte code points that are free, safest first."""
    return ([c for c in SAFE_SINGLE if c not in used]
            + [c for c in CONTROL_SINGLE if c not in used])


def free_slots(drawn, capitals: bool = True,
               lowercase: bool = False) -> list[int]:
    """Single-byte code points that may carry an Arabic glyph.

    Punctuation always: nothing else is going spare.  A case range on top of
    it, and those letters are not *taken*, they are **moved** -- the letter
    keeps its glyph at a two-byte code point, so every line this project
    writes still spells "MSF" and "KGB" correctly.  What it gives up is that
    letter in text nobody rendered through the plan, which draws an Arabic
    form instead.

    That trade is worth making because of where the pressure is:

        punctuation only   30 slots   1.269 bytes/char   54 lines too long
        with a case range  56 slots   1.106 bytes/char    1 line  too long

    **Which** case to spend is a question about what is left in English, and
    the answer changed once the text was nearly finished:

    * Capitals cost more than they look.  Staff codenames are all upper case
      and are read straight out of the save, never through this plan, so
      reclaiming A-Z turns every soldier on Mother Base into gibberish -- and
      the same goes for anything else drawn from data this toolkit does not
      render.
    * Lower case costs the untranslated lines, and only those.  When four
      fifths of the text was English that was ruinous; at 158 lines of 23 867
      it is the cheaper half of the trade.

    So both are offered and neither is assumed.  Taking both would fit
    everything and leave no English readable anywhere.

    Digits are left alone either way.  They buy nothing on top of a case
    range -- still one line over -- and a wrong digit in an untranslated line
    is a date or a count read wrongly, which is worse than a wrong letter.
    """
    slots = set(SAFE_SINGLE) | {
        c for c in range(0x21, 0x7F)
        if not chr(c).isalnum() and c not in KEEP and c not in MARKUP}
    if capitals:
        slots |= {c for c in range(0x41, 0x5B) if c not in MARKUP}
    if lowercase:
        slots |= {c for c in range(0x61, 0x7B) if c not in MARKUP}
    return sorted(slots)


def free_double(used) -> list[int]:
    return [c for c in range(DOUBLE_FIRST, DOUBLE_LAST + 1) if c not in used]


def plan(shaped_texts, used, singles=None, pool=None,
         relocate=frozenset(), must=(), include=frozenset(),
         only=None) -> dict[str, str]:
    """``{presentation form: replacement character}``.

    Everything above :data:`CEILING` is placed -- the presentation forms and
    the Arabic punctuation both, since the engine draws neither where Unicode
    keeps them -- and so is every code point in ``relocate``, which is how the
    text's own full stops get out of the way of the slot that wants them.

    Frequency-ordered: the commonest glyph gets the safest single-byte slot,
    and once those run out the rest go to two-byte code points.  ``pool``
    ``must`` names characters that need a home whether or not the text uses
    one yet.  A capital absent from every current translation would otherwise
    have its code point handed to an Arabic form, and the day somebody types
    it the line would silently draw Arabic instead.  They cost a font glyph
    each and sort last, which is where an unused character belongs.

    ``include`` names characters to place even below the ceiling -- a
    chosen character the stock face does not draw, which could be painted at
    its own code point but costs two bytes there.  ``only``, when given, is
    the chosen set: a character above the ceiling that is not in it is left
    out, because the face will not carry it.

    ``pool`` gives the single-byte slots explicitly -- the free ones plus whatever
    :func:`reclaim` has taken back -- and ``singles`` caps how many to spend.
    """
    frequency = collections.Counter()
    for text in shaped_texts:
        for ch in text:
            if ord(ch) in relocate or ch in include:
                frequency[ch] += 1
            elif ord(ch) > CEILING and (only is None or ch in only):
                frequency[ch] += 1

    for character in must or ():
        frequency.setdefault(character, 0)
    ones = list(pool) if pool is not None else free_single(used)
    if singles is not None:
        ones = ones[:singles]
    twos = free_double(used)

    mapping: dict[str, str] = {}
    for index, (ch, _n) in enumerate(frequency.most_common()):
        if index < len(ones):
            mapping[ch] = chr(ones[index])
        elif index - len(ones) < len(twos):
            mapping[ch] = chr(twos[index - len(ones)])
        else:
            break                       # nothing left; it stays as it is
    return mapping


def _outside_markup(text: str, replace) -> str:
    """Apply ``replace`` to the drawn parts of ``text``, leaving tags alone."""
    from pwtr.arabic import PROTECT

    out, at = [], 0
    for match in PROTECT.finditer(text):
        out.append(replace(text[at:match.start()]))
        out.append(match.group(0))
        at = match.end()
    out.append(replace(text[at:]))
    return "".join(out)


def encode(text: str, mapping: dict[str, str]) -> str:
    """Shaped Arabic -> the compact code points the font now carries."""
    return _outside_markup(
        text, lambda run: "".join(mapping.get(ch, ch) for ch in run))


def decode(text: str, mapping: dict[str, str]) -> str:
    back = {v: k for k, v in mapping.items()}
    return _outside_markup(
        text, lambda run: "".join(back.get(ch, ch) for ch in run))


def cost(text: str, mapping: dict[str, str]) -> int:
    """Bytes the encoded form of ``text`` takes."""
    return len(encode(text, mapping).encode("utf-8"))
