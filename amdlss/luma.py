# SPDX-License-Identifier: GPL-2.0-or-later
"""Luma Unreal Engine: a DLSS call for an Unreal game that ships no upscaler at all.

AMDLSS's other two routes need the game to call an upscaler already: the FSR drop-in replaces the game's FSR 3.1
DLL, and HelixSR Scaler (OptiScaler) replaces the DLSS, XeSS or FSR 2 call the game makes. A game that never calls
one keeps its engine's TAA, and OptiScaler has nothing to intercept. Luma Framework's Unreal Engine mod (Filoppi)
replaces the TAA pass of an Unreal Engine game with a DLSS (DLAA) call, so the call OptiScaler intercepts is the one
Luma makes, and HelixSR runs it on an AMD card.

This module:

  - fetches the pinned Luma UE release zip (resumable, SHA-256 checked, zip-slip checked) and unpacks it;
  - puts its files next to the game's .exe the way OptiScaler's own wiki pairs the two (page "Luma Unreal Engine
    Luma UE", tested with OptiScaler 0.9.4, the version inside HelixSR Scaler): Luma's dxgi.dll becomes
    ReShade64.dll, OptiScaler itself takes the name dxgi.dll, and OptiScaler.ini is set to load it
    ([Plugins] LoadReshade=true). Luma goes in first, so its dxgi.dll is out of the way when the Scaler install
    picks its own name;
  - installs HelixSR Scaler through amdlss.scaler; what this module placed is recorded in
    HelixSR\\amdlss-luma.json (moved and created files with SHA-256s), so Remove puts every file back exactly as
    amdlss.scaler's own record does for its files.

Nothing is bundled or redistributed: the zip comes from Luma's own release page on the player's PC. Its licence is a
custom MIT ("Commercial usage is possible but only after asking permission to the authors"); AMDLSS is free and is
not the one shipping or reusing their code. Luma UE needs DirectX 11, which AMDLSS cannot tell from a game's files:
plan_text and the after-install text say so instead of promising anything.
"""
import json
import os
import shutil
from pathlib import Path

from . import VERSION as APP_VERSION
from . import framegen, games as gamelist, helixsr, scaler
from .helixsr import HelixSRError

NAME = 'Luma UE'                              # what the window calls the mod
FULL_NAME = "Luma Framework's Unreal Engine mod"
BUILD = '749'
TAG = f'latest-{BUILD}'
ZIP_NAME = 'Luma-Unreal_Engine.zip'
ZIP_URL = f'https://github.com/Filoppi/Luma-Framework/releases/download/{TAG}/{ZIP_NAME}'
ZIP_SIZE = 37284374
ZIP_SHA256 = 'a357ce98700262064c280c6b9d56852e2876d22a0ccd04f18cc169d32213b2c2'
TOP = f'Luma-Unreal-Engine-{BUILD}'           # the folder the zip is unpacked into (the zip has no top folder)
PROJECT_URL = 'https://github.com/Filoppi/Luma-Framework'
WIKI_URL = f'{PROJECT_URL}/wiki#unreal-engine'
PAIRING_URL = 'https://github.com/optiscaler/OptiScaler/wiki/Luma-Unreal-Engine-Luma-UE'
EXTRA = 'AMDLSS_LUMA'                         # the data folder the user can put the unpacked zip in (scaler: AMDLSS_SCALER)
ADDON = 'Luma-Unreal Engine.addon'            # Luma's ReShade addon, and the folder its shaders live in
SHADERS = 'Luma'
DLSS = 'nvngx_dlss.dll'                       # the DLSS runtime Luma calls (NVIDIA's, shipped by Luma's zip)
SCALER_NAME = 'dxgi.dll'                      # what OptiScaler is called next to the game's .exe (the wiki's Method 1)
RESHADE = 'ReShade64.dll'                     # what Luma's dxgi.dll becomes: the name OptiScaler loads it under
RENAME = {SCALER_NAME: RESHADE}
RECORD = 'amdlss-luma.json'                   # AMDLSS's record, inside the game's HelixSR folder
SETTINGS = 'Luma.ini'                         # Luma's own settings file, written by its overlay (never by AMDLSS)


def install_folder(data_dir):
    """The folder that holds the unpacked zip once fetch() has run."""
    return Path(data_dir) / TOP


