# SPDX-License-Identifier: GPL-2.0-or-later
"""`python AMDLSS.pyw --selftest`: plain asserts on the logic that can break, no window, no network, no HelixSR."""
import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from . import VERSION, covers, crashes, framegen, fsr4, games, gpu, helixsr, identify, logs, reports, scaler, update


def check_parsing():
    printed = ('[helixsr-setup] looking for games with DLSS, XeSS or FSR ...\n'
               '    1. Aniimo                                       FSR 3.1 (HelixSR replaces the game\'s FSR DLL: '
               'select AMD FSR in the game)\n'
               '    2. Far Cry 6                                    DLSS / XeSS (through OptiScaler: select DLSS in '
               'the game)\n'
               '    3. Sven Co-op                                   HelixSR installed\n'
               '    4. Other Game                                   DLSS / XeSS only: not supported by the drop-in: '
               'use HelixSR Desktop (HelixSR Scaler)\n'
               '  10. A Game With A Very Long Name That Is Past The Pad HelixSR installed (OptiScaler, earlier '
               'version)\n')
    rows = games.parse_list_output(printed)
    assert [g.index for g in rows] == [1, 2, 3, 4, 10], rows
    assert rows[0].route == 'direct' and rows[0].fsr31
    assert rows[1].inputs == ['DLSS', 'XeSS'] and rows[1].route == 'optiscaler'
    assert rows[2].state == 'unknown' and rows[2].installed
    assert rows[3].inputs == ['DLSS', 'XeSS'] and rows[3].route == 'optiscaler'   # 1.9.0's own label
    assert rows[4].name.endswith('Past The Pad') and rows[4].state == 'unknown'
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
    # the OptiScaler route is HelixSR Scaler now: AMDLSS installs it itself, pinned by SHA-256, and the bridge
    # maps 1.9.0's 'unsupported' to it
    assert scaler.ZIP_URL.endswith(f'/v1.9.0/{scaler.ZIP_NAME}') and scaler.ZIP_SIZE > 100 << 20
    assert scaler.NAMES[0] == 'dxgi.dll' and scaler.LIST == 'HelixSR-Scaler-package.txt'
    assert "== 'unsupported'" in helixsr.BRIDGE   # 1.9.0's route for a DLSS/XeSS/FSR 2 only game
    plan = games.plan_text(games.Game(name='G', route='optiscaler', exe_folder='E:\\G'))
    assert 'HelixSR Scaler' in plan and 'Install into G?' in plan


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


def check_unsafe_folder():
    import ntpath
    assert games.is_root_folder('E:\\', ntpath) and games.is_root_folder('/') and not games.is_root_folder('E:\\Games', ntpath)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        content = root / 'xcaja' / 'Valor Mortis' / 'Content'
        win64 = content / 'ValorMortis' / 'Binaries' / 'Win64'
        win64.mkdir(parents=True)
        (win64 / 'ValorMortisMSG-Win64-Shipping.exe').write_bytes(b'MZ')
        (content / 'MicrosoftGame.config').write_text(
            '<Game><ShellVisuals DefaultDisplayName="Valor Mortis"/></Game>')
        other = root / 'Other'
        other.mkdir()
        (other / 'Other.exe').write_bytes(b'MZ')
        wide = games.Game(name='', folder=str(root), exe_folder=str(win64), inputs=['DLSS'], state='optiscaler')
        rows = games.classify([wide, games.Game(name='O', folder=str(other), exe_folder=str(other),
                                                 inputs=['DLSS'])], [str(content)])
        assert wide.folder == str(content) and wide.name == 'Valor Mortis'
        assert wide.reason == games.UNSAFE_FOLDER and not wide.can_install and games.visible(wide)
        client = helixsr.HelixSR(root, python='py.exe', running=lambda g: False)
        for act in (client.install, client.remove):
            try:
                act(wide)
            except helixsr.HelixSRError as error:
                assert 'folder covers other games' in str(error)
            else:
                raise AssertionError('a folder over other games was changed')
        assert crashes.game_exes(wide) == ['ValorMortisMSG-Win64-Shipping.exe']


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
        path = logs.collect(game, None, Path(folder) / 'data', Path(folder) / 'out', {}, windows=False)
        with zipfile.ZipFile(path) as archive:
            assert sorted(archive.namelist()) == ['crashes.txt', 'exe-folder/HelixSR/helixsr.log',
                                                  'files.txt', 'system.txt']
            assert b'HelixSR 1.8.1' in archive.read('files.txt')

