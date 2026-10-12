# SPDX-License-Identifier: GPL-2.0-or-later
"""Collect logs: one zip with the evidence for "HelixSR does nothing in this game", ready to send.

The questions a tester's zip has to answer are: did HelixSR load, did the game call DLSS or FSR at all, did OptiScaler
route the call to HelixSR, did the network run. HelixSR writes `helixsr.log` and `helixsr_loader.log` (FSR drop-in:
next to the game's FSR DLL; OptiScaler route: in the HelixSR folder next to the game's .exe; DLSS loader: next to the
.exe), OptiScaler writes `OptiScaler.log` next to the .exe, and `files.txt` says which DLLs are really in place.

Everything is read-only and every item is optional: a missing file or a failing query never stops the zip. The Windows
user name is replaced by `<user>` in every path and text, and nothing else from the user's profile is read. The zip goes
to the Desktop; the caller shows it in Explorer. Pure functions plus one subprocess (PowerShell, for the graphics card):
tests/ and --selftest exercise them directly.
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
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

from . import VERSION, framegen, helixsr

MAX_BYTES = 16 << 20        # a larger file goes in as its last 16 MiB, with a note on top
SESSION_LOG = 'amdlss-install.log'
# What is collected from every place HelixSR or OptiScaler may write to (matched on the lower-cased name).
PATTERNS = ('helixsr*.log', 'optiscaler*.log', 'fakenvapi*.log', 'optiscaler*.ini', 'fakenvapi*.ini',
            'helixsr-install*.json')
# DLLs whose owner is worth saying: the upscalers and the files OptiScaler and HelixSR take their names.
UPSCALER_PREFIXES = ('amd_fidelityfx', 'helixsr', 'nvngx', 'libxess', 'ffx_', 'fakenvapi', 'optiscaler')
OPTI_NAMES = ('dxgi.dll', 'winmm.dll', 'version.dll', 'dbghelp.dll', 'd3d12.dll', 'wininet.dll', 'winhttp.dll')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The JSON goes out as base64 of its UTF-8 bytes: PowerShell's console encoding would garble a name like "básico".
SYSTEM_SCRIPT = ("$g=@(Get-CimInstance Win32_VideoController|Select-Object Name,DriverVersion,"
                 "@{n='DriverDate';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}}});"
                 "$c=(Get-CimInstance Win32_Processor|Select-Object -First 1 -ExpandProperty Name);"
                 "$m=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
                 "$j=@{gpu=$g;cpu=$c;ram=$m}|ConvertTo-Json -Compress -Depth 3;"
                 "[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($j))")


# --- privacy -----------------------------------------------------------------------------------------------------

def user_names():
    """The Windows user name and the name of the profile folder, longest first."""
    names = {os.environ.get('USERNAME', ''), Path(os.environ.get('USERPROFILE', '')).name, Path.home().name}
    try:
        names.add(getpass.getuser())
    except (OSError, KeyError, ImportError):
        pass
    return sorted((n for n in names if n), key=len, reverse=True)


def redactor(names=None):
    """A function that replaces the user name by <user>: after `Users\\` always, and as a whole word anywhere when the
    name has at least four characters (a short name would hit ordinary words)."""
    names = user_names() if names is None else [n for n in names if n]
    if not names:
        return lambda text: text
    after_users = re.compile(r'(?i)(Users[\\/]+)(?:%s)(?![A-Za-z0-9_])' % '|'.join(map(re.escape, names)))
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

def places(game):
    """[(label, folder)] of the folders HelixSR and OptiScaler write to for `game`, each once: the .exe folder, its
    HelixSR folder and the folder of each FSR 3.1 DLL (where the FSR drop-in writes its log)."""
    exe = Path(game.exe_folder or game.folder)
    found = [('exe-folder', exe), ('exe-folder/HelixSR', exe / 'HelixSR')]
    for number, target in enumerate(game.fsr_targets, 1):
        found.append(('fsr-dll-folder' + (f'-{number}' if len(game.fsr_targets) > 1 else ''), Path(target).parent))
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


# --- what is written about the files and the PC ------------------------------------------------------------------

def file_version(path):
    """The Windows file version of a .dll or .exe ('1.2.3.4'), '' when it has none or cannot be read."""
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
    except OSError:
        return 'unreadable'
    return 'not HelixSR'


def files_text(game):
    """files.txt: the game, then name, size, time, version and owner of every .dll and .exe in each place."""
    lines = [f'Game: {game.name}', f'Folder: {game.folder}', f'.exe folder: {game.exe_folder}',
             f'State: {game.state_text} (route {game.active_route})', f'Supports: {game.supports}',
             f'Anti-cheat files: {", ".join(game.anticheat) or "none found"}']
    if game.exe_folder:
        lines.append(f'Frame generation on: {framegen.is_on(game.exe_folder)}; '
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


def system_info():
    """{'gpu': [{Name, DriverVersion, DriverDate}], 'cpu': str, 'ram': bytes} from PowerShell; {} when it fails."""
    try:
        done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', SYSTEM_SCRIPT],
                              capture_output=True, timeout=40, creationflags=NO_WINDOW)
        info = json.loads(base64.b64decode(done.stdout.strip()).decode('utf-8'))
        return info if isinstance(info, dict) else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):   # binascii.Error is a ValueError
        return {}


def system_text(helixsr_folder, info=None):
    """system.txt: Windows, graphics card(s) and driver, CPU, RAM, AMDLSS and HelixSR versions, helixsr_setup.json."""
    info = system_info() if info is None else info
    gpus = info.get('gpu') or []
    gpus = [gpus] if isinstance(gpus, dict) else gpus
    lines = [f'AMDLSS {VERSION}', f'HelixSR {helixsr.version_of(helixsr_folder) or "?"}' if helixsr_folder else
             'HelixSR folder: unknown', f'Windows: {platform.platform()} ({platform.version()})']
    for number, gpu in enumerate(gpus, 1):
        lines.append(f'GPU {number}: {gpu.get("Name", "?")}, driver {gpu.get("DriverVersion", "?")}, '
                     f'driver date {str(gpu.get("DriverDate") or "?")[:10]}')
    if not gpus:
        lines.append('GPU: could not be read')
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

def read_capped(path):
    """The file's bytes, or its last MAX_BYTES with a note on top; None when it cannot be read."""
    try:
        size = Path(path).stat().st_size
        with open(path, 'rb') as stream:
            if size > MAX_BYTES:
                stream.seek(size - MAX_BYTES)
                return f'[AMDLSS: the first {size - MAX_BYTES} bytes of this file are not included]\n'.encode() + \
                    stream.read()
            return stream.read()
    except OSError:
        return None


