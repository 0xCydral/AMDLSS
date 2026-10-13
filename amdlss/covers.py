# SPDX-License-Identifier: GPL-2.0-or-later
"""Game cover art from Steam's and GOG's public, keyless endpoints. CoverClient is the only code that builds a Steam
or GOG URL or talks to those hosts; the window asks it for a file path and draws whatever it gets.

amdlss.identify says offline which game a folder is (a Steam app id, a GOG id, titles). A Steam app id, from the
local appmanifest, is used as it is. Otherwise each title is looked up on Steam, and a result is taken only when
its name is the title exactly, or starts with the title as whole words and the store says it is a game (not DLC, a
soundtrack ...). With no Steam match, a GOG id gives GOG's own art. Anything less certain is no cover: a wrong
cover is worse than the tint.

Covers are cached as covers\\<steam appid>.jpg or covers\\gog-<id>.jpg in the data folder. covers\\names.json
remembers game name -> identity and identity -> cover (a miss included, so a game is looked up once; a miss is
tried again after RETRY_AFTER seconds).
"""
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .identify import identify, normalise

COMMUNITY_URL = 'https://steamcommunity.com/actions/SearchApps/{term}'
SEARCH_URL = 'https://store.steampowered.com/api/storesearch/?term={term}&cc=us&l=en'
HEADER_URL = 'https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg'
DETAILS_URL = 'https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic'
GOG_URL = 'https://api.gog.com/products/{gog_id}'
MAX_BYTES = 2 << 20
RETRY_AFTER = 7 * 24 * 3600
TIMEOUT = 15
PREFIX_CHECKS = 2   # prefix matches whose type is asked per title
JPEG = b'\xff\xd8\xff'
SOURCE = re.compile(r'(steam|gog):\d+')


