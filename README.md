# Peace Walker Arabic translation toolkit

An Arabic translation workbench for **Metal Gear Solid: Peace Walker (PC,
Master Collection)** — the same two windows as the MGS4 toolkit, pointed at a
different game.

* **`pwtr.py`** — the translation workbench. A table of every line, English
  beside Arabic, with a live preview of what the game will actually draw.
* **`pwtex_app.py`** — the texture workbench. The pictures with writing in
  them, yours beside the game's, and one button that writes.

Type ordinary Arabic. The shaping into presentation forms, the reversal into
the order the engine blits, and the mapping onto the font cells the engine can
reach all happen when you build — never in the project files, so every line
stays re-editable text.

```
pip install -r requirements.txt
python pwtr.py
```

Python 3.10+.

---

## The text

**File > New project** points at your copy of the game, copies the language
tables in, and reads the English out. Three tabs, because the game keeps its
text in three places that have nothing in common:

| tab | where it lives | lines |
|---|---|---|
| **Menus** | the 17 `.olang` tables in `MLG/Text` and `EXLANG/Text` | 1 631 English of 20 012 pool strings |
| **Story** | 4 458 `.olang` files inside the 2 137 compressed blocks of `SLOT.DAT` | 10 074 distinct English lines, subtitles included |
| **Briefing** | `BRIEFING.DAT` — the tapes | 4 987 English lines across 383 records |
| **Missions** | `.olang` tables inside `STAGEDAT.PDT` | 5071 distinct English lines |

The menus are read when the project is made. The other three are big
containers and wait to be asked, each from the **Build** menu: the story
(a 544 MB walk, about twenty seconds), the briefings (a couple of seconds)
and the mission text (a 487 MB walk, a few minutes).

A line appears **once**, however many scenes say it. Peace Walker deduplicates
its string pools, and the same English recurs across the script; one entry that
writes back to every reference is what keeps a translation consistent, and it
is why 52 396 stored strings come down to 10 074 things to actually translate.

Nothing is written into the game while you work. **Build** puts the rebuilt
files in the project's `output/`, and **Install** copies them in, saving each
original once as a `.bak` the first time it replaces it.

### What the workbench checks as you type

* **Dropped markup.** The engine reads `<I=ATK>` and `<C=FF4040>` — button
  prompts and colour changes, 53 distinct tags across the shipped text. A
  translation that loses one loses a button prompt in game and nothing says so.
  They are stashed before shaping so the reversal cannot scramble them, and the
  line warns if the set no longer matches the English.
* **Glyphs with nowhere to live.** See below.

---

## Bringing a PS3 or PSP translation across

**File > Import translations from another build** points at either toolkit's
`translations/` folder and fills the blanks. Run it twice — PS3 first, then PSP
— and PS3's wording stays wherever it had one. Nothing already translated here
is overwritten, so it is safe to re-run.

Matching is **on the English text**, never on an address. The three builds do
not agree on addresses: PS3 keys a slot string by `entity_key/ref_idx`, the PSP
by its own page numbering, and record 364 on PS3 is not record 364 here.
What they do agree on is the script.

Two details decide whether it works at all:

* **Line endings.** The PS3 CSVs store CRLF inside the text where the PC
  containers store a bare LF. Before normalising that, the PS3 briefings
  matched **0 of 4 987**. After, **4 974**. A silent zero that reads exactly
  like "the data is not there".
* **Stubs.** A translation of `.` is a placeholder, not a translation — both
  earlier builds used them to free bytes in a record that would not fit.
  Importing them would look like progress. They are counted and skipped:
  3 758 from PS3, 6 159 from PSP.

### What the imported Arabic then proves

Running the real thing through each container is the only honest way to know
what ships, and the answer differs sharply by container:

| container | result with the imported Arabic |
|---|---|
| **Story** | 362 blocks written, 99 636 string placements, **65 blocks too big** and left stock — the bulk of the script ships |
| **Briefing** | **26 of 383 records fit.** The other 357 need a median of 1.86x their budget, worst case 2.42x. The records that do fit hold 33 lines out of 4 987 — **0.7%** |

So the byte cap on the briefings is not a caveat to note and move past: in place,
that container cannot carry an Arabic translation at all. The append-and-remap
path is the only way it ships.

What the import yields against this project:

