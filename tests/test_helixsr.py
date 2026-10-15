# SPDX-License-Identifier: GPL-2.0-or-later
"""Command lines, built detection, the game list and the install and remove path, against a fake HelixSR folder."""
import os
import sys
from pathlib import Path

import pytest

from amdlss import games, helixsr, logs
from fake_helixsr import make


def test_setup_command_line_and_how_it_is_started(tmp_path, monkeypatch):
    assert helixsr.setup_command(windows=True) == ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                                   'helixsr-setup.ps1', '-Yes']
    assert helixsr.setup_command(windows=False) == ['bash', 'helixsr-setup.sh', '--yes']
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
    monkeypatch.setattr(helixsr, 'find_python', lambda windows=None: None)  # the probe would run the fake Popen
    (tmp_path / helixsr.BUILT).write_text('{}')  # a record of an earlier build, its DLL gone: it must not pass
    with pytest.raises(helixsr.HelixSRError):    # exit 0, but the stale BUILT file was deleted and not rewritten
        helixsr.build(tmp_path)
    assert not (tmp_path / helixsr.BUILT).exists()
    assert seen['stdin'] == helixsr.subprocess.DEVNULL
    assert seen['creationflags'] == helixsr.NO_WINDOW
    assert seen['cwd'] == str(tmp_path) and seen['command'] == helixsr.setup_command()  # the host's own setup


def test_a_setup_that_builds_nothing_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(helixsr, 'run_streaming', lambda *a, **k: (0, []))
    (tmp_path / helixsr.DLL).write_bytes(b'x')
    with pytest.raises(helixsr.HelixSRError, match='missing'):
        helixsr.build(tmp_path)


def test_a_failed_setup_reports_its_last_line(tmp_path, monkeypatch):
    monkeypatch.setattr(helixsr, 'run_streaming', lambda *a, **k: (1, ['one', 'checksum mismatch']))
    with pytest.raises(helixsr.HelixSRError, match='exit 1.*checksum mismatch'):
        helixsr.build(tmp_path)