def check_history_and_crashes():
    import base64
    import time as clock
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / 'Game'
        root.mkdir()
        (root / 'One.exe').write_bytes(b'MZ')
        (root / 'OptiScaler.log').write_bytes(b'run one C:\\Users\\Jordan\\x')
        game = games.Game(name='One', folder=str(root), exe_folder=str(root))
        data = Path(folder) / 'data'
        when = clock.struct_time((2026, 10, 13, 14, 5, 0, 0, 0, -1))
        kept = logs.preserve_history(game, data, logs.redactor(['Jordan']), when)
        assert kept is not None and kept.name == '20261013-140500'
        (root / 'OptiScaler.log').unlink()
        held = dict(logs.game_evidence(game, data, logs.redactor(['Jordan']), windows=False))
        assert held['history/20261013-140500/exe-folder/OptiScaler.log'] == b'run one C:\\Users\\<user>\\x'
        assert b'PROTON_LOG=1' in held['crashes.txt'] and reports.has_report(game, data)
        event = {'time': '2026-10-13 14:20:11', 'id': 1000, 'message': '',
                 'props': ['One.exe', '1.0', '0x1', 'dxgi.dll', '0.9.4.0', '0x2', '0xc0000005', '0x99',
                           '0x1', '0x1', 'E:\\One.exe', 'E:\\dxgi.dll']}
        answer = base64.b64encode(json.dumps([event]).encode()).decode()
        entries, why = crashes.crash_entries(['One.exe'], run=lambda command: answer)
        assert why is None and entries[0]['module'] == 'dxgi.dll' and entries[0]['exception'] == '0xc0000005'
        assert crashes.game_exes(game) == ['One.exe']
        assert 'Get-WinEvent -FilterHashtable' in crashes.event_command()[4]


def check_streamline():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / 'Game' / 'NVStreamline' / 'production'
        root.mkdir(parents=True)
        (root / 'sl.interposer.dll').write_bytes(b'MZ')
        assert not games.scan_streamline(Path(folder))   # one of the two DLLs is not DLSS frame generation
        (root / 'sl.dlss_g.dll').write_bytes(b'MZ')
        assert games.scan_streamline(Path(folder)) and not games.scan_streamline(Path(folder) / 'none')
        assert games.Game(name='x', streamline=False).framegen_reason == games.NO_DLSSG


def check_hang_and_unreal():
    hang = {'time': '2026-10-13 14:20:11', 'id': 1002, 'props': ['One.exe', '1.0', 'E:\\One.exe']}
    assert crashes.parse_event(hang, ['One.exe'])['name'] == 'AppHang'
    unreal = games.Game(name='U', exe_folder='/g/U/Proj/Binaries/Win64')
    assert crashes.unreal_project(unreal, windows=False) == ('Proj', '/g/U/Proj')
    assert crashes.running_text(games.Game(name='U', folder='E:\\U'), True, processes=['E:\\U\\U.exe']) == 'yes'


def check_reports():
    files = {'G/exe-folder/helixsr.log': 'ok\n', 'system.txt': 'x'}
    sample = {'app': 'AMDLSS', 'version': '0.1.0', 'game': 'G', 'files': files}
    assert reports.problem_of(sample) is None
    for bad in ({'../x.log': 'x'}, {'G/a.exe': 'x'}, {'a.log': 'x\x00'}, {'a/b/c/d/e/f/g/h.log': 'x'}):
        assert reports.problem_of({**sample, 'files': bad}), bad
    assert reports.valid_code('ABCD2345') and not reports.ReportClient('').configured
    assert reports.folder_names([games.Game(name='Far Cry 6'), games.Game(name='far cry 6')]) == ['Far-Cry-6', 'far-cry-6-2']


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
        assert 'frame generation is not built' in helixsr.fg_reason(folder)   # the setup made none (or an older one)
        (Path(folder) / helixsr.FG_SETUP).write_text('{}')
        fg = Path(folder) / helixsr.FG_FOLDER
        fg.mkdir()
        for name in helixsr.FG_DLLS:
            (fg / name).write_bytes(b'MZ')
        assert helixsr.fg_reason(folder) == '' and helixsr.fg_folder(folder) == fg
        assert helixsr.install_helixsr(folder, consented=False) is None  # declined: nothing runs, nothing is written
        assert sorted(p.name for p in Path(folder).iterdir() if p.is_file()) == \
            sorted([helixsr.DLL, helixsr.BUILT, helixsr.FG_SETUP])


