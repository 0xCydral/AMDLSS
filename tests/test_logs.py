# SPDX-License-Identifier: GPL-2.0-or-later
"""Collect logs: what goes into the zip, that the user name never does, that missing files are fine, and that the
logging switch in OptiScaler.ini changes two values and is put back."""
import json
import os
import time
import zipfile
from pathlib import Path

import pytest

from amdlss import framegen, games, helixsr, logs

USER = 'Jordan Casey'
INFO = {'gpu': [{'Name': 'AMD Radeon RX 7800 XT', 'DriverVersion': '32.0.21001.9024', 'DriverDate': '2026-09-01'}],
        'cpu': 'AMD Ryzen 7 7800X3D', 'ram': 34359738368}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A game folder with logs in all three places HelixSR, OptiScaler and ReShade write to, under a fake user
    profile."""
    monkeypatch.setattr(logs, 'user_names', lambda: [USER])
    root = tmp_path / 'Users' / USER / 'Games' / 'Fake Game'
    exe, helix, fsr = root / 'bin', root / 'bin' / 'HelixSR', root / 'plugins'
    for folder in (exe, helix, fsr):
        folder.mkdir(parents=True)
    (exe / 'Fake.exe').write_bytes(b'MZ')
    (exe / 'dxgi.dll').write_bytes(b'MZ OptiScaler 0.9.4')
    (exe / 'nvngx_dlss.dll').write_bytes(b'MZ plain dlss')
    (exe / 'zlib.dll').write_bytes(b'MZ')
    (exe / 'OptiScaler.log').write_bytes(f'[Info] loaded from C:\\Users\\{USER}\\Games\\Fake Game\\bin\r\n'.encode())
    (exe / 'OptiScaler.ini').write_bytes(b'[Log]\r\nLogToFile=true\r\n')
    (exe / 'fakenvapi.ini').write_bytes(b'[fakenvapi]\r\n')
    (exe / 'ReShade.log').write_bytes(f'[1] | INFO | loaded from C:\\Users\\{USER}\\Games\\Fake Game\\bin\\ReShade64.dll\r\n'.encode())
    (exe / 'ReShade.ini').write_bytes(b'[ADDON]\r\nAddonPath=\r\n')
    (helix / 'helixsr.log').write_bytes(b'network ready\n')
    (helix / 'helixsr-install.json').write_text(json.dumps({'folder': str(root), 'route': 'optiscaler'}), 'utf-8')
    (helix / 'helixsr.dll').write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (fsr / 'helixsr.log').write_bytes(b'fsr drop-in log\n')
    (fsr / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (fsr / 'unrelated.txt').write_text('not collected')
    game = games.Game(name='Fake: Game?', folder=str(root), exe_folder=str(exe), fsr31=True, inputs=['DLSS'],
                      fsr_targets=[str(fsr / 'amd_fidelityfx_upscaler_dx12.dll')], state='optiscaler')
    setup = tmp_path / 'HelixSR-1.8.1'
    setup.mkdir()
    (setup / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (setup / helixsr.BUILT).write_text(f'{{"built_by": "C:\\\\Users\\\\{USER}\\\\AppData"}}', 'utf-8')
    return game, setup, tmp_path / 'data', tmp_path / 'out'


def collect(fake, **options):
    game, setup, data, out = fake
    return logs.collect(game, setup, data, out, INFO, windows=False, **options)


def entries(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name).decode('utf-8') for name in archive.namelist()}


def test_the_zip_holds_every_log_from_every_place(fake):
    path = collect(fake, now=time.struct_time((2026, 10, 11, 14, 5, 0, 0, 0, -1)))
    assert path.parent == fake[3] and path.name == 'AMDLSS-logs-Fake-Game-20261011-1405.zip'
    held = entries(path)
    assert sorted(held) == ['crashes.txt', 'exe-folder/HelixSR/helixsr-install.json',
                            'exe-folder/HelixSR/helixsr.log', 'exe-folder/OptiScaler.ini',
                            'exe-folder/OptiScaler.log', 'exe-folder/ReShade.ini',
                            'exe-folder/ReShade.log', 'exe-folder/fakenvapi.ini',
                            'files.txt', 'fsr-dll-folder/helixsr.log', 'system.txt']
    assert 'not collected on Linux' in held['crashes.txt'] and 'PROTON_LOG=1' in held['crashes.txt']
    assert held['fsr-dll-folder/helixsr.log'] == 'fsr drop-in log\n'
    assert held['exe-folder/OptiScaler.ini'] == '[Log]\r\nLogToFile=true\r\n'
    assert held['exe-folder/ReShade.ini'] == '[ADDON]\r\nAddonPath=\r\n'
    assert held['exe-folder/ReShade.log'].startswith('[1] | INFO | loaded from C:\\Users\\<user>\\Games\\Fake Game\\bin\\ReShade64.dll')
    assert not list(fake[3].glob('*.part'))


def test_helixsr_ini_is_collected_next_to_helixsrs_dlls_and_redacted(fake):
    game = fake[0]
    root = Path(game.folder)
    (root / 'bin' / 'HelixSR' / 'helixsr.ini').write_bytes(f'[Upscaling]\nInt8 = auto\n; C:\\Users\\{USER}\\x\n'.encode())
    (root / 'plugins' / 'helixsr.ini').write_bytes(b'[Sharpening]\n')
    held = entries(collect(fake))
    assert held['exe-folder/HelixSR/helixsr.ini'] == '[Upscaling]\nInt8 = auto\n; C:\\Users\\<user>\\x\n'
    assert held['fsr-dll-folder/helixsr.ini'] == '[Sharpening]\n'
    assert 'exe-folder/HelixSR/helixsr.ini' in logs.confirm_text(game, fake[2])


def test_files_txt_says_which_dll_is_whose(fake):
    text = entries(collect(fake))['files.txt']
    rows = {line.split()[0]: line for line in text.splitlines() if line.split() and line.split()[0].endswith('.dll')}
    assert rows['amd_fidelityfx_upscaler_dx12.dll'].endswith('HelixSR 1.8.1')
    assert rows['helixsr.dll'].endswith('HelixSR 1.8.1')
    assert rows['dxgi.dll'].endswith('OptiScaler')
    assert rows['nvngx_dlss.dll'].endswith('not HelixSR')
    assert 'zlib.dll' in text and 'Fake.exe' in text and '== fsr-dll-folder' in text and '== exe-folder/HelixSR' in text
    assert 'State: OptiScaler' in text and 'unrelated.txt' not in text


def test_system_txt(fake):
    text = entries(collect(fake))['system.txt']
    assert 'AMDLSS ' in text and 'HelixSR 1.8.1' in text and ('Windows:' if os.name == 'nt' else 'Linux:') in text
    assert 'GPU 1: AMD Radeon RX 7800 XT, driver 32.0.21001.9024, driver date 2026-09-01' in text
    assert 'CPU: AMD Ryzen 7 7800X3D' in text and 'RAM: 32.0 GB' in text and '--- helixsr_setup.json ---' in text


def test_the_user_name_is_replaced_everywhere(fake):
    held = entries(collect(fake))
    for name, text in held.items():
        assert USER not in text and USER.lower() not in text.lower(), name
    assert 'C:\\Users\\<user>\\Games\\Fake Game\\bin' in held['exe-folder/OptiScaler.log']
    assert '<user>' in held['exe-folder/HelixSR/helixsr-install.json'] and '<user>' in held['files.txt']
    assert '<user>' in held['system.txt']   # the path inside helixsr_setup.json, JSON-escaped backslashes included


def test_redaction_rules():
    redact = logs.redactor(['Winter', 'WinterOS'])
    assert redact(r'C:\Users\WinterOS\x and C:/Users/winter/y') == r'C:\Users\<user>\x and C:/Users/<user>/y'
    assert redact('user WinterOS logged in; Winterfell stays') == 'user <user> logged in; Winterfell stays'
    assert redact(r'C:\\Users\\WinterOS\\a') == r'C:\\Users\\<user>\\a'   # as inside a JSON string
    short = logs.redactor(['Al'])
    assert short('Albert, Al, C:\\Users\\Al\\x') == 'Albert, Al, C:\\Users\\<user>\\x'   # a short name only after Users
    assert logs.redactor([])('anything') == 'anything'
    latin = 'caf\xe9 C:\\Users\\Jo\\n'.encode('latin-1')   # not UTF-8: every other byte survives
    assert logs.redact_bytes(latin, logs.redactor(['Jo'])) == 'caf\xe9 C:\\Users\\<user>\\n'.encode('latin-1')
    wide = '\ufeffC:\\Users\\Jo\\n'.encode('utf-16-le')
    assert logs.redact_bytes(wide, logs.redactor(['Jo'])).decode('utf-16') == 'C:\\Users\\<user>\\n'


def test_missing_files_and_folders_are_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, 'user_names', lambda: [])
    nothing = games.Game(name='Gone', folder=str(tmp_path / 'gone'), exe_folder=str(tmp_path / 'gone' / 'bin'),
                         fsr_targets=[str(tmp_path / 'gone' / 'x' / 'amd_fidelityfx_dx12.dll')])
    held = entries(logs.collect(nothing, None, tmp_path / 'data', tmp_path / 'out', {}, windows=False))
    assert sorted(held) == ['crashes.txt', 'files.txt', 'system.txt']
    assert 'GPU: could not be read' in held['system.txt'] and 'not found' in held['system.txt']
    empty = games.Game(name='Empty', folder=str(tmp_path), exe_folder='')
    assert sorted(entries(logs.collect(empty, tmp_path / 'nope', tmp_path / 'data', tmp_path / 'out', {},
                                       windows=False))) == ['crashes.txt', 'files.txt', 'system.txt']
    assert 'no log file exists' in logs.confirm_text(nothing, tmp_path / 'data')


def test_a_second_zip_in_the_same_minute_does_not_overwrite(fake):
    now = time.localtime()
    assert collect(fake, now=now) != collect(fake, now=now)
    assert len(list(fake[3].glob('*.zip'))) == 2


def test_a_huge_log_goes_in_as_its_tail(fake, monkeypatch):
    monkeypatch.setattr(logs, 'MAX_BYTES', 20)
    (Path(fake[0].exe_folder) / 'OptiScaler.log').write_bytes(b'x' * 50 + b'END')
    text = entries(collect(fake))['exe-folder/OptiScaler.log']
    assert text.startswith('[AMDLSS: the first 33 bytes') and text.endswith('x' * 17 + 'END')


def test_the_install_log_of_the_game_is_included_and_listed(fake):
    game, _setup, data, _out = fake
    assert 'amdlss-install.log' not in logs.contents(game, data)
    record = logs.GameLog(data, game.folder, lambda text, final=True: None)
    record.start('install Fake')
    record('installed in C:\\Users\\Jordan Casey\\Games')
    record('progress', final=False)
    record.fail('boom')
    assert 'amdlss-install.log' in logs.contents(game, data) and 'amdlss-install.log' in logs.confirm_text(game, data)
    text = entries(collect(fake))['amdlss-install.log']
    assert 'install Fake ===' in text and 'C:\\Users\\<user>\\Games' in text and 'error: boom' in text
    assert 'progress' not in text


def test_the_setup_output_is_kept_and_rides_in_the_zip_and_the_list(fake):
    """What HelixSR's own setup printed (the build and frame generation lines) is kept in the data folder, so a
    frame generation failure is readable after the window's log box is gone -- and it is collected."""
    game, _setup, data, _out = fake
    assert helixsr.SETUP_LOG not in logs.contents(game, data)
    log = helixsr.SetupLog(data)
    log.start("HelixSR's setup: helixsr-setup.sh --yes (in /tmp/HelixSR-1.9.0)")
    log('[helixsr-setup] downloading dxc')
    log('[helixsr-fg] 50% translate', final=False)   # a progress update is not kept
    log("[helixsr-fg] failed: dxc not found")
    assert helixsr.SETUP_LOG in logs.contents(game, data) and helixsr.SETUP_LOG in logs.confirm_text(game, data)
    text = entries(collect(fake))[helixsr.SETUP_LOG]
    assert 'helixsr-setup.sh --yes' in text and 'failed: dxc not found' in text and '50%' not in text