def extracted(target):
    """True when `target` holds an unpacked Luma UE zip (its loader, its addon and its shaders)."""
    folder = install_folder(target)
    return ((folder / SCALER_NAME).is_file() and (folder / ADDON).is_file()
            and (folder / SHADERS).is_dir())


def locate(data_dir, override=''):
    """The unpacked Luma UE package to use, or None: `override` (AMDLSS_LUMA) when it holds one, else
    <data_dir>/Luma-Unreal-Engine-<build>."""
    for folder in ([Path(override)] if override else []) + [install_folder(data_dir)]:
        if (folder / SCALER_NAME).is_file() and (folder / ADDON).is_file() and (folder / SHADERS).is_dir():
            return folder
    return None


def fetch(target, progress=None):
    """Downloads, verifies and unpacks the pinned release into `target`; returns its folder. The same pins, zip-slip
    screening and work folder as helixsr.fetch and scaler.fetch. `progress(done, total)` is optional."""
    target = Path(target)
    work = target / '.download'
    work.mkdir(parents=True, exist_ok=True)
    part = work / (ZIP_NAME + '.part')
    helixsr.download(part, progress, ZIP_URL, ZIP_SIZE)
    if part.stat().st_size != ZIP_SIZE or helixsr.sha256_of(part) != ZIP_SHA256:
        part.unlink(missing_ok=True)
        raise HelixSRError('the downloaded Luma UE zip does not match the pinned SHA-256; it was deleted')
    helixsr.unpack(part, target)
    part.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    if not extracted(target):
        raise HelixSRError(f'the zip does not hold {TOP}/{SCALER_NAME}, {TOP}/{ADDON} and {TOP}/{SHADERS}')
    return install_folder(target)


def files(package):
    """[(source, name)] for every file of the unpacked zip: the paths Luma ships, with its dxgi.dll renamed, since
    OptiScaler loads it as ReShade64.dll and keeps dxgi.dll for itself."""
    root = Path(package)
    return [(path, RENAME.get(path.relative_to(root).as_posix(), path.relative_to(root).as_posix()))
            for path in sorted(root.rglob('*')) if path.is_file()]


# --- the record in the game ---------------------------------------------------------------------------------------

def record_path(exe_folder):
    return Path(exe_folder) / scaler.FOLDER / RECORD


def installed(exe_folder):
    """True when AMDLSS's Luma record says the injection is installed in the game. Without it the game would look
    like a plain OptiScaler install (the Scaler record alone cannot tell the two apart)."""
    return bool(exe_folder) and record_path(exe_folder).is_file()


def _load(exe_folder):
    try:
        record = json.loads(record_path(exe_folder).read_text(encoding='utf-8'))
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def _put(source, target):
    """Copies `source` to `target` through a temporary name, so a half-written file never has the real name."""
    temporary = target.with_name(target.name + '.amdlss')
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install_into(exe_folder, package, record):
    """Copies every file of the unpacked zip into `exe_folder` (its dxgi.dll as ReShade64.dll), keeping every game
    file that had to give way in the HelixSR folder's backup, and fills record['moved']/record['created']."""
    exe_folder, backup = Path(exe_folder), Path(exe_folder) / scaler.FOLDER / scaler.BACKUP
    moved, created = record['moved'], record['created']
    for source, rel in files(package):
        target = exe_folder / rel
        if target.is_file() or target.is_symlink():
            kept = backup / rel
            kept.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, kept)
            moved.append({'name': rel, 'to': f'{scaler.FOLDER}/{scaler.BACKUP}/{rel}',
                          'sha256': helixsr.sha256_of(kept)})
        target.parent.mkdir(parents=True, exist_ok=True)
        _put(source, target)
        created.append({'name': rel, 'sha256': helixsr.sha256_of(target)})


def _prune(exe_folder, folders):
    """Removes the folders an install brought or emptied, deepest first; the game's own folder is never touched."""
    root = Path(exe_folder).resolve()
    for folder in sorted((Path(f) for f in folders), key=lambda p: len(p.parts), reverse=True):
        try:
            if folder.resolve() != root:
                folder.rmdir()
        except OSError:
            pass