| tab | lines | from PS3 | from PSP | filled | still blank |
|---|---|---|---|---|---|
| Menus | 1 631 | 928 | 55 | 60.3% | 648 |
| Story | 10 074 | 6 578 | 1 980 | 85.0% | 1 516 |
| Briefing | 4 987 | 4 974 | 0 | 99.7% | 12 |
| Missions | 5 071 | 2 750 | 2 300 | 99.6% | 20 |

---

## The mission text

`MLG/disc0_rel/009645fa.PDT` is **`STAGEDAT.PDT`**, and its text sits three
containers down: entity → inline archive → `.olang` table. 5071 distinct
English lines across 9 entities and 44 tables — mission
objectives, tutorials, award descriptions, install prompts.

Only **370 of those are new**: STAGEDAT keeps its own copy of lines that also
live in `SLOT.DAT` and in the briefings. That is not redundancy to skip — they
are different bytes in different containers, and translating one copy does
nothing for the other. So the tab seeds itself from the rest of the project on
extraction: a line already translated elsewhere arrives translated here, and
what is left to type is the 370.

Nothing else reads it, and two layers are why. The entities are zlib inside two
ciphers, which the format layer already handles. The archives inside them are
**not QAR** — a QAR keeps its directory at the end of the file, so testing for
one says "not a QAR" and stops, which is exactly what the texture unpacker
does.

These have the directory inline, and the shape only comes out by walking it:

```
u32 count
per member:  name (NUL-terminated, VARIABLE length)
             pad to 4,  u32 size,  pad to 16
             data,      one NUL before the next name
```

The variable-length name is the trap. The first two members of every entity are
`ICON0.png` and `ICON0A.png`, which pad to exactly 12 bytes — so a fixed
12-byte name field parses both and then lands one byte early on the third, and
every length after that is nonsense.

Unlike the briefings this container may grow: `pdt_pack` gives an entity that
outran its slack a new home at the end and the entity table records where it
went, so nothing downstream shifts. Untouched entities are copied across still
encrypted and never re-deflated, which is what makes an unedited repack
byte-identical.

The side effect is size. A rebuilt entity is appended and the space it used to
occupy is left behind, so the container grows by roughly the size of whatever
you touched — translating one string that occurs in seven entities produced a
524 MB file from a 511 MB one. That does not compound, because every build
starts from the stock container rather than from the last build, but a full
mission translation will add a few tens of megabytes.

---

## The briefing tapes

`MLG/disc0_rel/0076531d.DAT` is **`BRIEFING.DAT`** — `strcode("BRIEFING")` is
`0x76531D`, and the record layout matches the PS3 file field for field.

**It reads as noise unless you decrypt it a page at a time.** Every 4 KB page
carries its own keystream, because the engine reads each briefing as a
page-aligned window and the Master Collection cipher restarts on every read.
Decrypt the file as one stream — the obvious thing — and record 0 comes out
clean while the remaining 4 MB looks like compressed data under an unknown
scheme:

| decrypt | records found |
|---|---|
| one keystream, whole file | 4 |
| **fresh keystream per 4 KB page** | **2 761** |

A record is one language: record `0x000000` is the Japanese of a conversation
and `0x0944B0` is the English of the same one, with its own line breaks and its
own string count. The tab shows the English records.

### Nothing may grow, and that is the whole problem

The engine seeks each briefing to a position it works out at runtime, so a
record that grew would shift every record after it and those would stop
playing. Every rebuild here is therefore **in place**: the text body is
rewritten inside its original byte count, `fon_off`, `u5` and the audio suffix
are untouched, and the file length never changes — which is also what keeps the
page boundaries where the cipher needs them.

**Expect most translations not to fit.** The English fills its records to the
byte — 376 of the 383 are exactly full, with 1 802 bytes of slack in the entire
script — and an Arabic presentation form costs three bytes in UTF-8 where
English costs one. So the editor shows each record's budget live in the corner
(`record 0x0944B0 — 1 446 of 1 379 bytes`) and warns the moment a record goes
over. A record that does not fit is **left in English and reported**, never
truncated.

Lifting that cap needs the append-and-remap trick from the PS3 work — copy the
record's page window to the end of the file and hook the engine where it loads
the packed location word — and that is a patch against a different binary. It
is not done here.

