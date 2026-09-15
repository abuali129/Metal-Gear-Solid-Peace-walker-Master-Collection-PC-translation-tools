r"""Arabic, and the two very different places Peace Walker can draw it.

Translations are typed and stored as ordinary logical Arabic -- the way it
comes off a keyboard.  Everything below happens on build, so a line can always
be re-opened and edited as text.

Two conversions, in this order:

1. **Shaping.**  Logical Arabic becomes presentation forms in visual order.
   The engine has no shaper: it draws code points left to right, one glyph per
   byte, so the file has to carry the joined forms already in the order they
   appear on screen.  Markup is stashed first -- ``<I=ATK>`` and ``<C=FF4040>``
   are read by the engine, and reversing them turns them into text.

2. **Smuggling**, for the UI face only.  See below.

## The subtitle faces are easy

``FONT/*.xpr`` is indexed by real Unicode with tens of thousands of empty
slots, so the Arabic presentation block U+FE70..U+FEFF -- 141 code points, the
whole shaped alphabet plus the lam-alef ligatures -- can be painted at its own
code points and written out as-is.  Step 2 does nothing here.

## The UI face is not

The caps atlas has no character map at all.  A byte picks a cell, and the
engine masks a code point down to one byte to get there, so a glyph can only
be reached by painting it into a cell and storing the byte of that cell.

Measured on the shipped sheet, **63 of its 160 cells are empty** -- the
documented ``0x90..0xBF`` band, plus almost all of row 6, plus 0x60 and 0x7F.
Arabic needs 141 presentation forms.  So **the shaped alphabet does not fit**,
and no amount of packing makes it: even giving up every Latin letter reaches
only 115.

That matters far less than it sounds, because **only 80 of the 2 789 English
menu references are drawn by this face at all** -- the ones ``.olang`` marks
Style ``0x0001``, which is BUTTON CONFIG and PRESS START and their like.  The
other 2 709 are Style ``0x0402`` and go to the XPR face above, where there is
no limit.  So the budget below is spent on 80 short strings, and it is worth
measuring exactly what those need rather than assuming the whole alphabet.

What does fit is the free band *plus* cells given up by Latin letters the
translated UI no longer needs.  That is a real trade and not one to make
silently: give up ``A-Z`` and the accented lowercase range and the sheet has
room, at the price of every string still in English rendering as mush.  So the
capacity is computed and reported, and the caller chooses how much Latin to
spend -- :func:`capacity` and :func:`plan` are the whole of that decision.
"""

from __future__ import annotations

import re

#: Everything the engine reads rather than draws, and so must reach it byte
#: for byte: button icons, colour changes, and the three ways it splices a
#: value in.  Measured over every English string the game ships -- 104 tag
#: shapes, 431 lines with a ``$n``, 51 with printf, and four ``[snake_case]``
#: tokens.
#:
#: Protection is two things at once, and both are needed.  Shaping leaves the
#: region alone, so bidi cannot reorder ``$1`` into ``1$``; and the compact
#: plan leaves it alone, so a ``$`` whose code point now carries an Arabic
#: form is not relocated to a two-byte one the engine will never match.  That
#: the face draws something else at those code points does not matter -- the
#: engine consumes the token before anything is drawn.
#:
#: Bracketed text is only a token when it is lower snake case.  ``[097] Tank
#: Battle``, ``[METAL GEAR]`` and ``[Waiting Room]`` are labels a player
#: reads, and they are translated like any other words.  ``<Add Stage>`` and
#: ``<<Hunting Quests>`` are ordinary text and stay text.
PROTECT = re.compile(
    r"<[IC]=[^>]*>"             # <I=TM>, <C=FF4040> -- button icons, colour
    r"|\$\d+"                   # $1..$4, the team or item the game splices in
    r"|%[sdif]"                 # %s and %d, printf the engine fills
    r"|\[[a-z][a-z0-9_]*\]"     # [total_play_time], [monitor_number]
)

_PLACEHOLDER = "ZqZ{}ZqZ"
_PLACEHOLDER_RE = re.compile(r"ZqZ(\d+)ZqZ")

#: Line breaks split a string into independently shaped runs, the way the
#: game lays it out.  Kept so a paragraph is not reversed as one blob.
SEPARATORS = re.compile(r"(\r\n|\n|\r|\t)")

#: The Arabic presentation blocks, which are what a shaper actually emits.
FORMS_A = range(0xFB50, 0xFDFF + 1)     # ligatures, mostly unused here
FORMS_B = range(0xFE70, 0xFEFF + 1)     # the joined forms and lam-alef

