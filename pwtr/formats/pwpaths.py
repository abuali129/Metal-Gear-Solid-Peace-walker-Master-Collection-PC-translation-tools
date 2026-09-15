r"""Where the tools look for the game and where they put their output.

A released build must not carry anyone's private drive letters, so nothing here
is hardcoded to a machine:

  * the game is found by walking the usual Steam library locations, and every
    library listed in `libraryfolders.vdf`, so a second drive works too;
  * output defaults to an `output` folder next to the executable (or next to
    the sources when running from Python).

Both can always be overridden - the GUI has fields for them, and every CLI
takes an explicit path.
"""
from __future__ import annotations

import os
import re
import sys

GAME_SUBPATH = os.path.join('steamapps', 'common', 'MGS_PW', 'mgspw')


def app_dir() -> str:
    """The folder the program lives in - the exe's folder once frozen."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_output() -> str:
    return os.path.join(app_dir(), 'output')


def resource(name: str) -> str | None:
    """A file shipped alongside the program - the window icon, for instance.

    PyInstaller unpacks bundled data into a temporary folder and points
    `sys._MEIPASS` at it, so a frozen build must look there first; running from
    source it sits next to the sources, or one level up in the repo root.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (getattr(sys, '_MEIPASS', None), app_dir(), here,
                 os.path.dirname(here)):
        if base:
            p = os.path.join(base, name)
            if os.path.isfile(p):
                return p
    return None


def _steam_roots():
    seen, out = set(), []

    def add(p):
        if p and p not in seen and os.path.isdir(p):
            seen.add(p)
            out.append(p)

    # the registry knows where Steam itself is
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam'),
                          (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam')):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ('SteamPath', 'InstallPath'):
                        try:
                            add(os.path.normpath(winreg.QueryValueEx(k, name)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass

    for d in ('C:\\Program Files (x86)\\Steam', 'C:\\Program Files\\Steam'):
        add(d)
    for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        add('%s:\\SteamLibrary' % letter)
        add('%s:\\Steam' % letter)

    # every extra library Steam has registered
    for root in list(out):
        vdf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        if not os.path.isfile(vdf):
            continue
        try:
            text = open(vdf, 'r', encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        for m in re.finditer(r'"path"\s*"([^"]+)"', text):
            add(os.path.normpath(m.group(1).replace('\\\\', '\\')))
    return out


def find_game(hint: str | None = None) -> str | None:
    """Locate `...\\MGS_PW\\mgspw`, or None.  A hint that already points at the
    folder - or at its parent - is accepted as-is."""
    if hint:
        hint = os.path.normpath(hint)
        if _looks_like_game(hint):
            return hint
        cand = os.path.join(hint, 'mgspw')
        if _looks_like_game(cand):
            return cand
    for root in _steam_roots():
        cand = os.path.join(root, GAME_SUBPATH)
        if _looks_like_game(cand):
            return cand
    return None


def _looks_like_game(p: str) -> bool:
    return (os.path.isdir(p)
            and os.path.isdir(os.path.join(p, 'FONT'))
            and os.path.isdir(os.path.join(p, 'MLG')))
