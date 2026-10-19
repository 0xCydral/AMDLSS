# SPDX-License-Identifier: GPL-2.0-or-later
"""Collect logs: one zip with the evidence for "HelixSR does nothing in this game", ready to send.

The questions a tester's zip has to answer are: did HelixSR load, did the game call DLSS or FSR at all, did OptiScaler
route the call to HelixSR, did the network run. HelixSR writes `helixsr.log` and `helixsr_loader.log` (FSR drop-in:
next to the game's FSR DLL; OptiScaler route: in the HelixSR folder next to the game's .exe; DLSS loader: next to the
.exe), OptiScaler writes `OptiScaler.log` next to the .exe, and `files.txt` says which DLLs are really in place.
`crashes.txt` adds Windows' own crash record (amdlss.crashes, on Linux the Proton log beside it), and the logs
AMDLSS kept before earlier installs ride along under `history/` so a switch of route cannot delete the evidence
of the run that crashed.

Everything is read-only and every item is optional: a missing file or a failing query never stops the zip. The user
name is replaced by `<user>` in every path and text (in the Users folder and in /home), and nothing else from the
user's profile is read. The zip goes to the Desktop; the caller shows it in the file manager. Pure functions plus one
subprocess (PowerShell on Windows, for the graphics card; Linux reads /proc): tests/ and --selftest exercise them
directly.
"""
import base64
import ctypes
import fnmatch
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

from . import VERSION, crashes, framegen, fsr4, games as gamelist, gpu, helixsr, host

MAX_BYTES = 16 << 20        # a larger file goes in as its last 16 MiB, with a note on top
HISTORY_MAX_FILE_BYTES = 2 << 20   # one kept log: its last 2 MiB are enough to read a run back
HISTORY_KEEP_RUNS = 10      # snapshots kept per game in the data folder, oldest deleted first
HISTORY_MAX_GAME_BYTES = 32 << 20
HISTORY_SEND_RUNS = 5       # snapshots Collect logs and Send logs carry per game, the newest ones
SESSION_LOG = 'amdlss-install.log'
CRASHES = 'crashes.txt'
PROTON_LOG = 'proton.log'
# What is collected from every place HelixSR, OptiScaler or ReShade may write to (matched on the lower-cased name).
# ReShade.log carries the add-on registration (Luma's own messages too) and ReShade.ini Luma's settings.
PATTERNS = ('helixsr*.log', 'optiscaler*.log', 'fakenvapi*.log', 'reshade*.log', 'optiscaler*.ini',
            'fakenvapi*.ini', 'reshade*.ini', 'helixsr-install*.json', 'helixsr.ini', 'amdlss-fsr4*.json',
            'amdlss-scaler*.json', 'amdlss-dx12dlss*.json', 'amdlss_dx12_dlss*.log', 'amdlss_dx12_dlss*.ini')
# DLLs whose owner is worth saying: the upscalers and the files OptiScaler and HelixSR take their names.
UPSCALER_PREFIXES = ('amd_fidelityfx', 'helixsr', 'nvngx', 'libxess', 'ffx_', 'fakenvapi', 'optiscaler')
OPTI_NAMES = ('dxgi.dll', 'winmm.dll', 'version.dll', 'dbghelp.dll', 'd3d12.dll', 'wininet.dll', 'winhttp.dll')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The JSON goes out as base64 of its UTF-8 bytes: PowerShell's console encoding would garble a name like "básico".
SYSTEM_SCRIPT = ("$g=@(Get-CimInstance Win32_VideoController|Select-Object Name,PNPDeviceID,DriverVersion,"
                 "@{n='DriverDate';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}}});"
                 "$c=(Get-CimInstance Win32_Processor|Select-Object -First 1 -ExpandProperty Name);"
                 "$m=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
                 "$j=@{gpu=$g;cpu=$c;ram=$m}|ConvertTo-Json -Compress -Depth 3;"
                 "[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($j))")


# --- privacy -----------------------------------------------------------------------------------------------------

