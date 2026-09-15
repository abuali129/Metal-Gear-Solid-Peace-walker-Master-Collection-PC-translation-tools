r"""The words burned into the three pre-rendered scenes, as translatable text.

The Kant epigraph and the two halves of the chronology are video: their words
are pixels, not strings, so nothing in the game's containers holds them and no
extractor can reach them.  They are translated by subtitling over a blanked
frame -- see :mod:`pwtr.hardsub` -- which means somebody has to type the
English out once.  This module is that once.

Keeping it here rather than in a subtitle file is what makes the work
reusable: a team translating into another language gets the source text in the
workbench beside every other line, types a translation, and the build writes
the ``.ass`` for them.  Nobody needs to know what an override tag is.

## The shape of a scene

An ``.ass`` event's text is one long string.  Read as one, it is untranslatable
-- a hundred lines of chronology in a single cell.  It divides cleanly though::

    <date>\N\h\h\h\h\h\h<first body line>\N<more body lines>\N\N<next block>

Blocks are separated by a blank line, the date sits on its own, and the body
hangs under it by six hard spaces.  So a block is the unit: one dated entry,
one cell to translate.

## What is preserved

Composing does not invent the styling.  It takes an existing ``.ass`` as the
layout -- its ``[Script Info]``, its styles, and each event's override tags
verbatim -- and replaces only the words.  So the scroll speed, the fade, the
font, the alignment and the resolution are whatever the file already said, and
a team can tune those in a subtitle editor without this module having an
opinion -- including the scroll, which is deliberately never recalculated.

A roll's ``\move`` looks like it could be derived from the line count.  It
cannot: a line wider than the margins wraps, and how often depends on the
font, the size and the language.  Measured against the shipped Arabic, a
count-based estimate came out at half the real travel.  So the template's
geometry is kept and :func:`rows_in` only reports the count, for a warning.
"""
from __future__ import annotations

import re
from pathlib import Path

#: Separates one dated block from the next inside an event.  Captured, because
#: how *many* line breaks there are is layout, not punctuation: the Kant card
#: sits where it does on the strength of a run of seven and a trailing nine,
#: and normalising those to two moves the words up the screen.
BLOCK = re.compile(r"((?:\\N){2,})")

#: The override block an event opens with, if any.
TAGS = re.compile(r"^(\{[^}]*\})")

#: Override blocks wrapping a piece of text.  A block can carry them at either
#: end -- the splash cards close with a font size -- and they are styling, not
#: words: the 1080p cards end ``\fs95.294`` where the 720p ones end
#: ``\fs63.529``, so letting one through as text copies a size across
#: resolutions.
WRAP = re.compile(r"^((?:\{[^}]*\})*)(.*?)((?:\{[^}]*\})*)$", re.S)

#: The hanging indent a body line sits under, if its event uses one.
INDENT = re.compile(r"\\N((?:\\h)+)")


def _lines(block: str) -> list[str]:
    """A stored block as its display lines, indent markers removed.

    Only the ``\\h`` markers go.  Ordinary spaces are left exactly as written,
    including a trailing one -- tidying those away is a change to the file,
    and a round trip that is not byte-for-byte cannot be trusted to leave
    anything else alone either.
    """
    out = []
    for line in block.split(r"\N"):
        while line.startswith(r"\h"):
            line = line[2:]
        out.append(line)
    return [line for line in out if line.strip()]


