# SPDX-License-Identifier: GPL-2.0-or-later
"""HelixSR Scaler: OptiScaler with HelixSR built in, for games without an FSR 3.1 DLL.

HelixSR 1.9.0's own setup installed only the FSR drop-in and its installer reported a game without FSR 3.1 as
'unsupported'; 1.9.1's and later setups install HelixSR Scaler too ('scaler', or 'blocked' with anti-cheat), with the
same package AMDLSS downloads. AMDLSS still places the package itself: FSR 4.1.1, Luma UE and AMDLSS's own
record/backup work the same on both systems, and Remove restores exactly what an install replaced. AMDLSS
downloads the package (SHA-256 pinned), unpacks it and puts its files next to the game's .exe the way Scaler's own
installers do:

  - every file its HelixSR-Scaler-package.txt names, with OptiScaler.dll renamed to the first free of Scaler's own
    names (dxgi.dll first);
  - HelixSR's built amd_fidelityfx_dx12.dll as HelixSR\\helixsr.dll (HelixSR 1.9.2 and later have no frame
    generation of their own, so there is no helixsr-fg folder to copy; an install of an earlier AMDLSS that holds one
    is removed through its record as before);
  - every other OptiScaler copy next to the .exe (an older install, whatever it is called; its version resource or
    its bytes say so) is moved into HelixSR\\amdlss-scaler-backup too: the game loads whichever proxy it finds
    first, so a leftover would shadow ours;
  - every game file that had to give way goes to HelixSR\\amdlss-scaler-backup and comes back on Remove.

The record beside it (HelixSR\\amdlss-scaler.json) lists what was moved and created with SHA-256s, so Remove puts
back exactly that and keeps a file the user changed by hand (as amdlss.fsr4 does). Scaler's own manifest is not
written: its remover restores from *its* backup folder, which AMDLSS does not keep. An install AMDLSS 1.0.0 made
through HelixSR's old bridge is removed first by HelixSR's own installer (`uninstall N`, its uninstall_optiscaler);
AMDLSS never re-implements that.
"""
import json
import os
import shutil
from pathlib import Path

from . import VERSION as APP_VERSION
from . import helixsr, pe
from .helixsr import HelixSRError

VERSION = '1.0.6'
ZIP_NAME = f'HelixSR-Scaler-{VERSION}.zip'
ZIP_URL = f'https://github.com/lonewolf0622/HelixSR/releases/download/v{helixsr.VERSION}/{ZIP_NAME}'
ZIP_SIZE = 133228253
ZIP_SHA256 = '1256a5c51ba0db804d60c5ad896a06a17134f8b130a68fabda41621e8dc650af'
TOP = f'HelixSR-Scaler-{VERSION}'          # the folder the zip extracts to
LIST = 'HelixSR-Scaler-package.txt'        # the package's own file list: what its installer copies
DLL = 'OptiScaler.dll'
NAMES = ('dxgi.dll', 'winmm.dll', 'version.dll', 'dbghelp.dll', 'd3d12.dll', 'wininet.dll', 'winhttp.dll')
FOLDER = 'HelixSR'                         # the game's HelixSR folder: helixsr.dll, the record and the backup
OWN = 'helixsr.dll'
RECORD = 'amdlss-scaler.json'
BACKUP = 'amdlss-scaler-backup'            # inside FOLDER: the game's files an install had to replace
INI = 'OptiScaler.ini'
# The download's own files: they stay in the download, never go into a game (Scaler's own installers exclude the
# same list from HelixSR-Scaler-package.txt, which names them too).
NOT_COPIED = ('setup_linux.sh', 'setup_windows.bat', 'README.txt', 'README-Linux.txt', LIST)
LOG_FILES = ('OptiScaler.log', f'{FOLDER}/helixsr.log', f'{FOLDER}/helixsr.ini',
             f'{FOLDER}/helixsr_upscaler.dll', f'{FOLDER}/helixsr-fg/nvexec.log')
FG_OFF = f'{FOLDER}/helixsr-fg.off'        # frame generation switched off: Scaler parks the folder here


def install_folder(data_dir):
    """The folder that holds the package once fetch() has run."""
    return Path(data_dir) / TOP


def extracted(target):
    return (install_folder(target) / DLL).is_file() and (install_folder(target) / LIST).is_file()


