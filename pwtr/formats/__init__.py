"""The Peace Walker file formats, vendored from the Peace Walker Localization
Tool by Dmytro Bidlov (Little Bit Team), MIT -- see CREDITS.md.

The modules are kept **byte-identical to upstream** so a newer release can be
dropped straight in.  That means they import each other by bare name
(``import olang``, ``import slotdat``), which only resolves if their own folder
is on the path -- so this package puts it there on first import, and re-exports
them under ``pwtr.formats.<name>``.

Nothing above this package should reach into the game's bytes directly; the
project model in :mod:`pwtr.project` is the only caller.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if str(HERE) not in sys.path:
    # Appended, not inserted: these are generic names ("olang", "qar") and the
    # program's own modules must keep winning if one ever collides.
    sys.path.append(str(HERE))

#: Every vendored module, in dependency order so an import error names the
#: module that is actually missing rather than one three levels down.
MODULES = (
    "pwpaths", "pwcrypt", "olang", "qar", "pdt", "pdt_unpack", "pdt_pack",
    "pwtex", "pwbc", "txp_unpack", "txp_pack", "pwfont", "pwfont_unpack",
    "pwfontatlas", "pwxpr", "slotdat", "slotitem", "slottext", "pwfonts",
)


def load(name: str):
    """One vendored module by name."""
    if name not in MODULES:
        raise ImportError(f"{name} is not a vendored Peace Walker format module")
    return importlib.import_module(name)


def __getattr__(name: str):
    """``from pwtr.formats import olang`` without importing all nineteen.

    Several of them pull in numpy and Pillow and scan the Steam libraries at
    import time, so the text side of the app should not pay for the texture
    side just by starting.
    """
    if name in MODULES:
        module = load(name)
        globals()[name] = module
        return module
    raise AttributeError(name)


__all__ = [*MODULES, "load", "HERE"]
