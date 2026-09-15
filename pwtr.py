#!/usr/bin/env python3
"""pwtr -- Arabic translation workbench for Metal Gear Solid: Peace Walker.

    python pwtr.py                    open the workbench
    python pwtr.py <project>          open it on a project folder

Translations are typed as ordinary Arabic and stored that way.  The conversion
to presentation forms -- and, for the menu face, onto the atlas cells the
engine can actually reach -- happens when the containers are built.

The window needs PySide6; the shaping needs arabic-reshaper and python-bidi::

    pip install -r requirements.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    from pwtr.qt import AVAILABLE
    if not AVAILABLE:
        print("The workbench needs PySide6:\n\n    pip install PySide6\n",
              file=sys.stderr)
        return 1

    from pwtr.qt.workbench import main as workbench_main
    return workbench_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
