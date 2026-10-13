# SPDX-License-Identifier: GPL-2.0-or-later
"""SteamCovers with a stand-in transport: no test asks Steam."""
import http.client
import io
import json
import urllib.error
import urllib.parse

import pytest

from amdlss import covers, settings
from amdlss.games import Game

JPEG = b'\xff\xd8\xff\xe0' + b'x' * 100
DAY = 24 * 3600


class Steam:
    """A transport that stands in for Steam and GOG. `catalog` ({name: appid}) answers the store search, `apps`
    ({name: appid}) the community search (any app whose name contains the term), `types` the app details' type
    (game unless said otherwise), `images` the images (by app id for Steam's legacy path, else by address), `gog`
    ({id: images}) api.gog.com."""

    def __init__(self, catalog=None, images=None, down=False, details=None, apps=None, types=None, gog=None):
        self.catalog, self.images, self.down, self.urls = catalog or {}, images or {}, down, []
        self.details = details or {}   # appid -> the header_image the store's app details give
        self.apps, self.types, self.gog = apps or {}, types or {}, gog or {}

    def asked(self, part):
        return [url for url in self.urls if part in url]

    def __call__(self, url, limit):
        self.urls.append(url)
        if self.down:
            raise covers.CoversError('network', 'could not reach Steam (test)')
        if url.startswith('https://steamcommunity.com/actions/SearchApps/'):
            term = covers.normalise(urllib.parse.unquote(url.rsplit('/', 1)[1]))
            return json.dumps([{'appid': str(appid), 'name': name} for name, appid in self.apps.items()
                               if term in covers.normalise(name)]).encode()
        if url.startswith('https://store.steampowered.com/api/storesearch/'):
            items = [{'id': appid, 'name': name} for name, appid in self.catalog.items()]
            return json.dumps({'total': len(items), 'items': items}).encode()
        if url.startswith('https://store.steampowered.com/api/appdetails'):
            appid = int(url.split('appids=')[1].split('&')[0])
            data = {'type': self.types.get(appid, 'game'), 'header_image': self.details.get(appid)}
            return json.dumps({str(appid): {'success': True, 'data': data}}).encode()
        if url.startswith('https://api.gog.com/products/'):
            images = self.gog.get(int(url.rsplit('/', 1)[1]))
            return None if images is None else json.dumps({'images': images}).encode()
        if url.startswith('https://cdn.akamai.steamstatic.com/steam/apps/'):
            return self.images.get(int(url.split('/apps/')[1].split('/')[0]))   # the legacy path; None: a 404
        return self.images.get(url)   # any other address is keyed by itself


def make(tmp_path, steam, **options):
    return covers.CoverClient(tmp_path / 'data', fetch=steam, **options)


def manifest(library, appid, installdir):
    (library / 'steamapps').mkdir(parents=True, exist_ok=True)
    (library / 'steamapps' / f'appmanifest_{appid}.acf').write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"Universe"\t\t"1"\n\t"installdir"\t\t"{installdir}"\n}}\n')


def gog_game(folder, gog_id, name='Some Game', title='A GOG Title'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'goggame-{gog_id}.info').write_text(json.dumps({'gameId': str(gog_id), 'name': title}))
    return Game(name=name, folder=str(folder), exe_folder=str(folder))


def test_the_appmanifest_names_the_game_without_asking_steam(tmp_path):
    manifest(tmp_path / 'lib', 2054970, 'Dragons Dogma 2')
    manifest(tmp_path / 'lib', 1091500, 'Cyberpunk 2077')
    exe_folder = tmp_path / 'lib' / 'steamapps' / 'common' / 'DRAGONS DOGMA 2' / 'bin' / 'x64'   # case differs, nested
    steam = Steam(images={2054970: JPEG})
    found = make(tmp_path, steam).get(Game(name="Dragon's Dogma 2", folder=str(exe_folder)))
    assert found == tmp_path / 'data' / 'covers' / '2054970.jpg' and found.read_bytes() == JPEG
    assert len(steam.urls) == 1 and steam.urls[0].endswith('/steam/apps/2054970/header.jpg')   # no search


def test_no_manifest_falls_to_the_searches(tmp_path):
    (tmp_path / 'lib' / 'steamapps' / 'common' / 'Game').mkdir(parents=True)
    manifest(tmp_path / 'lib', 5, 'SomethingElse')
    steam = Steam({"Dragon's Dogma 2": 2054970}, {2054970: JPEG})
    found = make(tmp_path, steam).get(Game(name="Dragon's Dogma 2", folder=str(tmp_path / 'lib/steamapps/common/Game')))
    assert found.name == '2054970.jpg'
    assert steam.urls[0] == "https://steamcommunity.com/actions/SearchApps/Dragon%27s%20Dogma%202"
    assert steam.urls[1] == 'https://store.steampowered.com/api/storesearch/?term=Dragon%27s%20Dogma%202&cc=us&l=en'


