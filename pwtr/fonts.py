"""Painting Arabic into the game's two font systems.

The vendored :mod:`pwfonts` already finds every sheet and installs an alphabet
into them.  Two things about Arabic make it the wrong call to use directly, and
both are the reason this module exists:

**The mapping has to come from outside.**  ``pwfonts.install_everywhere``
builds its own map with ``pwfontatlas.plan``, which only ever uses the 48-cell
free band.  Arabic needs more cells than that, and which Latin ranges are
spent to get them is the *project's* decision -- see :mod:`pwtr.arabic`.  So
the mapping is passed in, already made.

**Arabic glyphs have to touch the cell edges.**  ``render_set`` centres each
glyph inside a 20 px ink box in a 28 px cell, which is right for Latin and
wrong here: a joined form whose stroke stops short of the edge leaves a visible
gap in the middle of every word.  :func:`render_joined` renders the same way --
one shared scale for the whole alphabet, so narrow letters stay narrow -- but
across the full cell width and with the connecting stroke running to both
edges.

Everything else is the vendored code doing the work: finding the sheets,
decoding BC, re-encoding only the 4x4 blocks a glyph covers, and splicing
SLOT.DAT blocks back within their own footprint.
"""

from __future__ import annotations

import os
from pathlib import Path

from pwtr.formats import pwfontatlas as FA
from pwtr.formats import pwcrypt, pwfonts, slotdat, slotitem


def survey(game: str | Path, include_slot: bool = True, progress=None) -> dict:
    """Every caps sheet in the game, split by whether it can take a mapping.

    Only sheets whose whole ``0x90..0xBF`` band is free can share one mapping.
    The rest already keep their own glyphs there and their free cells do not
    line up, so they are reported and left alone rather than overwritten.
    """
    found = pwfonts.find_caps_sheets(str(game), include_slot, progress)
    patchable, blocked = found["patchable"], found["blocked"]
    return {
        "patchable": patchable,
        "blocked": blocked,
        "txp_sheets": sum(1 for s in patchable if s["kind"] == "txp"),
        "slot_sheets": sum(1 for s in patchable if s["kind"] == "slot"),
        "packages": sorted({os.path.basename(s["path"])
                            for s in patchable if s["kind"] == "txp"}),
    }


def measure_cells(game: str | Path) -> dict:
    """Decode the shipped UI sheet and count the ink in all 160 cells.

    :data:`pwtr.arabic.FREE_CELLS` is this measurement, frozen as a literal so
    opening a project does not cost a BC decode.  Run this to check it against
    a particular copy of the game -- or to find out why a glyph that should
    have fitted did not.
    """
    atlas, _plain, name = FA.open_txp(
        str(Path(game) / "Text" / FA.FONT_TXP), FA.FONT_TEXTURE)
    image = atlas.image()
    alpha = image[..., 3] if image.ndim == 3 else image

    empty, inked, off_sheet = [], [], []
    for byte in range(0x20, 0x100):
        where = FA.cell_of(byte)
        if where is None:
            continue
        x, y = where
        if y + FA.CELL_H > atlas.h:
            off_sheet.append(byte)
            continue
        cell = alpha[y:y + FA.CELL_H, x:x + FA.CELL_W]
        (empty if int((cell > 32).sum()) < 8 else inked).append(byte)
    return {"sheet": name, "size": (atlas.w, atlas.h),
            "empty": empty, "inked": inked, "off_sheet": off_sheet,
            "cells": len(empty) + len(inked)}


def render_joined(chars, ttf_path: str, threshold: int | None = 110) -> dict:
    """char -> alpha cell, rendered so joined forms actually join.

    The one departure from :func:`pwfontatlas.render_set` is the ink box: the
    full 28 px cell rather than the 20 px Latin box, and no centring inset.  A
    medial form's stroke has to reach both cell walls or the word breaks up.
    """
    return FA.render_set(chars, ttf_path,
                         width=FA.CELL_W, max_width=FA.CELL_W,
                         threshold=threshold)


