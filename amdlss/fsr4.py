# SPDX-License-Identifier: GPL-2.0-or-later
"""FSR 4.1.1 as a per-game upscaler next to HelixSR.

HelixSR ships AMD's official FidelityFX upscaler DLL (amd_fidelityfx_upscaler_dx12.dll, file version 4.1.1.2740,
redistributed under AMD's license, which is copied beside every AMD DLL AMDLSS installs). It holds FSR 4 for RDNA 4
(FP8) and for RDNA 3 desktop cards (INT8), and FSR 3.1.5 for every other card. RDNA 2 (RX 6000) is not covered:
those cards need a community DLL that the user picks once and AMDLSS does not download. Where the DLL lives depends
on the release: HelixSR 1.9.0.2 built it into the HelixSR folder's amd-fsr, and 1.9.0 ships it in the HelixSR Scaler
package (OptiScaler\\amd_fidelityfx_upscaler_dx12.dll) — amd_dll() reads both.

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

from . import VERSION, framegen, gpu, helixsr, host, pe, scaler
from . import games as gamelist
from .helixsr import HelixSRError

DLL = 'amd_fidelityfx_upscaler_dx12.dll'
LICENSE = 'FidelityFX_v2_LICENSE.md'
FOLDER = 'amd-fsr'                     # in a pre-1.9.0 HelixSR folder: AMD's DLL and its license
PACKAGE_DLL = Path('OptiScaler') / DLL      # in the HelixSR Scaler package (1.9.0): AMD's DLL
PACKAGE_LICENSE = Path('Licenses') / LICENSE  # and its license, beside the package's other ones
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


def license_of(helixsr_folder, data_dir=None):
    """AMD's license file: it travels with every AMD DLL AMDLSS installs (from the HelixSR folder, or the HelixSR
    Scaler package where HelixSR 1.9.0 keeps AMD's DLL). None when neither holds it."""
    _dll, license_file = amd_dll(helixsr_folder, data_dir)
    return license_file if license_file is not None and license_file.is_file() else None


def amd_dll(helixsr_folder, data_dir):
    """(DLL, license) for AMD's official FSR 4 DLL: from the HelixSR folder (HelixSR 1.9.0.2 built it into
    amd-fsr), else from the HelixSR Scaler package (1.9.0 drops its own build and ships AMD's DLL under OptiScaler\\
    with its license under Licenses\\). (None, None) when neither is there."""
    legacy = Path(helixsr_folder) / FOLDER if helixsr_folder else None
    if legacy is not None and (legacy / DLL).is_file():
        return legacy / DLL, legacy / LICENSE
    package = scaler.locate(data_dir, os.environ.get('AMDLSS_SCALER', '')) if data_dir else None
    if package is not None and (package / PACKAGE_DLL).is_file():
        return package / PACKAGE_DLL, package / PACKAGE_LICENSE
    return None, None


def find_source(gpu_class, helixsr_folder, data_dir):
    """The DLL to use for this card. A picked DLL wins on RX 9000, 7000 and 6000; AMD's own is for RX 9000 and 7000;
    any other card has none."""
    if gpu_class == gpu.OTHER:
        return Source(None, '', "Your graphics card can't run FSR 4; AMD's DLL would run FSR 3.1.5.", 'your card')
    mine = user_dll(data_dir)
    if mine:
        return Source(mine, f'your DLL {pe.inspect(mine).version}', '', '', True)
    if gpu_class == gpu.RDNA2:
        return Source(None, '', "AMD has no FSR 4 DLL for RX 6000 cards, and AMDLSS found no community one (FSR 4.1.1b "
                      "or 4.0.2c INT8) in your games, Downloads, Desktop or Documents. Put it in one of those and "
                      "press Rescan, or press Choose FSR 4 DLL.", 'needs your DLL')
    if not helixsr_folder:
        return Source(None, '', "HelixSR is not installed, so AMD's FSR 4 DLL is missing.", 'no DLL')
    path, _license = amd_dll(helixsr_folder, data_dir)
    if path is None:
        return Source(None, '', "AMD's FSR 4 DLL is missing: the HelixSR Scaler package that carries it (HelixSR "
                                '1.9.0 does not build amd-fsr) is not downloaded: the download failed, or it '
                                'never ran. Press Install HelixSR to get it.', 'no DLL')
    try:
        good = _amd_dll_ok(path)
    except OSError:
        return Source(None, '', "The HelixSR folder has no AMD FSR 4 DLL (amd-fsr).", 'no DLL')
    if not good:
        return Source(None, '', f"AMD's FSR 4 DLL at {path} has changed (its SHA-256 differs from the one AMDLSS "
                                'expects), so AMDLSS will not use it.', 'DLL changed')
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
           gamelist.NO_UPSCALER: (f'The game has {gamelist.NO_UPSCALER}.', 'nothing to replace')}


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
    if license_file is not None and (loader or not source.user) and not (folder / LICENSE).exists():
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