def test_an_exact_name_is_accepted_wherever_it_is_found(tmp_path):
    near = {"Dragon's Dogma 2: Dark Arisen": 1, "Dragon's Dogma 2 Soundtrack": 2, 'Dragons Dogma 2': 3}
    client = make(tmp_path, Steam(near, {1: JPEG, 2: JPEG, 3: JPEG}))
    assert client.steam_match("Dragon's Dogma 2") is None   # the store search has no prefix rule: near misses lose
    assert client.get(Game(name="Dragon's Dogma 2")) is None and client.steam_match('Dragon') is None
    marked = make(tmp_path / 'b', Steam({"Dragon\u2019s Dogma\u2122 2": 4}))
    assert marked.steam_match("Dragon's Dogma 2") == 4 and marked.steam_match("DRAGON'S  Dogma\u00ae 2") == 4
    community = make(tmp_path / 'c', Steam(apps={'The Witcher 3 REDkit': 8, 'The Witcher 3': 9}))
    assert community.steam_match('the witcher 3') == 9   # exact beats an earlier longer name
    assert covers.normalise("Dragon's  Dogma\u00ae 2") == 'dragon s dogma 2'


REMASTERED = 'The Witcher 3: Wild Hunt \u2014 Remastered'


def test_a_title_that_starts_a_game_name_finds_the_game(tmp_path):
    steam = Steam(apps={'The Witcher 3 REDkit': 2684660, REMASTERED: 292030}, types={2684660: 'application'})
    client = make(tmp_path, steam)
    assert client.steam_match('The Witcher 3') == 292030   # REDkit also starts with it, but it is not a game
    assert len(steam.asked('appdetails')) == 2 and client.steam_match('The Witcher 3') == 292030
    assert len(steam.asked('appdetails')) == 2   # the answers are kept


def test_a_prefix_match_that_the_store_calls_dlc_is_rejected(tmp_path):
    steam = Steam(apps={'The Witcher 3 Wild Hunt Songs of the Past': 5, 'The Witcher 3 Wild Hunt Soundtrack': 6},
                  types={5: 'dlc', 6: 'music'})
    assert make(tmp_path, steam).steam_match('The Witcher 3') is None


def test_only_two_prefix_candidates_are_checked(tmp_path):
    steam = Steam(apps={f'Big Game {n}': n for n in range(1, 6)}, types={n: 'dlc' for n in range(1, 4)})
    assert make(tmp_path, steam).steam_match('Big Game') is None   # game 4 would pass, but is the fourth candidate
    assert len(steam.asked('appdetails')) == 2


def test_a_title_in_the_middle_of_a_name_is_never_taken(tmp_path):
    steam = Steam(apps={'LEGO The Witcher 3 Fan Pack': 1, 'Best of The Witcher 3': 2, 'Alan Wake Remastered': 3,
                        'Witcher 3 Wild Hunt': 4}, types={4: 'game'})
    client = make(tmp_path, steam)
    assert client.steam_match('The Witcher 3') is None
    assert client.steam_match('Alan Wake 2') is None   # a different game that merely shares the start
    assert client.steam_match('Wild Hunt') is None
    assert not steam.asked('appdetails')   # nothing even looked like a candidate


def test_a_title_another_game_holds_after_other_words_is_ambiguous(tmp_path):
    # Real answers for the folder name "Wildlands" (2026-10-13): the exact hit is an unrelated indie game.
    steam = Steam({'WILDLANDS': 1261370},
                  apps={"Tom Clancy's Ghost Recon® Wildlands": 460930, 'Wildlands Resurgence': 2, 'Sushi Wildlands': 3})
    assert make(tmp_path, steam).steam_match('Wildlands') is None
    sequel = Steam(apps={'Hades': 1, 'Hades II': 2})   # a name that only continues the title is no doubt
    assert make(tmp_path / 'b', sequel).steam_match('Hades') == 1


def test_a_one_word_title_has_no_prefix_rule(tmp_path):
    steam = Steam(apps={'Doom Eternal': 1})
    assert make(tmp_path, steam).steam_match('Doom') is None and not steam.asked('appdetails')


def test_the_witcher_end_to_end_from_a_folder_and_a_helixsr_name(tmp_path):
    steam = Steam(apps={REMASTERED: 292030}, images={292030: JPEG})
    game = Game(name='The Witcher 3', folder=str(tmp_path / 'GOG' / 'TheWitcher3_WildHunt'))
    assert make(tmp_path, steam).get(game).name == '292030.jpg'


def test_a_gog_game_without_a_steam_match_gets_gogs_art(tmp_path):
    steam = Steam(gog={123: {'background': '//images-1.gog-statics.com/bg.jpg', 'logo2x': '//x.gog-statics.com/l.jpg'}},
                  images={'https://images-1.gog-statics.com/bg.jpg': JPEG})
    found = make(tmp_path, steam).get(gog_game(tmp_path / 'GOG' / 'Only', 123))
    assert found.name == 'gog-123.jpg' and found.read_bytes() == JPEG
    assert steam.urls[-2:] == ['https://api.gog.com/products/123', 'https://images-1.gog-statics.com/bg.jpg']