def install_menu_face(game: str | Path, out_dir: str | Path, ttf_path: str,
                      mapping: dict, include_slot: bool = True,
                      progress=None) -> dict:
    """Paint ``mapping`` into every caps sheet that can take it.

    The caps face is duplicated byte-for-byte across several packages, and the
    game loads whichever one the current language selects -- so patching one
    and launching is the classic way to see no change at all.  All of them get
    the same glyphs, from the same mapping.
    """
    game, out_dir = str(game), str(out_dir)
    found = pwfonts.find_caps_sheets(game, include_slot, progress)
    targets = found["patchable"]
    glyphs = render_joined(mapping.keys(), ttf_path)

    # -- the .txp packages: open each file once, patch all of its sheets --
    by_file: dict[str, list] = {}
    for sheet in targets:
        if sheet["kind"] == "txp":
            by_file.setdefault(sheet["path"], []).append(sheet)

    written = []
    for path, group in sorted(by_file.items()):
        name = os.path.basename(path)
        data = bytearray(pwcrypt.decrypt(open(path, "rb").read(), name))
        for sheet in group:
            atlas = FA.Atlas(data, sheet["off"], sheet["w"], sheet["h"],
                             sheet["fmt"])
            for character, byte in mapping.items():
                atlas.put(byte, glyphs[character])
            data = atlas.data
        out = os.path.join(out_dir, os.path.relpath(path, game))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as handle:
            handle.write(pwcrypt.crypt(bytes(data), name))
        written.append({"file": name, "sheets": len(group), "out": out})

    # -- SLOT.DAT: rebuild each touched block, splice into a copy --
    slot_done, slot_over = 0, []
    slot_targets = [s for s in targets if s["kind"] == "slot"]
    if slot_targets:
        folder = os.path.join(game, "MLG", "disc0_rel")
        dat = os.path.join(folder, "002aba34.DAT")
        key = os.path.join(folder, "002aba34.KEY")
        _header, (high, low), records = slotdat.load_key(key)
        stream = slotdat.WordStream(high, low)
        by_page = {r.start: r for r in records}

        out_folder = os.path.join(out_dir, "MLG", "disc0_rel")
        os.makedirs(out_folder, exist_ok=True)
        out_dat = os.path.join(out_folder, "002aba34.DAT")
        with open(dat, "rb") as source, open(out_dat, "wb") as destination:
            while True:
                chunk = source.read(1 << 24)
                if not chunk:
                    break
                destination.write(chunk)

        pages: dict[int, list] = {}
        for sheet in slot_targets:
            pages.setdefault(sheet["page"], []).append(sheet)

        with open(dat, "rb") as source, open(out_dat, "r+b") as destination:
            for page, group in sorted(pages.items()):
                record = by_page[page]
                elements, head, _t = slotitem.parse(
                    slotdat.read_block(source, record, stream))
                for sheet in group:
                    element = elements[sheet["elem"]]
                    buffer = bytearray(element.data)
                    atlas = FA.Atlas(buffer, sheet["off"], sheet["w"],
                                     sheet["h"], sheet["fmt"])
                    for character, byte in mapping.items():
                        atlas.put(byte, glyphs[character])
                    element.data = bytes(atlas.data)
                payload = slotdat.build_block(slotitem.build(elements, head))
                if slotdat.pages_for(payload) > record.footprint:
                    slot_over.append(f"{page:05X}")
                    continue
                slotdat.write_block(destination, record, payload, stream)
                slot_done += 1

        with open(key, "rb") as source, \
                open(os.path.join(out_folder, "002aba34.KEY"), "wb") as dst:
            dst.write(source.read())

    return {
        "glyphs": len(mapping),
        "txp_files": written,
        "txp_sheets": sum(w["sheets"] for w in written),
        "slot_blocks": slot_done,
        "slot_overflow": slot_over,
        "slot_sheets": len(slot_targets),
        "blocked": found["blocked"],
        "blocked_count": len(found["blocked"]),
    }


def _is_arabic(character: str) -> bool:
    """A letter of the script, as opposed to punctuation travelling with it."""
    return any(0x0600 <= ord(c) <= 0x06FF or 0xFB50 <= ord(c) <= 0xFDFF
               or 0xFE70 <= ord(c) <= 0xFEFC for c in character)


