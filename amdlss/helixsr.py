# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything AMDLSS does with HelixSR lives in this file, and nothing in it changes HelixSR.

HelixSR (https://github.com/lonewolf0622/HelixSR, closed-source freeware DLL; its setup scripts are Apache-2.0 up to
1.8.1 and under the HelixSR Freeware License from 1.9.0) runs NVIDIA's DLSS network as Direct3D 12 compute on AMD
cards. This module:

  - fetches the pinned official release zip (resumable, SHA-256 checked, zip-slip checked) and extracts it;
  - runs HelixSR's own setup (`helixsr-setup.ps1 -NoFg -Dlss FILE`, on Linux `helixsr-setup.sh --no-fg --dlss FILE`)
    with stdin closed. AMDLSS downloads NVIDIA's DLSS DLL itself (fetch_dlss, pinned size and SHA-256) and hands it
    over, because the setups' `-Yes` / `--yes` would also install HelixSR into every game they find (2.0 passes it on
    to the games step). install() does nothing unless `consented` is true, and the frame generation downloads are
    skipped (`-NoFg`): AMDLSS does not offer HelixSR's frame generation;
  - lists the games by a small read-only bridge run in the Python HelixSR's setup uses (its embedded Windows Python; on
    Linux the system python3 with numpy, or HelixSR's portable one: find_python()). It imports HelixSR's game module
    (helixsr_games.py, up to 1.9 helixsr_install.py) and prints JSON; for a 1.9 folder whose bridge cannot run, the
    installer's printed `list` output is parsed instead;
  - installs and removes the FSR drop-in per game with HelixSR's own bridge (setup/helixsr_bridge.py install --way fsr,
    remove: what the HelixSR app and the setup run, JSON out); a 1.9 folder is driven by its installer (`install N`,
    `uninstall N`, `--folder PATH`) as before.
  - installs the Scaler route (HelixSR's OptiScaler, for a game without an FSR 3.1 DLL) itself in amdlss.scaler:
    the pinned HelixSR Scaler package next to the game's .exe, with AMDLSS's own record and backup. HelixSR's installer (1.9.1
    and later) installs Scaler the same way (its HelixSR-Scaler.files record); AMDLSS keeps its own path so FSR 4.1.1,
    Luma UE and Remove behave the same on both systems, and it recognises HelixSR's own installs through that record.

The pinned files are HelixSR 2.0.0. Nothing is bundled or redistributed: the zip comes from HelixSR's own release.
"""
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import games as gamelist
from . import host

VERSION = '2.0.0'
ZIP_NAME = 'HelixSR-2.0.0.zip'
ZIP_URL = f'https://github.com/lonewolf0622/HelixSR/releases/download/v{VERSION}/{ZIP_NAME}'
ZIP_SIZE = 136997025
ZIP_SHA256 = '0d60686eddbb0abd680b27d12a088344b212464ebf0a2dc18d10387e1c817f52'
TOP = f'HelixSR-{VERSION}'  # the folder the zip extracts to
DLL = 'amd_fidelityfx_dx12.dll'
# HelixSR 2.0 builds into this folder of the release (the DLL, helixsr.ini, helixsr-fg); up to 1.9 the DLL sat at the
# release root.
FSRFG = 'HelixSR-fsrfg'
BUILT = 'helixsr_setup.json'  # written at the release root when the setup has built the network
# NVIDIA's DLSS 310.7.0 DLL, which the setup builds HelixSR's network from (the same URL, size and SHA-256 as its own
# helixsr_setup.py): AMDLSS fetches it after the user's consent and hands it to the setup (see setup_command).
DLSS_NAME = 'nvngx_dlss.dll'
DLSS_URL = 'https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll'
DLSS_SIZE = 58977904
DLSS_SHA256 = 'be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e'
# HelixSR 2.0 has frame generation again (DLSS frame generation through HelixSR Scaler), but AMDLSS does not offer it
# yet: a game whose OptiScaler.ini still runs it from an earlier AMDLSS (or a 1.2 build) is switched off. Said where
# that happens.
NO_FG = ("AMDLSS does not offer HelixSR's own frame generation (DLSS frame generation). AMD FSR frame generation is "
         "still available.")
MARKER_FILE = 'helixsr-install.json'   # HelixSR's install record beside a drop-in DLL
FSR4_MARKER = 'amdlss-fsr4.json'       # AMDLSS's own record of an FSR 4.1.1 drop-in (amdlss.fsr4)
PACKAGE = 'helixsr-package.json'       # beside the extracted folder: which pinned zip its files came from
SETUP_LOG = 'helixsr-setup.log'        # the setup's output, kept for Collect logs and Send logs
SETUP_PS1, SETUP_SH = 'helixsr-setup.ps1', 'helixsr-setup.sh'
SETUP = SETUP_PS1 if host.WINDOWS else SETUP_SH
INSTALLER = Path('setup') / 'helixsr_install.py'          # up to 1.9: lists, installs, removes by number
BRIDGE_SCRIPT = Path('setup') / 'helixsr_bridge.py'       # 2.0: the app's and the setup's JSON bridge
NVIDIA_LICENSE_URL = 'https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt'
CHUNK = 1 << 20
BUILD_TIMEOUT = 1800  # seconds; the build takes about a minute plus the first-time downloads
COMMAND_TIMEOUT = 600  # seconds for one install or remove
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Runs inside HelixSR's own Python, read-only: HelixSR's scan, as JSON, with the state of each game.
BRIDGE = '''
import contextlib, json, os, sys
from pathlib import Path
here, extra = sys.argv[1], sys.argv[2:]
sys.path.insert(0, os.path.join(here, 'setup'))
try:
    import helixsr_games as h      # HelixSR 2.0: the game module the app and the setup share
except ImportError:
    import helixsr_install as h    # up to 1.9
# 2.0 puts HelixSR's DLL into a game's HelixSR-fsrfg folder (AMDLSS's Scaler install does too): under the DLL's own file
# name it would pass for the game's FSR 3.1 DLL. HelixSR's app skips it after the scan; skipping the folder in the walk
# keeps the game's inputs, targets and route right from the start.
getattr(h, 'WALK_SKIP', set()).add('helixsr-fsrfg')


def state(g):
    if g.dlss_installed:
        return 'dlss'
    if getattr(g, 'scaler_installed', False) or g.opti_installed:  # 1.9.1+ records Scaler in HelixSR-Scaler.files,
        return 'optiscaler'                                        # an earlier version only in its HelixSR marker
    if any(h.original(t).exists() and h.is_helixsr(t) for t in g.fsr_targets):
        return 'direct'
    return 'none'


def same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def installed_version(g):
    """The HelixSR a game runs: its install record's version, else the one inside the installed DLL; none: ''."""
    route, own = state(g), (g.target / h.FOLDER if g.target else None)
    if route != 'direct' and own is None:
        return ''
    folders = [t.parent for t in g.fsr_targets] if route == 'direct' else [own]
    dlls = (list(g.fsr_targets) if route == 'direct'
            else [g.target / 'HelixSR-fsrfg' / h.DLL, own / h.OWN, g.target / h.CORE])   # 2.0 layout first
    for folder in folders:
        try:
            version = json.loads((folder / h.MARKER).read_text()).get('helixsr')
        except (OSError, ValueError, AttributeError):
            continue
        if version and version != '?':
            return str(version)
    for dll in dlls:
        try:
            version = h.version_of(dll)
        except OSError:
            continue
        if version != '?':
            return version
    return ''


