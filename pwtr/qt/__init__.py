"""Qt front end.  Import guarded so the command line works without PySide6."""

from __future__ import annotations

try:
    import PySide6  # noqa: F401
    AVAILABLE = True
except ImportError:                     # pragma: no cover - environment
    AVAILABLE = False