def _is_letter(character: str) -> bool:
    """A letter or mark of any script -- what the size is measured against."""
    import unicodedata
    return any(unicodedata.category(c)[0] in "LM" for c in character)


def stock_codes(game: str | Path) -> set[int]:
    """Code points every stock subtitle face has a glyph for.

    Read from the ``.bak`` beside each face when there is one, because an
    installed face has glyphs of ours in it.  The engine only *reaches* the
    ones up to :data:`pwtr.compact.CEILING`; the rest are still worth knowing,
    since pointing a moved character at a glyph the face has is exact.
    """
    from pwtr.formats import pwxpr

    common = None
    for path in sorted((Path(game) / "FONT").glob("*.xpr")):
        stock = path.with_name(path.name + ".bak")
        editor = pwxpr.load(str(stock if stock.exists() else path), path.name)
        codes = {code for code, index in enumerate(editor.f.charmap) if index}
        common = codes if common is None else common & codes
    return common or set()


def _joins_forward(character: str) -> bool:
    """Does this form carry a connecting stroke to the glyph drawn after it?

    Shaping hands back visual order, so the glyph drawn next sits to the
    right -- which in Arabic is the letter *before* it in the word.  A final
    or a medial form is the one with a tongue reaching that way; an initial
    or an isolated form stops there.  Overlapping the advance is only right
    where a join actually happens: doing it to every letter pulls the next
    one into shapes that never touch, which is what made a bare alef-lam
    look crowded.
    """
    import unicodedata
    try:
        name = unicodedata.name(character)
    except (ValueError, TypeError):
        return False
    return name.endswith("FINAL FORM") or name.endswith("MEDIAL FORM")


def _ink(font, character, cell_h, pen):
    """Draw one glyph with the pen on ``pen`` and report its ink box."""
    from PIL import Image, ImageDraw

    canvas = Image.new("L", (cell_h * 4, cell_h * 4), 0)
    ImageDraw.Draw(canvas).text((cell_h, pen), character,
                                font=font, fill=255, anchor="ls")
    return canvas.getbbox(), canvas


def metrics(ttf_path: str, characters, cell_h: int, fill: float = 0.92,
            nudge=None) -> dict | None:
    """Where the letters sit in a cell, and what a bigger ``fill`` would cost.

    The geometry on its own, so the dialog can show exactly what the installer
    will paint without painting it.  Two passes: measure at the cell height to
    learn the shape of the script and settle a point size, then measure again
    at that size, because a hinted outline is not simply a scaled one and the
    baseline has to come from the size actually used.

    Only the script gets a say -- an em dash or a Hangul filler comes back
    full-width in a Japanese face and would drag every letter smaller.
    """
    from PIL import ImageFont

    nudge = nudge or {}
    pen = cell_h * 2
    probe = ImageFont.truetype(ttf_path, cell_h)
    rough = {}
    for character in dict.fromkeys(characters):
        box, _canvas = _ink(probe, character, cell_h, pen)
        if box:
            rough[character] = box
    if not rough:
        return None

    measured = ({c: b for c, b in rough.items() if _is_arabic(c)}
                or {c: b for c, b in rough.items() if _is_letter(c)} or rough)
    above = max(pen - box[1] for box in measured.values())
    below = max(box[3] - pen for box in measured.values())
    scale = (cell_h * fill) / max(1, above + below)
    size = max(1, int(round(cell_h * scale)))

    face = ImageFont.truetype(ttf_path, size)
    final = {}
    for character in rough:
        box, _canvas = _ink(face, character, cell_h, pen)
        if box:
            final[character] = box
    settled = ({c: b for c, b in final.items() if _is_arabic(c)}
               or {c: b for c, b in final.items() if _is_letter(c)} or final)
    sit = int(cell_h * (1 - fill)) + max(pen - box[1]
                                         for box in settled.values())

    clipped = {}
    for character, box in final.items():
        window = pen - sit - nudge.get(character, 0)
        over = (max(0, window - box[1])
                + max(0, box[3] - (window + cell_h)))
        if over:
            clipped[character] = over

    return {"size": size, "sit": sit, "scale": scale, "pen": pen,
            "boxes": final, "clipped": clipped, "wanted": above + below}