def user_names():
    """The user name and the name of the home folder, longest first."""
    names = {os.environ.get('USERNAME', ''), Path(os.environ.get('USERPROFILE', '')).name, Path.home().name}
    try:
        names.add(getpass.getuser())
    except (OSError, KeyError, ImportError):
        pass
    return sorted((n for n in names if n), key=len, reverse=True)


def redactor(names=None):
    """A function that replaces the user name by <user>: after `Users\\` or `/home/` always, and as a whole word
    anywhere when the name has at least four characters (a short name would hit ordinary words)."""
    names = user_names() if names is None else [n for n in names if n]
    if not names:
        return lambda text: text
    after_users = re.compile(r'(?i)(Users[\\/]+|(?<![A-Za-z0-9_])/home/)(?:%s)(?![A-Za-z0-9_])'
                             % '|'.join(map(re.escape, names)))
    long = [n for n in names if len(n) >= 4]
    anywhere = re.compile(r'(?i)(?<![A-Za-z0-9_])(?:%s)(?![A-Za-z0-9_])' % '|'.join(map(re.escape, long))) \
        if long else None

    def redact(text):
        text = after_users.sub(lambda m: m.group(1) + '<user>', text)
        return anywhere.sub('<user>', text) if anywhere else text
    return redact


def redact_bytes(data, redact):
    """`data` with the user name replaced, in the file's own encoding (UTF-8, UTF-16 with a BOM, else bytes as is)."""
    codec = 'utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'
    try:
        text = data.decode(codec)
    except UnicodeDecodeError:
        codec, text = 'latin-1', data.decode('latin-1')   # lossless: every byte comes back as it was
    return redact(text).encode(codec) if codec != 'utf-16' else b'\xff\xfe' + redact(text).encode('utf-16-le')


# --- where the files are -----------------------------------------------------------------------------------------

def _within(child, parent):
    """True when folder `child` is `parent` or inside it, compared the way the host's paths compare."""
    child, parent = (os.path.normcase(os.path.normpath(p)) for p in (child, parent))
    return child == parent or child.startswith(parent if parent.endswith(os.sep) else parent + os.sep)


def places(game):
    """[(label, folder)] of the folders HelixSR and OptiScaler write to for `game`, each once: the .exe folder, its
    HelixSR folder and the folder of each FSR 3.1 DLL (where the FSR drop-in writes its log). An FSR folder outside
    the game's own folder is another game's and is never listed or collected; a drive root listed as the folder
    scopes everything to the .exe folder, so a bad folder cannot sweep a whole drive into a report."""
    exe = Path(game.exe_folder or game.folder)
    found = [('exe-folder', exe), ('exe-folder/HelixSR', exe / 'HelixSR'),
             ('exe-folder/HelixSR-fsrfg', exe / 'HelixSR-fsrfg')]
    scope = game.folder if game.folder and not gamelist.is_root_folder(game.folder) else str(exe)
    for number, target in enumerate(game.fsr_targets, 1):
        folder = Path(target).parent
        if _within(str(folder), scope):
            found.append(('fsr-dll-folder' + (f'-{number}' if len(game.fsr_targets) > 1 else ''), folder))
    seen, out = set(), []
    for label, folder in found:
        key = os.path.normcase(os.path.normpath(folder))
        if key not in seen and folder.is_dir():
            seen.add(key)
            out.append((label, folder))
    return out


def listing(folder):
    """The files directly in `folder`, sorted by name; [] when it cannot be read."""
    try:
        return sorted((e for e in os.scandir(folder) if e.is_file()), key=lambda e: e.name.lower())
    except OSError:
        return []


def gather(game):
    """[(name in the zip, path)] of every log, ini and install record that exists for `game`."""
    found = []
    for label, folder in places(game):
        for entry in listing(folder):
            if any(fnmatch.fnmatchcase(entry.name.lower(), pattern) for pattern in PATTERNS):
                found.append((f'{label}/{entry.name}', Path(entry.path)))
    return found


