# SPDX-License-Identifier: GPL-2.0-or-later
"""`python AMDLSS.pyw --selftest`: plain asserts on the logic that can break, no window, no network, no HelixSR."""
import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from . import covers, framegen, games, helixsr, identify, logs, update


def check_parsing():
    printed = ('[helixsr-setup] looking for games with DLSS, XeSS or FSR ...\n'
               '    1. Aniimo                                       FSR 3.1 (HelixSR replaces the game\'s FSR DLL: '
               'select AMD FSR in the game)\n'
               '    2. Far Cry 6                                    DLSS / XeSS (through OptiScaler: select DLSS in '
               'the game)\n'
               '    3. Sven Co-op                                   HelixSR installed\n'
               '  10. A Game With A Very Long Name That Is Past The Pad HelixSR installed\n')
    rows = games.parse_list_output(printed)
    assert [g.index for g in rows] == [1, 2, 3, 10], rows
    assert rows[0].route == 'direct' and rows[0].fsr31
    assert rows[1].inputs == ['DLSS', 'XeSS'] and rows[1].route == 'optiscaler'
    assert rows[2].state == 'unknown' and rows[2].installed
    assert rows[3].name.endswith('Past The Pad')
    line = games.SENTINEL + json.dumps([{'index': 1, 'name': 'A', 'folder': 'C:\\g', 'state': 'direct'}])
    parsed = games.parse_bridge_output('noise\n' + line + '\n')
    assert parsed[0].state_text == 'FSR drop-in' and parsed[0].index == 1
    assert games.parse_bridge_output('no sentinel here') is None


def check_game_rules():
    dlss = games.Game(name='x', state='dlss')
    assert not dlss.can_install and dlss.installed  # the experimental route is never offered
    assert games.Game(name='x', route='direct').in_game() == 'AMD FSR'
    assert games.Game(name='x', route='optiscaler').in_game() == 'DLSS'
    assert games.anticheat_of('EasyAntiCheat_EOS.exe') == 'Easy Anti-Cheat'
    assert games.anticheat_of('BEService_x64.exe') == 'BattlEye'
    assert games.anticheat_of('faceitems.pak') is None  # a whole word, never the middle of a name


def check_routes():
    both = games.Game(name='x', fsr31=True, can_opti=True, inputs=['DLSS'], route='direct')
    assert both.route_choice and both.default_route == 'optiscaler'  # DLSS listed: offered first
    assert games.Game(name='x', fsr31=True, can_opti=True, inputs=['XeSS'], route='direct').default_route == 'direct'
    assert not games.Game(name='x', route='optiscaler').route_choice  # only one route works
    assert not games.Game(name='x', can_opti=True, state='optiscaler').route_choice  # installed
    assert both.in_game() == 'AMD FSR' and games.Game(name='x', state='optiscaler', route='direct').in_game() == 'DLSS'
    command = helixsr.HelixSR('C:/h', python='C:/p/python.exe').optiscaler_command('D:/g', ['E:/x'])
    assert [str(c) for c in command[1:3]] == ['-I', '-c']
    assert [str(c) for c in command[-3:]] == [str(Path('C:/h')), 'D:/g', 'E:/x']
    assert "g.route, g.chosen = 'optiscaler', g.can_opti" in helixsr.BRIDGE_INSTALL


def check_visibility():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'Ok').mkdir()
        (root / 'Ok' / 'Ok.exe').write_bytes(b'MZ')
        ok = games.Game(name='Ok', folder=str(root / 'Ok'), exe_folder=str(root / 'Ok'), inputs=['XeSS'])
        leftover = games.Game(name='Left', folder=str(root / 'Left'), exe_folder=str(root / 'Left'), inputs=['DLSS'])
        plain = games.Game(name='Plain', folder=str(root / 'Ok'), exe_folder=str(root / 'Ok'))
        kept = games.Game(name='Kept', folder=str(root / 'Left'), state='optiscaler')
        rows = games.classify([ok, leftover, plain, kept], [str(root / 'Typo')])
        assert [g.reason for g in rows] == ['', 'not installed', 'no DLSS, FSR 3.1 or XeSS found', '', 'folder not found']
        assert [g.name for g in rows if games.visible(g)] == ['Ok', 'Kept', 'Typo']
        assert games.count_text(rows) == '2 games ready, 2 hidden' and not leftover.can_install


def check_framegen():
    ini = b'[FrameGen]\r\n; c\r\nFGInput = nofg\r\nFGOutput=auto\r\n[Other]\r\nFGInput=keep\r\n'
    on = framegen.set_values(ini, framegen.ON)
    assert on == ini.replace(b'nofg', b'dlssg').replace(b'FGOutput=auto', b'FGOutput=fsrfg'), on
    assert framegen.read_values(on) == framegen.ON
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / 'OptiScaler.ini').write_bytes(ini)
        assert framegen.apply(folder, True, folder) and framegen.is_on(folder)
        assert framegen.apply(folder, False, folder) and (Path(folder) / 'OptiScaler.ini').read_bytes() == ini