# --- the OptiScaler route: point OptiScaler.ini at the FSR 4 DLL --------------------------------------------------
# HelixSR installs OptiScaler and sets [Libraries] FfxDx12Path and FfxDx12SRPath to its own two copies of one DLL (its
# installer notes that one file in both roles hangs the game when the upscaler starts). The same goes here: the FSR 4
# DLL is copied twice into <game .exe folder>\HelixSR\fsr4\ and each path points at one copy. [FSR] UpscalerIndex is left
# alone: which entry of AMD's DLL is FSR 4 on each card is not proven without that card, so the user picks it in
# OptiScaler's menu. framegen keeps the values HelixSR wrote, so switching back restores them.

OPTI_FOLDER = 'fsr4'
OPTI_COPIES = ('fsr4.dll', 'fsr4_upscaler.dll')   # FfxDx12Path, FfxDx12SRPath


def ini_path(path):
    """A path the way OptiScaler.ini holds it: a Windows path (under Proton the Linux root is Z:), as HelixSR writes."""
    resolved = str(Path(path).resolve())
    return resolved if host.WINDOWS else 'Z:' + resolved.replace('/', '\\')


def opti_folder(exe_folder):
    return Path(exe_folder) / 'HelixSR' / OPTI_FOLDER


def libraries(exe_folder):
    """The framegen Setting for the two library paths, as they read when FSR 4 is in use in this game."""
    folder = opti_folder(exe_folder)
    return framegen.Setting(b'Libraries', {'FfxDx12Path': ini_path(folder / OPTI_COPIES[0]),
                                           'FfxDx12SRPath': ini_path(folder / OPTI_COPIES[1])}, '#fsr4')


def in_use(exe_folder):
    """True when the game's OptiScaler.ini points at the FSR 4 copies."""
    return framegen.is_on(exe_folder, libraries(exe_folder))


def use_in_optiscaler(exe_folder, source, license_file, data_dir):
    """Copies the FSR 4 DLL twice next to HelixSR's files in the game and points OptiScaler.ini at the copies. Returns
    True when anything changed. A failure leaves no copies that were made for it."""
    folder = opti_folder(exe_folder)
    fresh = not folder.exists()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        wanted = [(source.path, folder / name) for name in OPTI_COPIES]
        if not source.user:
            wanted.append((license_file, folder / LICENSE))
        changed = False
        for origin, target in wanted:
            if not (target.is_file() and target.stat().st_size == Path(origin).stat().st_size
                    and sha256_of(target) == sha256_of(origin)):
                _put(origin, target)
                changed = True
        return framegen.apply(exe_folder, True, data_dir, libraries(exe_folder)) or changed
    except (OSError, HelixSRError) as error:
        if fresh:
            shutil.rmtree(folder, ignore_errors=True)
        if isinstance(error, HelixSRError):
            raise
        raise HelixSRError(f'FSR 4.1.1 was not set up: {error}')


def restore_optiscaler(exe_folder, data_dir):
    """Puts the library paths HelixSR wrote back and deletes the FSR 4 copies. Returns True when anything changed."""
    changed = framegen.apply(exe_folder, False, data_dir, libraries(exe_folder))
    folder = opti_folder(exe_folder)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
        try:
            folder.parent.rmdir()   # only if HelixSR's folder holds nothing else
        except OSError:
            pass
        changed = True
    return changed


# --- what is installed in a game ---------------------------------------------------------------------------------

def label_of(path):
    """"AMD's DLL 4.1.1.2740" for AMD's official FSR 4 DLL, else "your DLL <file version>"; '' when unreadable."""
    try:
        if os.stat(path).st_size == AMD_SIZE and sha256_of(path) == AMD_SHA256:
            return f"AMD's DLL {AMD_VERSION}"
        return f"your DLL {pe.inspect(path).version or '?'}"
    except (OSError, pe.PEError):
        return ''