#: Every cell that is actually empty on the shipped UI sheet, measured by
#: decoding ``008299c5.txp`` texture 10 and counting the ink in all 160 cells.
#:
#: The documented free band is rows 7-9, ``0x90..0xBF`` -- 48 cells.  Row 6 is
#: nearly empty too (only 0x80, 0x81 and 0x88 are inked), and 0x60 and 0x7F are
#: free in otherwise full rows, which brings the real total to **63**.  0x20 is
#: empty as well and is not offered: it is the space.
#:
#: Held as a literal, not measured on import, so the text side does not pull in
#: numpy and Pillow just to open a project.  :func:`pwtr.fonts.measure_cells`
#: re-derives it from the game if the sheet ever turns out to differ.
FREE_CELLS = (
    (0x60, 0x60), (0x7F, 0x7F), (0x82, 0x87), (0x89, 0x8F), (0x90, 0xBF),
)

#: Bytes past the last row that fits: cell 0xC0 starts at y=512 on a 512 px
#: sheet, so the engine can address them but there is nothing there to draw.
OFF_SHEET_FROM = 0xC0

#: Cell ranges holding Latin the UI stops needing once it is translated, in
#: the order they are worth spending.  Digits, punctuation and space are never
#: offered: menus count, price and time in them whatever the language.
BORROWABLE = (
    ("lowercase", 0x61, 0x7A,
     "a-z: this face has no real lowercase, so these cells hold the accented "
     "capitals that French, Spanish, German and Italian need"),
    ("uppercase", 0x41, 0x5A,
     "A-Z: every string still in English becomes unreadable"),
)


_RESHAPER = None


def _reshaper():
    """Built once; constructing one per line is slow over ten thousand of them.

    ``delete_harakat`` defaults to True in arabic_reshaper, which silently
    throws away every fatha, damma, kasra and shadda in the translation.
    """
    global _RESHAPER
    if _RESHAPER is None:
        import arabic_reshaper
        _RESHAPER = arabic_reshaper.ArabicReshaper(
            configuration={"delete_harakat": False})
    return _RESHAPER


def shape(text: str) -> str:
    """Logical Arabic -> presentation forms in visual order, run by run."""
    from bidi.algorithm import get_display

    saved: list[str] = []

    def stash(match):
        saved.append(match.group(0))
        return _PLACEHOLDER.format(len(saved) - 1)

    def shape_run(run: str) -> str:
        run = get_display(_reshaper().reshape(PROTECT.sub(stash, run)))
        return _PLACEHOLDER_RE.sub(lambda m: saved[int(m.group(1))], run)

    return "".join(
        part if SEPARATORS.fullmatch(part) or not part else shape_run(part)
        for part in SEPARATORS.split(text)
    )


def tokens(text: str) -> list[str]:
    """The engine markup in a string, sorted -- for comparing two versions."""
    return sorted(PROTECT.findall(text))


# -- the UI face's cell budget ---------------------------------------------

def free_cells(borrow: tuple[str, ...] = ()) -> list[int]:
    """Every byte a glyph may be painted into, lowest first.

    ``borrow`` names ranges from :data:`BORROWABLE` to give up on top of the
    empty band.  The band comes first, so a small alphabet never spends Latin
    it did not need to.
    """
    cells = [b for first, last in FREE_CELLS for b in range(first, last + 1)]
    for name, first, last, _why in BORROWABLE:
        if name in borrow:
            cells += list(range(first, last + 1))
    return sorted(cells)


def needed(text_or_forms) -> list[str]:
    """The distinct presentation-form characters a body of text needs.

    Pass shaped text, or an iterable of shaped strings.  Only characters in
    the Arabic blocks count -- ASCII in a translation is drawn by the cells
    the face already has.
    """
    if isinstance(text_or_forms, str):
        text_or_forms = [text_or_forms]
    seen: set[str] = set()
    for item in text_or_forms:
        for ch in item:
            if ord(ch) in FORMS_A or ord(ch) in FORMS_B:
                seen.add(ch)
    return sorted(seen)


def capacity(alphabet, borrow: tuple[str, ...] = ()) -> dict:
    """Does this alphabet fit the UI face, and what would make it fit?

    Returns what the font dialog shows: how many cells there are, how many are
    wanted, and -- when it does not fit -- which ranges would have to be given
    up to close the gap.
    """
    alphabet = list(dict.fromkeys(alphabet))
    cells = free_cells(borrow)
    short = len(alphabet) - len(cells)
    suggestion = []
    if short > 0:
        for name, first, last, why in BORROWABLE:
            if short <= 0:
                break
            if name in borrow:
                continue
            suggestion.append((name, why))
            short -= last - first + 1
    return {
        "wanted": len(alphabet),
        "cells": len(cells),
        "borrowed": list(borrow),
        "fits": len(alphabet) <= len(cells),
        "short": max(0, len(alphabet) - len(cells)),
        "would_fit_if": suggestion,
        "possible": short <= 0,
    }


def plan(alphabet, borrow: tuple[str, ...] = ()) -> dict:
    """character -> byte, filling the empty band before spending any Latin."""
    alphabet = list(dict.fromkeys(alphabet))
    cells = free_cells(borrow)
    if len(alphabet) > len(cells):
        raise ValueError(
            f"{len(alphabet)} glyphs do not fit {len(cells)} cells -- "
            f"give up a Latin range, or shorten the alphabet")
    return {ch: cells[i] for i, ch in enumerate(alphabet)}