@pytest.mark.parametrize('windows', [True, False])
def test_a_run_with_the_network_built_keeps_the_record_and_runs_helixsrs_own_setup(tmp_path, monkeypatch, windows):
    """HelixSR's own setup builds only what a folder is missing: with the network built it builds frame generation
    alone (-Yes/--yes are the whole command line; the script passes --fg-only on itself) and does not write
    helixsr_setup.json again. build() must keep that record (deleting it would report a failure after a good FG
    run), on Windows and Linux alike, and say in the log that frame generation is still missing."""
    commands = []

    def run(command, cwd, on_line=None, timeout=0, env=None):
        commands.append([str(part) for part in command])
        return 0, []

    monkeypatch.setattr(helixsr, 'run_streaming', run)
    (tmp_path / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.9.0\x00')
    (tmp_path / helixsr.BUILT).write_text('{}')   # the network was built here
    lines = []
    helixsr.build(tmp_path, on_line=lambda text, final=True: lines.append(text), windows=windows)
    assert (tmp_path / helixsr.BUILT).is_file()   # the FG-only run does not rewrite it: it must survive
    assert commands == [helixsr.setup_command(windows=windows)]
    assert any('frame generation is not built' in line for line in lines)   # one clear line, with the reason


@pytest.mark.parametrize('windows', [True, False])
def test_install_with_the_network_built_does_not_fetch_again(tmp_path, monkeypatch, windows):
    """A built folder whose frame generation is missing (the button's Update): the install runs the setup in place,
    which builds only the FG, and never downloads the zip again. Windows and Linux alike."""
    data = tmp_path / 'data'
    folder = helixsr.install_folder(data)
    (folder / 'setup').mkdir(parents=True)
    (folder / helixsr.SETUP).write_bytes(b'')
    (folder / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.9.0\x00')
    (folder / helixsr.BUILT).write_text('{}')
    commands = []

    def run(command, cwd, on_line=None, timeout=0, env=None):
        commands.append([str(part) for part in command])
        return 0, []

    monkeypatch.setattr(helixsr, 'run_streaming', run)
    monkeypatch.setattr(helixsr, 'fetch', lambda *a, **k: pytest.fail('a built folder must not be downloaded again'))
    if windows:
        monkeypatch.setattr(helixsr.host, 'is_windows', lambda *a, **k: True)
    from amdlss import scaler
    monkeypatch.setattr(scaler, 'fetch', lambda target, progress=None: None)
    assert helixsr.install_helixsr(data, consented=True) == folder
    assert commands == [helixsr.setup_command(windows=windows)]


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


def test_locate_falls_back_to_the_newest_older_folder_and_prefers_the_pinned_one(tmp_path):
    for name in ('HelixSR-1.8.9', 'HelixSR-1.8.10', 'HelixSR-1.10.0', 'HelixSR-1.9.0', 'HelixSR-2.0.0', 'HelixSR-nope',
                 'HelixSR-1.7.0'):
        (tmp_path / name).mkdir()
        (tmp_path / name / helixsr.DLL).write_bytes(b'x')
    (tmp_path / 'HelixSR-1.8.5').mkdir()   # no DLL: not a HelixSR folder
    assert helixsr.VERSION == '1.9.0' and helixsr.locate(tmp_path) == tmp_path / 'HelixSR-1.9.0'
    (tmp_path / 'HelixSR-1.9.0' / helixsr.DLL).unlink()
    assert helixsr.locate(tmp_path) == tmp_path / 'HelixSR-1.8.10'   # numeric order; the newer ones are above the pin
    assert helixsr.older('1.9.0', '1.10.0') and not helixsr.older('1.10.0', '1.9.0') and not helixsr.older('?', '1.9.0')


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
    assert games.match_game(listed, games.Game(name='x', folder=str(one) + os.sep)).name == 'One'


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


def test_install_and_remove_keep_the_games_logs_first(fake, tmp_path):
    client, one, _two = fake
    (one / 'OptiScaler.log').write_text('the run before the install\n', encoding='utf-8')
    data = tmp_path / 'data'
    keeping = helixsr.HelixSR(client.folder, python=sys.executable,
                              preserve=lambda game: logs.preserve_history(game, data))
    target = keeping.list_games()[0]
    keeping.install(target)
    kept = logs.history_snapshots(data, target.folder)
    assert len(kept) == 1 and (kept[0] / 'exe-folder' / 'OptiScaler.log').read_text() == \
        'the run before the install\n'
    (one / 'OptiScaler.log').write_text('the run before the remove\n', encoding='utf-8')
    keeping.remove(keeping.list_games()[0])
    kept = logs.history_snapshots(data, target.folder)
    assert len(kept) == 2
    assert (kept[-1] / 'exe-folder' / 'OptiScaler.log').read_text() == 'the run before the remove\n'


def test_a_failing_history_copy_never_stops_an_install(fake):
    client, _one, _two = fake

    def boom(game):
        raise RuntimeError('the disk is full')

    keeping = helixsr.HelixSR(client.folder, python=sys.executable, preserve=boom)
    assert keeping.install(keeping.list_games()[0]).state == 'direct'


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


def test_the_bridge_reports_which_games_can_use_either_route(fake):
    client, _one, _two = fake
    listed = client.list_games()
    assert [g.can_opti for g in listed] == [True, True, False]
    assert [g.route_choice for g in listed] == [True, True, False]
    assert listed[0].default_route == 'optiscaler'  # it lists DLSS


def test_the_default_route_stays_the_installers_when_the_game_has_no_dlss():
    game = games.Game(name='x', fsr31=True, can_opti=True, inputs=['XeSS'], route='direct')
    assert game.route_choice and game.default_route == 'direct'
    both = games.Game(name='x', fsr31=True, can_opti=True, inputs=['DLSS'], route='direct')
    assert both.default_route == 'optiscaler' and both.route_choice
    assert not games.Game(name='x', can_opti=True, inputs=['DLSS'], state='direct').route_choice  # installed


def run_recorder(monkeypatch):
    seen = []

    def record(command, cwd, on_line=None, timeout=0):
        seen.append([str(part) for part in command])
        return 1, ['stopped on purpose']

    monkeypatch.setattr(helixsr, 'run_streaming', record)
    return seen


def test_the_direct_route_runs_the_installers_own_install_n(fake, monkeypatch):
    client, _one, _two = fake
    game = client.list_games()[0]
    seen = run_recorder(monkeypatch)
    with pytest.raises(helixsr.HelixSRError):
        client.install(game)
    assert [command[3:5] for command in seen] == [['install', '1']]


def test_install_into_a_game_without_fsr31_uses_the_installers_own_refusal(fake):
    # HelixSR 1.9.0's installer refuses games without an FSR 3.1 DLL (HelixSR Scaler is the route there): the
    # error is the installer's own, in its own words
    client, _one, _two = fake
    three = client.list_games()[2]
    with pytest.raises(helixsr.HelixSRError, match='no FSR 3.1 DLL'):
        client.install(three)


def test_the_bridge_maps_the_new_installers_unsupported_route(fake):
    client, _one, _two = fake
    listed = client.list_games()
    assert [g.route for g in listed] == ['direct', 'direct', 'optiscaler']  # 'unsupported' -> the Scaler route
    assert [g.can_opti for g in listed] == [True, True, False]
    assert [g.state for g in listed] == ['none', 'none', 'none']


def test_the_bridge_reads_an_earlier_installs_record_and_version(fake):
    # an AMDLSS 1.0.0 install (OptiScaler 0.9.4 through the old bridge): the new installer reports it as an
    # install of an earlier version that can only be removed, and its own DLL names the version
    from fake_helixsr import old_install
    client, one, _two = fake
    old_install(one)
    listed = client.list_games()[0]
    assert listed.state == 'optiscaler' and listed.route == 'direct'   # the drop-in is what `install N` would do
    assert listed.helixsr == '1.9.0' and not listed.route_choice
    assert listed.installed and listed.default_route == 'optiscaler'
    # `uninstall N` removes it (HelixSR's own uninstall_optiscaler reads the same record)
    client.remove(listed)
    assert not (one / 'dxgi.dll').exists() and not (one / 'OptiScaler.ini').exists()
    assert not (one / 'HelixSR').exists()
    assert sorted(p.name for p in one.iterdir()) == ['amd_fidelityfx_upscaler_dx12.dll']


def test_the_bridge_reads_a_scaler_only_games_old_install(fake):
    # a game without FSR 3.1: an earlier install there shows as the Scaler route, which is also what Update does
    from fake_helixsr import old_install
    client, _one, _two = fake
    three = client.list_games()[2]
    Path(three.folder).mkdir()
    old_install(Path(three.folder))
    listed = client.list_games()[2]
    assert listed.state == 'optiscaler' and listed.route == 'optiscaler'
    assert listed.helixsr == '1.9.0' and listed.can_install and not listed.route_choice
    client.remove(listed)
    assert sorted(p.name for p in Path(three.folder).iterdir()) == []


def test_the_bridge_also_reports_games_helixsr_does_not_list_without_changing_its_numbering(tmp_path):
    plain, one = tmp_path / 'Plain', game_folder(tmp_path, 'One')
    plain.mkdir()
    folder = make(tmp_path, [{'name': 'Plain', 'folder': str(plain), 'fsr': False, 'inputs': []},
                             {'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']}])
    client = helixsr.HelixSR(folder, python=sys.executable)
    listed = client.list_games()
    assert [(g.name, g.index) for g in listed] == [('One', 1), ('Plain', 0)]  # `install 1` is still One
    assert listed[1].route == '' and not listed[1].inputs and not listed[1].fsr_targets
    assert client.install(listed[0]).state == 'direct'  # the numbering the installer sees is unchanged


def test_a_folder_added_by_hand_that_helixsr_drops_comes_back_as_an_unlisted_game(fake, tmp_path):
    client, _one, _two = fake
    dropped = tmp_path / 'PlainExtra'
    dropped.mkdir()
    listed = client.list_games([dropped])
    assert listed[-1].name == 'PlainExtra' and listed[-1].index == 0 and listed[-1].route == ''
    assert [g.index for g in listed[:-1]] == [1, 2, 3]
    assert [g.reason for g in games.classify(listed, [str(dropped)])][-1] == games.NO_UPSCALER


def test_a_game_helixsr_does_not_list_cannot_be_installed_from_here(tmp_path):
    plain = tmp_path / 'Plain'
    plain.mkdir()
    folder = make(tmp_path, [{'name': 'Plain', 'folder': str(plain), 'fsr': False, 'inputs': []}])
    client = helixsr.HelixSR(folder, python=sys.executable)
    (row,) = client.list_games()
    assert row.index == 0
    with pytest.raises(helixsr.HelixSRError, match='does not list Plain'):
        client.install(row)


def test_a_helixsr_without_the_scan_hooks_still_lists_its_games(fake, monkeypatch):
    client, _one, _two = fake
    installer = client.installer
    installer.write_text(installer.read_text(encoding='utf-8').replace('def games_from', 'def games_from_unused')
                         .replace('return out + games_from(extra, out)', 'return out'), encoding='utf-8')
    assert [g.name for g in client.list_games()] == ['One', 'Two', 'Three']


def test_the_bridge_reports_the_helixsr_version_a_game_runs(fake):
    client, one, _two = fake
    assert [g.helixsr for g in client.list_games()] == ['', '', '']   # nothing installed
    client.install(client.list_games()[0])
    assert client.list_games()[0].helixsr == '1.9.0'   # the direct install's record names it
    (one / 'helixsr-install.json').write_text('not json')
    (one / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'HELIX\x00HelixSR 1.8.1\x00')
    assert client.list_games()[0].helixsr == '1.8.1'   # no record: the DLL in the game is the fallback
    (one / 'HelixSR').mkdir(exist_ok=True)
    (one / 'HelixSR' / 'helixsr.dll').write_bytes(b'HELIX\x00HelixSR 1.8.9\x00')
    assert client.list_games()[0].helixsr == '1.8.1'   # the drop-in's own DLL answers for the direct route
    assert games.from_json([{'name': 'x'}])[0].helixsr == ''   # a bridge without the field: no hint


def test_install_and_remove_refuse_a_folder_that_covers_other_games(tmp_path):
    # 7B47A27X: HelixSR listed one game with the whole drive as its folder; its number must never run
    one = game_folder(tmp_path, 'One')
    folder = make(tmp_path, [{'name': '', 'folder': str(tmp_path), 'fsr': True, 'inputs': ['DLSS']},
                             {'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']}])
    client = helixsr.HelixSR(folder, python=sys.executable)
    wide = client.list_games()[0]
    assert wide.folder == str(tmp_path)
    for act in (client.install, client.remove):
        with pytest.raises(helixsr.HelixSRError, match='folder covers other games'):
            act(wide)
    assert (one / 'amd_fidelityfx_upscaler_dx12.dll').read_bytes() == b'GAME ORIGINAL'  # nothing ran


def test_a_drive_root_and_a_refused_row_never_reach_the_installer(fake):
    client, _one, _two = fake
    for game in (games.Game(name='Root', folder='E:\\'),
                 games.Game(name='Wide', folder='E:\\xcaja', reason=games.UNSAFE_FOLDER, state='optiscaler')):
        for act in (client.install, client.remove):
            with pytest.raises(helixsr.HelixSRError, match='folder covers other games'):
                act(game)
    root = 'E:\\' if os.name == 'nt' else '/'
    assert client.folder_flags([root, 'x']) == ['--folder', 'x'] and client.safe_extras([root]) == []
