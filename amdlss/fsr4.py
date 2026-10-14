# SPDX-License-Identifier: GPL-2.0-or-later
"""FSR 4.1.1 as a per-game upscaler next to HelixSR.

HelixSR 1.9.0 ships AMD's official FidelityFX upscaler DLL (amd-fsr/amd_fidelityfx_upscaler_dx12.dll, file version
4.1.1.2740, redistributed under AMD's license, which is copied beside every AMD DLL AMDLSS installs). It holds FSR 4 for
RDNA 4 (FP8) and for RDNA 3 desktop cards (INT8), and FSR 3.1.5 for every other card. RDNA 2 (RX 6000) is not covered:
those cards need a community DLL that the user picks once and AMDLSS does not download.

This file is the whole feature apart from the window:

  - which DLL to use on this PC (find_source) and the DLL a user picked (choose_dll, forget_dll);
  - whether a game can use it, and if not why (availability), shown on every game card;
  - the drop-in install and remove, done by AMDLSS itself with a record beside the game's DLL (install_dropin,
    remove_dropin); and the OptiScaler route switch, which points OptiScaler.ini at the FSR 4 DLL (use_in_optiscaler);
  - what is installed in a game (mark).
"""
import hashlib
import json
import os
import shutil
from collections import namedtuple
from pathlib import Path

from . import games as gamelist
from . import gpu, helixsr, pe
from .helixsr import HelixSRError

DLL = 'amd_fidelityfx_upscaler_dx12.dll'
LICENSE = 'FidelityFX_v2_LICENSE.md'
FOLDER = 'amd-fsr'                     # in the HelixSR folder: AMD's DLL and its license
USER_FOLDER = 'fsr4'                   # in the AMDLSS data folder: the DLL the user picked
AMD_VERSION = '4.1.1.2740'
AMD_SIZE = 28761864
AMD_SHA256 = 'd0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46'
NAME = 'FSR 4.1.1'

_VERIFIED = {}   # (path, size, mtime) -> whether the file matched the pin; AMD's DLL is 28 MB, hash it once


class Source(namedtuple('Source', 'path label problem short user', defaults=(False,))):
    """The FSR 4 DLL to use: `path` and `label` ("AMD's DLL 4.1.1.2740"); or no path, with the plain-words `problem`
    and the few words `short` for a game card."""


class Availability(namedtuple('Availability', 'ok routes reason short')):
    """Can a game use FSR 4.1.1: `routes` are the ways ('direct', 'optiscaler'), `reason` is why not."""

    @property
    def card_text(self):
        return f'{NAME}: ' + ('yes' if self.ok else f'no, {self.short}')


# --- the DLL ------------------------------------------------------------------------------------------------------

def validate(path):
    """The file version of an FSR 4 DLL the user picked; HelixSRError, saying why, when it is not one. Signed or not:
    the community builds for RX 6000 are unsigned."""
    name = Path(path).name
    try:
        info = pe.inspect(path)
    except pe.PEError as error:
        raise HelixSRError(f'{name} is not a Windows DLL ({error}).')
    if not info.is_64bit:
        raise HelixSRError(f'{name} is not a 64-bit DLL.')
    if 'ffxCreateContext' not in info.exports:
        raise HelixSRError(f'{name} does not export ffxCreateContext, so it is not an FSR (FidelityFX) DLL.')
    if not info.version.startswith('4'):
        raise HelixSRError(f"{name} has file version {info.version or 'none'}; an FSR 4 DLL's version starts with 4.")
    return info.version


def user_dll(data_dir):
    """The DLL the user picked and AMDLSS kept, or None (also when the kept file no longer checks out)."""
    path = Path(data_dir) / USER_FOLDER / DLL
    try:
        validate(path)
    except HelixSRError:
        return None
    return path