def test_gog_images_must_come_from_gogs_image_host(tmp_path):
    for bad in ('//evil.example/bg.jpg', 'http://images-1.gog-statics.com/bg.jpg', '//gog-statics.com.evil.io/b.jpg',
                'https://images-1.gog-statics.com@evil.example/b.jpg', None, 7):
        steam = Steam(gog={123: {'background': bad}}, images={bad: JPEG} if isinstance(bad, str) else {})
        assert make(tmp_path / str(abs(hash(str(bad)))), steam).get(gog_game(tmp_path / 'g', 123)) is None
        assert not [u for u in steam.urls if 'evil' in u or u.startswith('http:')]
    logo = Steam(gog={123: {'background': '//evil.example/b.jpg', 'logo2x': '//images-3.gog-statics.com/l.jpg'}},
                 images={'https://images-3.gog-statics.com/l.jpg': JPEG})
    assert make(tmp_path / 'logo', logo).get(gog_game(tmp_path / 'g', 123)).name == 'gog-123.jpg'


def test_a_gog_game_that_steam_knows_uses_steams_art(tmp_path):
    steam = Steam(apps={'Cyberpunk 2077': 1091500}, images={1091500: JPEG}, gog={7: {'background': '//a.gog-statics.com/b.jpg'}})
    found = make(tmp_path, steam).get(gog_game(tmp_path / 'GOG' / 'CP', 7, title='Cyberpunk 2077'))
    assert found.name == '1091500.jpg' and not steam.asked('api.gog.com')


def test_the_cache_follows_the_identity_not_the_folder_name(tmp_path):
    steam = Steam(gog={7: {'background': '//a.gog-statics.com/b.jpg'}}, images={'https://a.gog-statics.com/b.jpg': JPEG})
    client = make(tmp_path, steam)
    first = client.get(gog_game(tmp_path / 'GOG' / 'OldName', 7))
    asked = len(steam.urls)
    renamed = gog_game(tmp_path / 'GOG' / 'NewName', 7, name='Renamed By HelixSR')
    assert client.cached(renamed) is None   # the window's cheap look does not know the new name yet
    assert client.get(renamed) == first and len(steam.urls) == asked   # but nothing is searched or downloaded again
    assert client.cached(renamed) == first


def test_a_miss_is_remembered_and_asked_again_after_a_week(tmp_path):
    now = [1_000_000.0]
    steam = Steam()
    client = make(tmp_path, steam, clock=lambda: now[0])
    game = Game(name='Unknown Game')
    assert client.get(game) is None and len(steam.urls) == 2
    now[0] += 6 * DAY
    assert make(tmp_path, steam, clock=lambda: now[0]).get(game) is None   # a new run reads names.json
    assert len(steam.urls) == 2
    now[0] += 2 * DAY
    steam.catalog, steam.images = {'Unknown Game': 9}, {9: JPEG}
    assert client.get(game).name == '9.jpg' and len(steam.urls) == 5
    saved = json.loads((tmp_path / 'data' / 'covers' / 'names.json').read_text())
    assert saved['entries']['title:unknown game']['source'] == 'steam:9' and saved['names']['unknown game']


def test_a_game_is_asked_once_and_its_cover_is_then_cached(tmp_path):
    steam = Steam({'A': 1}, {1: JPEG})
    client = make(tmp_path, steam)
    game = Game(name='A')
    assert client.cached(game) is None
    path = client.get(game)
    assert client.cached(game) == path and client.get(game) == path and len(steam.urls) == 3
    assert make(tmp_path, Steam()).cached(game) == path   # and after a restart


def test_an_app_without_a_header_image_is_a_miss(tmp_path):
    steam = Steam({'A': 1})
    client = make(tmp_path, steam)
    assert client.get(Game(name='A')) is None and client.get(Game(name='A')) is None
    assert len(steam.urls) == 4   # both searches, the image and the app details, once


def test_a_newer_app_is_found_through_the_store_s_app_details(tmp_path):
    hashed = 'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/9/abc/header.jpg?t=1'
    steam = Steam({'New Game': 9}, {hashed: JPEG}, details={9: hashed})   # the legacy header.jpg is a 404 for app 9
    found = make(tmp_path, steam).get(Game(name='New Game'))
    assert found.name == '9.jpg' and steam.urls[-1] == hashed
    evil = 'https://evil.example/header.jpg'
    elsewhere = Steam({'New Game': 9}, {evil: JPEG}, details={9: evil})
    assert make(tmp_path / 'b', elsewhere).get(Game(name='New Game')) is None   # only Steam's own image host
    assert not any('evil' in url for url in elsewhere.urls)


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
    assert off.get(Game(name='B')) is None and len(steam.urls) == 3
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
    names = tmp_path / 'data' / 'covers' / 'names.json'
    for damaged in ('{"a": {"appid": "x"}}', '{"names": {}, "entries": {"a": {"source": "../x", "at": 0}}}', '[]', 'x'):
        names.write_text(damaged)
        client = make(tmp_path, Steam())
        assert (client.names, client.entries) == ({}, {})


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
