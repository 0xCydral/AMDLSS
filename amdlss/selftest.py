# SPDX-License-Identifier: GPL-2.0-or-later
"""`python AMDLSS.pyw --selftest`: plain asserts on the logic that can break, no window, no network, no HelixSR."""
import io
import json
import tempfile
import zipfile
from pathlib import Path

from . import games, helixsr


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
    for check in (check_parsing, check_game_rules, check_helixsr, check_zip_slip):
        check()
        print(f'ok  {check.__name__}')
    print('selftest passed')
    return 0