def choose_dll(path, data_dir):
    """Checks the DLL at `path` and keeps a copy in the data folder; returns its file version."""
    version = validate(path)
    folder = Path(data_dir) / USER_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (DLL + '.tmp')
    shutil.copyfile(path, temporary)
    os.replace(temporary, folder / DLL)
    return version


def forget_dll(data_dir):
    (Path(data_dir) / USER_FOLDER / DLL).unlink(missing_ok=True)


def sha256_of(path):
    return helixsr.sha256_of(path)


def _amd_dll_ok(path):
    stat = os.stat(path)
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _VERIFIED:
        _VERIFIED[key] = stat.st_size == AMD_SIZE and sha256_of(path) == AMD_SHA256
    return _VERIFIED[key]


def license_of(helixsr_folder):
    """AMD's license file in the HelixSR folder: it travels with every AMD DLL AMDLSS installs."""
    return Path(helixsr_folder) / FOLDER / LICENSE if helixsr_folder else None


def find_source(gpu_class, helixsr_folder, data_dir):
    """The DLL to use for this card. A picked DLL wins on RX 9000, 7000 and 6000; AMD's own is for RX 9000 and 7000;
    any other card has none."""
    if gpu_class == gpu.OTHER:
        return Source(None, '', "Your graphics card can't run FSR 4; AMD's DLL would run FSR 3.1.5.", 'your card')
    mine = user_dll(data_dir)
    if mine:
        return Source(mine, f'your DLL {pe.inspect(mine).version}', '', '', True)
    if gpu_class == gpu.RDNA2:
        return Source(None, '', 'AMD has no FSR 4 DLL for RX 6000 cards. Press Choose FSR 4 DLL and pick one you have.',
                      'needs your DLL')
    if not helixsr_folder:
        return Source(None, '', "HelixSR is not installed, so AMD's FSR 4 DLL is missing.", 'no DLL')
    path = Path(helixsr_folder) / FOLDER / DLL
    try:
        good = _amd_dll_ok(path)
    except OSError:
        return Source(None, '', "The HelixSR folder has no AMD FSR 4 DLL (amd-fsr).", 'no DLL')
    if not good:
        return Source(None, '', "AMD's FSR 4 DLL in the HelixSR folder has changed (its SHA-256 differs from the one "
                                'AMDLSS expects), so AMDLSS will not use it.', 'DLL changed')
    return Source(path, f"AMD's DLL {AMD_VERSION}", '', '')


# --- which games --------------------------------------------------------------------------------------------------

_HIDDEN = {gamelist.UNSAFE_FOLDER: ('AMDLSS will not change this folder: it covers other games.', 'folder'),
           gamelist.NO_UPSCALER: (f'The game has {gamelist.NO_UPSCALER}.', 'no FSR 3.1, DLSS or XeSS')}


def availability(game, source):
    """Can `game` use FSR 4.1.1 with `source`'s DLL? In this order: the folder is refused, anti-cheat files are there,
    the card has no DLL for it, the game has nothing to replace. The ways in are the drop-in (the game has an FSR
    3.1 DLL) and OptiScaler (DLSS, XeSS or FSR 2 inputs)."""
    if game.reason:
        reason, short = _HIDDEN.get(game.reason, (f'AMDLSS cannot use this game: {game.reason}.', 'game not usable'))
        return Availability(False, (), reason, short)
    if game.anticheat:
        return Availability(False, (), f"Anti-cheat found ({', '.join(game.anticheat)}): replacing game files can get "
                                       'you banned.', 'anti-cheat')
    if not source.path:
        return Availability(False, (), source.problem, source.short)
    routes = (('direct',) if game.fsr_targets else ()) + \
        (('optiscaler',) if game.inputs and (game.can_opti or game.route == 'optiscaler') else ())
    if not routes:
        return Availability(False, (), 'The game has no FSR 3.1, DLSS or XeSS for FSR 4.1.1 to replace.',
                            _HIDDEN[gamelist.NO_UPSCALER][1])
    return Availability(True, routes, '', 'yes')
