# SPDX-License-Identifier: GPL-2.0-or-later
"""SteamCovers with a stand-in transport: no test asks Steam."""
import http.client
import io
import json
import urllib.error

import pytest

from amdlss import covers, settings
from amdlss.games import Game

JPEG = b'\xff\xd8\xff\xe0' + b'x' * 100
DAY = 24 * 3600


class Steam:
    """A transport that answers store searches from `catalog` ({name: appid}) and header images from `images`."""

    def __init__(self, catalog=None, images=None, down=False, details=None):
        self.catalog, self.images, self.down, self.urls = catalog or {}, images or {}, down, []
        self.details = details or {}   # appid -> the header_image the store's app details give

    def __call__(self, url, limit):
        self.urls.append(url)
        if self.down:
            raise covers.CoversError('network', 'could not reach Steam (test)')
        if url.startswith('https://store.steampowered.com/api/storesearch/'):
            items = [{'id': appid, 'name': name} for name, appid in self.catalog.items()]
            return json.dumps({'total': len(items), 'items': items}).encode()
        if url.startswith('https://store.steampowered.com/api/appdetails'):
            appid = int(url.split('appids=')[1].split('&')[0])
            data = {'header_image': self.details.get(appid)}
            return json.dumps({str(appid): {'success': True, 'data': data}}).encode()
        if url.startswith('https://cdn.akamai.steamstatic.com/steam/apps/'):
            return self.images.get(int(url.split('/apps/')[1].split('/')[0]))   # the legacy path; None: a 404
        return self.images.get(url)   # any other address is keyed by itself


def make(tmp_path, steam, **options):
    return covers.SteamCovers(tmp_path / 'data', fetch=steam, **options)


def manifest(library, appid, installdir):
    (library / 'steamapps').mkdir(parents=True, exist_ok=True)
    (library / 'steamapps' / f'appmanifest_{appid}.acf').write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"Universe"\t\t"1"\n\t"installdir"\t\t"{installdir}"\n}}\n')


def test_the_appmanifest_names_the_game_without_asking_steam(tmp_path):
    manifest(tmp_path / 'lib', 2054970, 'Dragons Dogma 2')
    manifest(tmp_path / 'lib', 1091500, 'Cyberpunk 2077')
    exe_folder = tmp_path / 'lib' / 'steamapps' / 'common' / 'DRAGONS DOGMA 2' / 'bin' / 'x64'   # case differs, nested
    steam = Steam(images={2054970: JPEG})
    found = make(tmp_path, steam).get(Game(name="Dragon's Dogma 2", folder=str(exe_folder)))
    assert found == tmp_path / 'data' / 'covers' / '2054970.jpg' and found.read_bytes() == JPEG
    assert len(steam.urls) == 1 and steam.urls[0].endswith('/steam/apps/2054970/header.jpg')   # no search


def test_windows_paths_are_read_as_windows_paths(tmp_path):
    assert covers.manifest_appid('C:\\Games\\Plain') is None
    assert covers.manifest_appid('D:\\SteamLibrary\\steamapps\\common') is None   # the library itself, not a game
    assert covers.manifest_appid('') is None
    manifest(tmp_path / 'lib', 7, 'Game')
    assert covers.manifest_appid(str(tmp_path / 'lib' / 'steamapps' / 'common' / 'Game') + '/') == 7


def test_no_manifest_falls_to_the_store_search(tmp_path):
    (tmp_path / 'lib' / 'steamapps' / 'common' / 'Game').mkdir(parents=True)
    manifest(tmp_path / 'lib', 5, 'SomethingElse')
    steam = Steam({"Dragon's Dogma 2": 2054970}, {2054970: JPEG})
    found = make(tmp_path, steam).get(Game(name="Dragon's Dogma 2", folder=str(tmp_path / 'lib/steamapps/common/Game')))
    assert found.name == '2054970.jpg'
    assert steam.urls[0] == 'https://store.steampowered.com/api/storesearch/?term=Dragon%27s%20Dogma%202&cc=us&l=en'