# HelixSR's games() drops a game it has no route for. To report those too (index 0: not in HelixSR's list, so never
# the N of `install N`), note every Game it builds from the Steam libraries and the other stores, before the folders
# given by hand are read; the list itself, and its numbering, stay exactly HelixSR's.
seen, noting = [], [True]
real_game, real_from = getattr(h, 'Game', None), getattr(h, 'games_from', None)
if real_game and real_from:
    class Noting(real_game):
        def __init__(self, *args):
            super().__init__(*args)
            if noting[0]:
                seen.append(self)

    def from_folders(folders, listed=()):
        noting[0] = False
        return real_from(folders, listed)

    h.Game, h.games_from = Noting, from_folders
with contextlib.redirect_stdout(sys.stderr):
    found = h.games(extra)
if real_game and real_from:
    h.Game, h.games_from = real_game, real_from


def unlisted():
    """The Games HelixSR built but did not list, then the folders given by hand it dropped; one per folder."""
    out = []

    def add(g):
        if not any(same(g.folder, o.folder) for o in found + out):
            out.append(g)

    for g in seen:
        if not g.route:
            add(g)
    for folder in extra:
        d = Path(h.clean_path(folder)).resolve()
        if d.is_dir() and not any(h.overlaps(g.folder, d) for g in found + out):
            add(real_game(d.name, d, h.walk(d, 7)))
    return out


try:
    hidden = unlisted() if real_game and real_from else []
except Exception as error:
    print('hidden games skipped: %r' % (error,), file=sys.stderr)
    hidden = []


# HelixSR's own name for the Scaler route: 1.9.0's installer called it 'unsupported' (a game with DLSS, XeSS or
# FSR 2 and no FSR 3.1), 1.9.1 and later call it 'scaler' and refuse it ('blocked') in games with anti-cheat. All map to
# AMDLSS's 'optiscaler': a not-yet-updated 1.9.0 folder and the pinned 2.0.0 both work, and 'blocked' keeps
# AMDLSS's behaviour (a warning the player decides on).
SCALER_ROUTES = ('unsupported', 'scaler', 'blocked')


def route_of(g):
    """The route AMDLSS installs by: 'direct' (HelixSR's own drop-in) or 'optiscaler' (HelixSR Scaler).
    '' when neither can be installed."""
    if g.route in SCALER_ROUTES:
        return 'optiscaler' if g.inputs and g.target else ''
    return g.route or ''


def can_opti(g):
    """True when both routes work: the drop-in needs an FSR 3.1 DLL, the Scaler route needs DLSS/XeSS/FSR 2 and
    the folder of the game's .exe. HelixSR's installer has no can_opti (1.9.0 dropped its own), so AMDLSS decides."""
    return bool(g.fsr_targets and g.inputs and g.target)


rows = [(i, g) for i, g in enumerate(found, 1)] + [(0, g) for g in hidden]
rows = [{'index': i, 'name': g.name, 'folder': str(g.folder), 'inputs': list(g.inputs), 'fsr31': bool(g.fsr_targets),
         'can_opti': can_opti(g), 'route': route_of(g), 'state': state(g),
         'fsr_targets': [str(t) for t in g.fsr_targets],
         'exe_folder': str(g.target) if g.target else '', 'helixsr': installed_version(g)} for i, g in rows]