def at(hour, minute=0):
    return time.struct_time((2026, 10, 13, hour, minute, 0, 0, 0, -1))


def test_kept_history_survives_the_logs_being_removed_and_rides_in_the_zip(fake):
    game, setup, data, out = fake
    snapshot = logs.preserve_history(game, data, now=at(14, 5))
    assert snapshot is not None and snapshot.name == '20261013-140500'
    Path(game.exe_folder, 'OptiScaler.log').unlink()   # the remove that used to delete the evidence
    held = entries(collect(fake))
    kept = held['history/20261013-140500/exe-folder/OptiScaler.log']
    assert 'C:\\Users\\<user>\\Games\\Fake Game\\bin' in kept and USER not in kept
    assert 'history/20261013-140500/exe-folder/OptiScaler.log' in logs.contents(game, data)
    text = logs.confirm_text(game, data)
    assert 'history/: the logs AMDLSS kept before 1 earlier install' in text and 'crashes.txt' in text


def test_history_is_capped_per_file_and_the_oldest_runs_go_first(fake, monkeypatch):
    game, _setup, data, _out = fake
    monkeypatch.setattr(logs, 'HISTORY_KEEP_RUNS', 2)
    monkeypatch.setattr(logs, 'HISTORY_MAX_FILE_BYTES', 20)
    (Path(game.exe_folder) / 'OptiScaler.log').write_bytes(b'x' * 50 + b'END')
    for hour in (10, 11, 12):
        assert logs.preserve_history(game, data, now=at(hour)) is not None
    kept = logs.history_snapshots(data, game.folder)
    assert [folder.name for folder in kept] == ['20261013-110000', '20261013-120000']
    text = (kept[-1] / 'exe-folder' / 'OptiScaler.log').read_bytes().decode()
    assert text.startswith('[AMDLSS: the first 33 bytes') and text.endswith('x' * 17 + 'END')
    names = logs.history_names(game, data, runs=1)
    assert names and all(name.startswith('history/20261013-120000/') for name in names)