def locate(data_dir, override=''):
    """The Scaler package to use, or None: `override` (AMDLSS_SCALER) when it holds the package, else
    <data_dir>/HelixSR-Scaler-x for the pinned x."""
    for folder in ([Path(override)] if override else []) + [install_folder(data_dir)]:
        if (folder / DLL).is_file() and (folder / LIST).is_file():
            return folder
    return None


def fetch(target, progress=None):
    """Downloads, verifies and unpacks the pinned Scaler package into `target`; returns its folder. The same pins,
    zip-slip screening and work folder as helixsr.fetch. `progress(done, total)` is optional."""
    target = Path(target)
    work = target / '.download'
    work.mkdir(parents=True, exist_ok=True)
    part = work / (ZIP_NAME + '.part')
    helixsr.download(part, progress, ZIP_URL, ZIP_SIZE)
    if part.stat().st_size != ZIP_SIZE or helixsr.sha256_of(part) != ZIP_SHA256:
        part.unlink(missing_ok=True)
        raise HelixSRError('the downloaded HelixSR Scaler package does not match the pinned SHA-256; it was deleted')
    helixsr.unpack(part, target)
    part.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    if not extracted(target):
        raise HelixSRError(f'the package does not hold {TOP}/{DLL} and {TOP}/{LIST}')
    return install_folder(target)


def files(package):
    """The files the package installs, as its own HelixSR-Scaler-package.txt names them minus the download's own
    files (see NOT_COPIED)."""
    text = (Path(package) / LIST).read_text(encoding='utf-8', errors='replace')
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith('#') and line.strip() not in NOT_COPIED]


# --- the record in the game ---------------------------------------------------------------------------------------

def record_path(exe_folder):
    return Path(exe_folder) / FOLDER / RECORD


def installed(exe_folder):
    """True when AMDLSS's record says Scaler is installed in the game (HelixSR's own list cannot see it)."""
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


def install_into(package, exe_folder, helix_folder, name, record):
    """Copies the package's files (and HelixSR's built DLL) into `exe_folder`, keeping every
    game file that had to give way in the backup, and fills record['moved']/record['created']. `name` is what
    OptiScaler.dll is called there."""
    exe_folder, backup = Path(exe_folder), Path(exe_folder) / FOLDER / BACKUP
    moved, created = record['moved'], record['created']

    def put(source, rel):
        target = exe_folder / rel
        if target.is_file() or target.is_symlink():
            kept = backup / rel
            kept.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, kept)
            moved.append({'name': rel, 'to': f'{FOLDER}/{BACKUP}/{rel}', 'sha256': helixsr.sha256_of(kept)})
        target.parent.mkdir(parents=True, exist_ok=True)
        _put(source, target)
        created.append({'name': rel, 'sha256': helixsr.sha256_of(target)})

    for rel in files(package):
        put(package / rel, name if rel == DLL else rel)
    put(Path(helix_folder) / helixsr.DLL, f'{FOLDER}/{OWN}')


def _name_for(exe_folder):
    """What OptiScaler.dll is called in the game: the first of Scaler's own names the game does not have; dxgi.dll
    when it has them all (Scaler's own rule)."""
    for name in NAMES:
        if not (Path(exe_folder) / name).exists():
            return name
    return NAMES[0]


# Another OptiScaler copy under any name next to the game's .exe (an older install, or a pair another tool left
# behind) hooks the same calls ours does: the game loads whichever proxy it finds first, so ours can be shadowed and
# the game can hang inside the mix. The copy is moved aside and recorded, never deleted.
OPTISCALER_MARKER = b'OptiScaler'   # the name every OptiScaler build carries in its bytes, spoofed resource or not
MARKER_MAX = 64 << 20               # bytes of a file read for it (the name sits near the version resource's end)


def _mentions_marker(path):
    """True when the file's bytes carry OptiScaler's name (a copy that spoofs its own version resource shows only
    this); at most MARKER_MAX bytes are read, in blocks."""
    try:
        with open(path, 'rb') as stream:
            read, tail = 0, b''
            while read < MARKER_MAX:
                block = stream.read(8 << 20)
                if not block:
                    return False
                read += len(block)
                if OPTISCALER_MARKER in tail + block:
                    return True
                tail = block[-(len(OPTISCALER_MARKER) - 1):]
    except OSError:
        return False
    return False


