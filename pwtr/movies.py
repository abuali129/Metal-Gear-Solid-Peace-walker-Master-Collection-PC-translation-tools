r"""The pre-rendered movies, and the text burned into three of them.

``MLG/data/Mov`` and ``hqMov`` hold the game's video; ``EXLANG/data`` holds the
copies the extra language needed remade.  Both are wrapped in the same
``pwcrypt`` as every other container, keyed on the file's own name, so the
headers read as noise and nothing will open them.

Underneath they are ordinary files::

    .xmx    MP4, H.264 High -- 1280x720 at 30fps in Mov, 1920x1080 at 60 in hqMov
    .xsx    Ogg Vorbis, the soundtrack for the movie of the same stem

The cipher is one continuous stream from byte zero -- no per-page restart, as
:mod:`pwtr.formats.slotdat` has -- and the keystream for a short file is a
prefix of the keystream for a long one.  That last part is what makes this
worth doing: **a replacement may be any length**.  Nothing indexes these files
by size, and re-encoding a text card at a different bitrate is fine.

## Which ones carry text

Text on black, so they are the smallest files by a wide margin::

    MLG/data/Mov/00568c22.xmx    English     "In time, all standing armies..."
    MLG/data/Mov/00568c46.xmx    French
    MLG/data/Mov/00568c59.xmx    German
    MLG/data/Mov/00568ca8.xmx    Italian
    MLG/data/Mov/00568de4.xmx    Spanish
    EXLANG/data/Mov/00362e0c.xmx Portuguese

    EXLANG/data/Mov/0019f5e1.xmx  62s, the historical timeline, in Portuguese
    EXLANG/data/Mov/001a05e1.xmx  62s, the rest of it

The timeline has no English counterpart among the loose movies -- the smallest
MLG file that is not one of the five cards is the credits roll -- and its text
is in no ``.olang`` table either, so where the English build draws it from is
not yet known.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from pwtr.formats import pwcrypt

#: Where the movies live, relative to the game folder.
FOLDERS = (Path("MLG") / "data" / "Mov", Path("MLG") / "data" / "hqMov",
           Path("EXLANG") / "data" / "Mov", Path("EXLANG") / "data" / "hqMov")

SUFFIX = {".xmx": ".mp4", ".xsx": ".ogg"}


def decrypt(path: str | Path) -> bytes:
    """The file's real bytes.  The name keys the cipher, not the path."""
    path = Path(path)
    return pwcrypt.crypt(path.read_bytes(), path.name)


def encrypt(data: bytes, name: str) -> bytes:
    """Wrap a replacement for the game.  ``name`` is the name it will have."""
    return pwcrypt.crypt(data, name)


def extract(path: str | Path, out_dir: str | Path) -> Path:
    """Decrypt one movie into ``out_dir`` under its real extension."""
    path, out_dir = Path(path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plain = decrypt(path)
    out = out_dir / (path.stem + SUFFIX.get(path.suffix.lower(), ".bin"))
    out.write_bytes(plain)
    return out


def install(replacement: str | Path, target: str | Path,
            backup: bool = True) -> Path:
    """Encrypt ``replacement`` and put it where the game will find it.

    The original is kept as ``.bak`` the first time, the same way the text
    containers are -- and for the same reason, since after this the game's copy
    is no longer a source of anything.
    """
    replacement, target = Path(replacement), Path(target)
    if backup:
        keep = target.with_suffix(target.suffix + ".bak")
        if not keep.exists():
            keep.write_bytes(target.read_bytes())
    target.write_bytes(encrypt(replacement.read_bytes(), target.name))
    return target


def ink_bands(frame_png: str | Path, floor: int = 40, gap: int = 8):
    """``[(top, bottom)]`` of the rows carrying text in a still.

    For putting a translation on the line its English sat on rather than
    somewhere that looks about right.  Needs numpy and Pillow.
    """
    import numpy as np
    from PIL import Image

    grey = np.array(Image.open(frame_png).convert("L"))
    rows = np.where(grey.max(axis=1) > floor)[0]
    bands, previous = [], None
    for row in rows:
        if previous is None or row - previous > gap:
            bands.append([int(row), int(row)])
        else:
            bands[-1][1] = int(row)
        previous = row
    return [tuple(b) for b in bands]


def still(movie: str | Path, out_png: str | Path, at: float = 4.0,
          width: int = 0) -> Path:
    """One frame, for reading what a movie says.  Needs ffmpeg on PATH."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(movie),
           "-frames:v", "1"]
    if width:
        cmd += ["-vf", "scale=%d:-1" % width]
    cmd.append(str(out_png))
    subprocess.run(cmd, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return Path(out_png)