def recognise(path):
    """Which of AMD's two DLLs a file is, by size and SHA-256 ("AMD's FSR 4.1.1 DLL", "AMD's FSR loader"), else ''."""
    try:
        size = os.stat(path).st_size
        for expected, digest, name in ((AMD_SIZE, AMD_SHA256, f"AMD's FSR {AMD_VERSION[:5]} DLL"),
                                       (LOADER_SIZE, LOADER_SHA256, "AMD's FSR loader")):
            if size == expected and sha256_of(path) == digest:
                return name
    except OSError:
        pass
    return ''


def describe(game):
    """Which FSR 4 DLL runs in `game` ("AMD's DLL 4.1.1.2740", "your DLL 4.1.1.9"), '' when FSR 4.1.1 is not installed."""
    if game.upscaler != 'fsr4':
        return ''
    if game.state == 'fsr4':
        for folder in dropin_folders(target_folders(game)):
            try:
                label = json.loads((Path(folder) / MARKER).read_text(encoding='utf-8')).get('dll')
            except (OSError, ValueError, AttributeError):
                continue
            if label:
                return str(label)
        return 'unknown DLL'
    return label_of(opti_folder(game.exe_folder) / OPTI_COPIES[1]) or 'unknown DLL'

def target_folders(game):
    """The folders of the game's FSR 3.1 DLLs, once each: where a drop-in is recorded."""
    return list(dict.fromkeys(str(Path(target).parent) for target in game.fsr_targets))


def mark(game):
    """Sets `game.upscaler`, and `game.state` for a drop-in, from the files on disk: HelixSR does not know about a
    drop-in AMDLSS made, and sees an OptiScaler install the same whichever DLL it is pointed at."""
    scaler.mark(game)   # an AMDLSS Scaler install first: the OptiScaler route's state comes from its record
    game.upscaler = ''
    if game.state == 'none' and dropin_folders(target_folders(game)):
        game.state, game.upscaler = 'fsr4', 'fsr4'
    elif game.state == 'optiscaler' and game.exe_folder and in_use(game.exe_folder):
        game.upscaler = 'fsr4'
    return game


# --- what the window says ----------------------------------------------------------------------------------------

LOADER_NOTE = ("For this game AMDLSS also downloads AMD's FSR loader (26 KB, FidelityFX SDK 2.3.0) once from "
               "github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK and checks its SHA-256.")


def plan_text(game, route, source, data_dir):
    """What installing FSR 4.1.1 will do, before the user agrees."""
    if route == 'direct':
        files = ''.join(f'\n  {path}' for path in game.fsr_targets[:3])
        text = (f"AMDLSS replaces the game's FSR 3.1 DLL with AMD's {NAME} ({source.label}) and keeps the game's own "
                f"file next to it as *.original.dll:{files}")
        if game.state == 'direct':
            text += '\n\nHelixSR is removed first.'
        if any(Path(target).name.lower() == OLD_DLL for target in game.fsr_targets) and \
                not (Path(data_dir) / USER_FOLDER / LOADER).is_file():
            text += '\n\n' + LOADER_NOTE
    else:
        where = game.exe_folder or "the game's .exe folder"
        text = (f"AMDLSS downloads HelixSR Scaler {scaler.VERSION} (about {scaler.ZIP_SIZE >> 20} MB, checked by "
                f"SHA-256) and puts OptiScaler with HelixSR built in next to the game's .exe ({where}), then "
                f"points OptiScaler at {NAME} ({source.label}). Files it replaces are kept in the HelixSR folder's "
                f"{scaler.BACKUP} and put back by Remove.")
    if not source.user:
        text += "\n\nAMD's license notice (FidelityFX_v2_LICENSE.md) is copied next to AMD's DLL."
    return f'{text}\n\n{gamelist.WARNINGS}\n\nInstall into {game.name}?'


def after_text(game, route, source, generator='', fallback=''):
    """What to do in the game once FSR 4.1.1 is installed. `generator` is the frame generation AMDLSS left switched
    on (amdlss.framegen's AMD), or '' for none; `fallback` says why HelixSR's own DLSS 4 frame generation, which an
    earlier AMDLSS switched on in this game, was turned off."""
    text = f"{NAME} is installed in {game.name} ({source.label}).\n\n"
    if route == 'direct':
        text += "In the game's graphics settings choose AMD FSR."
    else:
        text += ("In the game's graphics settings choose DLSS (or XeSS if that is all it offers); it runs "
                 f"{NAME}. Press Insert in the game to open OptiScaler's menu, then Upscaling > Upscalers > FFX "
                 f"Upscaler, and pick {NAME}: the menu header names the version in use. To see it on screen, turn on "
                 "the FSR 4 watermark in that menu.")
        if generator == framegen.AMD:
            text += '\n\n' + gamelist.FRAMEGEN_HINT
    if fallback:
        text += f'\n\nDLSS 4 frame generation is off: {fallback}'
    return text + ('\n\nIf it looks unchanged in game, press Collect logs and send the zip.'
                   '\n\nThe Remove button puts the original files back.')