def is_optiscaler(path):
    """True when the DLL is an OptiScaler copy whatever it is called: its version resource names OptiScaler, or its
    bytes carry the name (OptiScaler's own proxies - d3d12.dll, dxgi.dll - spoof their version resource, so the
    system DLL's strings are in it). Any other mod's DLL is not an OptiScaler copy."""
    return (any('optiscaler' in word.lower() for word in pe.version_words(path))
            or _mentions_marker(path))


def optiscaler_copies(exe_folder, previous=None):
    """Sorted names of the DLLs next to the .exe that are OptiScaler copies other than ours; ours is the file the
    previous record names and hashes (an update's own file must not be taken for a leftover)."""
    ours = {item.get('name'): item.get('sha256') for item in (previous or {}).get('created', [])}
    found = []
    try:
        entries = sorted(Path(exe_folder).iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return found
    for path in entries:
        if not path.is_file() or path.suffix.lower() != '.dll':
            continue
        sha = ours.get(path.name)
        if sha:
            try:
                if helixsr.sha256_of(path) == sha:
                    continue   # AMDLSS's own file, as the record left it
            except OSError:
                pass
        if is_optiscaler(path):
            found.append(path.name)
    return found


def _move_aside(exe_folder, record, names):
    """Moves other OptiScaler copies next to the .exe into the backup and records them like every other file an
    install has to replace, so the game loads ours and Remove puts them back exactly. Returns the notes. A file that
    cannot be moved (the game is running) or that does not hash the same after the move raises HelixSRError naming
    it; nothing of it is ever deleted."""
    exe_folder, backup = Path(exe_folder), Path(exe_folder) / FOLDER / BACKUP
    notes = []
    for name in names:
        target, kept = exe_folder / name, backup / name
        try:
            before = helixsr.sha256_of(target)
            kept.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, kept)
            after = helixsr.sha256_of(kept)
        except OSError as error:
            raise HelixSRError(f'{name} is another OptiScaler copy that would shadow HelixSR, and AMDLSS could not '
                               f'move it aside ({error}). Close the game, remove that older mod (its file is '
                               f'{name}), then try again; nothing was changed.')
        if after != before:
            try:
                os.replace(kept, target)
            except OSError:
                pass
            raise HelixSRError(f'{name} did not hash the same after moving it aside (it is at {kept}). Remove that '
                               f'older OptiScaler copy from the game folder, then try again.')
        record['moved'].append({'name': name, 'to': f'{FOLDER}/{BACKUP}/{name}', 'sha256': after})
        notes.append(f'moved {name} aside: another OptiScaler copy (found by its version or its bytes) would '
                     f'shadow HelixSR; it is kept in {FOLDER}/{BACKUP} and Remove puts it back')
    return notes


