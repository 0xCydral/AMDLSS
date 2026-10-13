# SPDX-License-Identifier: GPL-2.0-or-later
"""Game cover art from Steam's public store API and CDN (no key, no account). SteamCovers is the only code that
builds a Steam URL or talks to Steam; the window asks it for a file path and draws whatever it gets.

A game's Steam app id comes from the local appmanifest_*.acf files when the game sits under steamapps\\common\\<dir>
(nothing is sent), else from a store search whose result is accepted only when its name equals the game's name.
The image is the CDN's legacy header.jpg, or for newer apps (404 there) the address the store's app details give.
Covers are cached as covers\\<appid>.jpg in the data folder, and covers\\names.json remembers name -> app id, a miss
included, so Steam is asked once per game (a miss again after RETRY_AFTER seconds).
"""
import http.client
import json
import ntpath
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH_URL = 'https://store.steampowered.com/api/storesearch/?term={term}&cc=us&l=en'
HEADER_URL = 'https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg'
DETAILS_URL = 'https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic'
MAX_BYTES = 2 << 20
RETRY_AFTER = 7 * 24 * 3600
TIMEOUT = 15
JPEG = b'\xff\xd8\xff'


class CoversError(Exception):
    """Something Steam or the disk did that stops one cover; `kind` ('network', 'response', 'disk') groups the log."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def download(url, limit=MAX_BYTES):
    """The body of `url`, read up to limit + 1 bytes; None for a 404, CoversError for anything else."""
    request = urllib.request.Request(url, headers={'User-Agent': 'AMDLSS'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise CoversError('network', f'Steam answered HTTP {error.code}')
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise CoversError('network', f'could not reach Steam ({error})')


def normalise(name):
    """A name for comparing: case, trademark signs, punctuation and spacing ignored."""
    return re.sub(r'\W+', ' ', re.sub('[™®©]', '', name).casefold()).strip()


def entry_ok(entry):
    """One names.json entry: {"appid": int or null, "at": seconds}."""
    return (isinstance(entry, dict) and isinstance(entry.get('at'), (int, float)) and 'appid' in entry and
            (entry['appid'] is None or isinstance(entry['appid'], int)))


def manifest_appid(folder):
    """The Steam app id of the game in `folder`, a Windows path ...\\steamapps\\common\\<installdir>[\\...]: the
    appmanifest in that steamapps folder whose installdir is <installdir>. None when there is none."""
    head = str(folder).rstrip('\\/')
    while True:
        parent, name = ntpath.split(head)
        if parent == head or not name:
            return None
        library, common = ntpath.split(parent)
        if common.casefold() == 'common' and ntpath.basename(library).casefold() == 'steamapps':
            break
        head = parent
    try:
        for manifest in sorted(Path(library).glob('appmanifest_*.acf')):
            text = manifest.read_text(encoding='utf-8', errors='replace')
            appid, installdir = (re.search(rf'"{key}"\s+"([^"]*)"', text, re.I) for key in ('appid', 'installdir'))
            if appid and installdir and appid.group(1).isdigit() and installdir.group(1).casefold() == name.casefold():
                return int(appid.group(1))
    except OSError:
        pass
    return None


class SteamCovers:
    """cached(game) is a cheap look at the disk (safe on the window's thread); get(game) may use the network and
    blocks, so call it off that thread. Both return the path of a JPEG or None. `fetch(url, limit)` is the transport
    (download() unless a test hands in another); `log(text)` hears each kind of failure once."""

    def __init__(self, data_dir, enabled=True, log=None, fetch=None, clock=time.time):
        self.folder = Path(data_dir) / 'covers'
        self.enabled, self.log, self.fetch, self.clock = enabled, log, fetch or download, clock
        self.lock, self.reported = threading.Lock(), set()
        self.index = self.load_index()

    def path_of(self, appid):
        return self.folder / f'{appid}.jpg'

    def load_index(self):
        try:
            data = json.loads((self.folder / 'names.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) and all(map(entry_ok, data.values())) else {}

    def remember(self, name, appid):
        """Writes name -> appid (None: Steam has no cover for it, retried later) into names.json, atomically."""
        with self.lock:
            self.index[normalise(name)] = {'appid': appid, 'at': int(self.clock())}
            part = self.folder / 'names.json.part'
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                part.write_text(json.dumps(self.index, indent=1), encoding='utf-8')
                os.replace(part, self.folder / 'names.json')
            except OSError as error:
                part.unlink(missing_ok=True)
                self.report(CoversError('disk', f'could not write the cover cache ({error})'))

    def report(self, error):
        if error.kind not in self.reported:
            self.reported.add(error.kind)
            if self.log:
                self.log(f'game covers: {error}; the cards keep their plain look (not repeated)')

    def cached(self, game):
        entry = self.index.get(normalise(game.name)) if self.enabled else None
        path = self.path_of(entry['appid']) if entry and entry['appid'] else None
        return path if path and path.is_file() else None

    def get(self, game):
        if not self.enabled:
            return None
        if path := self.cached(game):
            return path
        entry = self.index.get(normalise(game.name))
        if entry and entry['appid'] is None and self.clock() - entry['at'] < RETRY_AFTER:
            return None
        try:
            appid = entry and entry['appid']
            if not appid:
                appid = manifest_appid(game.folder) or self.search(game.name)
                self.remember(game.name, appid)
            path = self.fetch_cover(appid) if appid else None
            if appid and not path:   # Steam has no header image for that app
                self.remember(game.name, None)
            return path
        except CoversError as error:
            self.report(error)
            return None

    def body(self, url):
        body = self.fetch(url, MAX_BYTES)
        if body is not None and len(body) > MAX_BYTES:
            raise CoversError('response', 'Steam sent a file that is too large')
        return body

    def search(self, name):
        """The app id of the store search result named exactly like `name`, else None (never a near miss)."""
        body = self.body(SEARCH_URL.format(term=urllib.parse.quote(name)))
        if body is None:
            return None
        try:
            items = json.loads(body)['items']
        except (ValueError, KeyError, TypeError):
            items = None
        if not isinstance(items, list):
            raise CoversError('response', 'Steam sent an answer the store search does not use')
        wanted = normalise(name)
        for item in items:
            if isinstance(item, dict) and isinstance(item.get('id'), int) and \
                    normalise(str(item.get('name'))) == wanted:
                return item['id']
        return None

    def header_url(self, appid):
        """The header image address the store lists for `appid`, if it is on Steam's own image host."""
        try:
            url = json.loads(self.body(DETAILS_URL.format(appid=appid)))[str(appid)]['data']['header_image']
            parts = urllib.parse.urlsplit(url)
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        return url if parts.scheme == 'https' and (parts.hostname or '').endswith('.steamstatic.com') else None

    def fetch_cover(self, appid):
        body = self.body(HEADER_URL.format(appid=appid))
        if body is None:   # newer apps keep the image under a hashed path that only the store's app details give
            url = self.header_url(appid)
            body = self.body(url) if url else None
        if body is None:
            return None
        if not body.startswith(JPEG):
            raise CoversError('response', 'Steam sent a cover that is not a JPEG image')
        path = self.path_of(appid)
        part = path.with_name(path.name + '.part')
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            part.write_bytes(body)
            os.replace(part, path)
        except OSError as error:
            part.unlink(missing_ok=True)
            raise CoversError('disk', f'could not write the cover cache ({error})')
        return path
