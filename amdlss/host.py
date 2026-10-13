# SPDX-License-Identifier: GPL-2.0-or-later
"""Which system AMDLSS runs on, decided in this one file.

Windows keeps its paths and commands. Linux runs the same program against Proton games: POSIX paths, the XDG data
folder, HelixSR's shell setup and xdg-open. Functions take `windows=` (None: the host) so tests can check both
sides on either host."""
import ntpath
import os
import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == 'nt'


def is_windows(windows=None):
    return WINDOWS if windows is None else bool(windows)


def path_module(windows=None):
    """ntpath for Windows game folders, posixpath for Linux ones: the separators and case rules of the paths."""
    return ntpath if is_windows(windows) else posixpath


def data_home(windows=None):
    """Where per-user program data lives: %LOCALAPPDATA%, or $XDG_DATA_HOME (else ~/.local/share)."""
    if is_windows(windows):
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')


def clean_env(base=None):
    """The environment for a program of the system. The frozen Linux binary puts its own libraries first in
    LD_LIBRARY_PATH (PyInstaller keeps the user's value in LD_LIBRARY_PATH_ORIG); bash, python3, curl or xdg-open
    started with that would load them, so the user's value comes back."""
    env = dict(os.environ if base is None else base)
    if getattr(sys, 'frozen', False):
        original = env.pop('LD_LIBRARY_PATH_ORIG', None)
        if original is None:
            env.pop('LD_LIBRARY_PATH', None)
        else:
            env['LD_LIBRARY_PATH'] = original
    return env


def restore_library_path():
    """Gives this process's environment the user's LD_LIBRARY_PATH back, so programs started without clean_env (the
    browser behind webbrowser.open) do not load the frozen binary's libraries. This process keeps its own: glibc reads
    LD_LIBRARY_PATH only at start."""
    env = clean_env()
    for key in ('LD_LIBRARY_PATH', 'LD_LIBRARY_PATH_ORIG'):
        if key in env:
            os.environ[key] = env[key]
        else:
            os.environ.pop(key, None)


def xdg_open(target):
    """Starts xdg-open on `target` (a folder), detached, with the system's environment."""
    return subprocess.Popen(['xdg-open', str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=clean_env())


def open_folder(folder):
    """Shows `folder` in the file manager (Explorer on Windows, xdg-open on Linux)."""
    if WINDOWS:
        os.startfile(folder)
    else:
        xdg_open(folder)


# --- Linux: where a Proton wine can be, for HelixSR's shell setup ---------------------------------------------

def steam_libraries(home=None):
    """Every Steam folder that can hold Proton: the usual Steam roots and the library paths listed in their
    steamapps/libraryfolders.vdf (a game drive is a second library), without repeats."""
    home = Path(home or Path.home())
    roots = [home / '.local' / 'share' / 'Steam', home / '.steam' / 'steam', home / '.steam' / 'root']
    found = list(roots)
    for root in roots:
        try:
            text = (root / 'steamapps' / 'libraryfolders.vdf').read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        found += [Path(path.replace('\\\\', '\\')) for path in re.findall(r'"path"\s+"([^"]+)"', text)]
    unique = {}
    for folder in found:
        unique.setdefault(os.path.realpath(folder), folder)
    return list(unique.values())


def _proton_rank(wine):
    """Sort key of .../<Proton name>/files/bin/wine: 'Proton - Experimental' first, then the highest version."""
    name = wine.parents[2].name
    return 'Experimental' in name, tuple(int(n) for n in re.findall(r'\d+', name))


def find_proton_wine(home=None):
    """The best Proton wine in any Steam library (official Proton first, then custom builds in
    compatibilitytools.d), or None."""
    libraries = steam_libraries(home)
    ranked = []
    for pattern in ('steamapps/common/Proton*/files/bin/wine', 'compatibilitytools.d/*/files/bin/wine'):
        wines = [w for library in libraries for w in library.glob(pattern) if os.access(w, os.X_OK)]
        ranked += sorted(wines, key=_proton_rank, reverse=True)
    return ranked[0] if ranked else None


def setup_finds_wine(env, home=None):
    """True when helixsr-setup.sh finds a wine by itself: one of the four Proton places it globs, else `wine` on PATH."""
    home = Path(home or Path.home())
    places = ('.local/share/Steam/steamapps/common/Proton*', '.steam/steam/steamapps/common/Proton*',
              '.local/share/Steam/compatibilitytools.d/*', '.steam/root/compatibilitytools.d/*')
    if any(os.access(w, os.X_OK) for place in places for w in home.glob(f'{place}/files/bin/wine')):
        return True
    return shutil.which('wine', path=env.get('PATH', os.defpath)) is not None