def _unwind(exe_folder, record, force=False):
    """Undoes a record: files AMDLSS created are deleted (one the user replaced is kept and noted, unless `force`),
    then the files it moved come back. Returns the notes."""
    notes, kept, folders = [], set(), {Path(exe_folder) / SHADERS}
    for item in reversed(record.get('created', [])):
        path = Path(exe_folder) / item['name']
        folders.add(path.parent)
        if not path.is_file():
            continue
        if force or helixsr.sha256_of(path) == item['sha256']:
            path.unlink()
        else:
            kept.add(item['name'])
            notes.append(f'kept {path}: it was changed after AMDLSS put it there')
    for item in reversed(record.get('moved', [])):
        original, path = Path(exe_folder) / item['to'], Path(exe_folder) / item['name']
        folders.add(path.parent)
        folders.add(original.parent)
        if not original.exists():
            notes.append(f"{item['to']} is missing in {exe_folder}: {item['name']} could not be put back")
        elif path.exists():
            if item['name'] not in kept:
                notes.append(f"kept {path}; the game's own file is still there as {item['to']}")
        else:
            os.replace(original, path)
    _prune(exe_folder, folders)
    return notes


def remove(exe_folder):
    """Puts back every file the Luma install replaced in `exe_folder` and deletes what it created (the record holds
    SHA-256s; a file the user changed after the install is kept and reported). Returns the notes. The HelixSR Scaler
    install is a separate record and is removed separately (remove_game does both)."""
    exe_folder = Path(exe_folder)
    record = _load(exe_folder)
    if record is None:
        return []
    notes = _unwind(exe_folder, record)
    record_path(exe_folder).unlink(missing_ok=True)
    # the record's own folder too: _unwind's prune ran while the record was still in it (a Scaler install left there
    # keeps it, since it is not empty)
    _prune(exe_folder, {Path(exe_folder) / SHADERS, Path(exe_folder) / scaler.FOLDER})
    return notes


def mark(game):
    """Sets `game.state` and `game.route` from AMDLSS's Luma record, so an injected game keeps its state (and its
    Remove) instead of reading as a plain OptiScaler install."""
    if game.exe_folder and installed(game.exe_folder):
        game.state, game.route = gamelist.INJECT, gamelist.INJECT
    return game


# --- what the window says ----------------------------------------------------------------------------------------

ROW_NOTE = (f'This game ships no upscaler of its own: AMDLSS injects a DLSS call into it with {FULL_NAME} '
            f'(experimental, DirectX 11 only), and HelixSR Scaler runs it.')


def download_text(done, total):
    """What the window says while this route's downloads run: Luma's own zip, or Scaler's package (whose pinned size
    tells the two apart)."""
    what = f'{NAME} {BUILD}' if total != scaler.ZIP_SIZE else f'HelixSR Scaler {scaler.VERSION}'
    return f'Downloading {what}: {done >> 20} of {total >> 20} MB'


def plan_text(game):
    """What installing the injection will do, before the user agrees."""
    where = game.exe_folder or "the game's .exe folder"
    return (f"AMDLSS downloads {FULL_NAME} build {BUILD} from Luma's own release page (about {ZIP_SIZE >> 20} MB, "
            f"checked by SHA-256, custom MIT licence) and puts it next to the game's .exe ({where}), with HelixSR "
            f"Scaler {scaler.VERSION} (about {scaler.ZIP_SIZE >> 20} MB, checked by SHA-256).\n\n"
            f"{FULL_NAME} replaces the game's TAA with a DLSS call, and HelixSR Scaler runs that call on your card, "
            f"the way it runs a game's own DLSS. It needs the game to run in DirectX 11; some games need a '-dx11' "
            f"launch option for that, and AMDLSS cannot check it for you.\n\n"
            f"Files it replaces are kept in the HelixSR folder's {scaler.BACKUP} and put back by Remove.\n\n"
            f'{gamelist.WARNINGS}\n\nInstall into {game.name}?')


def after_text(game):
    """What to do in the game once the injection is installed."""
    return (f"{FULL_NAME} build {BUILD} is installed in {game.name}, with HelixSR Scaler {scaler.VERSION}.\n\n"
            f"Start the game and press Home: Luma's overlay (with ReShade's) opens. Set 'Upscaler/Super Resolution' "
            f"to DLSS - the game's own menu has no upscaler entry, and this is the call HelixSR runs. Press Insert "
            f"for OptiScaler's overlay: it should name DLSS as the input.\n\n"
            f"If the frame rate is capped like Vsync, turn Vsync off in OptiScaler's overlay. If the game crashes or "
            f"shows a non-DirectX 11 device error, set DontCreateD3D12DeviceForLuma=true in its OptiScaler.ini.\n\n"
            f"Luma UE is experimental here: the pairing is the one its and OptiScaler's authors document and test, "
            f"not one AMDLSS has tested in a game.\n\n"
            f"If it looks unchanged in game, press Collect logs and send the zip."
            f"\n\nThe Remove button puts the original files back.")