def _runtime_files(exe_folder):
    """The files OptiScaler and HelixSR make while the game runs (Scaler's own remover deletes these too)."""
    for name in LOG_FILES:
        (Path(exe_folder) / name).unlink(missing_ok=True)
    shutil.rmtree(Path(exe_folder) / FG_OFF, ignore_errors=True)


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
    then the files it moved come back. Returns the notes. OptiScaler.ini is deleted like Scaler's own remover does
    it: it is the settings file (AMDLSS's frame generation switches and the user's edits go there), and the game's
    own file, if it had one, is in the backup and comes back."""
    notes, kept, folders = [], set(), {Path(exe_folder) / FOLDER}
    for item in reversed(record.get('created', [])):
        path = Path(exe_folder) / item['name']
        folders.add(path.parent)
        if not path.is_file():
            continue
        if force or item['name'] == INI or helixsr.sha256_of(path) == item['sha256']:
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
    """Puts back every file install_game replaced in `exe_folder` and deletes what it created (the record holds
    SHA-256s; a file the user changed after the install is kept and reported). Returns the notes."""
    exe_folder = Path(exe_folder)
    record = _load(exe_folder)
    if record is None:
        return []
    notes = _unwind(exe_folder, record)
    _runtime_files(exe_folder)
    record_path(exe_folder).unlink(missing_ok=True)
    _prune(exe_folder, {exe_folder / FOLDER})
    return notes


# --- install game -------------------------------------------------------------------------------------------------

def _say(on_line, notes):
    for note in notes:
        if on_line:
            on_line(note, True)


def install_notes(game, exe_folder, scaler_name, record):
    """What a finished install says in the game log: the game, where its files went, and every file it placed there
    (the record keeps them all; a long list is cut to the first ten). Without this the log of a successful install
    says nothing, and a tester reads that as a failure."""
    placed = [item['name'] for item in record.get('created', [])]
    listed = ', '.join(placed[:10]) + (f' and {len(placed) - 10} more' if len(placed) > 10 else '')
    return [f'HelixSR installed in {game.name} (OptiScaler, running as {scaler_name}) in {exe_folder}',
            f'placed {len(placed)} files: {listed}']


def install_game(helix, game, extras=(), data_dir=None, on_line=None, progress=None, name='', placed=False):
    """Installs (or updates) HelixSR Scaler in `game` (the OptiScaler route) and returns the game with its state
    set. The folder is safe, the game closed and still in HelixSR's list (helix.guard; `placed` uses
    helix.guard_placed instead, for amdlss.luma's injected route, whose games HelixSR does not list at all). AMDLSS
    1.0.0's bridge install, or our own older one, is taken out first; the user's OptiScaler.ini settings are kept.
    `name` forces what OptiScaler.dll is called there (the injected route needs dxgi.dll, whatever an earlier install
    used); '' keeps the previous install's name, else Scaler's own first free one."""
    fresh = helix.guard_placed(game, extras) if placed else helix.guard(game, extras)
    if not fresh.exe_folder:
        raise HelixSRError(f"HelixSR does not know {game.name}'s .exe folder, so Scaler has nowhere to go")
    exe = Path(fresh.exe_folder)
    override = os.environ.get('AMDLSS_SCALER', '')
    package = locate(data_dir, override) if data_dir else None
    if package is None:
        if not data_dir:
            raise HelixSRError('no AMDLSS data folder is set, so HelixSR Scaler cannot be downloaded')
        package = fetch(data_dir, progress)
    settings = None
    previous = _load(exe) or {}
    if installed(exe):                       # our own install: take it out, keep the user's OptiScaler.ini
        ini = exe / INI
        settings = ini.read_bytes() if ini.is_file() else None
        ours = not any(item.get('name') == INI for item in previous.get('moved', []))
        _say(on_line, remove(exe))
        if ours:
            ini.unlink(missing_ok=True)      # ours (perhaps edited): the package's own version goes back
    elif fresh.state in ('optiscaler', 'direct'):   # an install of HelixSR's own (AMDLSS 1.0.0's bridge too)
        helix.remove(fresh, extras, on_line)
    name = name or str(previous.get('name') or '') or _name_for(exe)
    others = optiscaler_copies(exe, previous)   # another copy would shadow ours: it is moved aside, not deleted
    record = {'amdlss': APP_VERSION, 'scaler': VERSION, 'name': name, 'moved': [], 'created': []}
    try:
        _say(on_line, _move_aside(exe, record, others))
        install_into(package, exe, helix.folder, name, record)
    except (OSError, HelixSRError) as error:
        _unwind(exe, record, force=True)
        record_path(exe).unlink(missing_ok=True)
        _prune(exe, {exe / FOLDER})
        raise HelixSRError(f'HelixSR Scaler was not installed ({error}); the game\'s files are as they were.')
    record_path(exe).parent.mkdir(parents=True, exist_ok=True)
    if settings is not None:
        ini = exe / INI
        if ini.is_file():
            ini.write_bytes(settings)
            for item in record['created']:
                if item['name'] == INI:
                    item['sha256'] = helixsr.sha256_of(ini)
    record_path(exe).write_text(json.dumps(record, indent=1), encoding='utf-8')
    _say(on_line, install_notes(fresh, exe, name, record))
    fresh.state, fresh.route, fresh.upscaler = 'optiscaler', 'optiscaler', ''
    return fresh


def mark(game):
    """Sets `game.state` from AMDLSS's record: HelixSR's own list cannot see a Scaler install."""
    if game.exe_folder and game.state == 'none' and installed(game.exe_folder):
        game.state = game.route = 'optiscaler'
    return game


def plan_text(game):
    """What installing HelixSR Scaler will do, before the user agrees (gamelist.plan_text's OptiScaler route)."""
    where = game.exe_folder or "the game's .exe folder"
    text = (f"AMDLSS downloads HelixSR Scaler {VERSION} (about {ZIP_SIZE >> 20} MB, checked by SHA-256, GPL-3.0) "
            f"and puts OptiScaler with HelixSR built in next to the game's .exe ({where}). Files it replaces "
            f"are kept in the HelixSR folder's {BACKUP} and put back by Remove.")
    if installed(game.exe_folder):
        text += ' The OptiScaler.ini settings you have are kept.'
    return text