def session_log_path(data_dir, folder):
    """AMDLSS's own record of the installs and removals it ran in one game folder."""
    key = os.path.normcase(os.path.realpath(folder))
    name = re.sub(r'[^A-Za-z0-9]+', '-', os.path.basename(os.path.normpath(folder))).strip('-')[:40] or 'game'
    return Path(data_dir) / 'logs' / f'{name}-{hashlib.sha1(key.encode()).hexdigest()[:8]}.log'


def setup_log_path(data_dir):
    """HelixSR's own setup output as the data folder keeps it (amdlss.helixsr.SetupLog): every run's lines, so a
    frame generation failure can be read back after the window's log box is gone."""
    return Path(data_dir) / helixsr.SETUP_LOG


def setup_log_evidence(data_dir, redact):
    """[(name, bytes)] of the setup output for a zip or a report; [] when no setup ran yet or it wrote nothing."""
    path = setup_log_path(data_dir)
    data = read_capped(path) if path.is_file() else None
    return [(helixsr.SETUP_LOG, redact_bytes(data, redact))] if data else []


class GameLog:
    """Appends the output of an install or remove to the game's own log in AMDLSS's data folder, and passes each line
    on to `sink(text, final)` (the window). Call it like an on_line function."""

    def __init__(self, data_dir, folder, sink=None):
        self.path = session_log_path(data_dir, folder) if folder else None
        self.sink = sink

    def write(self, text):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'a', encoding='utf-8') as stream:
                stream.write(text + '\n')
        except OSError:
            pass   # a log that cannot be written must never stop an install

    def start(self, heading):
        self.write(f'=== {time.strftime("%Y-%m-%d %H:%M:%S")} {heading} ===')

    def fail(self, error):
        self.write(f'error: {error}')

    def __call__(self, text, final=True):
        if final:
            self.write(text)
        if self.sink:
            self.sink(text, final)


# --- history: the logs of earlier runs, kept across install, update and remove -----------------------------------

def history_root(data_dir, folder):
    """Where the kept runs of one game folder live: <data folder>/history/<the session log's name>, or None."""
    return Path(data_dir) / 'history' / session_log_path(data_dir, folder).stem if folder else None


def history_snapshots(data_dir, folder):
    """The kept snapshot folders of a game, oldest first (their names sort by time: YYYYMMDD-HHMMSS)."""
    root = history_root(data_dir, folder)
    if root is None:
        return []
    try:
        return sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda entry: entry.name)
    except OSError:
        return []


def _tree_bytes(folder):
    total = 0
    for path in folder.rglob('*'):
        try:
            total += path.stat().st_size if path.is_file() else 0
        except OSError:
            continue
    return total


def _prune_history(data_dir, folder):
    """Deletes the oldest snapshots until the game is within HISTORY_KEEP_RUNS and HISTORY_MAX_GAME_BYTES."""
    snapshots = history_snapshots(data_dir, folder)
    total = sum(_tree_bytes(snapshot) for snapshot in snapshots)
    while snapshots and (len(snapshots) > HISTORY_KEEP_RUNS or total > HISTORY_MAX_GAME_BYTES):
        oldest = snapshots.pop(0)
        total -= _tree_bytes(oldest)
        shutil.rmtree(oldest, ignore_errors=True)


def preserve_history(game, data_dir, redact=None, now=None):
    """Copies the game's current logs (everything gather() finds) into history/<game>/<stamp>/ in the data
    folder, the user name replaced as in a report, each file cut to its last HISTORY_MAX_FILE_BYTES. Called
    before every install, update and remove, so the run a tester is about to replace survives it (a route
    switch once deleted the OptiScaler.log of the run that crashed). Returns the snapshot folder, or None
    when there was nothing to keep. A copy that fails never stops the install: OSError answers None."""
    root = history_root(data_dir, game.folder)
    if root is None:
        return None
    redact = redact or redactor()
    held = []
    for name, source in gather(game):
        data = read_capped(source, HISTORY_MAX_FILE_BYTES)
        if data is not None:
            held.append((name, redact_bytes(data, redact)))
    if not held:
        return None
    stamp = time.strftime('%Y%m%d-%H%M%S', now or time.localtime())
    snapshot, number = root / stamp, 1
    while snapshot.exists():
        number += 1
        snapshot = root / f'{stamp}-{number}'
    try:
        for name, data in held:
            target = snapshot / Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except OSError:
        return None
    try:
        _prune_history(data_dir, game.folder)
    except OSError:
        pass
    return snapshot


