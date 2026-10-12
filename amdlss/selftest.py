# SPDX-License-Identifier: GPL-2.0-or-later
"""`python AMDLSS.pyw --selftest`: plain asserts on the logic that can break, no window, no network, no HelixSR."""
import io
import json
import tempfile
import zipfile
from pathlib import Path

from . import framegen, games, helixsr, logs


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
    assert [str(c) for c in command[1:3]] == ['-I', '-c'] and [str(c) for c in command[-3:]] == ['C:\\h', 'D:/g', 'E:/x']
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
    assert helixsr.setup_command() == ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                       'helixsr-setup.ps1', '-Yes']
    client = helixsr.HelixSR('C:/h', python='C:/p/python.exe')
    command = client.install_command(3, ['D:/g'])
    assert command[-4:] == ['install', '3', '--folder', 'D:/g'], command
    assert client.remove_command(3)[-2:] == ['uninstall', '3']
    assert list(helixsr.feed_lines([b'a\r', b'\nb\rc\r\n', b'tail'])) == [('a', True), ('b', False), ('c', True),
                                                                          ('tail', True)]
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00' + b'x' * 100)
        assert helixsr.version_of(folder) == '1.8.1'
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


def run():
    for check in (check_parsing, check_game_rules, check_routes, check_visibility, check_framegen, check_logging_switch,
                  check_collect_logs, check_helixsr, check_zip_slip):
        check()
        print(f'ok  {check.__name__}')
    print('selftest passed')
    return 0
