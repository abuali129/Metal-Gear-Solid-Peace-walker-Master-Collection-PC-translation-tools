"""Unpacking, as a command the window can run in another process.

    python -m pwtr.unpack_runner txp   <game> <out>
    python -m pwtr.unpack_runner xpr   <game> <out>
    python -m pwtr.unpack_runner stage <game> <out>

The vendored unpackers each have a ``main()``, but it writes into a fixed
folder next to the sources and takes no destination.  Rather than edit files
that are kept byte-identical to upstream, this calls their ``unpack()``
directly with the folder we actually want.

It is a separate process for the same reason the MGS4 edit-set builder used
one: this is pure Python doing gigabytes of BC decoding, it holds the
interpreter lock for every second of it, and no window survives that.  Run this
way the window stays alive and the log fills as it goes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def txp(game: Path, out: Path) -> int:
    """Every ``.txp`` package in Text and loading."""
    from pwtr.formats import txp_unpack

    packages = []
    for folder in ("Text", "loading"):
        source = game / folder
        if source.is_dir():
            packages += sorted(source.glob("*.txp"))

    print(f"{len(packages)} package(s) to unpack", flush=True)
    for number, path in enumerate(packages, 1):
        print(f"[{number}/{len(packages)}] {path.name}", flush=True)
        txp_unpack.unpack(str(path), str(out))
    return len(packages)


def xpr(game: Path, out: Path) -> int:
    """The two XPR2 subtitle faces in FONT."""
    from pwtr.formats import pwfont_unpack

    faces = sorted((game / "FONT").glob("*.xpr"))
    print(f"{len(faces)} face(s) to unpack", flush=True)
    for number, path in enumerate(faces, 1):
        print(f"[{number}/{len(faces)}] {path.name}", flush=True)
        pwfont_unpack.unpack(str(path), str(out))
    return len(faces)


def stage(game: Path, out: Path) -> int:
    """STAGEDAT.PDT, followed all the way down to the textures.

    An entity is not a texture: the pictures sit three levels in -- entity,
    QAR sub-archive, ``.txp`` member, DDS -- so this yields thousands of
    pictures rather than 557 opaque blobs, and takes a while to do it.
    """
    from pwtr.formats import pdt_unpack

    path = game / "MLG" / "disc0_rel" / "009645fa.PDT"
    if not path.is_file():
        print(f"STAGEDAT.PDT is not there: {path}", flush=True)
        return 0
    print(f"unpacking {path.name} -- 511 MB, this is the slow one", flush=True)
    pdt_unpack.unpack(str(path), str(out), True)
    return 1


JOBS = {"txp": txp, "xpr": xpr, "stage": stage}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in JOBS:
        print(__doc__, file=sys.stderr)
        return 2
    job, game, out = argv[0], Path(argv[1]), Path(argv[2])
    if not game.is_dir():
        print(f"not a folder: {game}", file=sys.stderr)
        return 1
    os.makedirs(out, exist_ok=True)
    count = JOBS[job](game, out)
    print(f"done -- {count} item(s) -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