def test_only_an_exact_name_is_accepted(tmp_path):
    near = {"Dragon's Dogma 2: Dark Arisen": 1, "Dragon's Dogma 2 Soundtrack": 2, 'Dragons Dogma 2': 3}
    client = make(tmp_path, Steam(near, {1: JPEG, 2: JPEG, 3: JPEG}))
    assert client.search("Dragon's Dogma 2") is None   # a wrong cover is worse than the tint
    assert client.get(Game(name="Dragon's Dogma 2")) is None and client.search('Dragon') is None
    marked = make(tmp_path / 'b', Steam({"Dragon\u2019s Dogma\u2122 2": 4}))
    assert marked.search("Dragon's Dogma 2") == 4 and marked.search("DRAGON'S  Dogma\u00ae 2") == 4
    assert covers.normalise("Dragon's  Dogma\u00ae 2") == 'dragon s dogma 2'


def test_a_miss_is_remembered_and_asked_again_after_a_week(tmp_path):
    now = [1_000_000.0]
    steam = Steam()
    client = make(tmp_path, steam, clock=lambda: now[0])
    game = Game(name='Unknown Game')
    assert client.get(game) is None and len(steam.urls) == 1
    now[0] += 6 * DAY
    assert make(tmp_path, steam, clock=lambda: now[0]).get(game) is None   # a new run reads names.json
    assert len(steam.urls) == 1
    now[0] += 2 * DAY
    steam.catalog, steam.images = {'Unknown Game': 9}, {9: JPEG}
    assert client.get(game).name == '9.jpg' and len(steam.urls) == 3
    assert json.loads((tmp_path / 'data' / 'covers' / 'names.json').read_text())['unknown game']['appid'] == 9


def test_a_game_is_asked_once_and_its_cover_is_then_cached(tmp_path):
    steam = Steam({'A': 1}, {1: JPEG})
    client = make(tmp_path, steam)
    game = Game(name='A')
    assert client.cached(game) is None
    path = client.get(game)
    assert client.cached(game) == path and client.get(game) == path and len(steam.urls) == 2
    assert make(tmp_path, Steam()).cached(game) == path   # and after a restart


def test_a_newer_app_is_found_through_the_store_s_app_details(tmp_path):
    hashed = 'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/9/abc/header.jpg?t=1'
    steam = Steam({'New Game': 9}, {hashed: JPEG}, details={9: hashed})   # the legacy header.jpg is a 404 for app 9
    found = make(tmp_path, steam).get(Game(name='New Game'))
    assert found.name == '9.jpg' and steam.urls[-1] == hashed
    evil = 'https://evil.example/header.jpg'
    elsewhere = Steam({'New Game': 9}, {evil: JPEG}, details={9: evil})
    assert make(tmp_path / 'b', elsewhere).get(Game(name='New Game')) is None   # only Steam's own image host
    assert not any('evil' in url for url in elsewhere.urls)


def test_an_app_without_a_header_image_is_a_miss(tmp_path):
    steam = Steam({'A': 1})
    client = make(tmp_path, steam)
    assert client.get(Game(name='A')) is None and client.get(Game(name='A')) is None
    assert len(steam.urls) == 3   # the search, the image and the app details, once


@pytest.mark.parametrize('body', [b'<html>nope</html>', b'GIF89a', b''])
def test_a_body_that_is_not_a_jpeg_is_rejected(tmp_path, body):
    heard = []
    client = make(tmp_path, Steam({'A': 1}, {1: body}), log=heard.append)
    assert client.get(Game(name='A')) is None
    assert not list((tmp_path / 'data' / 'covers').glob('*.jpg*')) and len(heard) == 1 and 'not a JPEG' in heard[0]


def test_an_oversized_body_is_rejected(tmp_path):
    steam = Steam({'A': 1}, {1: JPEG + b'0' * covers.MAX_BYTES})
    assert make(tmp_path, steam).get(Game(name='A')) is None
    assert not list((tmp_path / 'data' / 'covers').glob('*.jpg*'))