# --- install, switch and remove ----------------------------------------------------------------------------------
# `helix` is an amdlss.helixsr.HelixSR. Whatever is installed is removed before the other upscaler goes in, except
# on the OptiScaler route, where OptiScaler stays and only the library paths change.

def _say(on_line, notes):
    for note in notes:
        if on_line:
            on_line(note, True)


def _relisted(helix, game, extras):
    after = gamelist.match_game(helix.list_games(extras), game)
    if after is None:
        raise HelixSRError(f'{game.name} disappeared from the list after the change: refresh the list')
    return mark(after)


def install_game(helix, game, extras, want, route, source, license_file, data_dir, on_line=None, progress=None):
    """Installs or updates the upscaler `want` ('helixsr' or 'fsr4') in `game` and returns the game as listed
    afterwards. `route` is the user's pick when both routes work and nothing is installed, else None. For 'fsr4',
    `game.fsr4` (the Availability) says whether the game can use it and by which routes."""
    if want == 'helixsr':
        if game.state != 'fsr4' and game.upscaler != 'fsr4':
            if (route or game.route) == 'optiscaler':
                return scaler.install_game(helix, game, extras, data_dir, on_line, progress)
            return mark(helix.install(game, extras, on_line))
        fresh = mark(helix.guard(game, extras))
        if fresh.state == 'fsr4':
            _say(on_line, remove_dropin(target_folders(fresh)))
            return mark(helix.install(fresh, extras, on_line))
        restore_optiscaler(fresh.exe_folder, data_dir)
        if not scaler.installed(fresh.exe_folder):
            _say(on_line, ["HelixSR's earlier install stays; its Update replaces it with HelixSR Scaler."])
        return mark(fresh)
    if not (game.fsr4 and game.fsr4.ok):
        raise HelixSRError(game.fsr4.reason if game.fsr4 else f'{NAME} has not been checked for {game.name}.')
    fresh = mark(helix.guard(game, extras))
    if fresh.state in ('fsr4', 'direct'):
        way = 'direct'
    elif fresh.state == 'optiscaler':
        way = 'optiscaler'
    else:
        way = route or game.fsr4.routes[0]
    if way not in game.fsr4.routes:
        raise HelixSRError(f'{game.name} cannot use {NAME} by the {way} route.')
    if fresh.state == 'fsr4':
        _say(on_line, remove_dropin(target_folders(fresh)))
    elif fresh.state == 'direct':
        helix.remove(game, extras, on_line)
    if way == 'direct':
        install_dropin(_relisted(helix, game, extras).fsr_targets, source, data_dir, license_file, progress)
    else:
        after = scaler.install_game(helix, game, extras, data_dir, on_line, progress)
        try:
            use_in_optiscaler(after.exe_folder, source, license_file, data_dir)
        except HelixSRError as error:
            raise HelixSRError(f'HelixSR Scaler is installed in {game.name}, but {NAME} could not be set up: {error}')
    return _relisted(helix, game, extras)


def remove_game(helix, game, extras, data_dir, on_line=None):
    """Removes whichever upscaler is installed in `game`, putting every file back; returns the game as listed after."""
    if game.state == 'fsr4':
        fresh = mark(helix.guard(game, extras))
        if fresh.state == 'fsr4':
            _say(on_line, remove_dropin(target_folders(fresh)))
            return _relisted(helix, game, extras)
    if game.upscaler == 'fsr4':
        helix.guard(game, extras)
        restore_optiscaler(game.exe_folder, data_dir)
    if scaler.installed(game.exe_folder):
        fresh = mark(helix.guard(game, extras))
        _say(on_line, scaler.remove(fresh.exe_folder))
        fresh.state, fresh.upscaler = 'none', ''
    else:
        fresh = helix.remove(game, extras, on_line)
    if game.exe_folder:
        framegen.forget(game.exe_folder, data_dir)
    return mark(fresh)