def check_logging_switch():
    ini = b'[Upscalers]\r\nLogLevel=x\r\n[Log]\r\nLogToFile=auto\r\nLogLevel = auto\r\n[Other]\r\n'
    on = framegen.set_values(ini, framegen.LOGGING.values, framegen.LOGGING)
    assert on == ini.replace(b'LogToFile=auto', b'LogToFile=true').replace(b'= auto', b'= 2'), on
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / 'OptiScaler.ini').write_bytes(ini)
        (Path(folder) / 'OptiScaler.log').write_text('ours')
        assert framegen.apply(folder, True, folder, framegen.LOGGING) and framegen.is_on(folder, framegen.LOGGING)
        framegen.forget(folder, folder)   # Remove: the record and the log it made are gone
        assert not (Path(folder) / 'OptiScaler.log').exists() and not (Path(folder) / framegen.RECORD).exists()


def check_collect_logs():
    redact = logs.redactor(['Jordan'])
    assert redact('C:\\Users\\Jordan\\Games and Jordan') == 'C:\\Users\\<user>\\Games and <user>'
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / 'Game'
        (root / 'HelixSR').mkdir(parents=True)
        (root / 'HelixSR' / 'helixsr.log').write_bytes(b'path C:\\Users\\Jordan\\x')
        (root / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
        game = games.Game(name='G: x', folder=str(root), exe_folder=str(root))
        path = logs.collect(game, None, Path(folder) / 'data', Path(folder) / 'out', {})
        with zipfile.ZipFile(path) as archive:
            assert sorted(archive.namelist()) == ['exe-folder/HelixSR/helixsr.log', 'files.txt', 'system.txt']
            assert b'HelixSR 1.8.1' in archive.read('files.txt')

def check_helixsr():
    assert helixsr.setup_command(windows=True) == ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                                   'helixsr-setup.ps1', '-Yes']
    assert helixsr.setup_command(windows=False) == ['bash', 'helixsr-setup.sh', '--yes']
    assert 'portable' in helixsr.consent_text(windows=False) and 'Proton' in helixsr.consent_text(windows=False)
    assert '%LOCALAPPDATA%' in helixsr.consent_text(windows=True)
    assert not helixsr.has_numpy('amdlss-no-such-python')
    client = helixsr.HelixSR('C:/h', python='C:/p/python.exe')
    command = client.install_command(3, ['D:/g'])
    assert command[-4:] == ['install', '3', '--folder', 'D:/g'], command
    assert client.remove_command(3)[-2:] == ['uninstall', '3']
    assert list(helixsr.feed_lines([b'a\r', b'\nb\rc\r\n', b'tail'])) == [('a', True), ('b', False), ('c', True),
                                                                          ('tail', True)]
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00' + b'x' * 100)
        assert helixsr.version_of(folder) == '1.8.1'
        assert helixsr.older('1.9.0', '1.10.0') and not helixsr.older('1.10.0', '1.9.0') and not helixsr.older('?', '1.9.0')
        assert not helixsr.built(folder)
        (Path(folder) / helixsr.BUILT).write_text('{}')
        assert helixsr.built(folder)
        assert helixsr.install_helixsr(folder, consented=False) is None  # declined: nothing runs, nothing is written
        assert sorted(p.name for p in Path(folder).iterdir()) == sorted([helixsr.DLL, helixsr.BUILT])


def check_zip_slip():
    with tempfile.TemporaryDirectory() as folder:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('../evil.txt', 'x')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            try:
                helixsr.safe_members(archive, Path(folder) / 'target')
            except helixsr.HelixSRError:
                return
    raise AssertionError('a zip with a path outside the folder was accepted')