def parse(path: str | Path) -> list[list[str]]:
    """``[[block, ...], ...]`` -- one list per event, in file order.

    Each block comes back as plain text with real newlines, which is what a
    translator should see and edit.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    events = []
    for line in text.splitlines():
        if not line.startswith("Dialogue"):
            continue
        body = line.split(",", 9)[9]
        found = TAGS.match(body)
        rest = body[len(found.group(1)):] if found else body
        blocks = [piece for piece in BLOCK.split(rest) if _is_text(piece)]
        events.append(["\n".join(_lines(_wrap(b)[1])) for b in blocks])
    return events


def _wrap(piece: str) -> tuple[str, str, str]:
    """``(tags before, the words, tags after)``."""
    found = WRAP.match(piece)
    return found.group(1), found.group(2), found.group(3)


def _is_text(piece: str) -> bool:
    """Words, as opposed to a separator or a run of blank lines."""
    body = _wrap(piece)[1]
    return bool(body.replace(r"\N", "").replace(r"\h", "").strip())


def _encode(block: str, model: str = "") -> str:
    """Plain text back into one event's worth of ``.ass``.

    ``model`` is the piece this replaces.  Whether the body hangs under its
    first line, and by how much, is read from there rather than assumed: the
    chronology indents by six hard spaces and the centred cards by none, and
    handing a card the roll's indent shifts it off centre.
    """
    lines = [line for line in block.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    found = INDENT.search(model)
    return (lines[0] + r"\N" + (found.group(1) if found else "")
            + r"\N".join(lines[1:]))


#: ``\fn<family>`` and ``\fs<size>`` inside an override block.  ``\fscx`` and
#: ``\fsp`` start the same way but are scale and spacing, and the digit after
#: ``\fs`` is what tells them apart.
FONT_TAG = re.compile(r"\\fn[^\\}]*")
SIZE_TAG = re.compile(r"\\fs([\d.]+)")
OVERRIDE = re.compile(r"\{[^}]*\}")


def _number(value: float) -> str:
    text = ("%.3f" % value).rstrip("0").rstrip(".")
    return text or "0"


def restyle(text: str, font: str | None = None, scale: float = 1.0) -> str:
    """Put a chosen font and size on a composed subtitle.

    The size is a scale, not a number of points, because the scenes do not
    share one: the roll is set at 36, the splash cards at 50 and 63.5, the
    Kant card at 48, and the 1080p files at half as much again.  Scaling keeps
    all of that in proportion; one absolute size would flatten it.

    Both the styles and the override tags are changed, since a tag overrides
    its style and the shipped files set the font in both places.  With neither
    given the text comes back untouched.
    """
    if not font and abs(scale - 1.0) < 1e-9:
        return text

    def tags(match):
        block = match.group(0)
        if font:
            block = FONT_TAG.sub(lambda _m: r"\fn" + font, block)
        if abs(scale - 1.0) >= 1e-9:
            block = SIZE_TAG.sub(
                lambda m: r"\fs" + _number(float(m.group(1)) * scale), block)
        return block

    out = []
    for line in text.splitlines(True):
        stripped = line.rstrip("\r\n")
        ending = line[len(stripped):]
        if stripped.startswith("Style:"):
            head, rest = stripped.split(":", 1)
            fields = rest.split(",")
            if len(fields) > 2:
                if font:
                    fields[1] = font
                if abs(scale - 1.0) >= 1e-9:
                    fields[2] = _number(float(fields[2]) * scale)
            out.append(head + ":" + ",".join(fields) + ending)
        elif stripped.startswith("Dialogue"):
            out.append(OVERRIDE.sub(tags, stripped) + ending)
        else:
            out.append(line)
    return "".join(out)


def rows_in(events: list[list[str]]) -> int:
    """How many written lines a scene has, blank separators included.

    Not how many it *draws*: a line wider than the margins wraps, and how
    often depends on the font, the size and the language.  Measured on the
    shipped Arabic, the roll travels about twice the height its written lines
    account for -- which is why nothing here recomputes the scroll.  It is a
    number to compare against the English, not to set geometry from.
    """
    rows = 0
    for blocks in events:
        rows += sum(len([l for l in b.splitlines() if l.strip()])
                    for b in blocks)
        rows += max(0, len(blocks) - 1)
    return rows


def compose(template: str | Path, events: list[list[str]]) -> str:
    """A new ``.ass``: the template's styling, these words.

    ``events`` must line up with the template's own events; a scene whose
    blocks have been added to or removed from is not something this can place,
    and saying so beats writing a file that plays wrong.
    """
    text = Path(template).read_text(encoding="utf-8-sig")
    out, at = [], 0
    for line in text.splitlines(True):
        if not line.startswith("Dialogue"):
            out.append(line)
            continue
        if at >= len(events):
            raise ValueError("the template has more events than there is text")
        # Keep the line's own ending, including its absence on a last line
        # that has none -- a file that gains a newline is a file that differs.
        stripped = line.rstrip("\r\n")
        ending = line[len(stripped):]
        head, body = stripped.split(",", 9)[:9], stripped.split(",", 9)[9]
        found = TAGS.match(body)
        tags = found.group(1) if found else ""
        rest = body[len(tags):]

        # Walk the template's own pieces and swap only the ones that are
        # words.  Everything between them -- the separators, the runs of
        # blank lines that hold a card at the height it sits at -- is copied
        # across untouched, because it is layout and this does not lay out.
        blocks = list(events[at])
        pieces = []
        for piece in BLOCK.split(rest):
            if _is_text(piece):
                if not blocks:
                    raise ValueError("the template has more blocks in an "
                                     "event than there is text for")
                lead, model, trail = _wrap(piece)
                pieces.append(lead + _encode(blocks.pop(0), model)
                              + trail)
            else:
                pieces.append(piece)
        if blocks:
            raise ValueError("%d block(s) of text have nowhere to go in the "
                             "template" % len(blocks))
        out.append(",".join(head) + "," + tags + "".join(pieces) + ending)
        at += 1
    if at != len(events):
        raise ValueError("there is text for %d event(s) and the template has "
                         "%d" % (len(events), at))
    return "".join(out)
