# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything AMDLSS does with HelixSR lives in this file, and nothing in it changes HelixSR.

HelixSR (https://github.com/lonewolf0622/HelixSR, closed-source freeware DLL, Apache-2.0 setup scripts) runs NVIDIA's
DLSS network as Direct3D 12 compute on AMD cards. This module:

  - fetches the pinned official release zip (resumable, SHA-256 checked, zip-slip checked) and extracts it;
  - runs HelixSR's own `helixsr-setup.ps1 -Yes` with stdin closed (`-Yes` accepts NVIDIA's DLSS DLL download, so the
    caller must have asked the user: install() does nothing unless `consented` is true);
  - lists the games, installs and removes per game by running HelixSR's own setup/helixsr_install.py with HelixSR's
    embedded Python (`install N`, `uninstall N`, `--folder PATH`). The list comes from a small read-only bridge run in
    that same Python (it imports helixsr_install and prints JSON); if the bridge cannot run, the installer's printed
    `list` output is parsed instead.

The pinned files are HelixSR 1.8.1. Nothing is bundled or redistributed: the zip comes from HelixSR's own release.
"""
import hashlib
import http.client
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import games as gamelist

VERSION = '1.8.1'
ZIP_NAME = f'HelixSR-{VERSION}.zip'
ZIP_URL = f'https://github.com/lonewolf0622/HelixSR/releases/download/v{VERSION}/{ZIP_NAME}'
ZIP_SIZE = 56650779
ZIP_SHA256 = '5b2d06db22d59aaa8c931fc06d3a8e354e65dde1bef9fce90086542f02d4a627'
TOP = f'HelixSR-{VERSION}'  # the folder the zip extracts to
DLL = 'amd_fidelityfx_dx12.dll'
BUILT = 'helixsr_setup.json'  # written beside the DLL when the setup has built the network
SETUP = 'helixsr-setup.ps1'
INSTALLER = Path('setup') / 'helixsr_install.py'
NVIDIA_LICENSE_URL = 'https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt'
CHUNK = 1 << 20
BUILD_TIMEOUT = 1800  # seconds; the build takes about a minute plus the first-time downloads
COMMAND_TIMEOUT = 600  # seconds for one install or remove
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Runs inside HelixSR's own Python, read-only: HelixSR's scan, as JSON, with the state of each game.
BRIDGE = '''
import contextlib, json, os, sys
here, extra = sys.argv[1], sys.argv[2:]
sys.path.insert(0, os.path.join(here, 'setup'))
import helixsr_install as h


def state(g):
    if g.dlss_installed:
        return 'dlss'
    if g.opti_installed:
        return 'optiscaler'
    if any(h.original(t).exists() and h.is_helixsr(t) for t in g.fsr_targets):
        return 'direct'
    return 'none'


with contextlib.redirect_stdout(sys.stderr):
    found = h.games(extra)
rows = [{'index': i, 'name': g.name, 'folder': str(g.folder), 'inputs': list(g.inputs), 'fsr31': bool(g.fsr_targets),
         'route': g.route, 'state': state(g), 'fsr_targets': [str(t) for t in g.fsr_targets],
         'exe_folder': str(g.target) if g.target else ''} for i, g in enumerate(found, 1)]
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


def extracted(target):
    return (install_folder(target) / SETUP).is_file() and (install_folder(target) / DLL).is_file()


def built(folder):
    return (Path(folder) / BUILT).is_file() and (Path(folder) / DLL).is_file()


def version_of(folder):
    """HelixSR's version, read the way its own installer does (a string inside the DLL); '' when unreadable."""
    try:
        with open(Path(folder) / DLL, 'rb') as stream:
            match = re.search(rb'\x00HelixSR (\d+\.\d+\.\d+)\x00', stream.read(4 << 20))
    except OSError:
        return ''
    return match.group(1).decode() if match else ''


def locate(data_dir, override=''):
    """The HelixSR folder to use, or None: `override` (AMDLSS_HELIXSR) if it holds the DLL, else <data_dir>/HelixSR-x."""
    for folder in ((Path(override),) if override else ()) + (install_folder(data_dir),):
        if (folder / DLL).is_file():
            return folder
    return None