def to_font_bytes(text: str, mapping: dict) -> str:
    """Shaped Arabic -> the code points whose low byte hits our cells.

    A character the mapping does not cover is left alone: ASCII passes through
    to the cells the face already has, and anything else would be drawn wrong
    either way -- leaving it visible is how it gets noticed.
    """
    return "".join(chr(mapping[c]) if c in mapping else c for c in text)


def from_font_bytes(text: str, mapping: dict) -> str:
    back = {chr(byte): ch for ch, byte in mapping.items()}
    return "".join(back.get(ch, ch) for ch in text)


def unmapped(text: str, mapping: dict) -> list[str]:
    """Presentation forms in ``text`` that no cell can draw."""
    return [ch for ch in needed(text) if ch not in mapping]


# -- what a shaped string still needs beyond the joined forms ---------------

#: The tashkeel: fatha, damma, kasra, sukun, shadda and the tanween, plus the
#: superscript alef.  A shaper leaves these as combining marks in the Arabic
#: block -- they have no joined form -- so they never reach Forms-B.
HARAKAT = frozenset(range(0x064B, 0x0653)) | {0x0670}

#: Bidi formatting codes.  A shaper leaves some of these in, and they are
#: invisible only to something that understands them -- the engine draws one
#: glyph per code point, so a stray RLM comes out as a box in the middle of a
#: sentence.  Stripped with the harakat, for the same reason.
CONTROLS = frozenset({0x200E, 0x200F, 0x061C}) | frozenset(range(0x202A, 0x2030))


def strip_harakat(text: str) -> str:
    """Drop the vowel marks.

    The engine has no notion of a combining mark: it advances the pen by each
    glyph's own width, so a mark mapped to a glyph takes horizontal space
    instead of sitting over the letter before it, and the word comes apart.
    Undiacritised Arabic is what nearly every game translation ships, so this
    is the default -- but it is a choice, and :func:`shape` leaves the marks
    in for anyone who has somewhere to put them.
    """
    return "".join(c for c in text
                   if ord(c) not in HARAKAT and ord(c) not in CONTROLS)


def outside_forms(text: str) -> list[str]:
    """Characters a shaped string carries that the Forms-B block cannot draw.

    ASCII is excluded: the faces already have it.  What this catches is the
    tashkeel, and anything else a translation smuggled in from the base
    Arabic block that shaping did not convert.
    """
    return sorted({c for c in text
                   if ord(c) > 0x7F and ord(c) not in FORMS_B
                   and ord(c) not in FORMS_A})

#: The marks that sit above or below a letter rather than beside it.
MARKS = frozenset(range(0x064B, 0x0653)) | {0x0670}

#: Bearings a mark can be painted at, in pixels.  A mark is drawn with a zero
#: advance so the pen does not move, and the shaped text already puts it
#: before its letter, so both land on the same x -- proven in game.  What is
#: left is centring, and the bearing field is read *unsigned*: it can push a
#: glyph right, never pull it left.  Pushing is all that is needed, since the
#: mark starts at the letter's left edge.
BEARINGS = (0, 3, 6, 9, 12, 15, 18, 21, 24)

#: Where the variant placeholders live before the plan gives them real code
#: points.  Private use, so nothing in the game's own text can collide.
VARIANT_BASE = 0xE000


def mark_variants() -> dict:
    """``{placeholder: (mark, bearing)}`` -- every mark at every bearing."""
    out = {}
    for n, mark in enumerate(sorted(MARKS)):
        for b, bearing in enumerate(BEARINGS):
            out[chr(VARIANT_BASE + n * len(BEARINGS) + b)] = (chr(mark),
                                                              bearing)
    return out


def place_marks(shaped: str, widths: dict) -> str:
    """Swap each mark for the variant that centres it over its letter.

    ``widths`` is the drawn width of each glyph, which is what decides the
    push: a mark over a four pixel alef wants none, over a fifty pixel seen it
    wants twenty.  The mark keeps its place in the string -- the shaping has
    already put it before the letter it belongs to, which is exactly where a
    zero advance needs it.
    """
    order = {chr(m): n for n, m in enumerate(sorted(MARKS))}
    out = []
    for index, ch in enumerate(shaped):
        if ord(ch) not in MARKS:
            out.append(ch)
            continue
        letter = shaped[index + 1] if index + 1 < len(shaped) else ""
        room = widths.get(letter, 26) - widths.get(ch, 14)
        want = max(0, room // 2)
        bearing = min(BEARINGS, key=lambda b: abs(b - want))
        out.append(chr(VARIANT_BASE + order[ch] * len(BEARINGS)
                       + BEARINGS.index(bearing)))
    return "".join(out)