def contents(game, data_dir):
    """The names that go into the zip: the files found, then the three AMDLSS writes."""
    names = [name for name, _path in gather(game)]
    log = session_log_path(data_dir, game.folder) if game.folder else None
    return names + ['files.txt', 'system.txt'] + ([SESSION_LOG] if log and log.is_file() else [])


def confirm_text(game, data_dir):
    names = contents(game, data_dir)
    found = [n for n in names if n not in ('files.txt', 'system.txt', SESSION_LOG)]
    lines = [f'AMDLSS writes one zip to your Desktop for {game.name}:', '']
    lines += [f'  {n}' for n in found] or ["  (no log file exists in the game's folders yet: that is a finding too)"]
    lines += ['  files.txt: names, sizes, dates and versions of the .dll and .exe files in those folders, and whether '
              "each upscaler DLL is HelixSR's",
              '  system.txt: Windows, graphics card and driver, CPU, RAM, the AMDLSS and HelixSR versions']
    if SESSION_LOG in names:
        lines.append(f"  {SESSION_LOG}: what AMDLSS printed when it installed into this game")
    lines += ['', 'Your Windows user name is replaced by <user> everywhere. Nothing else from your user folder is '
              'included and nothing is sent anywhere: you send the zip yourself.', '', 'Collect logs?']
    return '\n'.join(lines)


def desktop_dir():
    """The user's Desktop (it can be moved, for example into OneDrive), else <home>\\Desktop."""
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


def zip_name(game, now=None):
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', game.name).strip('-.')[:40] or 'game'
    return f'AMDLSS-logs-{slug}-{time.strftime("%Y%m%d-%H%M", now or time.localtime())}.zip'


def collect(game, helixsr_folder, data_dir, out_dir=None, info=None, now=None):
    """Writes the zip for `game` and returns its path. `info` replaces the PowerShell query (tests)."""
    redact = redactor()
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
            for name, source in gather(game):
                data = read_capped(source)
                if data is not None:
                    archive.writestr(name, redact_bytes(data, redact))
            archive.writestr('files.txt', redact(files_text(game)))
            archive.writestr('system.txt', redact(system_text(helixsr_folder, info)))
            own = session_log_path(data_dir, game.folder) if game.folder else None
            data = read_capped(own) if own else None
            if data is not None:
                archive.writestr(SESSION_LOG, redact_bytes(data, redact))
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def reveal(path, say=print):
    """Opens Explorer with the file selected; a failure is only reported to `say`."""
    try:
        subprocess.Popen(f'explorer /select,"{path}"')
    except OSError as error:
        say(f'could not open Explorer: {error}')