class CoversError(Exception):
    """Something a cover host or the disk did that stops one cover; `kind` ('network', 'response', 'disk') groups
    the log."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def download(url, limit=MAX_BYTES):
    """The body of `url`, read up to limit + 1 bytes; None for a 404, CoversError for anything else."""
    request = urllib.request.Request(url, headers={'User-Agent': 'AMDLSS'})
    host = urllib.parse.urlsplit(url).hostname
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise CoversError('network', f'{host} answered HTTP {error.code}')
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise CoversError('network', f'could not reach {host} ({error})')


def entry_ok(entry):
    """One names.json cover entry: {"source": "steam:<id>" / "gog:<id>" / null, "at": seconds}."""
    return (isinstance(entry, dict) and isinstance(entry.get('at'), (int, float)) and 'source' in entry and
            (entry['source'] is None or (isinstance(entry['source'], str) and SOURCE.fullmatch(entry['source']))))


def key_of(identity):
    """What the cache calls a game: its Steam id, else its GOG id, else its best title."""
    if identity.steam_appid:
        return f'steam:{identity.steam_appid}'
    if identity.gog_id:
        return f'gog:{identity.gog_id}'
    return f'title:{normalise(identity.titles[0])}' if identity.titles else None


def https_on(url, domain):
    """`url` (protocol-relative allowed) as an https address, or None unless its host is on `domain`."""
    if not isinstance(url, str):
        return None
    parts = urllib.parse.urlsplit('https:' + url if url.startswith('//') else url)
    return parts.geturl() if parts.scheme == 'https' and (parts.hostname or '').endswith('.' + domain) else None


class CoverClient:
    """cached(game) is a cheap look at the disk (safe on the window's thread); get(game) reads the game's launcher
    files and may use the network, so call it off that thread. Both return the path of a JPEG or None.
    `fetch(url, limit)` is the transport (download() unless a test hands in another); `log(text)` hears each kind of
    failure once."""

    def __init__(self, data_dir, enabled=True, log=None, fetch=None, clock=time.time):
        self.folder = Path(data_dir) / 'covers'
        self.enabled, self.log, self.fetch, self.clock = enabled, log, fetch or download, clock
        self.lock, self.reported, self.details = threading.Lock(), set(), {}
        self.names, self.entries = self.load_index()

    def path_of(self, source):
        kind, _, number = source.partition(':')
        return self.folder / (f'{number}.jpg' if kind == 'steam' else f'{kind}-{number}.jpg')

    def load_index(self):
        try:
            data = json.loads((self.folder / 'names.json').read_text(encoding='utf-8'))
            names, entries = data['names'], data['entries']
            if all(isinstance(k, str) and isinstance(v, str) for k, v in names.items()) and \
                    all(map(entry_ok, entries.values())):
                return names, entries
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return {}, {}

    def remember(self, game_name, key, source):
        """Game name -> identity key, and key -> source (None: no cover found, tried again later), into names.json."""
        with self.lock:
            self.names[normalise(game_name)] = key
            self.entries[key] = {'source': source, 'at': int(self.clock())}
            part = self.folder / 'names.json.part'
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                part.write_text(json.dumps({'names': self.names, 'entries': self.entries}, indent=1), encoding='utf-8')
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
        entry = self.entries.get(self.names.get(normalise(game.name))) if self.enabled else None
        path = self.path_of(entry['source']) if entry and entry['source'] else None
        return path if path and path.is_file() else None

    def get(self, game):
        if not self.enabled:
            return None
        if path := self.cached(game):
            return path
        identity = identify(game)
        key = key_of(identity)
        if key is None:
            return None
        entry = self.entries.get(key)
        if entry and entry['source'] is None and self.clock() - entry['at'] < RETRY_AFTER:
            return None
        try:
            source = entry and entry['source']
            if not source:
                source = self.resolve(identity)
                self.remember(game.name, key, source)
            path = self.path_of(source) if source else None
            if path and path.is_file():   # another folder of the same game fetched it
                self.remember(game.name, key, source)
                return path
            path = self.fetch_cover(source) if source else None
            if source and not path:   # the host has no image for it
                self.remember(game.name, key, None)
            return path
        except CoversError as error:
            self.report(error)
            return None

    def body(self, url):
        body = self.fetch(url, MAX_BYTES)
        if body is not None and len(body) > MAX_BYTES:
            raise CoversError('response', 'a cover host sent a file that is too large')
        return body

    def resolve(self, identity):
        """'steam:<id>' or 'gog:<id>' for the art to use for `identity`, or None. Steam first, titles in order."""
        if identity.steam_appid:
            return f'steam:{identity.steam_appid}'
        for title in identity.titles:
            if appid := self.steam_match(title):
                return f'steam:{appid}'
        return f'gog:{identity.gog_id}' if identity.gog_id else None

    def listed(self, url, read):
        """The (id, name) pairs of one Steam search answer: `read` pulls the list out of the decoded JSON."""
        body = self.body(url)
        if body is None:
            return []
        try:
            items = read(json.loads(body))
            return [(int(i['appid'] if 'appid' in i else i['id']), str(i['name'])) for i in items]
        except (ValueError, KeyError, TypeError):
            raise CoversError('response', 'Steam sent a search answer that cannot be read')

    def steam_match(self, title):
        """The Steam app id for `title`, else None: an exact name, or the first search result that continues the
        title with whole words and that the store says is a game (so 'The Witcher 3' finds the Remastered game,
        never its soundtrack, and no title is taken from the middle of a name)."""
        wanted = normalise(title)
        if not wanted:
            return None
        quoted = urllib.parse.quote(title, safe='')
        apps = self.listed(COMMUNITY_URL.format(term=quoted), lambda data: data)
        for appid, name in apps:
            if normalise(name) == wanted:
                return appid
        for appid, name in self.listed(SEARCH_URL.format(term=quoted), lambda data: data['items']):
            if normalise(name) == wanted:
                return appid
        if len(wanted.split()) < 2:   # one word is too short to tell a sequel from the game
            return None
        later = [appid for appid, name in apps if normalise(name).startswith(wanted + ' ')][:PREFIX_CHECKS]
        return next((appid for appid in later if self.app_data(appid).get('type') == 'game'), None)

    def app_data(self, appid):
        """The store's basic details of an app ({} when it has none), asked once per run."""
        if appid not in self.details:
            try:
                data = json.loads(self.body(DETAILS_URL.format(appid=appid)))[str(appid)]['data']
            except (ValueError, KeyError, TypeError):
                data = {}
            self.details[appid] = data if isinstance(data, dict) else {}
        return self.details[appid]

    def fetch_cover(self, source):
        kind, _, number = source.partition(':')
        body = self.steam_image(int(number)) if kind == 'steam' else self.gog_image(int(number))
        if body is None:
            return None
        if not body.startswith(JPEG):
            raise CoversError('response', 'a cover host sent a cover that is not a JPEG image')
        path = self.path_of(source)
        part = path.with_name(path.name + '.part')
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            part.write_bytes(body)
            os.replace(part, path)
        except OSError as error:
            part.unlink(missing_ok=True)
            raise CoversError('disk', f'could not write the cover cache ({error})')
        return path

    def steam_image(self, appid):
        body = self.body(HEADER_URL.format(appid=appid))
        if body is None:   # newer apps keep the image under a hashed path that only the store's app details give
            url = https_on(self.app_data(appid).get('header_image'), 'steamstatic.com')
            body = self.body(url) if url else None
        return body

    def gog_image(self, gog_id):
        """GOG's wide background for the product (its logo when there is none), from api.gog.com."""
        body = self.body(GOG_URL.format(gog_id=gog_id))
        if body is None:
            return None
        try:
            images = json.loads(body)['images']
            urls = [https_on(images.get(key), 'gog-statics.com') for key in ('background', 'logo2x')]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise CoversError('response', 'api.gog.com sent an answer that cannot be read')
        url = next(filter(None, urls), None)
        return self.body(url) if url else None
