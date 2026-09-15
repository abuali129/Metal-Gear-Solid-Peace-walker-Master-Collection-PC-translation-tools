#!/usr/bin/env python3
"""Texture workbench: edit Peace Walker textures and put them in a package.

    python pwtex_app.py [unpacked folder]

The folder is the one the unpacker writes -- a subfolder per .txp package,
holding its DDS files.  With no argument it opens the last one used, or offers
to make one.
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

    from PySide6.QtWidgets import QApplication, QMessageBox
    from pwtr.project import find_game
    from pwtr.qt import theme
    from pwtr.qt.texturebench import TextureBench, last_opened, remember
    from pwtr.textures import TextureError, TextureSet

    app = QApplication(sys.argv[:1])
    theme.apply(app)

    textures = None
    if argv:
        game = find_game()
        if game is None:
            QMessageBox.critical(None, "Peace Walker textures",
                                 "The game was not found in your Steam "
                                 "libraries. Open the folder from the window "
                                 "instead, which asks where it is.")
        else:
            try:
                textures = TextureSet(argv[0], game)
                remember(argv[0], str(game))
            except (TextureError, OSError) as error:
                QMessageBox.critical(None, "Peace Walker textures", str(error))
                return 1
    else:
        textures = last_opened()

    window = TextureBench(textures)
    window.show()
    if window.textures is None:
        window.open_folder()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
