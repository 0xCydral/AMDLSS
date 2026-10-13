# SPDX-License-Identifier: GPL-2.0-or-later
"""AMDLSS updates itself from its GitHub releases, the way the Bloodborne port's launcher does.

Version order, the beta switch and the release lookup are adapted from launcher/bbport_launcher_win.py of that port
(https://github.com/0xCydral/bloodborne_pc, GPL-2.0-or-later). The self-replace differs because AMDLSS is one exe:

  1. the release's AMDLSS.exe is downloaded next to the running exe and its SHA-256 is checked against the `digest`
     GitHub lists for the asset (no digest, or another one: nothing is replaced);
  2. the running AMDLSS.exe is renamed to AMDLSS.exe.old (Windows allows that for a running exe), the new file is
     moved into its place, the new exe starts with the same arguments and the old one exits;
  3. the next start deletes AMDLSS.exe.old.

On Linux the asset is `AMDLSS-linux-x86_64` and step 2 is one os.replace over the running file (it keeps running
from the old inode), the new file taking the old one's mode; there is no .old to clean up.

From source nothing is replaced: the window only offers the release page. `AMDLSS.exe --update` does the whole thing
without a window. AMDLSS_FAKE_VERSION makes the running version look like another one (tests only).
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional

from . import VERSION, host

RELEASES_API = 'https://api.github.com/repos/0xCydral/AMDLSS/releases?per_page=100'
RELEASES_PAGE = 'https://github.com/0xCydral/AMDLSS/releases'
ASSET_WINDOWS, ASSET_LINUX = 'AMDLSS.exe', 'AMDLSS-linux-x86_64'


def asset_name(windows=None):
    """The release file this system runs."""
    return ASSET_WINDOWS if host.is_windows(windows) else ASSET_LINUX


ASSET_NAME = asset_name()
OLD_SUFFIX, PART_SUFFIX = '.old', '.download'
CHUNK = 1 << 20
FROZEN = bool(getattr(sys, 'frozen', False))


class UpdateError(Exception):
    """A reason, in one line, why the update check or the update did not happen."""


class Release(NamedTuple):
    version: str
    url: Optional[str]     # this system's asset (asset_name()); None when the release has none
    sha256: Optional[str]  # from the asset's `digest`; None when GitHub lists none
    size: int
    page: str


def version_tuple(text):
    """A sort key for '0.1.0', 'v0.1.0-beta.3' or 'v0.1.0': the numbers, then 1 for a final release and 0 for a
    pre-release (so 0.1.0 is above 0.1.0-beta.4), then the pre-release parts (beta.10 above beta.2, rc above beta)."""
    match = re.match(r'\D*?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?', text or '')
    numbers = tuple(int(part) for part in match.group(1).split('.')) if match else ()
    numbers += (0,) * (4 - len(numbers))
    parts = tuple((0, int(part), '') if part.isdigit() else (1, 0, part)
                  for part in ((match and match.group(2)) or '').split('.') if part)
    return numbers, 0 if parts else 1, parts


def is_prerelease(version):
    return version_tuple(version)[1] == 0


def release_version(tag):
    """'0.1.0-beta.3' from the tag 'v0.1.0-beta.3'."""
    return re.sub(r'^v', '', tag or '')


def current_version():
    return os.environ.get('AMDLSS_FAKE_VERSION') or VERSION


def digest_of(asset):
    """The lowercase SHA-256 hex of an asset dict from GitHub's API ('digest': 'sha256:...'), or None."""
    digest = asset.get('digest')
    match = re.fullmatch(r'sha256:([0-9a-fA-F]{64})', digest) if isinstance(digest, str) else None
    return match.group(1).lower() if match else None


def newest_release(releases, beta, windows=None):
    """The newest Release in GitHub's /releases list that may be offered: final ones, and pre-releases only when
    BETA. None when there is none. Only v<version> tags count; drafts never do."""
    if not isinstance(releases, list):
        raise ValueError('GitHub did not return a list of releases')
    wanted = asset_name(windows).lower()
    best = None
    for release in releases:
        tag = release.get('tag_name') if isinstance(release, dict) else None
        if not isinstance(tag, str) or not re.match(r'v\d', tag) or release.get('draft'):
            continue
        version = release_version(tag)
        if (release.get('prerelease') or is_prerelease(version)) and not beta:
            continue
        if best is None or version_tuple(version) > version_tuple(best.version):
            assets = release.get('assets')
            asset = next((a for a in assets if isinstance(a, dict) and isinstance(a.get('name'), str)
                          and a['name'].lower() == wanted), None) if isinstance(assets, list) else None
            url = asset.get('browser_download_url') if asset else None
            size = asset.get('size') if asset and isinstance(asset.get('size'), int) else 0
            best = Release(version, url if isinstance(url, str) else None, digest_of(asset) if asset else None, size,
                           release.get('html_url') or RELEASES_PAGE)
    return best