def history_names(game, data_dir, runs=HISTORY_SEND_RUNS):
    """The names the newest `runs` snapshots take in a zip or report: history/<stamp>/<place>/<file>."""
    names = []
    for snapshot in history_snapshots(data_dir, game.folder)[-runs:]:
        for path in sorted(snapshot.rglob('*')):
            if path.is_file():
                names.append(f'history/{snapshot.name}/{path.relative_to(snapshot).as_posix()}')
    return names


def history_evidence(game, data_dir, redact, runs=HISTORY_SEND_RUNS):
    """[(name, bytes)] of the newest `runs` kept snapshots, oldest first, redacted again on the way out (the
    copies on disk were redacted when kept; a changed user name is replaced too)."""
    held = []
    for snapshot in history_snapshots(data_dir, game.folder)[-runs:]:
        for path in sorted(snapshot.rglob('*')):
            if not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            held.append((f'history/{snapshot.name}/{path.relative_to(snapshot).as_posix()}',
                         redact_bytes(data, redact)))
    return held


# --- what is written about the files and the PC ------------------------------------------------------------------

def file_version(path):
    """The Windows file version of a .dll or .exe ('1.2.3.4'), '' when it has none or cannot be read (Linux: '')."""
    if not host.WINDOWS:
        return ''
    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ''
        buffer = ctypes.create_string_buffer(size)
        info, length = ctypes.c_void_p(), ctypes.c_uint()
        if not version.GetFileVersionInfoW(str(path), 0, size, buffer) or not version.VerQueryValueW(
                buffer, '\\', ctypes.byref(info), ctypes.byref(length)) or not length.value:
            return ''
        fixed = ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32 * 13)).contents   # VS_FIXEDFILEINFO
        high, low = fixed[2], fixed[3]
        return f'{high >> 16}.{high & 0xffff}.{low >> 16}.{low & 0xffff}'
    except (AttributeError, OSError, ValueError):
        return ''


def owner_of(path):
    """Whose DLL this is, read the way HelixSR's own installer does: 'HelixSR 1.8.1', 'HelixSR DLSS loader',
    'OptiScaler' or 'not HelixSR' for a DLL that could be an upscaler; '' for any other file."""
    name = Path(path).name.lower()
    if not name.endswith('.dll') or not (name.startswith(UPSCALER_PREFIXES) or name in OPTI_NAMES):
        return ''
    try:
        with open(path, 'rb') as stream:
            head = stream.read(4 << 20)
            match = re.search(rb'HelixSR (\d+\.\d+\.\d+)', head)
            if match:
                return 'HelixSR ' + match.group(1).decode()
            if b'HelixSR DLSS loader' in head[:1 << 20]:
                return 'HelixSR DLSS loader'
            if (name in OPTI_NAMES or name.startswith('optiscaler')) and b'OptiScaler' in head + stream.read(28 << 20):
                return 'OptiScaler'
            if name.startswith('amd_fidelityfx') and fsr4.recognise(path):
                return fsr4.recognise(path)
    except OSError:
        return 'unreadable'
    return 'not HelixSR'