def check_scaler():
    """HelixSR Scaler's placement, record and removal, on a stand-in package and a fake game (no network)."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        package = root / 'package'
        listed = ('OptiScaler.dll', 'OptiScaler.ini', 'OptiScaler/streamline/sl.dlss_g.dll')
        for rel in listed + ('README.txt',):
            path = package / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f'stand-in {rel}'.encode())
        (package / scaler.LIST).write_text('\n'.join(listed), encoding='utf-8')
        assert scaler.files(package) == list(listed)
        helix = root / helixsr.TOP
        helix.mkdir()
        (helix / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.9.0\x00')
        fg = helix / helixsr.FG_FOLDER
        fg.mkdir()
        for name in helixsr.FG_DLLS:
            (fg / name).write_bytes(b'MZ')
        (helix / helixsr.FG_SETUP).write_text('{}')
        game = root / 'Game'
        game.mkdir()
        (game / 'Game.exe').write_bytes(b'MZ')
        (game / 'OptiScaler.ini').write_bytes(b'[FrameGen]\r\nFGInput=auto\r\nFGOutput=auto\r\n')
        before = {str(p.relative_to(game)): p.read_bytes() for p in game.rglob('*') if p.is_file()}
        record = {'amdlss': VERSION, 'scaler': scaler.VERSION, 'name': 'dxgi.dll', 'moved': [], 'created': []}
        scaler.install_into(package, game, helix, 'dxgi.dll', record)
        assert (game / 'dxgi.dll').read_bytes() == b'stand-in OptiScaler.dll'
        assert (game / 'OptiScaler' / 'streamline' / 'sl.dlss_g.dll').is_file()
        assert (game / 'HelixSR' / 'helixsr.dll').read_bytes().startswith(b'MZ')
        assert (game / 'HelixSR' / 'helixsr-fg' / 'nvexec64.dll').is_file()
        assert (game / 'OptiScaler.ini').read_bytes() == b'stand-in OptiScaler.ini'   # the game's own was kept
        assert record['moved'] and record['moved'][0]['name'] == 'OptiScaler.ini'
        assert not (game / 'README.txt').exists()      # not in the package list: never copied
        scaler.record_path(game).write_text(json.dumps(record), encoding='utf-8')
        assert scaler.installed(game)
        scaler.remove(game)
        assert not scaler.installed(game)
        assert {str(p.relative_to(game)): p.read_bytes() for p in game.rglob('*') if p.is_file()} == before


def check_running_game():
    locked = 'PermissionError: [WinError 5] Acceso denegado: "E:\\Games\\One\\dxgi.dll"'
    assert helixsr.plain_failure(locked, 'One') == helixsr.still_running_message('One')
    assert helixsr.plain_failure('boom', 'One') is None
    assert helixsr.running_from(r'E:\Games\One', windows=True, run=lambda command: 'E:\\Games\\One\\game.exe\n')
    assert not helixsr.running_from(r'E:\Games\One', windows=True, run=lambda command: 'C:\\Windows\\explorer.exe\n')
    assert not helixsr.inside(r'E:\Games\OneTwo\game.exe', r'E:\Games\One', windows=True)
    game = games.Game(name='One', folder=r'E:\Games\One', state='optiscaler')
    client = helixsr.HelixSR('C:/h', python='C:/p/python.exe', running=lambda g: True)
    for act in (client.install, client.remove):
        try:
            act(game)
        except helixsr.HelixSRError as error:
            assert 'still running' in str(error)
        else:
            raise AssertionError('an install or remove ran while the game was running')


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
    from . import ui
    if ui.ImageTk is not None:   # ImageTk loads this on its first photo; a frozen build without it draws no cover
        import importlib
        importlib.import_module('PIL._tkinter_finder')
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


def check_fsr4():
    assert gpu.classify([gpu.Gpu('', '1002', '744c'), gpu.Gpu('', '10de', '2684')]) == gpu.RDNA3
    assert gpu.classify([gpu.Gpu('AMD Radeon 780M Graphics', '1002', '15bf')]) == gpu.OTHER
    ready = fsr4.Source(Path('x.dll'), "AMD's DLL", '', '')
    both = games.Game(name='x', fsr_targets=['a.dll'], inputs=['DLSS'], can_opti=True, route='direct')
    assert fsr4.availability(both, ready).routes == ('direct', 'optiscaler')
    assert fsr4.availability(games.Game(name='x', anticheat=['BattlEye']), ready).card_text == \
        'FSR 4.1.1: no, anti-cheat'
    with tempfile.TemporaryDirectory() as folder:   # the drop-in round trip on a fake game
        root = Path(folder)
        (root / 'Game').mkdir()
        (root / 'amd').mkdir()
        dll = root / 'Game' / fsr4.DLL
        dll.write_bytes(b'GAME')
        (root / 'amd' / fsr4.DLL).write_bytes(b'FSR4')
        (root / 'amd' / fsr4.LICENSE).write_text('AMD license')
        source = fsr4.Source(root / 'amd' / fsr4.DLL, "AMD's DLL", '', '')
        fsr4.install_dropin([str(dll)], source, root / 'data', root / 'amd' / fsr4.LICENSE)
        assert dll.read_bytes() == b'FSR4' and (root / 'Game' / 'amd_fidelityfx_upscaler_dx12.original.dll').read_bytes() == b'GAME'
        assert fsr4.dropin_folders([root / 'Game']) == [str(root / 'Game')]
        assert fsr4.remove_dropin([root / 'Game']) == []
        assert [p.name for p in (root / 'Game').iterdir()] == [fsr4.DLL] and dll.read_bytes() == b'GAME'


def run():
    for check in (check_parsing, check_game_rules, check_routes, check_visibility, check_unsafe_folder, check_framegen, check_logging_switch,
                  check_collect_logs, check_history_and_crashes, check_streamline, check_hang_and_unreal, check_reports,
                  check_helixsr, check_scaler, check_running_game, check_zip_slip, check_update, check_identify,
                  check_covers, check_fsr4):
        check()
        print(f'ok  {check.__name__}')
    print('selftest passed')
    return 0