def newer(release, running=None):
    """`release` when it is above the running version, else None."""
    if release and version_tuple(release.version) > version_tuple(running or current_version()):
        return release
    return None


def fetch_release(beta, windows=None):
    """newest_release() of what GitHub lists now. Raises UpdateError (one readable line) on any failure."""
    request = urllib.request.Request(RELEASES_API, headers={'Accept': 'application/vnd.github+json',
                                                            'User-Agent': 'AMDLSS'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return newest_release(json.load(response), beta, windows)
    except urllib.error.HTTPError as error:
        limited = error.code in (403, 429)
        raise UpdateError(f'GitHub refused the request ({"rate limit, try later" if limited else error.code})')
    except (OSError, ValueError) as error:
        raise UpdateError(f'could not ask GitHub: {error}')


def exe_path():
    """The running executable (AMDLSS.exe, or AMDLSS-linux-x86_64), or None when running from source."""
    return Path(sys.executable) if FROZEN else None


def sibling(exe, suffix):
    return Path(exe).with_name(Path(exe).name + suffix)


def download(url, part, sha256, progress=None):
    """Fetches `url` into `part`; the file is deleted and UpdateError raised unless its SHA-256 is `sha256`."""
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'AMDLSS'})
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as response, open(part, 'wb') as out:
            total, done = int(response.headers.get('Content-Length') or 0), 0
            for block in iter(lambda: response.read(CHUNK), b''):
                out.write(block)
                digest.update(block)
                done += len(block)
                if progress and total:
                    progress(done, total)
        if digest.hexdigest() != sha256.lower():
            raise UpdateError('the download does not match the SHA-256 GitHub lists, nothing was changed')
    except (OSError, ValueError) as error:
        Path(part).unlink(missing_ok=True)
        raise UpdateError(f'download failed: {error}')
    except UpdateError:
        Path(part).unlink(missing_ok=True)
        raise


def swap(exe, new, windows=None):
    """Puts `new` where the running exe is. Windows: rename it to <exe>.old first, and put it back when the move
    fails. Linux: one rename over the running file (this process keeps the old inode), the new file first taking
    the old one's mode so it stays executable."""
    exe = Path(exe)
    if not host.is_windows(windows):
        os.chmod(new, stat.S_IMODE(os.stat(exe).st_mode))
        os.replace(new, exe)
        return
    old = sibling(exe, OLD_SUFFIX)
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)
        raise


def apply(release, exe, progress=None, windows=None):
    """Downloads `release`, checks it and replaces `exe` with it. Fails closed: no digest, no change."""
    if not release.url:
        raise UpdateError(f'{release.version} has no {asset_name(windows)} to download')
    if not release.sha256:
        raise UpdateError(f'GitHub lists no SHA-256 for {release.version}, so it is not installed')
    part = sibling(exe, PART_SUFFIX)
    try:
        download(release.url, part, release.sha256, progress)
        swap(exe, part, windows)
    except OSError as error:
        raise UpdateError(f'could not replace {Path(exe).name}: {error}')
    finally:
        part.unlink(missing_ok=True)


def restart(exe, args, windows=None):
    """Starts `exe` with `args`, detached from this process. PyInstaller's reset variable makes the new one-file exe
    unpack itself instead of reusing this process's soon-deleted folder."""
    if host.is_windows(windows):
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        return subprocess.Popen([str(exe), *args], cwd=str(Path(exe).parent), stdin=subprocess.DEVNULL,
                                close_fds=True, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                                creationflags=flags)
    return subprocess.Popen([str(exe), *args], cwd=str(Path(exe).parent), stdin=subprocess.DEVNULL, close_fds=True,
                            env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}, start_new_session=True)


def cleanup_old(exe, tries=10, delay=1.0):
    """Deletes what an update left beside `exe` (<exe>.old, a half download). The old exe can still be closing when
    the new one starts, so a locked file is tried again. True when nothing is left."""
    leftovers = [sibling(exe, OLD_SUFFIX), sibling(exe, PART_SUFFIX)]
    for attempt in range(tries):
        for path in leftovers:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if not any(path.exists() for path in leftovers):
            return True
        if attempt < tries - 1:
            time.sleep(delay)
    return False


def command(argv, beta, say=print):
    """`AMDLSS.exe --update`: the whole update without a window. Returns the exit code."""
    try:
        release = newer(fetch_release(beta))
        if release is None:
            say(f'AMDLSS {current_version()} is up to date')
            return 0
        if not FROZEN:
            say(f'AMDLSS {release.version} is available: {release.page}')
            return 0
        say(f'updating to {release.version}')
        apply(release, exe_path())
        restart(exe_path(), [a for a in argv if a != '--update'])
        return 0
    except UpdateError as error:
        say(f'update failed: {error}')
        return 1