def files_text(game):
    """files.txt: the game, then name, size, time, version and owner of every .dll and .exe in each place."""
    lines = [f'Game: {game.name}', f'Folder: {game.folder}', f'.exe folder: {game.exe_folder}',
             f'State: {game.state_text} (route {game.active_route})', f'Supports: {game.supports}',
             f'Anti-cheat files: {", ".join(game.anticheat) or "none found"}']
    if game.upscaler == 'fsr4':
        lines.append(f'Upscaler: {fsr4.NAME} ({fsr4.describe(game)})')
    if game.fsr4:
        lines.append(game.fsr4.card_text + ('' if game.fsr4.ok else f' ({game.fsr4.reason})'))
    if game.exe_folder:
        lines.append(f'Frame generation: {framegen.name_of(framegen.generator_of(game.exe_folder))}; '
                     f'OptiScaler logging on: {framegen.is_on(game.exe_folder, framegen.LOGGING)}')
    folders = places(game)
    backup = Path(game.exe_folder or game.folder) / 'HelixSR' / 'backup'
    if backup.is_dir():
        folders.append(('exe-folder/HelixSR/backup', backup))
    for label, folder in folders:
        lines += ['', f'== {label}: {folder}']
        rows = [e for e in listing(folder) if e.name.lower().endswith(('.dll', '.exe'))]
        for entry in rows:
            stat = entry.stat()
            when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
            lines.append(f'{entry.name:<48} {stat.st_size:>12} {when}  {file_version(entry.path) or "-":<16} '
                         f'{owner_of(entry.path)}'.rstrip())
        if not rows:
            lines.append('(no .dll or .exe here)')
    return '\n'.join(lines) + '\n'


def _linux_system_info():
    """CPU and RAM from /proc, in the shape of the Windows answer, and the cards' PCI ids from sysfs (their names have no
    one standard place)."""
    info = {'gpu_pci': [{'PNPDeviceID': f'VEN_{card.vendor}&DEV_{card.device}'} for card in gpu.read_sysfs()]}
    try:
        for line in Path('/proc/cpuinfo').read_text(errors='replace').splitlines():
            if line.startswith('model name'):
                info['cpu'] = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
    try:
        for line in Path('/proc/meminfo').read_text(errors='replace').splitlines():
            if line.startswith('MemTotal:'):
                info['ram'] = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    return info


def system_info(windows=None):
    """{'gpu': [{Name, DriverVersion, DriverDate}], 'cpu': str, 'ram': bytes} from PowerShell (Linux: /proc, without
    the graphics card); {} when it fails."""
    if not host.is_windows(windows):
        return _linux_system_info()
    try:
        done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', SYSTEM_SCRIPT],
                              capture_output=True, timeout=40, creationflags=NO_WINDOW)
        info = json.loads(base64.b64decode(done.stdout.strip()).decode('utf-8'))
        return info if isinstance(info, dict) else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):   # binascii.Error is a ValueError
        return {}


def system_text(helixsr_folder, info=None, windows=None):
    """system.txt: the system, graphics card(s) and driver, CPU, RAM, AMDLSS and HelixSR versions, helixsr_setup.json."""
    info = system_info(windows) if info is None else info
    gpus = info.get('gpu') or []
    gpus = [gpus] if isinstance(gpus, dict) else gpus
    lines = [f'AMDLSS {VERSION}', f'HelixSR {helixsr.version_of(helixsr_folder) or "?"}' if helixsr_folder else
             'HelixSR folder: unknown',
             f'{"Windows" if host.is_windows(windows) else "Linux"}: {platform.platform()} ({platform.version()})']
    for number, card in enumerate(gpus, 1):
        lines.append(f'GPU {number}: {card.get("Name", "?")}, driver {card.get("DriverVersion", "?")}, '
                     f'driver date {str(card.get("DriverDate") or "?")[:10]}')
    if not gpus:
        lines.append('GPU: could not be read')
    cards = gpu.from_info(gpus + list(info.get('gpu_pci') or []))
    family = gpu.classify(cards)
    lines.append(f'FSR 4 card: {family} ({gpu.TEXT[family]})' if cards else 'FSR 4 card: could not be read')
    lines.append(f'CPU: {(info.get("cpu") or "").strip() or platform.processor() or "?"}')
    ram = info.get('ram')
    lines.append(f'RAM: {round(int(ram) / 2**30, 1)} GB' if ram else 'RAM: unknown')
    lines.append(f'Collected: {time.strftime("%Y-%m-%d %H:%M:%S")}')
    setup = Path(helixsr_folder) / helixsr.BUILT if helixsr_folder else None
    try:
        lines += ['', f'--- {helixsr.BUILT} ---', setup.read_text(encoding='utf-8', errors='replace').rstrip()]
    except (OSError, AttributeError):
        lines += ['', f'{helixsr.BUILT}: not found']
    return '\n'.join(lines) + '\n'