def test_preserving_a_game_without_logs_keeps_nothing(tmp_path):
    game = games.Game(name='Empty', folder=str(tmp_path / 'gone'), exe_folder=str(tmp_path / 'gone'))
    assert logs.preserve_history(game, tmp_path / 'data') is None
    assert logs.history_snapshots(tmp_path / 'data', game.folder) == []
    assert logs.history_snapshots(tmp_path / 'data', '') == []


def test_the_proton_log_rides_in_the_zip_redacted(fake, monkeypatch, tmp_path):
    game = fake[0]
    proton = tmp_path / 'steam-1234.log'
    proton.write_bytes(f'proton for C:\\Users\\{USER}\\game\n'.encode())
    monkeypatch.setattr(logs.crashes, 'proton_log_path', lambda found, home=None: proton)
    held = entries(collect(fake))
    assert held['proton.log'] == f'proton for C:\\Users\\<user>\\game\n'
    assert 'proton.log' in logs.contents(game, fake[2], windows=False)


def test_the_confirm_text_lists_what_goes_in_the_zip(fake):
    text = logs.confirm_text(fake[0], fake[2])
    for name in ('exe-folder/OptiScaler.log', 'exe-folder/HelixSR/helixsr.log', 'fsr-dll-folder/helixsr.log',
                 'files.txt', 'system.txt'):
        assert name in text
    assert '<user>' in text and 'Desktop' in text and text.endswith('Collect logs?')


