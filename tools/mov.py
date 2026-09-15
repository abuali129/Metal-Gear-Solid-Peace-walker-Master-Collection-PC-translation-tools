"""Decrypt, inspect and replace the game's pre-rendered movies.

    python tools/mov.py list                       what is there, smallest first
    python tools/mov.py extract <file> <out dir>   .xmx -> .mp4, .xsx -> .ogg
    python tools/mov.py still <file> <out.png>     one frame, to read it
    python tools/mov.py bands <frame.png>          which rows the text sits on
    python tools/mov.py install <new.mp4> <file>   encrypt it into the game

`<file>` is a path in the game folder.  Installing keeps a .bak the first time
and accepts a replacement of any length.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pwtr import movies
from pwtr.project import find_game


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    what, rest = argv[0], argv[1:]

    if what == "list":
        game = find_game()
        if game is None:
            print("the game folder was not found")
            return 1
        for folder in movies.FOLDERS:
            here = game / folder
            if not here.is_dir():
                continue
            print("== %s" % folder)
            files = sorted(here.iterdir(), key=lambda p: p.stat().st_size)
            for path in files[:8]:
                print("   %10d  %s" % (path.stat().st_size, path.name))
            if len(files) > 8:
                print("   ... %d more" % (len(files) - 8))
        return 0

    if what == "extract" and len(rest) == 2:
        print(movies.extract(rest[0], rest[1]))
        return 0

    if what == "still" and len(rest) >= 2:
        at = float(rest[2]) if len(rest) > 2 else 4.0
        print(movies.still(rest[0], rest[1], at))
        return 0

    if what == "bands" and len(rest) == 1:
        for top, bottom in movies.ink_bands(rest[0]):
            print("   rows %4d .. %4d" % (top, bottom))
        return 0

    if what == "install" and len(rest) == 2:
        target = movies.install(rest[0], rest[1])
        print("installed -> %s (%d bytes)" % (target, target.stat().st_size))
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
