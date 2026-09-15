# Peace Walker translation toolkit

A fan-translation workbench for **Metal Gear Solid: Peace Walker** (PC,
*Master Collection*). It reads every piece of English the game shows, gives
you a table to translate it in, and writes it back into the game files —
together with the font glyphs your language needs.

It works for any language. Right-to-left scripts that need shaping (Arabic)
are handled on build; everything else — Cyrillic, Greek, accented Latin,
and so on — is written as typed.

* **`pwtr.py`** — the translation workbench.
* **`pwtex_app.py`** — the texture workbench, for pictures with writing in them.

Nothing touches the game until you choose **Install**, and every file it
replaces is backed up first.

---

## Requirements

* **Python 3.11** (3.10+ works; see *Python version* below for why 3.11 is
  recommended)
* `pip install -r requirements.txt`
* Optional: `pip install zopfli` — packs the story container tighter, so
  fewer blocks are refused for size.
* Optional: [ffmpeg](https://ffmpeg.org/) on your `PATH` — only needed to
  subtitle the pre-rendered movies.

```
pip install -r requirements.txt
python pwtr.py
```

On Windows, `pwtr311.bat` opens the workbench with CPython 3.11 from its
default install location.

### Python version

The story container (`SLOT.DAT`) is made of zlib blocks that must not grow
past their original footprint. Python 3.14 bundles zlib-ng, which
compresses a few per cent looser than classic zlib — enough to push
borderline blocks over. Build with 3.11, or install `zopfli`.

---

## Quick start

1. **File > New project** — point it at the game's `mgspw` folder (the one
   holding `METAL GEAR SOLID PEACE WALKER.exe`). The project is a separate
   folder; the game is never modified while you work.
2. **Build > Extract everything** — reads the English from every source.
3. Translate in the tabs. Save with **Ctrl+S**.
4. **Fonts > Install characters into the game's fonts** — choose your
   script and the characters your language uses.
5. **Build > Build everything**, then read the report (see below).
6. **Build > Install into the game**, and play.
7. When you are ready to release: **Build > Bundle a release zip**.

---

## The tabs

| Tab | What it holds | Where it lives |
|---|---|---|
| **Menus** | menus, options, system messages | `.olang` tables in `MLG/Text` and `EXLANG/Text` |
| **Story** | cutscene subtitles, radio calls, co-op and tutorial lines | `SLOT.DAT` |
| **Briefing** | the briefing tapes | `BRIEFING.DAT` |
| **Missions** | objectives, tutorials, award descriptions | `STAGEDAT.PDT` |
| **Staff** | Mother Base staff name tables | `STAGEDAT.PDT` |
| **Speakers** | names on the radio bar under a subtitle | the game executable |
| **Movies** | text burned into three pre-rendered movies | `MLG/data/Mov`, `hqMov` |

A line appears **once**, however many places use it; translating it
translates every copy.

### Working in the table

* **Untranslated only** and **Too long only** filter the list; **Next
  untranslated** and **Next too long** (Ctrl+G) walk it.
* Typing a block number (Story) into the filter lists every line in that block.
* The editor warns when a translation drops engine markup. Keep these exactly
  as they are in the English:
  * `<I=...>` button icons and `<C=...>` colour changes
  * `$1`, `%d`, `%s` — values the game fills in
  * `[snake_case]` tokens
* Line breaks matter. Keep leading blank lines in captions — they position the
  text on screen — and use real line breaks, not a typed `\n`.

---

## Size limits

Each container has its own rules, and the build tells you which lines did not
fit. A line that does not fit is **left in English, never truncated**.

| Container | Rule |
|---|---|
| Menus, Missions | may grow — no practical limit |
| Story | each compressed block must fit its original footprint; lines share their block's room |
| Story radio/co-op lines (`ypk`) | each line group must fit its original size |
| Story voice subtitles (`ohd`) | 63 bytes per line |
| Briefing | each record is fixed size; lines share their record's budget |
| Staff names | 15 bytes each |
| Speakers | fixed room in the executable |

The byte cost of a line depends on your font plan: characters placed on
one-byte code points cost one byte, the rest two. See *Fonts*.

### The build report

Every build ends with what is still English in game:

```
STILL ENGLISH IN GAME -- Story
  1 translated line(s) did not fit, so the game shows their English instead:
    slot/00693/10/ypk0.0  95 byte(s) over  "Head for the FSLN boathouse..."
  To fix: in the Story tab tick "Too long only" (or use Next too long, Ctrl+G),
  shorten those translations, and build again.
```

Untranslated lines are counted too, with a pointer to **Untranslated only**.

---

## Fonts

**Fonts > Install characters into the game's fonts** paints your characters
into the game's subtitle faces and chooses the code points they are stored at.