# --- the logging switch in OptiScaler.ini -------------------------------------------------------------------------

INI = (b'; header\r\n[Upscalers]\r\nLogLevel=decoy\r\n\r\n[Log]\r\n; Should a log file be created\r\nLogToFile=auto\r\n'
       b'\r\nLogLevel = auto\r\nLogToConsole=auto\r\n\r\n[FrameGen]\r\nFGInput=auto\r\nFGOutput=auto\r\n')


@pytest.fixture
def exe(tmp_path):
    folder = tmp_path / 'game'
    folder.mkdir()
    (folder / 'OptiScaler.ini').write_bytes(INI)
    return folder


def test_logging_changes_two_values_and_nothing_else(exe, tmp_path):
    assert framegen.apply(exe, True, tmp_path, framegen.LOGGING)
    expected = INI.replace(b'LogToFile=auto', b'LogToFile=true').replace(b'LogLevel = auto', b'LogLevel = 2')
    assert (exe / 'OptiScaler.ini').read_bytes() == expected   # the decoy LogLevel in [Upscalers] and every CRLF stay
    assert framegen.is_on(exe, framegen.LOGGING) and not framegen.is_on(exe)
    assert not framegen.apply(exe, True, tmp_path, framegen.LOGGING)   # already on: nothing to do


def test_logging_and_frame_generation_keep_separate_records(exe, tmp_path):
    framegen.apply(exe, True, tmp_path)
    framegen.apply(exe, True, tmp_path, framegen.LOGGING)
    assert len(json.loads((tmp_path / framegen.RECORD).read_text())) == 2
    assert framegen.apply(exe, False, tmp_path)   # frame generation off leaves logging on
    assert framegen.is_on(exe, framegen.LOGGING) and not framegen.is_on(exe)
    assert framegen.apply(exe, False, tmp_path, framegen.LOGGING) and (exe / 'OptiScaler.ini').read_bytes() == INI
    assert not (tmp_path / framegen.RECORD).exists()


