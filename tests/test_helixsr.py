# SPDX-License-Identifier: GPL-2.0-or-later
"""Command lines, built detection, the game list and the install and remove path, against a fake HelixSR folder."""
import sys
from pathlib import Path

import pytest

from amdlss import games, helixsr
from fake_helixsr import make


def test_setup_command_line_and_how_it_is_started(tmp_path, monkeypatch):
    assert helixsr.setup_command() == ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                       'helixsr-setup.ps1', '-Yes']
    seen = {}

    class FakeProcess:
        stdout = type('S', (), {'read1': lambda self, n: b''})()

        def wait(self):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    def fake_popen(command, **kwargs):
        seen.update(kwargs, command=command)
        return FakeProcess()

    monkeypatch.setattr(helixsr.subprocess, 'Popen', fake_popen)
    (tmp_path / helixsr.BUILT).write_text('{}')
    (tmp_path / helixsr.DLL).write_bytes(b'x')
    with pytest.raises(helixsr.HelixSRError):  # exit 0, but the stale BUILT file was deleted and not rewritten
        helixsr.build(tmp_path)
    assert seen['stdin'] == helixsr.subprocess.DEVNULL
    assert seen['creationflags'] == helixsr.NO_WINDOW
    assert seen['cwd'] == str(tmp_path) and seen['command'][-1] == '-Yes'


def test_a_setup_that_builds_nothing_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(helixsr, 'run_streaming', lambda *a, **k: (0, []))
    (tmp_path / helixsr.DLL).write_bytes(b'x')
    with pytest.raises(helixsr.HelixSRError, match='missing'):
        helixsr.build(tmp_path)


def test_a_failed_setup_reports_its_last_line(tmp_path, monkeypatch):
    monkeypatch.setattr(helixsr, 'run_streaming', lambda *a, **k: (1, ['one', 'checksum mismatch']))
    with pytest.raises(helixsr.HelixSRError, match='exit 1.*checksum mismatch'):
        helixsr.build(tmp_path)


def test_built_detection(tmp_path):
    assert not helixsr.built(tmp_path)
    (tmp_path / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    assert not helixsr.built(tmp_path)  # the DLL alone is the unbuilt download
    (tmp_path / helixsr.BUILT).write_text('{}')
    assert helixsr.built(tmp_path) and helixsr.version_of(tmp_path) == '1.8.1'


def test_locate_prefers_the_override_then_the_data_folder(tmp_path):
    assert helixsr.locate(tmp_path) is None
    inner = helixsr.install_folder(tmp_path)
    inner.mkdir()
    (inner / helixsr.DLL).write_bytes(b'x')
    assert helixsr.locate(tmp_path) == inner
    other = tmp_path / 'other'
    other.mkdir()
    (other / helixsr.DLL).write_bytes(b'x')
    assert helixsr.locate(tmp_path, str(other)) == other
    assert helixsr.locate(tmp_path, str(tmp_path / 'missing')) == inner


def test_installer_command_lines(tmp_path):
    client = helixsr.HelixSR(tmp_path, python='py.exe')
    python = Path('py.exe')
    assert client.install_command(7) == [python, client.installer, tmp_path, 'install', '7']
    assert client.remove_command(7, ['D:/a', 'E:/b']) == [python, client.installer, tmp_path, 'uninstall', '7',
                                                          '--folder', 'D:/a', '--folder', 'E:/b']
    assert client.list_command()[-1] == 'list'
    assert client.installer.name == 'helixsr_install.py' and client.installer.parent.name == 'setup'


def game_folder(root, name):
    folder = root / name
    folder.mkdir()
    (folder / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'GAME ORIGINAL')
    return folder


@pytest.fixture
def fake(tmp_path):
    one, two = game_folder(tmp_path, 'One'), game_folder(tmp_path, 'Two')
    folder = make(tmp_path, [{'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']},
                             {'name': 'Two', 'folder': str(two), 'fsr': True, 'inputs': ['DLSS', 'XeSS']},
                             {'name': 'Three', 'folder': str(tmp_path / 'Three'), 'fsr': False, 'inputs': ['XeSS']}])
    return helixsr.HelixSR(folder, python=sys.executable), one, two


def test_the_bridge_lists_games_as_json(fake):
    client, one, _two = fake
    listed = client.list_games()
    assert [g.name for g in listed] == ['One', 'Two', 'Three']
    assert [g.index for g in listed] == [1, 2, 3]
    assert listed[0].route == 'direct' and listed[0].fsr31 and listed[0].state == 'none'
    assert listed[2].route == 'optiscaler' and not listed[2].fsr31 and listed[2].supports == 'XeSS'
    assert games.match_game(listed, games.Game(name='x', folder=str(one) + '\\')).name == 'One'


def test_extra_folders_reach_the_installer(fake, tmp_path):
    client, _one, _two = fake
    extra = tmp_path / 'Extra'
    extra.mkdir()
    assert client.list_games([extra])[-1].name == 'Extra'


def test_the_list_falls_back_to_the_printed_output(fake, monkeypatch):
    client, _one, _two = fake
    monkeypatch.setattr(client, 'bridge_command', lambda extras=(): [client.python, '-c', 'raise SystemExit(5)'])
    listed = client.list_games()
    assert [(g.index, g.name, g.route) for g in listed] == [(1, 'One', 'direct'), (2, 'Two', 'direct'),
                                                            (3, 'Three', 'optiscaler')]


def test_install_then_remove_restores_the_original(fake):
    client, one, two = fake
    dll = one / 'amd_fidelityfx_upscaler_dx12.dll'
    lines = []
    target = client.list_games()[0]
    after = client.install(target, on_line=lambda text, final: lines.append(text))
    assert after.state == 'direct' and after.route == 'direct'
    assert dll.read_bytes().startswith(b'HELIX')
    assert (one / 'amd_fidelityfx_upscaler_dx12.original.dll').read_bytes() == b'GAME ORIGINAL'
    assert any('One' in line for line in lines)
    assert (two / 'amd_fidelityfx_upscaler_dx12.dll').read_bytes() == b'GAME ORIGINAL'  # the other game is untouched
    client.remove(client.list_games()[0])
    assert dll.read_bytes() == b'GAME ORIGINAL' and not (one / 'amd_fidelityfx_upscaler_dx12.original.dll').exists()


def test_a_game_that_left_the_list_is_not_touched(fake):
    client, one, _two = fake
    ghost = games.Game(name='Gone', folder=str(one.parent / 'Gone'))
    with pytest.raises(helixsr.HelixSRError, match='no longer'):
        client.install(ghost)
    assert (one / 'amd_fidelityfx_upscaler_dx12.dll').read_bytes() == b'GAME ORIGINAL'


def test_a_failing_installer_is_reported(fake, monkeypatch):
    client, _one, _two = fake
    monkeypatch.setattr(helixsr, 'run_streaming', lambda *a, **k: (1, ['boom']))
    with pytest.raises(helixsr.HelixSRError, match='boom'):
        client.install(games.Game(name='One', folder=client.list_games()[0].folder))


def test_missing_helixsr_python_is_a_clear_error(tmp_path):
    client = helixsr.HelixSR(tmp_path, python=tmp_path / 'nope.exe')
    with pytest.raises(helixsr.HelixSRError, match='Python is missing'):
        client.list_games()


def test_progress_lines_are_split_from_finished_lines():
    assert list(helixsr.feed_lines([b'10%\r20%\r', b'30%\r\ndone\n'])) == [
        ('10%', False), ('20%', False), ('30%', True), ('done', True)]