def helixsr_python():
    """The embedded Python HelixSR's setup downloads into %LOCALAPPDATA%\\HelixSR."""
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'HelixSR' / 'python' / 'python.exe'


def consent_text():
    return (f"AMDLSS downloads HelixSR {VERSION} (about 57 MB) from github.com/lonewolf0622/HelixSR, checks its "
            f"SHA-256, and runs HelixSR's own setup.\n\n"
            f"The setup downloads NVIDIA's DLSS 310.7.0 DLL from NVIDIA's GitHub. That DLL is NVIDIA's, under "
            f"NVIDIA's license:\n{NVIDIA_LICENSE_URL}\n\n"
            f"The setup builds HelixSR's network on this PC from it (about a minute) and deletes the DLL. It also "
            f"downloads Python, numpy and the shader compiler once, about 150 MB, into %LOCALAPPDATA%\\HelixSR. "
            f"The built network stays on this PC: do not share it.\n\n"
            f"Do you agree and want to continue?")


# --- download and extract -----------------------------------------------------------------------------------------

def _download(part, progress):
    """Fills `part` with the zip, resuming what an earlier run left; the caller checks the hash."""
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have > ZIP_SIZE:
            part.unlink()
            have = 0
        if have == ZIP_SIZE:
            return
        request = urllib.request.Request(ZIP_URL, headers={'User-Agent': 'AMDLSS'})
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
                            progress(have, ZIP_SIZE)
        except urllib.error.HTTPError as error:
            if error.code == 416:  # the part is longer than the file: drop it
                part.unlink(missing_ok=True)
            elif attempt == 3:
                raise HelixSRError(f'download failed: HTTP {error.code} from {ZIP_URL}')
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as error:
            if attempt == 3:
                raise HelixSRError(f'download failed: {error}')
        if part.exists() and part.stat().st_size == ZIP_SIZE:
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


def extract(part, target):
    """Unpacks the zip into `target`, every entry checked first so a bad one leaves nothing behind."""
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
                    os.replace(temporary, destination)
                except OSError:  # no half-written copy stays
                    temporary.unlink(missing_ok=True)
                    raise
    except zipfile.BadZipFile as error:
        raise HelixSRError(f'the zip is damaged: {error}')
    if not extracted(target):
        raise HelixSRError(f'the zip does not hold {TOP}\\{SETUP} and {TOP}\\{DLL}')


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
    part.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    return install_folder(target)


# --- running HelixSR's own scripts --------------------------------------------------------------------------------

def child_env():
    """UTF-8 for every child, so game names with accents come back as written."""
    return {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}


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


def run_streaming(command, cwd, on_line=None, timeout=COMMAND_TIMEOUT):
    """Runs `command` with stdin closed and no console window, passing each line to on_line(text, final).
    Returns (exit code, the last few finished lines). A process that outlives `timeout` is killed."""
    try:
        process = subprocess.Popen([str(part) for part in command], cwd=str(cwd), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
                                   env=child_env())
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


def setup_command():
    """HelixSR's own setup, unmodified; run with the HelixSR folder as the working directory."""
    return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SETUP, '-Yes']


def build(folder, on_line=None):
    """Runs the setup in `folder`. Raises HelixSRError unless it ends with the network built."""
    folder = Path(folder)
    (folder / BUILT).unlink(missing_ok=True)  # a file of an earlier build must not pass for this one
    code, tail = run_streaming(setup_command(), folder, on_line, BUILD_TIMEOUT)
    if code != 0:
        raise HelixSRError(f'HelixSR setup failed (exit {code}): {tail[-1] if tail else "no output"}')
    if not built(folder):
        raise HelixSRError(f'HelixSR setup finished but {BUILT} is missing in {folder}')


def install_helixsr(data_dir, consented, progress=None, on_line=None, force=False):
    """Fetch (unless already extracted and `force` is off), then build. Runs nothing at all unless `consented`
    (the user answered yes to consent_text()). Returns the HelixSR folder, or None when declined."""
    if not consented:
        return None
    if force or not extracted(data_dir):
        fetch(data_dir, progress)
    folder = install_folder(data_dir)
    build(folder, on_line)
    return folder