print('@SENTINEL@' + json.dumps(rows))
'''.replace('@SENTINEL@', gamelist.SENTINEL)


class HelixSRError(Exception):
    pass


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def install_folder(target):
    """The folder that holds the DLL once fetch() and build() have run."""
    return Path(target) / TOP


def dll_path(folder):
    """HelixSR's DLL in a release folder: HelixSR-fsrfg/ (2.0), else the folder itself (up to 1.9)."""
    new = Path(folder) / FSRFG / DLL
    return new if new.is_file() else Path(folder) / DLL


def extracted(target):
    return (install_folder(target) / SETUP).is_file() and dll_path(install_folder(target)).is_file()


def built(folder):
    return (Path(folder) / BUILT).is_file() and dll_path(folder).is_file()


def version_of(folder):
    """HelixSR's version, read the way its own installer does (a string inside the DLL); '' when unreadable."""
    try:
        with open(dll_path(folder), 'rb') as stream:
            match = re.search(rb'\x00HelixSR (\d+\.\d+\.\d+)\x00', stream.read(4 << 20))
    except OSError:
        return ''
    return match.group(1).decode() if match else ''


def version_key(text):
    """(1, 10, 0) for '1.10.0' so versions compare as numbers; None when `text` is not dotted digits."""
    return tuple(int(part) for part in text.split('.')) if re.fullmatch(r'\d+(\.\d+)*', text or '') else None


def older(version, other):
    """True when both are versions and `version` is below `other` (1.9.0 < 1.10.0)."""
    a, b = version_key(version), version_key(other)
    return a is not None and b is not None and a < b




def locate(data_dir, override=''):
    """The HelixSR folder to use, or None: `override` (AMDLSS_HELIXSR) if it holds the DLL, else <data_dir>/HelixSR-x
    for the pinned x, else the newest older HelixSR-x there (a user who has not updated yet)."""
    for folder in ((Path(override),) if override else ()) + (install_folder(data_dir),):
        if dll_path(folder).is_file():
            return folder
    found = []
    for folder in Path(data_dir).glob('HelixSR-*'):
        version = folder.name[len('HelixSR-'):]
        if older(version, VERSION) and dll_path(folder).is_file():
            found.append((version_key(version), folder))
    return max(found)[1] if found else None


def helixsr_data(windows=None):
    """The folder HelixSR's setup downloads into (%LOCALAPPDATA%\\HelixSR, or ~/.local/share/HelixSR)."""
    return host.data_home(windows) / 'HelixSR'


def portable_python():
    """The portable Python HelixSR's Linux setup downloads into ~/.local/share/HelixSR when the system has none."""
    return helixsr_data(False) / 'python' / 'bin' / 'python3'


_NUMPY = {}


def has_numpy(python):
    """True for a Python 3.8+ that imports numpy: the test helixsr-setup.sh makes. Checked once per path."""
    key = str(python)
    if key not in _NUMPY:
        try:
            done = subprocess.run([key, '-c', 'import numpy, sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'],
                                  capture_output=True, timeout=60)
            _NUMPY[key] = done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _NUMPY[key] = False
    return _NUMPY[key]


def find_python(windows=None):
    """The Python HelixSR's setup and installer run under, or None. Windows: its embedded Python in
    %LOCALAPPDATA%\\HelixSR. Linux, as in helixsr-setup.sh: HelixSR's portable Python first, then the
    system's python3, each needing numpy."""
    if host.is_windows(windows):
        found = helixsr_data(True) / 'python' / 'python.exe'
        return found if found.is_file() else None
    candidates = [portable_python()]
    system = shutil.which('python3')
    if system:
        candidates.append(Path(system))
    return next((python for python in candidates if has_numpy(python)), None)


def helixsr_python(windows=None):
    """find_python(), or where it will be once the setup has run (so error messages can name it)."""
    if host.is_windows(windows):
        return helixsr_data(True) / 'python' / 'python.exe'
    return find_python(False) or portable_python()


def consent_text(windows=None):
    downloads = ("downloads Python, numpy and the shader compiler once, about 150 MB, into %LOCALAPPDATA%\\HelixSR."
                 if host.is_windows(windows) else
                 "uses Python 3 with numpy (your system's when it has them; otherwise it downloads a portable "
                 "Python, about 70 MB, into ~/.local/share/HelixSR) and runs the shader compiler through Proton.")
    from . import scaler   # lazy: scaler imports this module
    return (f"AMDLSS downloads HelixSR {VERSION} (about {ZIP_SIZE >> 20} MB) and HelixSR Scaler {scaler.VERSION} "
            f"(about {scaler.ZIP_SIZE >> 20} MB, the package with OptiScaler and AMD's FSR 4 DLL) from "
            f"github.com/lonewolf0622/HelixSR, checks their SHA-256, and runs HelixSR's own setup.\n\n"
            f"AMDLSS also downloads NVIDIA's DLSS 310.7.0 DLL (about {DLSS_SIZE >> 20} MB) from NVIDIA's GitHub, "
            f"checks its SHA-256 and gives it to the setup. That DLL is NVIDIA's, under NVIDIA's license:\n"
            f"{NVIDIA_LICENSE_URL}\n\n"
            f"The setup builds HelixSR's network on this PC from it (about a minute) and AMDLSS deletes the DLL. "
            f"The setup's separate downloads for HelixSR's frame generation are skipped: AMDLSS does not use it. "
            f"The setup also {downloads} "
            f"The built network stays on this PC: do not share it.\n\n"
            f"Do you agree and want to continue?")


# --- download and extract -----------------------------------------------------------------------------------------

def _download(part, progress):
    """Fills `part` with HelixSR's zip (see download)."""
    download(part, progress, ZIP_URL, ZIP_SIZE)


def download(part, progress, url, size):
    """Fills `part` with the file at `url` (exactly `size` bytes), resuming what an earlier run left; the caller checks
    the hash."""
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        if have == size:
            return
        request = urllib.request.Request(url, headers={'User-Agent': 'AMDLSS'})
        if have:
            request.add_header('Range', f'bytes={have}-')
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if have and response.status != 206:
                    have = 0  # the server ignored the range: start again
                with open(part, 'ab' if have else 'wb') as out:
                    for block in iter(lambda: response.read(CHUNK), b''):
                        out.write(block)
                        have += len(block)
                        if progress:
                            progress(have, size)
        except urllib.error.HTTPError as error:
            if error.code == 416:  # the part is longer than the file: drop it
                part.unlink(missing_ok=True)
            elif attempt == 3:
                raise HelixSRError(f'download failed: HTTP {error.code} from {url}')
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as error:
            if attempt == 3:
                raise HelixSRError(f'download failed: {error}')
        if part.exists() and part.stat().st_size == size:
            return
    raise HelixSRError('download failed: the file stayed incomplete')


def safe_members(archive, target):
    """[(member, destination)] for the zip; HelixSRError when one would land outside `target` (zip-slip)."""
    root = Path(target).resolve()
    members = []
    for member in archive.infolist():
        destination = (root / member.filename).resolve()
        if destination != root and root not in destination.parents:
            raise HelixSRError(f'the zip holds a path outside the folder ({member.filename}); nothing was extracted')
        members.append((member, destination))
    return members


def unpack(part, target):
    """Unpacks the zip at `part` into `target`, every entry checked first (safe_members) so a bad one leaves nothing
    behind. Generic: the caller checks what the package should hold."""
    try:
        with zipfile.ZipFile(part) as archive:
            for member, destination in safe_members(archive, target):
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + '.tmp')
                try:
                    with archive.open(member) as source, open(temporary, 'wb') as out:
                        for block in iter(lambda: source.read(CHUNK), b''):
                            out.write(block)
                    if (member.external_attr >> 16) & 0o111:   # zipfile drops the mode; the Linux setup runs these
                        os.chmod(temporary, 0o755)
                    os.replace(temporary, destination)
                except OSError:  # no half-written copy stays
                    temporary.unlink(missing_ok=True)
                    raise
    except zipfile.BadZipFile as error:
        raise HelixSRError(f'the zip is damaged: {error}')


def extract(part, target):
    """Unpacks HelixSR's zip into `target` (nothing stays behind when an entry escapes it)."""
    unpack(part, target)
    if not extracted(target):
        raise HelixSRError(f'the zip does not hold {TOP}/{SETUP} and {TOP}/{FSRFG}/{DLL}')


def write_package_record(target, folder=None):
    """Remembers which pinned zip was extracted into `target`: `package_current` trusts a folder only with this
    record. Written the moment the verified zip is on disk, so an earlier AMDLSS's folder (or another zip's) never
    passes for the current package."""
    folder = Path(folder) if folder else install_folder(target)
    (Path(target) / PACKAGE).write_text(json.dumps(
        {'version': VERSION, 'zip_name': ZIP_NAME, 'zip_sha256': ZIP_SHA256, 'folder': folder.name},
        indent=1) + '\n', encoding='utf-8')


def package_current(data_dir):
    """True when the pinned zip's files were extracted into this data dir (see write_package_record) and they went
    to the pinned folder. A folder without that record, or one from another zip, counts as outdated."""
    try:
        record = json.loads((Path(data_dir) / PACKAGE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get('zip_sha256') == ZIP_SHA256 and record.get('folder') == TOP


def forget_build(folder):
    """Drops the build record of the files a fresh extraction just replaced, so a failure of the coming build cannot
    let the replaced build pass for the new files. build() writes it again; the extracted package carries none."""
    (Path(folder) / BUILT).unlink(missing_ok=True)


def fetch(target, progress=None):
    """Downloads, verifies and extracts the pinned release into `target`; returns the HelixSR folder.
    `progress(done, total)` is optional. Raises HelixSRError (OSError when a file cannot be written)."""
    target = Path(target)
    work = target / '.download'
    work.mkdir(parents=True, exist_ok=True)
    part = work / (ZIP_NAME + '.part')
    _download(part, progress)
    if part.stat().st_size != ZIP_SIZE or sha256_of(part) != ZIP_SHA256:
        part.unlink(missing_ok=True)
        raise HelixSRError('the downloaded zip does not match the pinned SHA-256; it was deleted')
    extract(part, target)
    folder = install_folder(target)
    forget_build(folder)             # the replaced folder's build records must not pass for these files
    write_package_record(target, folder)
    part.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    return folder


def fetch_dlss(target, progress=None):
    """Downloads NVIDIA's DLSS 310.7.0 DLL (pinned size and SHA-256) into `target`/.download and returns its path. The
    setup builds HelixSR's network from it; it asks for it itself only when its caller passes `-Yes` / `--yes`, which
    AMDLSS cannot (see setup_command). The caller deletes it after the build (drop_dlss)."""
    work = Path(target) / '.download'
    work.mkdir(parents=True, exist_ok=True)
    part, path = work / (DLSS_NAME + '.part'), work / DLSS_NAME
    download(part, progress, DLSS_URL, DLSS_SIZE)
    if part.stat().st_size != DLSS_SIZE or sha256_of(part) != DLSS_SHA256:
        part.unlink(missing_ok=True)
        raise HelixSRError("the downloaded NVIDIA DLSS DLL does not match the pinned SHA-256; it was deleted")
    os.replace(part, path)
    return path


def drop_dlss(dlss):
    """Deletes the DLSS DLL fetch_dlss made (NVIDIA's file: it is not kept) and its work folder when empty."""
    Path(dlss).unlink(missing_ok=True)
    try:
        Path(dlss).parent.rmdir()
    except OSError:
        pass


# --- running HelixSR's own scripts --------------------------------------------------------------------------------

def child_env():
    """UTF-8 for every child, so game names with accents come back as written."""
    return {**host.clean_env(), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}


def feed_lines(chunks):
    """(text, final) for each line in a stream of byte chunks. A line ended by a bare carriage return is a progress
    update (final False); one ended by a newline is a finished line (final True)."""
    buffer = b''
    for chunk in chunks:
        buffer += chunk
        while True:
            match = re.search(rb'\r\n|\n|\r', buffer)
            if not match or (match.group() == b'\r' and match.end() == len(buffer)):
                break  # a lone \r at the end may be the first half of \r\n
            text = buffer[:match.start()].decode('utf-8', errors='replace').strip()
            buffer = buffer[match.end():]
            if text:
                yield text, match.group() != b'\r'
    tail = buffer.decode('utf-8', errors='replace').strip()
    if tail:
        yield tail, True


def run_streaming(command, cwd, on_line=None, timeout=COMMAND_TIMEOUT, env=None):
    """Runs `command` with stdin closed and no console window, passing each line to on_line(text, final).
    Returns (exit code, the last few finished lines). A process that outlives `timeout` is killed."""
    try:
        process = subprocess.Popen([str(part) for part in command], cwd=str(cwd), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
                                   env=env or child_env())
    except OSError as error:
        raise HelixSRError(f'could not start {Path(str(command[0])).name}: {error}')
    timer = threading.Timer(timeout, process.kill)
    timer.start()
    tail = []
    try:
        for text, final in feed_lines(iter(lambda: process.stdout.read1(4096), b'')):
            if final:
                tail = (tail + [text])[-5:]
            if on_line:
                on_line(text, final)
        code = process.wait()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
    return code, tail


def setup_command(windows=None, dlss=None):
    """HelixSR's own setup, unmodified; run with the HelixSR folder as the working directory and stdin closed. Not
    `-Yes` / `--yes`: HelixSR 2.0's setup ends by running its games step with the same flag, which installs HelixSR
    into every game it finds (1.9's did not pass it on). Without it that step reads stdin, gets the end of the input
    on the first game and changes nothing. So the consent AMDLSS asked for is carried by the files it passes: the
    DLSS DLL (`-Dlss`, `--dlss`), and no frame generation build (`-NoFg`, `--no-fg`), which would ask for NVIDIA's
    frame generation DLLs and NGX core. `dlss` is None only when the network is built already."""
    if host.is_windows(windows):
        return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SETUP_PS1, '-NoFg',
                *(['-Dlss', str(dlss)] if dlss else [])]
    return ['bash', SETUP_SH, '--no-fg', *(['--dlss', str(dlss)] if dlss else [])]


def with_wine(env, data_dir):
    """(env, found): the setup's environment, and whether it has a wine to run the shader compiler. The setup looks for
    Proton in a few fixed Steam folders and for `wine` on PATH; a Proton in another Steam library would be missed, so a
    one-line `wine` wrapper for it goes into <data dir>/bin, first on PATH."""
    if host.setup_finds_wine(env):
        return env, True
    wine = host.find_proton_wine()
    if wine is None:
        return env, False
    shim = Path(data_dir) / 'bin'
    shim.mkdir(parents=True, exist_ok=True)
    (shim / 'wine').write_text(f'#!/bin/sh\nexec {shlex.quote(str(wine))} "$@"\n', encoding='utf-8')
    (shim / 'wine').chmod(0o755)
    return {**env, 'PATH': f'{shim}{os.pathsep}{env.get("PATH", "")}'}, True


NO_WINE_HINT = ('AMDLSS found no Proton in any Steam library and no wine on PATH: install Proton in Steam (any '
                'library) or wine, then try again.')


class SetupLog:
    """Appends HelixSR's setup output to <data dir>/SETUP_LOG and passes every line on to `sink(text, final)`
    (the window). Call it like an on_line function. The build's output is otherwise lost with the window, which
    is what makes a frame generation failure undiagnosable after the fact; Collect logs and Send logs pick the
    file up. A log that cannot be written never stops a build."""

    def __init__(self, data_dir, sink=None):
        self.path = Path(data_dir) / SETUP_LOG if data_dir else None
        self.sink = sink

    def write(self, text):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'a', encoding='utf-8') as stream:
                stream.write(text + '\n')
        except OSError:
            pass

    def start(self, heading):
        self.write(f'=== {time.strftime("%Y-%m-%d %H:%M:%S")} {heading} ===')

    def __call__(self, text, final=True):
        if final:
            self.write(text)
        if self.sink:
            self.sink(text, final)


def build(folder, on_line=None, windows=None, data_dir=None, dlss=None):
    """Runs the setup in `folder`. Raises HelixSRError unless it ends with the network built. On Linux `data_dir`
    is where a wine wrapper may be written (see with_wine); it also gets the setup's output as SETUP_LOG. The
    setup itself builds only what `folder` is missing (HelixSR's own scripts do); `dlss` is the DLSS DLL it builds
    from (fetch_dlss), needed unless `folder` is built already."""
    folder = Path(folder)
    log = SetupLog(data_dir, on_line)
    if not built(folder):
        (folder / BUILT).unlink(missing_ok=True)  # a file of an earlier build must not pass for this one
    env, wine = child_env(), True
    if not host.is_windows(windows):
        if find_python(False) is None:
            # no Python with numpy: the setup would offer a sudo package install it cannot ask about from a window,
            # so take its portable-Python route (the read-only-system one) instead
            env['HELIXSR_FORCE_PORTABLE'] = '1'
        if data_dir is not None:
            env, wine = with_wine(env, data_dir)
    command = setup_command(windows, None if built(folder) else dlss)
    log.start(f"HelixSR's setup: {' '.join(command)} (in {folder})")
    code, tail = run_streaming(command, folder, log, BUILD_TIMEOUT, env)
    if code != 0:
        hint = f' {NO_WINE_HINT}' if not wine and any('Proton or Wine' in line for line in tail) else ''
        raise HelixSRError(f'HelixSR setup failed (exit {code}): {tail[-1] if tail else "no output"}{hint}')
    if not built(folder):
        raise HelixSRError(f'HelixSR setup finished but {BUILT} is missing in {folder}')


def install_helixsr(data_dir, consented, progress=None, on_line=None, force=False):
    """Fetch (unless already extracted and `force` is off), then build. Runs nothing at all unless `consented`
    (the user answered yes to consent_text()). Returns the HelixSR folder, or None when declined. The pinned
    HelixSR Scaler package is fetched too (it is where AMD's FSR 4 DLL lives since 1.9.0), so FSR 4.1.1 works
    right after this, without an OptiScaler install; a failed Scaler download does not undo the HelixSR install
    (the FSR 4.1.1 message then says the download failed)."""
    if not consented:
        return None
    if force or not extracted(data_dir):
        fetch(data_dir, progress)
    folder = install_folder(data_dir)
    dlss = None if built(folder) else fetch_dlss(data_dir, progress)   # NVIDIA's DLL: the setup builds from it
    try:
        build(folder, on_line, data_dir=data_dir, dlss=dlss)
    finally:
        if dlss:
            drop_dlss(dlss)
    from . import scaler   # lazy: scaler imports this module
    try:
        if scaler.locate(data_dir) is None:
            scaler.fetch(data_dir, progress)
    except (HelixSRError, OSError) as error:
        if on_line:
            on_line(f'HelixSR Scaler was not downloaded ({error}): FSR 4.1.1 will not be available until it is',
                    True)
    return folder


# --- is the game still running? -----------------------------------------------------------------------------------

# How a locked game file reads in the installer's output, in English and in a Spanish Windows' words.
LOCKED = ('permissionerror', 'errno 13', 'winerror 5', 'access is denied', 'acceso denegado')
PROCESS_SCRIPT = ('Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path } | '
                  'Select-Object -ExpandProperty Path')


def still_running_message(name):
    """What the window says when a game's files cannot be touched because the game is open."""
    return f"Close {name} first. It is still running, so its files can't be replaced."


def plain_failure(text, name):
    """still_running_message(name) when `text` (a failed installer's output) says its file was locked, else None."""
    return still_running_message(name) if any(marker in text.lower() for marker in LOCKED) else None


def inside(path, folder, windows=None):
    """True when `path` is `folder` or under it, compared the way that system's paths compare."""
    pathmod = host.path_module(windows)
    path, folder = (pathmod.normcase(pathmod.normpath(p)) for p in (path, folder))
    return path == folder or path.startswith(folder if folder.endswith(pathmod.sep) else folder + pathmod.sep)


def process_paths(run=None):
    """The executable path of every running process, asked of PowerShell; [] when the query fails. `run(command)`
    replaces the subprocess call and returns its output text (tests)."""
    command = ['powershell', '-NoProfile', '-NonInteractive', '-Command', PROCESS_SCRIPT]
    if run is not None:
        out = run(command)
    else:
        try:
            done = subprocess.run(command, capture_output=True, timeout=30, creationflags=NO_WINDOW, env=child_env())
        except (OSError, subprocess.TimeoutExpired):
            return []
        out = done.stdout.decode('utf-8', errors='replace')
    return [line.strip() for line in out.splitlines() if line.strip()]


def _proc_path(path):
    """`path` spelled the Linux way: backslashes become slashes and a Windows extended-length prefix (\\?\\,
    which a readlink under wine returns) comes off. Proton names a game by its Windows path (Z:\\...), and this
    Linux check is itself tested on Windows, where every path arrives Windows-spelled."""
    path = str(path).replace('\\', '/')
    if path.startswith('//?/UNC/'):
        path = '//' + path[8:]
    elif path.startswith('//?/'):
        path = path[4:]
    return path


def _running_from_proc(folder, proc):
    """The Linux half of running_from(): one /proc entry per process, its exe link and its command line."""
    try:
        entries = list(proc.iterdir())
    except OSError:
        return False
    base = _proc_path(folder).rstrip('/')
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            exe = _proc_path(os.readlink(entry / 'exe').removesuffix(' (deleted)'))
        except OSError:
            exe = ''
        if exe and inside(exe, base, False):
            return True
        try:
            cmdline = (entry / 'cmdline').read_bytes().decode('utf-8', errors='replace')
        except OSError:
            continue
        for arg in cmdline.split('\x00'):
            shown = _proc_path(arg)
            if arg and (base + '/' in shown or shown.endswith(base) or inside(shown, base, False)):
                return True
    return False


def running_from(folder, windows=None, run=None, proc=None):
    """True when a process runs from inside `folder`, best effort: Windows asks PowerShell for every process path,
    Linux reads /proc (the exe link, else a command line naming the folder, which is how Proton shows). A query
    that fails answers False, so a broken probe never blocks an install."""
    if not folder:
        return False
    try:
        if host.is_windows(windows):
            return any(inside(path, folder, True) for path in process_paths(run))
        return _running_from_proc(folder, Path(proc) if proc else Path('/proc'))
    except OSError:
        return False


def game_running(game, windows=None, run=None, proc=None):
    """True when a process runs from inside `game`'s folder (see running_from). A drive root listed as the
    folder would match every process on the drive: only the .exe folder counts then."""
    folder = game.exe_folder if gamelist.is_root_folder(game.folder) else (game.folder or game.exe_folder)
    return running_from(folder, windows, run, proc)


class HelixSR:
    """A built HelixSR folder and the games it can be installed into. HelixSR's files are only read, except by its
    own installer when AMDLSS runs it."""

    def __init__(self, folder, python=None, running=None, preserve=None):
        self.folder = Path(folder)
        self._python_given = python is not None   # an explicit Python is used as given (tests); else find_python()'s
        self.python = Path(python or helixsr_python())
        self.running = running if running is not None else game_running   # is the game still open? (tests say so)
        self.preserve = preserve   # keep the game's logs before a change (amdlss.logs.preserve_history), or None

    @property
    def version(self):
        return version_of(self.folder)

    @property
    def installer(self):
        return self.folder / INSTALLER

    @property
    def modern(self):
        """True for a HelixSR 2.0 folder: its JSON bridge installs and removes (a 1.9 folder: its installer, by number)."""
        return (self.folder / BRIDGE_SCRIPT).is_file()

    def _need_python(self):
        if self.python.is_file():
            return
        if not self._python_given and not host.WINDOWS:
            raise HelixSRError(f"HelixSR's Python is missing: no Python 3 with numpy (the system python3 or "
                               f"HelixSR's portable Python under {portable_python().parent.parent}): "
                               f"run Install HelixSR again")
        raise HelixSRError(f"HelixSR's Python is missing ({self.python}): run Install HelixSR again")

    @staticmethod
    def safe_extras(extras):
        """Folders added by hand that may reach HelixSR: a drive root never does (its scan widens to every game)."""
        return [str(extra) for extra in extras if not gamelist.is_root_folder(extra)]

    @classmethod
    def folder_flags(cls, extras):
        flags = []
        for extra in cls.safe_extras(extras):
            flags += ['--folder', extra]
        return flags

    def list_command(self, extras=()):
        return [self.python, self.installer, self.folder, 'list', *self.folder_flags(extras)]

    def install_command(self, index, extras=()):
        return [self.python, self.installer, self.folder, 'install', str(index), *self.folder_flags(extras)]

    def remove_command(self, index, extras=()):
        return [self.python, self.installer, self.folder, 'uninstall', str(index), *self.folder_flags(extras)]

    def bridge_command(self, extras=()):
        return [self.python, '-I', '-', self.folder, *[str(extra) for extra in extras]]

    def cli_command(self, verb, game):
        """HelixSR 2.0's own bridge for one game, by its folder: `install` puts the FSR drop-in (the Scaler route is
        AMDLSS's own, amdlss.scaler), `remove` takes HelixSR out (whichever way it is in the game)."""
        extra = ['--way', 'fsr'] if verb == 'install' else []
        return [self.python, '-I', self.folder / BRIDGE_SCRIPT, self.folder, verb, '--game', game.folder, *extra]

    def list_games(self, extras=()):
        """The games HelixSR finds in the Steam libraries plus `extras` (folders added by hand)."""
        self._need_python()
        extras = self.safe_extras(extras)
        try:
            done = subprocess.run([str(part) for part in self.bridge_command(extras)], input=BRIDGE.encode(),
                                  capture_output=True, timeout=120, creationflags=NO_WINDOW, env=child_env())
            found = gamelist.parse_bridge_output(done.stdout.decode('utf-8', errors='replace'))
            if done.returncode == 0 and found is not None:
                return found
            why = done.stderr.decode('utf-8', errors='replace').strip().splitlines()
        except (OSError, subprocess.TimeoutExpired) as error:
            why = [str(error)]
        if self.modern:   # a 2.0 installer has no printed list to fall back on
            raise HelixSRError(f"HelixSR's game list failed: {why[-1] if why else 'no output'}")
        return self._list_from_printout(extras)

    def _list_from_printout(self, extras):
        lines = []
        code, _tail = run_streaming(self.list_command(extras), self.folder,
                                    lambda text, final: lines.append(text) if final else None, 120)
        if code != 0:
            raise HelixSRError(f"HelixSR's game list failed (exit {code}): {lines[-1] if lines else 'no output'}")
        return gamelist.parse_list_output('\n'.join(lines))

    def _need_closed(self, game):
        """A running game holds its DLLs: name that instead of letting the installer die on a locked file."""
        if self.running(game):
            raise HelixSRError(still_running_message(game.name))

    @staticmethod
    def _need_safe(game):
        """A folder that covers other games is never changed: HelixSR acts by list number, and that number's
        FSR DLLs are every game's on the drive. Checked again on the fresh list in _act."""
        reason = game.reason or (gamelist.UNSAFE_FOLDER if gamelist.is_root_folder(game.folder) else '')
        if reason:
            raise HelixSRError(f'AMDLSS will not change {game.name or "this game"}: {reason}')

    def guard(self, game, extras):
        """The checks every change to a game starts with, for changes AMDLSS makes itself too: the folder is safe,
        the game is closed, and the game is still in HelixSR's fresh list. Keeps the game's logs, and returns the
        game as listed now. For everything that runs HelixSR's installer by the game's list number."""
        self._need_safe(game)
        self._need_closed(game)
        return self._fresh(game, extras)

    def guard_placed(self, game, extras):
        """The checks for a change AMDLSS places itself (HelixSR Scaler, and the injected route): the folder is safe
        and the game is closed. A game HelixSR's list does not hold at all is fine here: a game with no upscaler of
        its own is what the injected route is for, and HelixSR's games() drops it, so it has no number - and this path
        never runs the installer by one. When the list does hold the game, its fresh entry is what comes back."""
        self._need_safe(game)
        self._need_closed(game)
        return self._fresh(game, extras, numbered=False)

    @staticmethod
    def need_no_fsr4(game):
        """FSR 4.1.1 (amdlss.fsr4) in a game's drop-in folders and HelixSR's installer must not meet: HelixSR would
        take our DLL for the game's own and overwrite the real one's backup."""
        for target in game.fsr_targets:
            if (Path(target).parent / FSR4_MARKER).is_file():
                raise HelixSRError(f'FSR 4.1.1 is installed in {game.name}: remove it first (pick HelixSR as the '
                                   f'upscaler and press Update, or press Remove).')

    def _fresh(self, game, extras, numbered=True):
        listed = self.list_games(extras)
        fresh = gamelist.match_game(listed, game)
        if fresh is None:
            raise HelixSRError(f'{game.name} is no longer in HelixSR\'s list: refresh the list and try again')
        if gamelist.unsafe_reason(fresh, listed, extras=extras):
            raise HelixSRError(f'AMDLSS will not change {game.name or "this game"}: {gamelist.UNSAFE_FOLDER}')
        if numbered and not fresh.index:  # a game HelixSR does not list has no number to install or remove by
            raise HelixSRError(f'HelixSR does not list {game.name}, so it cannot be changed from here')
        if self.preserve:   # the run about to be replaced is evidence: keep its logs, never block on the copy
            try:
                self.preserve(fresh)
            except Exception:   # noqa: BLE001 (a failed copy must not stop the install)
                pass
        return fresh

    def _run_cli(self, verb, fresh, on_line):
        """Runs HelixSR 2.0's bridge (one JSON object out: ok, error, messages), its messages going to `on_line`."""
        try:
            done = subprocess.run([str(part) for part in self.cli_command(verb, fresh)], cwd=str(self.folder),
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=COMMAND_TIMEOUT,
                                  creationflags=NO_WINDOW, env=child_env())
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HelixSRError(f'HelixSR could not run for {fresh.name}: {error}')
        text = done.stdout.decode('utf-8', errors='replace')
        try:
            answer = json.loads(next(line for line in reversed(text.splitlines()) if line.strip().startswith('{')))
        except (StopIteration, ValueError):
            answer = {}
        for message in answer.get('messages') or []:
            if on_line:
                on_line(str(message), True)
        if not answer.get('ok'):
            why = str(answer.get('error') or '') or (text.strip().splitlines() or ['no output'])[-1]
            raise HelixSRError(plain_failure(why, fresh.name) or
                               f'HelixSR failed for {fresh.name} (exit {done.returncode}): {why}')

    def _act(self, verb, game, extras, on_line):
        """Installs ('install') or removes ('uninstall') HelixSR's drop-in: through the 2.0 bridge by the game's folder,
        a 1.9 folder through its installer by the game's number in its fresh list."""
        fresh = self._fresh(game, extras)
        if self.modern:
            self._run_cli('install' if verb == 'install' else 'remove', fresh, on_line)
        else:
            build_command = self.install_command if verb == 'install' else self.remove_command
            code, tail = run_streaming(build_command(fresh.index, extras), self.folder, on_line)
            if code != 0:   # a locked file is said in plain words; the raw output stays in the game's install log
                raise HelixSRError(plain_failure('\n'.join(tail), game.name) or
                                   f'HelixSR failed for {game.name} (exit {code}): {tail[-1] if tail else "no output"}')
        after = gamelist.match_game(self.list_games(extras), game)
        if after is None:
            raise HelixSRError(f'{game.name} disappeared from the list after the change: refresh the list')
        return fresh, after

    def install(self, game, extras=(), on_line=None):
        """Installs HelixSR's drop-in into `game` (a game with an FSR 3.1 DLL); returns the game as listed
        afterwards. The OptiScaler route is HelixSR Scaler, which AMDLSS installs itself (amdlss.scaler). Raises
        HelixSRError when HelixSR fails or the game does not show the drop-in installed."""
        self._need_safe(game)
        self._need_closed(game)
        self.need_no_fsr4(game)
        _fresh, after = self._act('install', game, extras, on_line)
        if after.state not in ('direct', 'unknown'):  # 'unknown': only the printed list was readable
            raise HelixSRError(f'HelixSR finished but {game.name} shows as "{after.state_text}", not '
                               f'"{gamelist.STATE_TEXT["direct"]}": read the log above')
        after.route = 'direct'
        return after

    def remove(self, game, extras=(), on_line=None):
        """Removes HelixSR from `game` (HelixSR's own uninstall: everything put back as it was)."""
        self._need_safe(game)
        self._need_closed(game)
        _fresh, after = self._act('uninstall', game, extras, on_line)
        if after.installed:
            raise HelixSRError(f'HelixSR finished but {game.name} still shows as "{after.state_text}": '
                               f'read the log above')
        return after