# --- the zip -----------------------------------------------------------------------------------------------------

def read_capped(path, limit=None):
    """The file's bytes, or its last `limit` bytes (MAX_BYTES by default) with a note on top; None when it
    cannot be read."""
    limit = MAX_BYTES if limit is None else limit
    try:
        size = Path(path).stat().st_size
        with open(path, 'rb') as stream:
            if size > limit:
                stream.seek(size - limit)
                return f'[AMDLSS: the first {size - limit} bytes of this file are not included]\n'.encode() + \
                    stream.read()
            return stream.read()
    except OSError:
        return None


def contents(game, data_dir, windows=None):
    """The names that go into the zip: the files found, the AMDLSS writes, the crash record, the Proton log
    when there is one, the kept history, and HelixSR's setup output when one was run."""
    names = [name for name, _path in gather(game)]
    log = session_log_path(data_dir, game.folder) if game.folder else None
    proton = [] if host.is_windows(windows) or not crashes.proton_log_path(game) else [PROTON_LOG]
    unreal = [crashes.UNREAL_LOG] if crashes.unreal_log_path(game, windows) else []
    return (names + ['files.txt', CRASHES] + proton + unreal + ['system.txt']
            + ([helixsr.SETUP_LOG] if setup_log_path(data_dir).is_file() else [])
            + ([SESSION_LOG] if log and log.is_file() else []) + history_names(game, data_dir))


def confirm_text(game, data_dir, windows=None):
    names = contents(game, data_dir, windows)
    kept = [n for n in names if n.startswith('history/')]
    found = [n for n in names if n not in ('files.txt', 'system.txt', SESSION_LOG, CRASHES, PROTON_LOG, crashes.UNREAL_LOG)
             and not n.startswith('history/')]
    system = 'Windows' if host.is_windows(windows) else 'Linux'
    lines = [f'AMDLSS writes one zip to your Desktop for {game.name}:', '']
    lines += [f'  {n}' for n in found] or ["  (no log file exists in the game's folders yet: that is a finding too)"]
    lines += ['  files.txt: names, sizes, dates and versions of the .dll and .exe files in those folders, and whether '
              "each upscaler DLL is HelixSR's",
              f'  system.txt: {system}, graphics card and driver, CPU, RAM, the AMDLSS and HelixSR versions',
              f'  {CRASHES}: ' + ("Windows' record of this game's crashes (which DLL faulted), and the names "
                                  'of its crash dumps (the dumps themselves stay on this PC)'
                                  if host.is_windows(windows) else
                                  "a note on the Proton log, this system's crash evidence")]
    if PROTON_LOG in names:
        lines.append(f'  {PROTON_LOG}: the Proton log of the last run (PROTON_LOG=1)')
    if crashes.UNREAL_LOG in names:
        lines.append(f"  {crashes.UNREAL_LOG}: the last {crashes.UNREAL_LOG_LINES} lines of the game's newest Unreal "
                     'Engine log, and in crashes.txt the names and sizes of its crash folders (never their files)')
    if kept:
        runs = len({n.split('/')[1] for n in kept})
        lines.append(f'  history/: the logs AMDLSS kept before {runs} earlier install, update or '
                     f'remove run{"" if runs == 1 else "s"} of this game')
        lines += [f'  {n}' for n in kept]
    if SESSION_LOG in names:
        lines.append(f"  {SESSION_LOG}: what AMDLSS printed when it installed into this game")
    lines += ['', 'Your user name is replaced by <user> everywhere. Nothing else from your user folder is '
              'included and nothing is sent anywhere: you send the zip yourself.', '', 'Collect logs?']
    return '\n'.join(lines)