class HelixSR:
    """A built HelixSR folder and the games it can be installed into. HelixSR's files are only read, except by its
    own installer when AMDLSS runs it."""

    def __init__(self, folder, python=None):
        self.folder = Path(folder)
        self.python = Path(python or helixsr_python())

    @property
    def version(self):
        return version_of(self.folder)

    @property
    def installer(self):
        return self.folder / INSTALLER

    def _need_python(self):
        if not self.python.is_file():
            raise HelixSRError(f"HelixSR's Python is missing ({self.python}): run Install HelixSR again")

    @staticmethod
    def folder_flags(extras):
        flags = []
        for extra in extras:
            flags += ['--folder', str(extra)]
        return flags

    def list_command(self, extras=()):
        return [self.python, self.installer, self.folder, 'list', *self.folder_flags(extras)]

    def install_command(self, index, extras=()):
        return [self.python, self.installer, self.folder, 'install', str(index), *self.folder_flags(extras)]

    def remove_command(self, index, extras=()):
        return [self.python, self.installer, self.folder, 'uninstall', str(index), *self.folder_flags(extras)]

    def bridge_command(self, extras=()):
        return [self.python, '-I', '-', self.folder, *[str(extra) for extra in extras]]

    def list_games(self, extras=()):
        """The games HelixSR finds in the Steam libraries plus `extras` (folders added by hand)."""
        self._need_python()
        extras = [str(extra) for extra in extras]
        try:
            done = subprocess.run([str(part) for part in self.bridge_command(extras)], input=BRIDGE.encode(),
                                  capture_output=True, timeout=120, creationflags=NO_WINDOW, env=child_env())
            found = gamelist.parse_bridge_output(done.stdout.decode('utf-8', errors='replace'))
            if done.returncode == 0 and found is not None:
                return found
        except (OSError, subprocess.TimeoutExpired):
            pass
        return self._list_from_printout(extras)

    def _list_from_printout(self, extras):
        lines = []
        code, _tail = run_streaming(self.list_command(extras), self.folder,
                                    lambda text, final: lines.append(text) if final else None, 120)
        if code != 0:
            raise HelixSRError(f"HelixSR's game list failed (exit {code}): {lines[-1] if lines else 'no output'}")
        return gamelist.parse_list_output('\n'.join(lines))

    def _act(self, build_command, game, extras, on_line):
        fresh = gamelist.match_game(self.list_games(extras), game)
        if fresh is None:
            raise HelixSRError(f'{game.name} is no longer in HelixSR\'s list: refresh the list and try again')
        code, tail = run_streaming(build_command(fresh.index, extras), self.folder, on_line)
        if code != 0:
            raise HelixSRError(f'HelixSR failed for {game.name} (exit {code}): {tail[-1] if tail else "no output"}')
        after = gamelist.match_game(self.list_games(extras), game)
        if after is None:
            raise HelixSRError(f'{game.name} disappeared from the list after the change: refresh the list')
        return fresh, after

    def install(self, game, extras=(), on_line=None):
        """Installs HelixSR's default route into `game`; returns the game as listed afterwards. Raises HelixSRError
        when HelixSR fails or the game does not show the route installed."""
        fresh, after = self._act(self.install_command, game, extras, on_line)
        if after.state not in (fresh.route, 'unknown'):  # 'unknown': only the printed list was readable
            raise HelixSRError(f'HelixSR finished but {game.name} shows as "{after.state_text}", not '
                               f'"{gamelist.STATE_TEXT.get(fresh.route, fresh.route)}": read the log above')
        after.route = fresh.route
        return after

    def remove(self, game, extras=(), on_line=None):
        """Removes HelixSR from `game` (HelixSR's own uninstall: everything put back as it was)."""
        _fresh, after = self._act(self.remove_command, game, extras, on_line)
        if after.installed:
            raise HelixSRError(f'HelixSR finished but {game.name} still shows as "{after.state_text}": '
                               f'read the log above')
        return after