Why a plan is needed at all: the engine only looks up low code points (up to
U+024F). Anything above — Cyrillic, Greek, Arabic — must be moved to a code
point it can reach, and the text is written in those code points on build.

1. **Script** — *Arabic* (shaped into joined forms, right to left) or
   *Other* (drawn as typed, left to right).
2. **Choose characters...** — browse Unicode blocks or search, tick single
   characters or whole blocks. Characters the game's face already draws are
   greyed out. Presets: *Everything my translation uses*, *Arabic letters*.
   Sets can be saved and shared as a plain list.
   **Automatic** plans whatever the translation uses.
3. **Subtitle face** — a TrueType font that has your characters.
4. Adjust **Size** and per-letter **Nudge** against the live preview — it is
   drawn by the same code that paints the face.
5. **Spend** — which case range (capitals or lower case) gives up its
   one-byte code points to your letters. Those Latin letters keep working in
   your translations; only text never rebuilt through the plan (untranslated
   lines, names read from saves) is affected.
6. **Install into project output**, then rebuild the text.

**Freeze mapping** keeps the installed code points and only repaints glyphs —
use it while tuning size and nudges, so nothing needs rebuilding. Changing the
script or the character set turns it off.

The **caps atlas** (short Style `0x0001` menu strings such as *PRESS START*)
is a separate 160-cell grid with 63 free cells. **Fonts > What the menu face
can hold** reports whether your translation of those strings fits.

---

## Bringing in an existing translation

**File > Import translations from another build** reads a folder of CSV
files and fills blank lines. Each CSV needs two columns:

| column | contents |
|---|---|
| `source_text` | the English line |
| `translation` | your translation |

* Matching is on the **English text**, not on file or address — so a CSV from
  another platform's toolkit, a spreadsheet export, or your own script all work.
* Line endings are normalised before matching.
* Lines already translated are never overwritten; it is safe to run again,
  and to run several sources in order of preference.
* Placeholder translations made only of `.`, `-` or spaces are skipped.

---

## Installing and releasing

* **Build > Install into the game** copies the built files in. The first time
  a file is replaced its original is kept beside it as `.bak`; files the game
  did not have are marked and removed again by Restore.
* **Build > Restore the originals** puts the game back as it was.
* **Build > Bundle a release zip** packs everything built, in the game's own
  folder layout, so players extract it into `mgspw`. You are asked whether to
  include the patched executable (it carries the speaker names) — think
  before distributing a modified executable.

---

## Movies

Three pre-rendered movies have English burned into the picture. The **Movies**
tab holds that text; the build draws your translation over a blanked frame
with ffmpeg and re-encodes the movie.

* Keep the same number of lines in each block, with one blank line between
  blocks — the scrolling sequences are timed to it. The tab warns if a count
  differs.
* **Pre-rendered subtitle font and size** chooses the look;
  **Preview the subtitled pre-rendered movies** writes a playable `.mp4`
  with sound before anything is installed.

---

## Staff names in a save

Staff are named when recruited and the name is stored in the save. **Build >
Rename the staff in a save** rewrites names in an existing save file.

---

## Textures

**Textures > Unpack the game's textures** writes each texture package as a
folder of DDS files. Edit them in any image editor, then open the folder in the
texture workbench. A replacement must keep the original's width, height and
format; **Check** reports any that do not before anything is written.

---

## Project layout

```
pwtr.py               the translation workbench
pwtex_app.py          the texture workbench
pwtr311.bat           open the workbench with CPython 3.11
pwtr/
    project.py        a project: extract, build, install, restore, bundle
    arabic.py         shaping for right-to-left scripts, markup protection
    compact.py        the font plan: which code point each character is stored at
    fonts.py          painting glyphs into the game's faces
    briefing.py       BRIEFING.DAT
    stagedat.py       STAGEDAT.PDT
    staff.py          staff name tables
    exenames.py       speaker names in the executable
    hardsub.py        subtitling the pre-rendered movies
    importer.py       importing CSV translations
    textures.py       texture packages
    formats/          the Peace Walker file formats -- see CREDITS.md
    qt/               the windows and dialogs
tools/                command-line scripts used during development
docs/FORMATS.md       the file formats, documented
```

---

## Credits

The Peace Walker file formats — the cipher, `.olang`, `.PDT`, `SLOT.DAT`, the
fonts and the textures — were reverse engineered by **Dmytro Bidlov (Little Bit
Team)** in the [Peace Walker Localization Tool](https://t.me/LittleBitUA), MIT
licensed. That work is vendored in `pwtr/formats/` and is what makes any of
this possible. See [CREDITS.md](CREDITS.md).

This project ships **no game data**. Metal Gear Solid and Peace Walker are
trademarks of Konami; this toolkit is independent and not endorsed by Konami.