def desktop_dir(windows=None):
    """The user's Desktop (it can be moved, for example into OneDrive, or named by the XDG user dirs), else
    <home>/Desktop."""
    if not host.is_windows(windows):
        try:
            for line in (Path.home() / '.config' / 'user-dirs.dirs').read_text(errors='replace').splitlines():
                if line.startswith('XDG_DESKTOP_DIR='):
                    folder = line.split('=', 1)[1].strip().strip('"').replace('$HOME', str(Path.home()))
                    if os.path.isdir(folder):
                        return Path(folder)
        except OSError:
            pass
        return Path.home() / 'Desktop'
    try:
        guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('B4BFCC3A-DB2C-424C-B029-7FE99A87C641').bytes_le)
        path = ctypes.c_wchar_p()
        shell = ctypes.windll.shell32
        if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
            folder = path.value
            ctypes.windll.ole32.CoTaskMemFree(path)
            if folder and os.path.isdir(folder):
                return Path(folder)
    except (AttributeError, OSError, ValueError):
        pass
    return Path.home() / 'Desktop'


def slug(name):
    """`name` as a short file-name-safe label (the zip's name and, in a report, the game's folder)."""
    return re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-.')[:40] or 'game'


def zip_name(game, now=None):
    return f'AMDLSS-logs-{slug(game.name)}-{time.strftime("%Y%m%d-%H%M", now or time.localtime())}.zip'


def game_evidence(game, data_dir, redact, windows=None, crash_run=None, crash_events=None, processes=None):
    """[(name, bytes)] for one game, the user name already replaced: the files found, files.txt, the crash
    record (amdlss.crashes; `crash_run` is its PowerShell seam, tests, and `crash_events` and `processes` the two
    queries shared across games), the Proton log on Linux when present, the tail of an Unreal Engine game's newest
    log, AMDLSS's own record of the installs and removals,
    and the kept history, newest snapshots last."""
    found = []
    for name, source in gather(game):
        data = read_capped(source)
        if data is not None:
            found.append((name, redact_bytes(data, redact)))
    found.append(('files.txt', redact(files_text(game)).encode('utf-8')))
    found.append(crashes.crashes_evidence(game, redact, windows, crash_run, events=crash_events, processes=processes))
    found += crashes.unreal_evidence(game, redact, windows)
    if not host.is_windows(windows):
        proton = crashes.proton_log_path(game)
        data = read_capped(proton) if proton else None
        if data is not None:
            found.append((PROTON_LOG, redact_bytes(data, redact)))
    own = session_log_path(data_dir, game.folder) if game.folder else None
    data = read_capped(own) if own else None
    if data is not None:
        found.append((SESSION_LOG, redact_bytes(data, redact)))
    return found + history_evidence(game, data_dir, redact)


def system_evidence(helixsr_folder, redact, info=None):
    """('system.txt', bytes): `info` replaces the PowerShell query (tests)."""
    return 'system.txt', redact(system_text(helixsr_folder, info)).encode('utf-8')


def evidence(game, helixsr_folder, data_dir, info=None, windows=None, crash_run=None):
    """[(name, bytes)] of everything Collect logs puts in the zip for `game`."""
    redact = redactor()
    return (game_evidence(game, data_dir, redact, windows, crash_run)
            + setup_log_evidence(data_dir, redact)
            + [system_evidence(helixsr_folder, redact, info)])


def collect(game, helixsr_folder, data_dir, out_dir=None, info=None, now=None, windows=None, crash_run=None):
    """Writes the zip for `game` and returns its path. `info` replaces the system query (tests)."""
    held = evidence(game, helixsr_folder, data_dir, info, windows, crash_run)
    target = Path(out_dir) if out_dir else desktop_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = target / zip_name(game, now)
    for number in range(2, 100):
        if not path.exists():
            break
        path = path.with_name(f'{zip_name(game, now)[:-4]}-{number}.zip')
    partial = path.with_name(path.name + '.part')
    try:
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in held:
                archive.writestr(name, data)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def reveal(path, say=print, windows=None):
    """Shows the file in the file manager (Explorer with it selected; on Linux xdg-open on its folder); a failure
    is only reported to `say`."""
    try:
        if host.is_windows(windows):
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            host.xdg_open(Path(path).parent)
    except OSError as error:
        say(f'could not open {"Explorer" if host.is_windows(windows) else "the file manager"}: {error}')