def render_line(ttf_path: str, text: str, cell_h: int, fill: float = 0.92,
                nudge=None, characters=None, scale=2):
    """The line as the installed face would draw it.

    Same point size, same baseline row, same crop -- columns only -- and the
    same advance, so this is not an impression of the result but the result.
    ``characters`` is the whole set the face will carry, which is what decides
    the size; pass it, or the preview of a short line will be measured only
    against itself.
    """
    from PIL import Image
    import numpy as np

    shape = metrics(ttf_path, characters or text, cell_h, fill, nudge)
    if shape is None:
        return None
    from PIL import ImageFont
    face = ImageFont.truetype(ttf_path, shape["size"])
    nudge = nudge or {}

    tiles = []
    for character in text:
        if character == " ":
            tiles.append(np.zeros((cell_h, max(1, cell_h // 3)), np.uint8))
            continue
        box, canvas = _ink(face, character, cell_h, shape["pen"])
        if box is None:
            continue
        window = shape["pen"] - shape["sit"] - nudge.get(character, 0)
        tiles.append(np.array(canvas.crop((box[0], window,
                                           box[2], window + cell_h)),
                              dtype=np.uint8))
    width = sum(t.shape[1] for t in tiles)
    sheet = np.zeros((cell_h, max(1, width)), np.uint8)
    x = 0
    for tile in tiles:
        sheet[:, x:x + tile.shape[1]] = tile
        x += tile.shape[1]
    image = Image.fromarray(255 - sheet)
    return image.resize((image.width * scale, image.height * scale),
                        Image.LANCZOS) if scale != 1 else image


def install_subtitle_face(game: str | Path, out_dir: str | Path,
                          ttf_path: str, characters, threshold=None,
                          progress=None, remap=None, nudge=None,
                          fill=0.92, overlay=None, alias=None,
                          join=0, tighten=0) -> dict:
    """Paint the shaped forms into the XPR faces.

    ``overlay`` names the code points drawn *over* the letter that follows
    rather than beside it -- the harakat -- and how far right each should sit.
    They are painted with an advance of zero, so the pen does not move and the
    next glyph lands on the same x.

    The offset is baked into the tile rather than set as a bearing.  Both were
    tried in game: a zero advance does stop the pen, but the bearing is added
    to the pen as well as to the glyph, so a mark pushed right pushed the
    following letter with it and opened exactly the gap the zero advance was
    there to avoid.  A wider tile with the ink further into it moves the mark
    and leaves the pen alone.

    ``fill`` is how much of the cell's height the script is allowed to take,
    ascender to deepest descender.  It is the only size control there is: the
    cell is fixed, and Arabic wants 71 pixels of it against the 67 there are,
    so the letters are always scaled down to fit and ``fill`` says by how much
    further.  1.0 is as large as they go without the deepest descender being
    clipped, and leaves nothing spare for a downward ``nudge``.

    ``nudge`` moves one form up or down in its cell, in pixels, for the cases
    where the font's own metric does not put it where the eye wants it.

    ``join`` trims the antialiased fringe off the sides of a letter, keeping
    only columns that hold a pixel at least this opaque.  The advance is the
    tile width, so tiles abut exactly -- but the ink box keeps the fade at the
    end of a connecting stroke, and a stroke petering out to alpha 40 meeting
    one rising from alpha 15 is a pale column where solid ink belongs.  That
    is the hairline gap that shows up between medial and final forms, worst on
    the letters whose tongue runs right to the edge of the cell.  Trimming
    makes each tile end on ink, so the strokes meet solid.  Zero leaves the
    fringe alone.  Marks are never trimmed: they are placed by padding their
    own tile, and taking columns off would move them.

    ``tighten`` takes pixels off the advance without touching the tile, so
    the next letter is drawn that much further back and the two overlap.
    Trimming fixes the fade the rasteriser leaves in the atlas; this is for
    the fade the *engine* leaves, which trimming cannot reach.  Tiles are
    stored a pixel apart, and a quad sampled with linear filtering takes half
    a texel of that empty column into each edge -- so a join drawn from two
    solid edges still meets through two half-faded ones.  One pixel of
    overlap covers it.  Only the forms that join that way are pulled in --
    the finals and the medials -- because an isolated or initial form ends
    where it ends, and dragging the next letter into one crowds a pair that
    never touched.  Marks keep their zero advance regardless.

    ``alias`` maps *code point to draw at* -> *character whose glyph to use*,
    for the characters the compact plan **moves** rather than replaces: the
    capitals.  A moved capital must look exactly as it did, and repainting it
    from the TTF does not achieve that -- it comes back in a different face at
    the Arabic scale and baseline, so ``.LIFE`` draws its capitals from one
    font and everything around them from another, which reads as broken
    spacing and alignment.  The charmap is code point -> glyph index, so the
    new code point is simply pointed at the glyph the stock face already uses.
    That is pixel-identical by construction, and it spends no donor record and
    no atlas space -- an aliased capital is strictly cheaper than a painted
    one.

    ``remap`` breaks the assumption that a glyph lives at its own code point.
    It maps *code point to draw at* -> *character to draw*, which is what makes
    the compact encoding possible: an Arabic form rendered at, say, U+005C
    costs one byte in the file instead of three.  Without it each character is
    drawn at itself, which is the ordinary case.

    No smuggling here and no cell budget: the character map is indexed by real
    Unicode with 65 375 slots, so U+FE70..U+FEFC goes in as itself.  What it
    does cost is **glyph records** -- every one of the 643 is already spoken
    for, and growing the table would move the texture and invalidate every
    offset in the header.  So a record is borrowed instead, pointed at fresh
    atlas space, and the character map aimed at it.  The donors are taken from
    the CJK range first: an Arabic build has no use for 323 kanji.

    Two things are done differently from the vendored installer, and both are
    the difference between Arabic and Latin:

    * **No padding, and the advance is the glyph's exact width.**  The Latin
      path pads a pixel each side and advances one further, which is sensible
      spacing for letters that stand apart -- and it puts a three pixel gap
      through the middle of every Arabic word, because a joined form's stroke
      is supposed to meet its neighbour at the cell edge.
    * **One shared scale and one shared baseline** measured over the whole
      set, so the letters sit on a line rather than each finding its own.
    """
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    from pwtr.formats import pwxpr

    game, out_dir = Path(game), Path(out_dir)
    letters = list(dict.fromkeys(characters))
    remap = remap or {}
    nudge = nudge or {}
    overlay = overlay or {}
    alias = alias or {}
    wanted_all = letters
    faces = []

    for path in sorted((game / "FONT").glob("*.xpr")):
        # Paint onto the stock face, never onto one already painted: a second
        # pass over an installed face finds its free band gone and gives up.
        # The name still has to be the real one -- the cipher is keyed on it.
        stock = path.with_name(path.name + ".bak")
        editor = pwxpr.load(str(stock if stock.exists() else path), path.name)
        top, band = editor.free_band()
        if band < editor.cell_h:
            faces.append({"file": path.name, "painted": 0,
                          "error": "no free band in the atlas"})
            continue

        # Never repaint a character the face already draws.  Anything the
        # stock charmap has a glyph for is *moved*, not replaced -- the
        # capitals the plan relocates, and the CJK and fullwidth punctuation
        # that survive in a few lines -- and the charmap is code point ->
        # glyph index, so pointing the new code point at the old glyph is
        # exact.  Repainting them from the Arabic TTF is what put two faces
        # inside ".LIFE", and it spends a donor record per character on
        # glyphs the game already had: 152 of them went on kanji, which cost
        # this face three of its harakat.
        kept = {}
        for slot in wanted_all:
            character = alias.get(slot, remap.get(slot, slot))
            if slot in overlay:
                continue            # a mark is ours to draw, never the face's
            code = ord(character)
            index = (editor.f.charmap[code]
                     if code <= editor.f.last_code else 0)
            if index:
                kept[slot] = index

        letters = [slot for slot in wanted_all if slot not in kept]

        # Paint what the face can hold rather than refusing the lot.  An
        # unpainted slot is not blank: the code point keeps whatever glyph the
        # stock face had there, so a Latin letter draws where Arabic was
        # meant.  Zero glyphs painted is therefore the worst outcome
        # available, not the safe one -- and since ``letters`` is ordered by
        # the plan, which is ordered by frequency, what gets left out is what
        # the text needs least.
        # Borrowing the record a moved character now points at would
        # repurpose the very glyph it draws, so those indices leave the pool.
        for slot, index in kept.items():
            editor.set_charmap(ord(slot), index)
        held = set(kept.values())
        donors = [g for g in editor.donors(len(letters) + len(held))
                  if g not in held][:len(letters)]
        short = max(0, len(letters) - len(donors))
        if short:
            letters = letters[:len(donors)]

        # -- one measurement, shared with the dialog's preview --
        wanted = [remap.get(slot, slot) for slot in letters]
        shape = metrics(ttf_path, wanted, editor.cell_h, fill, nudge)
        if shape is None:
            faces.append({"file": path.name, "painted": 0,
                          "error": "the font rendered nothing"})
            continue
        size, sit, pen = shape["size"], shape["sit"], shape["pen"]
        face = ImageFont.truetype(ttf_path, size)
        drawn = {}
        for character in dict.fromkeys(wanted):
            box, canvas = _ink(face, character, editor.cell_h, pen)
            if box:
                drawn[character] = (box, canvas)
        clipped = shape["clipped"]

        x, y, painted, mapping = 0, top, 0, {}
        for slot in letters:
            character = remap.get(slot, slot)
            if character not in drawn:
                continue
            box, canvas = drawn[character]
            # The window is the full cell height, placed so the pen's row
            # lands on ``sit``; a nudge slides the window, not the glyph.
            window = pen - sit - nudge.get(character, 0)
            width = box[2] - box[0]
            tile = np.array(canvas.crop((box[0], window,
                                         box[2], window + editor.cell_h)),
                            dtype=np.uint8)
            # A mark is centred by painting it further into its own tile, not
            # by the bearing field: the engine adds the bearing to the pen as
            # well as to the glyph, so pushing a mark right pushes the letter
            # after it right too, and the mark that should have cost nothing
            # opens a gap instead.  Widening the tile leaves the pen alone.
            if join and slot not in overlay and _is_arabic(character):
                columns = tile.max(axis=0)
                lo, hi = 0, tile.shape[1]
                while lo < hi - 1 and columns[lo] < join:
                    lo += 1
                while hi - 1 > lo and columns[hi - 1] < join:
                    hi -= 1
                tile = tile[:, lo:hi]
                width = tile.shape[1]

            shift = overlay.get(slot, 0)
            if shift:
                padded = np.zeros((editor.cell_h, width + shift), np.uint8)
                padded[:, shift:] = tile
                tile, width = padded, width + shift
            if threshold is not None:
                tile = np.where(tile >= threshold, tile, 0).astype(np.uint8)

            if x + width >= editor.w:
                x, y = 0, y + editor.cell_h
            if y + editor.cell_h > editor.h:
                break
            editor.write_atlas(y, x, tile)

            record = donors[painted]
            # advance == width: no gap, so the joining strokes meet.  A mark
            # is the exception -- no advance at all, and a push to the right
            # to sit it over the letter drawn next.
            step = width
            if tighten and slot not in overlay                     and _joins_forward(character):
                step = max(1, width - tighten)
            editor.set_glyph(record, x, y, x + width, y + editor.cell_h,
                             0, width,
                             0 if slot in overlay else step)
            editor.set_charmap(ord(slot), record)
            mapping[slot] = record
            x += width + 1          # one pixel between tiles IN THE ATLAS
            painted += 1            # only; it is not part of the rectangle
            if progress:
                progress(painted, len(letters))

        destination = out_dir / "FONT" / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        editor.save(str(destination))
        faces.append({"file": path.name, "painted": painted,
                      "aliased": len(kept), "band_row": top,
                      "band_height": band, "out": str(destination),
                      "size": size, "clipped": clipped,
                      "short": short + max(0, len(letters) - painted)})

    return {"faces": faces, "letters": len(letters)}
