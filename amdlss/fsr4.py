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
import json
import os
import shutil
from collections import namedtuple
from pathlib import Path

from . import VERSION, gpu, helixsr, pe
from . import games as gamelist
from .helixsr import HelixSRError

DLL = 'amd_fidelityfx_upscaler_dx12.dll'
LICENSE = 'FidelityFX_v2_LICENSE.md'
FOLDER = 'amd-fsr'                     # in the HelixSR folder: AMD's DLL and its license
USER_FOLDER = 'fsr4'                   # in the AMDLSS data folder: the DLL the user picked
ORIGIN = 'origin.txt'                  # beside it: the path it came from
AMD_VERSION = '4.1.1.2740'
AMD_SIZE = 28761864
AMD_SHA256 = 'd0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46'
NAME = 'FSR 4.1.1'
MARKER = helixsr.FSR4_MARKER           # AMDLSS's record beside the game's DLL: what it moved and what it created
OLD_DLL = 'amd_fidelityfx_dx12.dll'    # the FSR 3.1 loader-style DLL most games ship
# AMD's FidelityFX SDK v2.3.0 loader: interface-compatible with OLD_DLL, it loads DLL from beside itself
LOADER = 'amd_fidelityfx_loader_dx12.dll'
LOADER_URL = ('https://raw.githubusercontent.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/v2.3.0/Kits/FidelityFX/'
              'signedbin/' + LOADER)
LOADER_SIZE = 26376
LOADER_SHA256 = 'e2d85aa05a9bd9ed8b38935fdf5199372cca6f74c12015143bb6f945ee1608aa'

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
    (folder / ORIGIN).write_text(str(path), encoding='utf-8')
    return version


def forget_dll(data_dir):
    for name in (DLL, ORIGIN):
        (Path(data_dir) / USER_FOLDER / name).unlink(missing_ok=True)


def user_origin(data_dir):
    """Where the kept DLL came from (the path it was found or picked at), '' when unknown."""
    try:
        return (Path(data_dir) / USER_FOLDER / ORIGIN).read_text(encoding='utf-8').strip()
    except OSError:
        return ''


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


# --- finding the community DLL for RX 6000 -----------------------------------------------------------------------
# AMD's DLL has no RDNA 2 path, so an RX 6000 owner needs a community build (unsigned, no license, not downloaded by
# AMDLSS). Testers keep it where HelixSR's README sends them (a my-fsr folder) or just download it, so AMDLSS looks in
# those places instead of asking. AMD's own build is never a candidate: it is what rdna3 and rdna4 use anyway.

DEPTH = 4                 # folders below a searched root
MAX_VISITED = 20000       # files and folders looked at per root, so a huge tree is not walked for minutes
SKIP_DIRS = frozenset(('node_modules', '.git', 'steamapps', 'appdata', 'windows', 'program files',
                       'program files (x86)', 'history', 'covers', 'logs'))
ARCHIVES = ('.7z', '.zip')
Found = namedtuple('Found', 'path version')
Discovery = namedtuple('Discovery', 'found hint')


def _walk(root):
    """Every file under `root` within DEPTH folders, sorted by name, at most MAX_VISITED entries looked at."""
    todo, seen = [(Path(root), 0)], 0
    while todo:
        folder, level = todo.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > MAX_VISITED:
                return
            if entry.is_file(follow_symlinks=False):
                yield Path(entry.path)
            elif level < DEPTH and entry.name.lower() not in SKIP_DIRS and entry.is_dir(follow_symlinks=False):
                todo.append((Path(entry.path), level + 1))


def _is_candidate(path):
    name = path.name.lower()
    return name == DLL or (name.endswith('.dll') and path.parent.name.lower() == 'my-fsr')


def _community(path):
    """Found for an FSR 4 DLL that is not AMD's official one, else None."""
    try:
        if os.stat(path).st_size == AMD_SIZE and sha256_of(path) == AMD_SHA256:
            return None
        return Found(path, validate(path))
    except (OSError, HelixSRError):
        return None