The format and the in-place rebuild are ported from the PS3 toolkit's
`briefing.py`; the per-page cipher is what this build adds.

---

## The fonts

Peace Walker has two font systems that matter, and which one draws a line is
decided per line by the `.olang` **style** on its reference. The workbench
shows it above the preview.

### The XPR faces — no limit

`FONT/*.xpr` are XPR2 bundles with a character map indexed by real Unicode,
65,375 slots wide. **All 141 assigned code points of Arabic Presentation
Forms-B (U+FE70..U+FEFC) install into both shipped faces**, at unchanged file
size, with the character map resolving every one of them. Verified.

They cost glyph *records*, not slots: all 643 are already spoken for and
growing the table would move the texture and invalidate the header, so a
record is borrowed, pointed at fresh atlas space, and the map aimed at it. The
donors come from the CJK range — an Arabic build has no use for 323 kanji, and
141 is well inside that.

These faces draw the story, and by way of style `0x0402` they draw **2,709 of
the 2,789 English menu references** too.

### The caps atlas — 63 cells, and it does not matter

The other 80 menu references are style `0x0001`: `BUTTON CONFIG`, `PRESS
START`, `CANCEL: BACK`. They go to a painted grid with no character map, where
a byte picks a cell.

Measured on the shipped sheet, **63 of its 160 cells are empty** — the
documented `0x90..0xBF` band is 48 of them, and almost all of row 6 plus `0x60`
and `0x7F` make up the rest. Arabic has 141 forms, so the full block **cannot**
go here; even giving up every Latin letter reaches 115.

It does not need to. Those 80 strings are short and repetitive: translated in
full they come to **21 distinct glyph shapes**, which fits the free band with
42 cells to spare and no Latin given up at all. The toolkit plans from the
project's own style-`0x0001` text for exactly this reason, and **Fonts > What
the menu face can hold** reports the real number.

### Harakat

A shaper leaves the vowel marks as combining characters in the base Arabic
block; they have no joined form and never reach Forms-B. The engine has no
notion of a combining mark either — it advances the pen by each glyph's width,
so a mark would take horizontal space and pull the word apart. They are
stripped on build by default, which is what nearly every game translation
ships.

### The punctuation Forms-B does not have

Painting U+FE70..U+FEFC is not enough. Measured against a real translation,
nine characters it uses are outside that block and outside what the game's
faces carry — and two of them are everywhere:

| | uses |
|---|---|
| `،` Arabic comma | 3 668 |
| `؟` Arabic question mark | 1 803 |
| `ـ` tatweel | 554 |
| em dash, Arabic semicolon, percent, Hangul filler, pe | 137 |

Without them every sentence ends in a box. They are installed alongside the
block. The ninth is U+200F, a right-to-left mark: invisible only to something
that understands it, and the engine draws one glyph per code point, so it is
stripped with the harakat rather than painted.

### Fonts on this machine

Tahoma, Segoe UI and Microsoft Sans Serif each cover all 141 forms; Arial,
Calibri, Times New Roman and Sakkal Majalla cover 140. Tahoma is what the
verification above was done with.

---

## The textures

**Textures > Unpack the game's textures** writes a folder per `.txp` package,
holding its DDS files. Step 1 (`Text` and `loading`) is the one a translation
needs — the menu art and the UI font sheets are in there. Steps 2 and 3 are the
subtitle faces and `STAGEDAT.PDT`, and each runs in its own process because
they are gigabytes of pure-Python BC decoding and would otherwise freeze the
window solid.

Then paint the DDS files in whatever editor you like and open the folder in the
texture workbench. "Edited" means **differs from what the game ships**, read
from the game's own package rather than from a duplicate that could drift, so
an untouched dump shows as clean.

A replacement must keep the original's width, height, format and byte length.
The game addresses textures by offset inside the package, so anything else
pushes every later texture out of place — **Check** names those before anything
is written.

---

## Project layout

