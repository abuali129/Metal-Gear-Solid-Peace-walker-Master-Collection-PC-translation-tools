# Credits

## The file formats

Everything under `pwtr/formats/` is the work of **Dmytro Bidlov**, of
[Little Bit Team](https://t.me/LittleBitUA), from the **Peace Walker
Localization Tool**. It is MIT licensed — the licence is kept verbatim as
[LICENSE.upstream](LICENSE.upstream), and `docs/FORMATS.md` is their write-up
of every format, in English and Ukrainian.

That work is the foundation of this one. It is not a library that happened to
be useful; it is the reverse engineering of a game that stores almost nothing
by name and encrypts every asset under a cipher keyed by its own file name:

* the Master Collection stream cipher, both directions, byte-exact on every
  shipped asset
* the 24-bit rotate-and-add name hash that resolves every archive on disk
* `.olang`, the six-language string pool — byte-exact round trip on all 17
  shipped files
* `SLOT.DAT`, three cipher layers deep, where the story text actually lives —
  2 137 / 2 137 blocks decoding, and the discovery that `pageCount` is not the
  block's real footprint
* `.PDT` and its QAR sub-archives — an untouched repack of the 511 MB
  `STAGEDAT` is byte-identical to the original
* the `.txp` texture packages and BC encoding
* the three separate font systems, and the reading of the UI atlas grid: `cell
  = byte - 0x20`, with the first cell of each row sitting alone at the right
  edge of the sheet — confirmed against strings the game itself ships

The vendored modules are kept **byte-identical to upstream** so a newer release
can be dropped straight in. `pwtr/formats/__init__.py` is the only addition,
and it exists solely to put their folder on the import path.

## What this project adds

The workbench: the project model, the Arabic layer, the two windows, and the
cell budget that says whether an Arabic menu translation can fit the UI face at
all. Those are this repository's, and any mistakes in them are too.

The UI is carried over from the MGS4 Arabic translation toolkit — same dark
palette, same table-and-editor layout, same find/replace — because a translator
who has used one should not have to learn the other.

## Not affiliated

This project ships no game data. Metal Gear Solid and Peace Walker are
trademarks of Konami. Neither this toolkit nor the Peace Walker Localization
Tool is endorsed by or affiliated with Konami.