def _game_files(game):
    exe = Path(game.exe_folder) if game.exe_folder else None
    if exe:
        yield exe / DLL
        yield from sorted((exe / 'HelixSR' / 'my-fsr').glob('*.dll'))
    for target in game.fsr_targets:
        yield Path(target).parent / DLL


def discover(games, user_dirs, data_dir):
    """The best community FSR 4 DLL under the games' folders, `user_dirs` (Downloads, Desktop, Documents) and the AMDLSS
    data folder: the highest file version, the first found on a tie. With none, a hint when a .7z or .zip that looks
    like an FSR 4 download sits in `user_dirs`: AMDLSS never unpacks archives."""
    paths = [path for game in games for path in _game_files(game)]
    paths += [path for root in (*user_dirs, data_dir) for path in _walk(root) if _is_candidate(path)]
    unique = {}
    for path in paths:
        unique.setdefault(os.path.normcase(os.path.realpath(path)), path)
    found = [one for one in map(_community, unique.values()) if one]
    best = max(found, key=lambda one: helixsr.version_key(one.version) or (), default=None)
    return Discovery(best, '' if best else _archive_hint(user_dirs))


def _archive_hint(user_dirs):
    for root in user_dirs:
        try:
            names = sorted(entry.name for entry in os.scandir(root) if entry.is_file())
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if low.endswith(ARCHIVES) and 'fsr' in low and '4' in low:
                return f'Unpack {name} and AMDLSS will find the DLL.'
    return ''


def auto_find(games, user_dirs, data_dir):
    """Keeps the best DLL discover() finds, unless a DLL is already kept (a saved choice is never swapped behind the
    user's back). Returns the Discovery; its `found` is None when nothing was kept."""
    if user_dll(data_dir):
        return Discovery(None, '')
    result = discover(games, user_dirs, data_dir)
    if result.found:
        choose_dll(result.found.path, data_dir)
    return result


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


# --- the drop-in: AMDLSS swaps the game's FSR DLL itself ----------------------------------------------------------
# A game with an FSR 3.1 DLL loads either amd_fidelityfx_upscaler_dx12.dll (replaced by FSR 4's DLL as it is) or
# amd_fidelityfx_dx12.dll (replaced by AMD's loader, with FSR 4's DLL beside it). The game's own file is renamed to
# *.original.dll, as HelixSR does, and a record beside it (MARKER) lists every file moved and created with SHA-256s, so
# Remove puts back exactly that and keeps a file the user replaced by hand.

def _original(path):
    return path.with_name(path.stem + '.original.dll')


def _put(source, target):
    """Copies `source` to `target` through a temporary name, so a half-written DLL never has the real name."""
    temporary = target.with_name(target.name + '.amdlss')
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def loader_path(data_dir, progress=None):
    """AMD's loader in the data folder, downloaded and checked against its pin the first time a game needs it."""
    path = Path(data_dir) / USER_FOLDER / LOADER
    if path.is_file() and path.stat().st_size == LOADER_SIZE and sha256_of(path) == LOADER_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(LOADER + '.part')
    helixsr.download(part, progress, LOADER_URL, LOADER_SIZE)
    if part.stat().st_size != LOADER_SIZE or sha256_of(part) != LOADER_SHA256:
        part.unlink(missing_ok=True)
        raise HelixSRError("the downloaded AMD loader does not match the pinned SHA-256; it was deleted")
    os.replace(part, path)
    return path


def _preflight(folder, targets):
    if (folder / MARKER).exists():
        raise HelixSRError(f'FSR 4.1.1 is already installed in {folder}: remove it first.')
    if (folder / helixsr.MARKER_FILE).exists():
        raise HelixSRError(f'HelixSR is installed in {folder}: remove HelixSR first.')
    names = {target.name.lower(): target for target in targets}
    if OLD_DLL in names and DLL not in names and (folder / DLL).exists():
        names[DLL] = folder / DLL    # the file beside the old loader is kept as an original too
    for target in names.values():
        if _original(target).exists():
            raise HelixSRError(f'{_original(target).name} already exists in {folder}: AMDLSS will not overwrite it.')