def check_update():
    order = ['0.1.0-beta.3', '0.1.0-beta.4', '0.1.0-beta.10', '0.1.0']
    assert [update.version_tuple(v) for v in order] == sorted(update.version_tuple(v) for v in order)
    sha = hashlib.sha256(b'new').hexdigest()
    asset = {'name': 'AMDLSS.exe', 'browser_download_url': 'https://x/AMDLSS.exe', 'digest': f'sha256:{sha}'}
    linux = {'name': 'AMDLSS-linux-x86_64', 'browser_download_url': 'https://x/AMDLSS-linux-x86_64',
             'digest': f'sha256:{sha}'}
    both = [{'tag_name': 'v0.1.0', 'assets': [linux, asset]}]   # each system takes its own file
    assert update.newest_release(both, beta=False, windows=True).url.endswith('AMDLSS.exe')
    assert update.newest_release(both, beta=False, windows=False).url.endswith('AMDLSS-linux-x86_64')
    listed = [{'tag_name': 'v0.1.0-beta.5', 'prerelease': True, 'assets': [asset]},
              {'tag_name': 'v0.1.0-beta.4', 'prerelease': True, 'assets': [{**asset, 'digest': None}]}]
    assert update.newest_release(listed, beta=False, windows=True) is None
    best = update.newest_release(listed, beta=True, windows=True)
    assert best.version == '0.1.0-beta.5' and best.sha256 == sha and update.newer(best, '0.1.0-beta.4')
    assert update.newest_release(listed[1:], beta=True, windows=True).sha256 is None   # no digest: never installed
    with tempfile.TemporaryDirectory() as folder:
        exe, new = Path(folder) / 'AMDLSS.exe', Path(folder) / 'AMDLSS.exe.download'
        exe.write_bytes(b'old')
        new.write_bytes(b'new')
        update.swap(exe, new, windows=True)
        assert exe.read_bytes() == b'new' and (Path(folder) / 'AMDLSS.exe.old').read_bytes() == b'old'
        assert update.cleanup_old(exe, tries=1) and sorted(p.name for p in Path(folder).iterdir()) == ['AMDLSS.exe']
        mine, fresh = Path(folder) / 'AMDLSS-linux-x86_64', Path(folder) / 'AMDLSS-linux-x86_64.download'
        mine.write_bytes(b'old')
        if os.name != 'nt':
            mine.chmod(0o755)
        fresh.write_bytes(b'new')
        update.swap(mine, fresh, windows=False)   # one rename over the running file, no .old beside it
        assert mine.read_bytes() == b'new' and not (Path(folder) / 'AMDLSS-linux-x86_64.old').exists()
        assert os.name == 'nt' or mine.stat().st_mode & 0o111   # and it stayed executable


def check_identify():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / 'TheWitcher3_WildHunt'
        (root / 'bin' / 'x64').mkdir(parents=True)
        (root / 'goggame-1207664663.info').write_text(json.dumps({'gameId': '1207664663', 'name': 'Witcher: Complete'}))
        game = games.Game(name='The Witcher 3', folder=str(root), exe_folder=str(root / 'bin' / 'x64'))
        found = identify.identify(game)
        assert found.gog_id == 1207664663 and found.steam_appid is None and found.source == 'gog'
        assert found.titles == ['Witcher: Complete', 'The Witcher 3', 'The Witcher 3 Wild Hunt'], found.titles
    assert identify.tidy_folder_name('Cyberpunk2077') == 'Cyberpunk 2077' and identify.generic_product('UE4')


def check_covers():
    jpeg = b'\xff\xd8\xff\xe0' + b'x' * 50
    asked = []
    found = [{'appid': '292030', 'name': 'The Witcher 3: Wild Hunt \u2014 Remastered'},
             {'appid': '5', 'name': 'The Witcher 3: Wild Hunt \u2014 Soundtrack'}]

    def hosts(url, limit):   # Steam knows The Witcher 3 (and a soundtrack), GOG knows one more game
        asked.append(url)
        if 'SearchApps' in url:
            return json.dumps(found if 'Witcher' in url else []).encode()
        if 'appdetails' in url:
            appid = url.split('appids=')[1].split('&')[0]
            return json.dumps({appid: {'data': {'type': 'game' if appid == '292030' else 'dlc'}}}).encode()
        if 'api.gog.com' in url:
            return json.dumps({'images': {'background': '//images-1.gog-statics.com/b.jpg'}}).encode()
        return jpeg if '/292030/' in url or 'gog-statics.com' in url else None

    assert identify.normalise("Dragon's  Dogma\u00ae 2") == identify.normalise('dragon\u2019s dogma 2')
    with tempfile.TemporaryDirectory() as folder:
        client = covers.CoverClient(folder, fetch=hosts)
        assert client.steam_match('The Witcher 3') == 292030   # a game that continues the title, never DLC
        assert client.steam_match('Witcher') is None and client.steam_match('Wild Hunt') is None   # not from the middle
        assert client.get(games.Game(name='The Witcher 3')).name == '292030.jpg'
        assert client.get(games.Game(name='Unlisted')) is None and client.cached(games.Game(name='Unlisted')) is None
        asked.clear()
        assert client.get(games.Game(name='Unlisted')) is None and not asked   # a miss is remembered
        gog = identify.Identity(gog_id=7, titles=['Only On GOG'])
        assert client.fetch_cover(client.resolve(gog)).name == 'gog-7.jpg'
        assert sorted(p.name for p in (Path(folder) / 'covers').iterdir()) == ['292030.jpg', 'gog-7.jpg', 'names.json']
        client.enabled = False
        assert client.get(games.Game(name='The Witcher 3')) is None


def run():
    for check in (check_parsing, check_game_rules, check_routes, check_visibility, check_framegen, check_logging_switch,
                  check_collect_logs, check_helixsr, check_zip_slip, check_update, check_identify, check_covers):
        check()
        print(f'ok  {check.__name__}')
    print('selftest passed')
    return 0