```
pwtr.py               the translation workbench
pwtex_app.py          the texture workbench
pwtr/
    project.py        a project: originals, text, output, install, restore
    arabic.py         shaping, markup protection, and the menu cell budget
    briefing.py       BRIEFING.DAT: the page cipher, records, in-place rebuild
    stagedat.py       STAGEDAT.PDT: the inline archives and the text in them
    fonts.py          painting Arabic into the two font systems
    textures.py       unpacked packages, what is edited, and splicing back
    unpack_runner.py  the unpackers, as a command another process can run
    formats/          the Peace Walker file formats, vendored -- see CREDITS.md
    qt/
        workbench.py    the translation window
        texturebench.py the texture window
        fontinstall.py  the font dialog, and the capacity it reports
        unpackset.py    the three unpack steps
        findreplace.py  find and replace across both containers
        theme.py        one dark palette for both windows
docs/FORMATS.md       the upstream write-up of every format
```

Every module under `pwtr/formats/` also runs standalone from the command line.

---

## What is verified, and what is not

Measured on the real game files, by this toolkit:

| | |
|---|---|
| `.olang` rebuild, untouched | 17 / 17 byte-identical |
| `.txp` repack, untouched | byte-identical on the 15 MB, 84-texture `008299c5` |
| menu text write-back | English references only; French, German, Italian, Japanese and Spanish columns untouched |
| story extraction | 10 074 distinct lines out of 2 137 blocks, ~17 s |
| `BRIEFING.DAT` page cipher | re-encrypting an untouched decrypt reproduces the game file byte for byte |
| briefing rebuild | a no-op build over all 2 624 records is byte-identical; an edit changes only its own record and keeps the file length |
| STAGEDAT archives | 8 / 8 archives in the first 40 entities rebuild byte-identical from their own members |
| Arabic shaping | markup survives the reversal |
| Forms-B into the XPR faces | 141 / 141 painted on both, charmap resolves all, file size unchanged |
| the caps atlas | 63 of 160 cells measured empty; the 80 Style 0x0001 strings need 21 shapes |

**Not tested in the running game.** The formats are analysed and the round
trips are proven on disk, but nothing here has been put in front of the
renderer yet. Keep the `.bak` files, and expect to iterate.

Known limits:

* The caps atlas cannot hold the full Forms-B block under any arrangement —
  115 cells against 141. It does not have to; see **The fonts** above.
* Some joins still show a hairline gap where a form's connecting stroke is
  antialiased below the crop threshold. Readable, not yet perfect.
* Harakat are stripped, because the engine cannot place a combining mark.
* Briefing records cannot grow, and the English leaves almost no slack, so most
  briefing translations will be refused until the append-and-remap path exists.
* 478 of the 2 624 briefing records declare more strings than they hold — the
  surplus offsets point into the audio block — and are never written to.
* Two `SLOT.DAT` blocks are known upstream to overflow their footprint when
  recompressed after a font edit. They are reported and skipped, never
  relocated — the container tiles the whole file, so there is nowhere to move
  them to.

---

## Credits

The Peace Walker file formats — the cipher, `.olang`, `.PDT`, `SLOT.DAT`, the
fonts and the textures — were reverse engineered by **Dmytro Bidlov (Little Bit
Team)** in the [Peace Walker Localization
Tool](https://t.me/LittleBitUA), MIT licensed. That work is vendored here in
`pwtr/formats/` and is what makes any of this possible. See
[CREDITS.md](CREDITS.md).

This project ships **no game data**. Metal Gear Solid and Peace Walker are
trademarks of Konami; this toolkit is independent and not endorsed by Konami.


## Tuning the font

**Fonts > Install Arabic into the game's fonts** is where the letters get
their size and their alignment, and it shows the result before it writes
anything.  The preview is not an impression: it is drawn by the same code at
the same point size on the same baseline row with the same crop, so what it
shows is what would be painted.

* **Size** is one number, ``fill``, and it is capped by the cell.  The face is
  cut into 67 pixel cells and Arabic wants 80 of them -- alef with hamza is 60
  by itself and the meem bowl reaches 20 below the line -- so the letters are
  always scaled down; ``fill`` says by how much further.  Past 1.0 the tallest
  and deepest forms lose ink, and the dialog names them and counts the pixels
  rather than letting it happen quietly.
* **Nudge** moves one form up or down, in pixels, for the cases where the face
  itself draws a letter off the line.  It is not for a line that looks
  crooked overall -- that was a per-glyph fault in the painter, and it is
  fixed at the source.

The plan -- which code point carries which glyph -- belongs to the project,
not the dialog, because the text is *stored* as those code points.  Change it
and the text has to be rebuilt.