def test_a_failed_write_leaves_no_part_file(tmp_path, monkeypatch):
    def refuse(src, dst):
        raise OSError('disk full')

    monkeypatch.setattr(covers.os, 'replace', refuse)
    heard = []
    client = make(tmp_path, Steam({'A': 1}, {1: JPEG}), log=heard.append)
    assert client.get(Game(name='A')) is None
    assert list((tmp_path / 'data' / 'covers').iterdir()) == []
    assert len(heard) == 1 and 'could not write' in heard[0]


def test_each_kind_of_failure_is_logged_once(tmp_path):
    heard = []
    client = make(tmp_path, Steam(down=True), log=heard.append)
    assert [client.get(Game(name=n)) for n in 'ABC'] == [None] * 3
    assert len(heard) == 1 and heard[0].startswith('game covers: could not reach Steam')
    assert not (tmp_path / 'data' / 'covers' / 'names.json').exists()   # being offline is not a miss to remember


def test_the_default_transport_turns_every_failure_into_one_error(monkeypatch):
    def raising(error):
        def urlopen(request, timeout):
            raise error
        return urlopen

    for error in (urllib.error.URLError('no route'), TimeoutError('slow'), http.client.IncompleteRead(b'')):
        monkeypatch.setattr(covers.urllib.request, 'urlopen', raising(error))
        with pytest.raises(covers.CoversError) as raised:
            covers.download('https://x/')
        assert raised.value.kind == 'network'
    monkeypatch.setattr(covers.urllib.request, 'urlopen', raising(urllib.error.HTTPError('u', 404, 'nf', {}, None)))
    assert covers.download('https://x/') is None
    monkeypatch.setattr(covers.urllib.request, 'urlopen', raising(urllib.error.HTTPError('u', 503, 'down', {}, None)))
    with pytest.raises(covers.CoversError):
        covers.download('https://x/')


def test_switched_off_it_never_touches_the_transport(tmp_path):
    steam = Steam({'A': 1}, {1: JPEG})
    client = make(tmp_path, steam)
    game = Game(name='A')
    path = client.get(game)
    client.enabled = False
    assert client.get(game) is None and client.cached(game) is None   # not even what is on disk
    off = make(tmp_path / 'other', steam, enabled=False)
    assert off.get(Game(name='B')) is None and len(steam.urls) == 2
    client.enabled = True
    assert client.cached(game) == path


def test_the_switch_is_saved_and_defaults_to_on(tmp_path):
    assert settings.load_covers(tmp_path) is True
    settings.save_covers(tmp_path, False)
    assert settings.load_covers(tmp_path) is False
    settings.save_beta(tmp_path, True)   # another key does not disturb it
    assert settings.load_covers(tmp_path) is False and settings.load_beta(tmp_path, False) is True


def test_a_damaged_name_cache_is_ignored(tmp_path):
    (tmp_path / 'data' / 'covers').mkdir(parents=True)
    (tmp_path / 'data' / 'covers' / 'names.json').write_text('{"a": {"appid": "x"}}')
    assert make(tmp_path, Steam()).index == {}


def test_cover_art_is_cropped_to_the_card_with_a_dark_foot():
    image = pytest.importorskip('PIL.Image')
    from amdlss.ui import BG, cover_art
    stream = io.BytesIO()
    image.new('RGB', (460, 215), '#ffffff').save(stream, 'JPEG')
    stream.seek(0)
    art = cover_art(stream, 190, 124, 8)
    assert art.size == (190, 124) and art.mode == 'RGB'
    assert art.getpixel((95, 2))[0] > 200 and art.getpixel((95, 120))[0] < 40   # white art, scrim at the bottom
    assert art.getpixel((0, 0)) == tuple(int(BG[i:i + 2], 16) for i in (1, 3, 5))   # a rounded corner shows the window