# --- install and remove ------------------------------------------------------------------------------------------

def _say(on_line, notes):
    for note in notes:
        if on_line:
            on_line(note, True)


def install_game(helix, game, extras=(), data_dir=None, on_line=None, progress=None):
    """Installs (or updates) the injection in `game` and returns the game with its state set: Luma UE's files first,
    then HelixSR Scaler (amdlss.scaler, as dxgi.dll), then OptiScaler.ini's [Plugins] LoadReshade=true. The folder is
    safe, the game closed and still in HelixSR's list (helix.guard). Nothing is left changed when it fails."""
    if game.anticheat:
        raise HelixSRError(f"AMDLSS does not inject into a game with anti-cheat files ({', '.join(game.anticheat)}): "
                           f'the account can be banned.')
    fresh = helix.guard_placed(game, extras)
    if not fresh.exe_folder:
        raise HelixSRError(f"HelixSR does not know {game.name}'s .exe folder, so {NAME} has nowhere to go")
    exe = Path(fresh.exe_folder)
    override = os.environ.get(EXTRA, '')
    package = locate(data_dir, override) if data_dir else None
    if package is None:
        if not data_dir:
            raise HelixSRError(f'no AMDLSS data folder is set, so {NAME} cannot be downloaded')
        package = fetch(data_dir, progress)
    if _load(exe) is not None:                       # our own install: take it out first, so the record stays true
        _say(on_line, remove(exe))
    record = {'amdlss': APP_VERSION, 'luma': BUILD, 'zip_sha256': ZIP_SHA256, 'moved': [], 'created': []}
    try:
        install_into(exe, package, record)
    except (OSError, HelixSRError) as error:
        _unwind(exe, record, force=True)
        raise HelixSRError(f'{NAME} was not installed ({error}); the game\'s files are as they were.')
    try:
        after = scaler.install_game(helix, fresh, extras, data_dir, on_line, progress, name=SCALER_NAME, placed=True)
    except HelixSRError as error:
        _unwind(exe, record, force=True)
        raise HelixSRError(f'{NAME} was not installed ({error}); the game\'s files are as they were.')
    record_path(exe).parent.mkdir(parents=True, exist_ok=True)
    record_path(exe).write_text(json.dumps(record, indent=1), encoding='utf-8')
    _say(on_line, [f'injected a DLSS call into {game.name}: {FULL_NAME} build {BUILD} and HelixSR Scaler '
                   f'{scaler.VERSION}, running as {SCALER_NAME} in {exe}'])
    try:
        if framegen.apply(exe, True, data_dir, framegen.RESHADE):
            _say(on_line, [f'{scaler.INI}: {framegen.ini_path(exe)} now loads {RESHADE} (LoadReshade=true)'])
    except (HelixSRError, OSError) as error:
        raise HelixSRError(f'{NAME} and HelixSR Scaler are installed in {game.name}, but {scaler.INI} could not be '
                           f'set to load it ({error}). Press Remove, then Install again.')
    after.state, after.route, after.upscaler = gamelist.INJECT, gamelist.INJECT, ''
    return after


def remove_game(helix, game, extras=(), data_dir=None, on_line=None):
    """Removes the injection and the Scaler install from `game`, putting every file back; returns the game as listed
    after. The Luma record is undone before the Scaler one, since the Scaler folder holds both records. The guard is
    the placement one: a game HelixSR does not list at all is exactly what this route installed into."""
    fresh = helix.guard_placed(game, extras)
    exe = fresh.exe_folder
    if exe:
        _say(on_line, remove(exe))
        if scaler.installed(exe):
            _say(on_line, scaler.remove(exe))
        if data_dir:
            framegen.forget(exe, data_dir)
    fresh.state, fresh.route, fresh.upscaler = 'none', '', ''
    return fresh
