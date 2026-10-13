# SPDX-License-Identifier: GPL-2.0-or-later
"""Which system AMDLSS runs on, decided in this one file.

Windows keeps its paths and commands. Linux runs the same program against Proton games: POSIX paths, the XDG data
folder, HelixSR's shell setup and xdg-open. Functions take `windows=` (None: the host) so tests can check both
sides on either host."""
import ntpath
import os
import posixpath
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