def _install_folder(folder, targets, source, loader, license_file, record):
    names = {target.name.lower(): target for target in targets}
    moved, created = record['moved'], record['created']

    def move(path):
        target = _original(path)
        sha = sha256_of(path)
        os.replace(path, target)
        moved.append({'name': path.name, 'to': target.name, 'sha256': sha})

    def create(name, origin):
        _put(origin, folder / name)
        created.append({'name': name, 'sha256': sha256_of(folder / name)})

    if DLL in names:
        move(names[DLL])
    elif (folder / DLL).exists():
        move(folder / DLL)   # beside the old loader: kept as an original too
    if OLD_DLL in names:
        move(names[OLD_DLL])
        create(names[OLD_DLL].name, loader)
    create(names[DLL].name if DLL in names else DLL, source.path)
    if (loader or not source.user) and not (folder / LICENSE).exists():
        create(LICENSE, license_file)


def _unwind(folder, record, force=False):
    """Undoes a record: files AMDLSS created are deleted (one the user replaced is kept and noted, unless `force`),
    then the files it moved come back. Returns the notes."""
    notes, kept = [], set()
    for item in reversed(record.get('created', [])):
        path = folder / item['name']
        if not path.is_file():
            continue
        if force or sha256_of(path) == item['sha256']:
            path.unlink()
        else:
            kept.add(item['name'])
            notes.append(f"kept {path}: it was changed after AMDLSS put it there")
    for item in reversed(record.get('moved', [])):
        original, path = folder / item['to'], folder / item['name']
        if not original.exists():
            notes.append(f"{item['to']} is missing in {folder}: {item['name']} could not be put back")
        elif path.exists():
            if item['name'] not in kept:
                notes.append(f"kept {path}; the game's own file is still there as {item['to']}")
        else:
            os.replace(original, path)
    return notes


def dropin_folders(folders):
    """The folders (of `folders`) that hold an FSR 4.1.1 record, in the order given."""
    return [str(folder) for folder in folders if (Path(folder) / MARKER).is_file()]


def install_dropin(targets, source, data_dir, license_file, progress=None):
    """Swaps the DLL at each of `targets` for FSR 4.1.1 (see the section note) and writes a record in each folder.
    Everything is checked before anything moves, and a failure halfway puts every file back. HelixSRError for a
    refusal or a failure; nothing is left changed."""
    folders = {}
    for target in map(Path, targets):
        folders.setdefault(target.parent, []).append(target)
    for folder, here in folders.items():
        _preflight(folder, here)
    loader = loader_path(data_dir, progress) if any(t.name.lower() == OLD_DLL for t in map(Path, targets)) else None
    records = {}
    try:
        for folder, here in folders.items():
            records[folder] = {'amdlss': VERSION, 'upscaler': NAME, 'dll': source.label, 'moved': [], 'created': []}
            _install_folder(folder, here, source, loader, license_file, records[folder])
        for folder, record in records.items():
            (folder / MARKER).write_text(json.dumps(record, indent=1), encoding='utf-8')
    except (OSError, HelixSRError) as error:
        for folder, record in records.items():
            (folder / MARKER).unlink(missing_ok=True)
            _unwind(folder, record, force=True)
        raise HelixSRError(f'FSR 4.1.1 was not installed ({error}); the game\'s files are as they were.')


def remove_dropin(folders):
    """Puts back what install_dropin moved in each of `folders` and deletes what it created. Returns notes about
    files it kept because the user changed them."""
    notes = []
    for folder in map(Path, folders):
        try:
            record = json.loads((folder / MARKER).read_text(encoding='utf-8'))
            if not isinstance(record, dict):
                raise ValueError('not an object')
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            raise HelixSRError(f"AMDLSS's FSR 4.1.1 record in {folder} cannot be read ({error}); nothing was changed.")
        notes += _unwind(folder, record)
        (folder / MARKER).unlink(missing_ok=True)
    return notes