def test_remove_leaves_no_trace(exe, tmp_path):
    (exe / 'OptiScaler.log').write_text('made by our switch')
    framegen.apply(exe, True, tmp_path, framegen.LOGGING)
    framegen.forget(exe, tmp_path)
    assert not (tmp_path / framegen.RECORD).exists() and not (exe / 'OptiScaler.log').exists()
    framegen.forget(exe, tmp_path)   # twice, and with no log: fine


def test_a_log_the_switch_did_not_make_is_not_deleted(exe, tmp_path):
    (exe / 'OptiScaler.ini').write_bytes(INI.replace(b'LogToFile=auto', b'LogToFile=true').replace(
        b'LogLevel = auto', b'LogLevel = 2'))   # the user's own ini already logs: no record, so the log is theirs
    (exe / 'OptiScaler.log').write_text('theirs')
    assert not framegen.apply(exe, True, tmp_path, framegen.LOGGING)
    framegen.forget(exe, tmp_path)
    assert (exe / 'OptiScaler.log').read_text() == 'theirs'


def test_an_ini_without_a_log_section_is_an_error_not_a_guess(exe, tmp_path):
    (exe / 'OptiScaler.ini').write_bytes(b'[FrameGen]\r\nFGInput=auto\r\nFGOutput=auto\r\n')
    with pytest.raises(helixsr.HelixSRError, match=r'LogToFile line in \[Log\]'):
        framegen.apply(exe, True, tmp_path, framegen.LOGGING)
    assert not framegen.is_on(exe, framegen.LOGGING)


def test_the_after_install_text_points_to_collect_logs():
    for state in ('optiscaler', 'direct'):
        text = games.after_text(games.Game(name='G', state=state, route=state))
        assert 'If it looks unchanged in game, press Collect logs and send the zip.' in text


def test_system_info_reads_powershells_base64_json_and_survives_a_failure(monkeypatch):
    import base64
    import subprocess
    from types import SimpleNamespace
    payload = json.dumps({'gpu': {'Name': 'Adaptador básico', 'DriverVersion': '1.0', 'DriverDate': '2006-06-20'},
                          'cpu': 'AMD Ryzen   ', 'ram': 8 * 2**30})
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=base64.b64encode(payload.encode()) + b'\r\n'))
    info = logs.system_info(windows=True)
    text = logs.system_text(None, info, windows=True)   # a single GPU arrives as an object, not a list
    assert 'GPU 1: Adaptador básico, driver 1.0, driver date 2006-06-20' in text and 'CPU: AMD Ryzen\n' in text
    assert 'RAM: 8.0 GB' in text and 'HelixSR folder: unknown' in text and 'Windows:' in text
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'not base64 at all!'))
    assert logs.system_info(windows=True) == {}

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('powershell', 40)
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert logs.system_info(windows=True) == {}


def test_another_games_fsr_folder_is_never_collected(tmp_path):
    # 7B47A27X: folder E:\ listed the FSR folders of seven other games in files.txt
    win64 = tmp_path / 'Valor' / 'Binaries' / 'Win64'
    (win64 / 'HelixSR').mkdir(parents=True)
    (win64 / 'Valor-Win64-Shipping.exe').write_bytes(b'MZ')
    (win64 / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'valor')
    (win64 / 'HelixSR' / 'helixsr.log').write_bytes(b'valor log\n')
    other = tmp_path / 'OtherGame' / 'Binaries'
    other.mkdir(parents=True)
    (other / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'other')
    (other / 'OptiScaler.log').write_bytes(b'other log\n')
    root = 'E:\\' if os.name == 'nt' else '/'
    for folder in (root, str(tmp_path / 'Valor')):   # a drive root, and a normal folder with a foreign target
        game = games.Game(name='Valor', folder=folder, exe_folder=str(win64), fsr31=True,
                          fsr_targets=[str(win64 / 'amd_fidelityfx_upscaler_dx12.dll'),
                                       str(other / 'amd_fidelityfx_upscaler_dx12.dll')])
        # the game's own FSR folder is its exe folder (listed once); the other game's never appears
        assert [label for label, _ in logs.places(game)] == ['exe-folder', 'exe-folder/HelixSR']
        assert [name for name, _ in logs.gather(game)] == ['exe-folder/HelixSR/helixsr.log']
        assert 'OtherGame' not in logs.files_text(game)
