# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows crash evidence: Event ID 1000/1001 matched to the game's exes through the injectable PowerShell seam,
minidumps listed and never read, and the Proton log on Linux. No PowerShell runs here."""
import base64
import json
import os
import time
from pathlib import Path

from amdlss import crashes, games, logs

USER = 'Jordan Casey'
EVENT_1000 = {'time': '2026-10-13 14:20:11', 'id': 1000, 'provider': 'Application Error',
              'message': 'Faulting application name: One.exe',
              'props': ['One.exe', '1.0.0.0', '0x65000000', 'dxgi.dll', '0.9.4.0', '0x66000000',
                        '0xc0000005', '0x0000000000123456', '0x1234', '0x01dc0000',
                        r'E:\Games\One\One.exe', r'E:\Games\One\dxgi.dll', 'report-id']}
EVENT_OTHER = {'time': '2026-10-13 09:00:00', 'id': 1000, 'provider': 'Application Error', 'message': '',
               'props': ['Other.exe', '2.0', '0x1', 'kernelbase.dll', '10.0', '0x2', '0xe06d7363', '0x99',
                         '0x1', '0x1', r'C:\Other\Other.exe', r'C:\Windows\System32\kernelbase.dll']}
EVENT_1001_ES = {'time': '2026-10-12 20:00:00', 'id': 1001, 'provider': 'Windows Error Reporting',
                 'message': ('Nombre de la aplicación con errores: One.exe, versión: 1.0\n'
                             'Nombre del módulo con errores: amd_fidelityfx_dx12.dll, versión: 1.9.0.0\n'
                             'Código de excepción: 0xc0000409\nDesplazamiento de errores: 0x000000000000abcd\n'),
                 'props': []}


def serve(*events):
    """A run(command) that answers like the PowerShell query: base64 of the events as JSON."""
    payload = base64.b64encode(json.dumps(list(events)).encode()).decode()
    return lambda command: payload + '\r\n'


def make_game(tmp_path, name='One'):
    root = tmp_path / 'Games' / name
    (root / 'bin').mkdir(parents=True)
    (root / 'One.exe').write_bytes(b'MZ')
    (root / 'bin' / 'Helper.exe').write_bytes(b'MZ')
    (root / 'readme.txt').write_text('x')
    return games.Game(name=name, folder=str(root), exe_folder=str(root))


def test_the_games_exes_are_found_in_the_folder_tree(tmp_path):
    game = make_game(tmp_path)
    assert crashes.game_exes(game) == ['One.exe', 'Helper.exe'] or \
        sorted(crashes.game_exes(game)) == ['Helper.exe', 'One.exe']
    gone = games.Game(name='Gone', folder=str(tmp_path / 'gone'), exe_folder=str(tmp_path / 'gone'))
    assert crashes.game_exes(gone) == []


def test_the_powershell_query_is_a_bounded_filter_hashtable_for_both_event_ids():
    command = crashes.event_command()
    assert command[:4] == ['powershell', '-NoProfile', '-NonInteractive', '-Command']
    script = command[4]
    for needle in ('Get-WinEvent -FilterHashtable', "LogName='Application'", 'Id=@(1000,1001,1002)',
                   'AddDays(-14)', '-MaxEvents 500', 'ToBase64String'):
        assert needle in script, needle
    assert 'AddDays(-7)' in crashes.event_command(7)[4]


def test_event_1000_replacement_strings_give_the_faulting_module():
    entry = crashes.parse_event(EVENT_1000, ['One.exe'])
    assert entry == {'when': '2026-10-13 14:20:11', 'exe': 'One.exe', 'module': 'dxgi.dll',
                     'version': '0.9.4.0', 'exception': '0xc0000005', 'offset': '0x0000000000123456',
                     'event': '1000', 'path': r'E:\Games\One\dxgi.dll'}
    assert crashes.parse_event(EVENT_OTHER, ['One.exe']) is None
    spanish = crashes.parse_event(EVENT_1001_ES, ['One.exe'])
    assert spanish['module'] == 'amd_fidelityfx_dx12.dll' and spanish['exception'] == '0xc0000409'
    assert spanish['event'] == '1001'


def test_crashes_come_through_the_runner_seam_and_failures_are_one_line(tmp_path):
    game = make_game(tmp_path)
    entries, why = crashes.crash_entries(['One.exe'], serve(EVENT_OTHER, EVENT_1000, EVENT_1001_ES))
    assert why is None and [entry['when'] for entry in entries] == ['2026-10-12 20:00:00', '2026-10-13 14:20:11']
    text = crashes.crashes_text(game, windows=True, run=serve(EVENT_1000),
                                dumps_dir=tmp_path / 'none', wer_folders=[tmp_path / 'none'])
    assert text.startswith('Windows crashes for One (Application log, last 14 days, Event ID 1000, 1001 and 1002)\n')
    assert ('2026-10-13 14:20:11 | One.exe | faulting module: dxgi.dll | module version: 0.9.4.0 | '
            'exception: 0xc0000005 | offset: 0x0000000000123456 | Event ID: 1000 | '
            'module path: E:\\Games\\One\\dxgi.dll') in text
    empty = crashes.crashes_text(game, windows=True, run=serve(),
                                 dumps_dir=tmp_path / 'none', wer_folders=[])
    assert "No matching crash was recorded for this game's executables." in empty

    def boom(command):
        raise OSError('powershell is not here')

    failed = crashes.crashes_text(game, windows=True, run=boom, dumps_dir=tmp_path / 'none', wer_folders=[])
    assert 'The Windows event log could not be read: powershell is not here. Collection continued' in failed
    garbage = crashes.crashes_text(game, windows=True, run=lambda command: 'not base64 json',
                                   dumps_dir=tmp_path / 'none', wer_folders=[])
    assert 'could not be read' in garbage


def test_minidumps_and_wer_folders_are_listed_never_read(tmp_path):
    game = make_game(tmp_path)
    dumps = tmp_path / 'CrashDumps'
    dumps.mkdir()
    (dumps / 'One.exe.1234.dmp').write_bytes(b'DUMPBYTES')
    (dumps / 'Other.exe.9.dmp').write_bytes(b'x')
    wer = tmp_path / 'ReportArchive'
    (wer / 'AppCrash_One.exe_abc').mkdir(parents=True)
    (wer / 'AppCrash_Other.exe_def').mkdir()
    text = crashes.crashes_text(game, windows=True, run=serve(), dumps_dir=dumps, wer_folders=[wer])
    assert 'One.exe.1234.dmp  9 bytes' in text and 'AppCrash_One.exe_abc' in text
    assert 'Other.exe' not in text.split('Crash dumps listed only')[1] and 'DUMPBYTES' not in text
    name, data = crashes.crashes_evidence(game, logs.redactor([USER]), windows=True, run=serve(),
                                          dumps_dir=dumps, wer_folders=[wer])
    assert name == 'crashes.txt' and b'One.exe.1234.dmp' in data and b'DUMPBYTES' not in data


def test_the_user_name_is_redacted_from_crash_paths(tmp_path):
    home_game = make_game(tmp_path / 'Users' / USER)
    event = dict(EVENT_1000, props=list(EVENT_1000['props']))
    event['props'][11] = f'C:\\Users\\{USER}\\Games\\One\\dxgi.dll'
    name, data = crashes.crashes_evidence(home_game, logs.redactor([USER]), windows=True, run=serve(event),
                                          dumps_dir=tmp_path / 'none', wer_folders=[])
    text = data.decode()
    assert USER not in text and 'C:\\Users\\<user>\\Games\\One\\dxgi.dll' in text


def test_on_linux_the_proton_log_is_the_crash_evidence(tmp_path):
    library = tmp_path / 'Steam' / 'steamapps'
    game_root = library / 'common' / 'OneGame'
    game_root.mkdir(parents=True)
    (library / 'appmanifest_1234.acf').write_text('"appid" "1234"\n"installdir" "OneGame"\n"name" "One"\n')
    game = games.Game(name='One', folder=str(game_root), exe_folder=str(game_root))
    assert crashes.steam_appid(game) == 1234
    text = crashes.crashes_text(game, windows=False, home=tmp_path)
    assert 'not collected on Linux' in text and 'no Proton log was found at ~/steam-1234.log' in text
    assert 'PROTON_LOG=1' in text
    (tmp_path / 'steam-1234.log').write_text('proton started\n')
    assert crashes.proton_log_path(game, tmp_path) == tmp_path / 'steam-1234.log'
    text = crashes.crashes_text(game, windows=False, home=tmp_path)
    assert 'the Proton log for Steam app 1234 is included as proton.log' in text
    unknown = games.Game(name='Two', folder=str(tmp_path / 'elsewhere'), exe_folder='')
    assert 'could not tell this game' in crashes.crashes_text(unknown, windows=False, home=tmp_path)


def test_a_game_without_exes_skips_the_query(tmp_path):
    called = []
    game = games.Game(name='Empty', folder=str(tmp_path), exe_folder=str(tmp_path))
    text = crashes.crashes_text(game, windows=True, run=lambda command: called.append(command) or '',
                                dumps_dir=tmp_path, wer_folders=[])
    assert 'No .exe file was found in the game folder, so the event log was not searched.' in text
    # the event query never ran (the running-game probe may); collection still produced the file
    assert not [command for command in called if 'Get-WinEvent' in command[4]] and os.name


def wer(when, name, *signature):
    """An Event 1001 as Get-WinEvent hands it over, in a Spanish Windows: the text is localized and says nothing the
    replacement strings do not (fault bucket, bucket type, event name, response, cab id, P1..P10, then the rest)."""
    p = list(signature) + [''] * (10 - len(signature))
    return {'time': when, 'id': 1001, 'provider': 'Windows Error Reporting',
            'message': 'Cubo de errores 1, tipo 4\nNombre del evento: ' + name + '\nP4:\nP5: 1.0\n',
            'props': ['1', '4', name, 'No disponible', '0'] + p + [r'\\?\C:\WER\a.tmp', 'store', '', '0', 'id']}


APPCRASH = wer('2026-10-12 11:50:29', 'APPCRASH', 'One.exe', '1.0.0.0', '65000000', 'atiadlxx.dll_unloaded',
               '32.0.21045.5002', '66000000', 'c0000005', '0000000000068bc5', '', '')
BEX64 = wer('2026-10-12 12:00:00', 'BEX64', 'One.exe', '1.0.0.0', '65000000', 'ClassicExplorer64.dll', '4.2.0.0',
            '548d2d73', '0000000000032f8e', 'c0000409', '0000000000000000')
HANG = wer('2026-10-12 13:00:00', 'AppHangB1', 'One.exe', '1.0.0.0', '65000000', 'ab12cd', '0', '10.0.26100.1')


def test_event_1001_replacement_strings_give_the_module_in_any_windows_language():
    crash = crashes.parse_event(APPCRASH, ['One.exe'])
    assert (crash['exe'], crash['module'], crash['version'], crash['exception'], crash['offset'], crash['event'],
            crash['name']) == ('One.exe', 'atiadlxx.dll_unloaded', '32.0.21045.5002', 'c0000005',
                               '0000000000068bc5', '1001', 'APPCRASH')
    swapped = crashes.parse_event(BEX64, ['One.exe'])   # BEX64 puts the offset in P7 and the exception code in P8
    assert (swapped['module'], swapped['exception'], swapped['offset']) == ('ClassicExplorer64.dll', 'c0000409',
                                                                         '0000000000032f8e')
    stopped = crashes.parse_event(HANG, ['One.exe'])   # a hang names no module
    assert stopped['name'] == 'AppHangB1' and stopped['exception'] == '' and stopped['module'] == 'unknown'
    assert 'event name: AppHangB1' in crashes.format_entry(stopped)
    assert crashes.parse_event(wer('2026-10-12 13:00:00', 'APPCRASH', 'Other.exe'), ['One.exe']) is None


def test_event_1001_with_missing_fields_still_parses_and_never_reads_the_p_labels_from_the_text():
    short = dict(APPCRASH, props=APPCRASH['props'][:8])   # cut after P3
    entry = crashes.parse_event(short, ['One.exe'])
    assert entry['exe'] == 'One.exe' and entry['module'] == 'unknown' and entry['exception'] == ''
    # no replacement strings at all, only the text
    textonly = crashes.parse_event(dict(APPCRASH, props=[], message='P1: One.exe\nP4:\nP5: 1.0\n'), ['One.exe'])
    assert textonly['exe'] == 'One.exe' and textonly['module'] == 'unknown'   # the text's "P4:" is not a module


def test_a_1001_that_repeats_a_1000_is_shown_once_but_a_lone_1001_and_a_hang_stay():
    first = dict(EVENT_1000, time='2026-10-12 11:50:27')
    twin = dict(APPCRASH, time='2026-10-12 11:50:29')      # two seconds after, as Windows writes it
    later = dict(APPCRASH, time='2026-10-12 12:30:00')     # no 1000 beside it
    entries = crashes.entries_from([twin, later, HANG, first], ['One.exe'])
    assert [(entry['when'][11:], entry['event']) for entry in entries] == [
        ('11:50:27', '1000'), ('12:30:00', '1001'), ('13:00:00', '1001')]
    other = dict(first, time='2026-10-12 11:50:28', props=['Other.exe'] + first['props'][1:])
    assert len(crashes.entries_from([other, twin], ['One.exe', 'Other.exe'])) == 2   # a different exe is not a twin


def test_crashes_txt_carries_the_event_name_and_the_reader_reads_it_back(tmp_path):
    game = make_game(tmp_path)
    text = crashes.crashes_text(game, windows=True, run=serve(APPCRASH, HANG), dumps_dir=tmp_path / 'none',
                                wer_folders=[])
    assert ('2026-10-12 11:50:29 | One.exe | faulting module: atiadlxx.dll_unloaded | module version: '
            '32.0.21045.5002 | exception: c0000005 | offset: 0000000000068bc5 | Event ID: 1001 | module path:  | '
            'event name: APPCRASH') in text


# --- Event 1002 (Application Hang), whether the game was running, and Unreal Engine games --------------------------

# Property order of a real Windows 7 record (BleepingComputer thread 533442): exe, version, process id (hex), start time
# (hex FILETIME), termination time, application path, report id. Windows 10/11 name AppName, AppVersion, ExeFileName
# (the path) and HangType instead (Elevenforum thread 43337).
HANG_1002 = {'time': '2026-10-13 17:04:02', 'id': 1002, 'provider': 'Application Hang', 'message': '',
             'props': ['One.exe', '1.0.0.0', '1044', '01cf68bcc5c44fc8', '20', r'E:\Games\One\One.exe',
                       '86a52d05-d4ba-11e3-853e-e03f4984a912']}
HANG_1002_NEW = {'time': '2026-10-13 17:05:00', 'id': 1002, 'provider': 'Application Hang', 'message': '',
                 'props': ['One.exe', '1.0.0.0', r'E:\Games\One\One.exe', 'Cross-thread;Cross-process']}


def test_event_1002_is_an_application_hang_in_both_property_layouts_and_in_any_language():
    for raw in (HANG_1002, HANG_1002_NEW):
        entry = crashes.parse_event(raw, ['One.exe'])
        assert (entry['exe'], entry['event'], entry['name'], entry['module']) == ('One.exe', '1002', 'AppHang',
                                                                               'unknown')
        line = crashes.format_entry(entry)
        assert 'Event ID: 1002' in line and line.endswith('event name: AppHang')
    # the text only (a localized message, no replacement strings): the exe is found by its name
    spanish = dict(HANG_1002, props=[], message='El programa One.exe versión 1.0.0.0 dejó de interactuar con Windows')
    assert crashes.parse_event(spanish, ['One.exe'])['name'] == 'AppHang'
    assert crashes.parse_event(dict(HANG_1002, props=['Other.exe', '1.0']), ['One.exe']) is None
    assert crashes.parse_event(dict(HANG_1002, id=1003), ['One.exe']) is None


def test_a_hang_reaches_crashes_txt_and_the_query_asks_for_1002(tmp_path):
    game = make_game(tmp_path)
    text = crashes.crashes_text(game, windows=True, run=serve(HANG_1002), dumps_dir=tmp_path / 'none',
                                wer_folders=[], processes=[])
    assert '2026-10-13 17:04:02 | One.exe | faulting module: unknown' in text and 'event name: AppHang' in text
    assert 'Event ID 1000, 1001 and 1002' in text


def test_running_when_collected_uses_the_install_check_but_not_unreal_crash_reporter(tmp_path):
    game = games.Game(name='One', folder=r'E:\Games\One', exe_folder=r'E:\Games\One\Binaries')
    asked = crashes.running_text
    assert asked(game, True, processes=[r'C:\Windows\explorer.exe', r'E:\Games\One\Binaries\One.exe']) == 'yes'
    assert asked(game, True, processes=[r'C:\Windows\explorer.exe', r'E:\Games\Two\Two.exe']) == 'no'
    assert asked(game, True, processes=[r'E:\Games\One\Engine\Binaries\CrashReportClient.exe']) == 'no'
    assert asked(game, True, processes=[]) == 'unknown'          # PowerShell failed: not "not running"
    assert asked(games.Game(name='Nowhere'), True, processes=[r'E:\x.exe']) == 'unknown'
    assert asked(game, True, run=lambda command: r'E:\Games\One\One.exe') == 'yes'   # asks PowerShell when not shared

    def broken(command):
        raise OSError('no powershell')

    assert asked(game, True, run=broken) == 'unknown'
    proc = tmp_path / 'proc'
    (proc / '42').mkdir(parents=True)
    (proc / '42' / 'cmdline').write_bytes(b'wine\x00/games/One/One.exe\x00')
    linux = games.Game(name='One', folder='/games/One', exe_folder='/games/One')
    assert asked(linux, False, proc=proc) == 'yes'
    assert asked(games.Game(name='Two', folder='/games/Two'), False, proc=proc) == 'no'


def test_crashes_txt_says_whether_the_game_was_running(tmp_path):
    game = make_game(tmp_path)
    for running, word in (([str(Path(game.folder) / 'One.exe')], 'yes'), (['C:/Windows/explorer.exe'], 'no')):
        text = crashes.crashes_text(game, windows=True, run=serve(), dumps_dir=tmp_path / 'none', wer_folders=[],
                                    processes=running)
        assert f'Running when collected: {word}\n' in text
    linux = crashes.crashes_text(game, windows=False, home=tmp_path, proc=tmp_path / 'noproc')
    assert 'Running when collected: no' in linux


def unreal_game(tmp_path, project='Proj'):
    root = tmp_path / 'Games' / 'UE'
    exe_folder = root / project / 'Binaries' / 'Win64'
    exe_folder.mkdir(parents=True)
    (exe_folder / f'{project}-Win64-Shipping.exe').write_bytes(b'MZ')
    return games.Game(name='UE', folder=str(root), exe_folder=str(exe_folder))


def age(path, days):
    when = time.time() - days * 86400
    os.utime(path, (when, when))


def test_the_unreal_project_is_the_folder_above_binaries():
    ue = games.Game(name='UE', folder=r'E:\SteamLibrary\steamapps\common\Ready Or Not',
                    exe_folder=r'E:\SteamLibrary\steamapps\common\Ready Or Not\ReadyOrNot\Binaries\Win64')
    assert crashes.unreal_project(ue, True) == ('ReadyOrNot', r'E:\SteamLibrary\steamapps\common\Ready Or Not\ReadyOrNot')
    proton = games.Game(name='UE', folder='/games/UE', exe_folder='/games/UE/Proj/Binaries/Win64/')
    assert crashes.unreal_project(proton, False) == ('Proj', '/games/UE/Proj')
    assert crashes.unreal_project(games.Game(name='x', folder=r'E:\G\Engine\Binaries\Win64'), True) == ('', '')
    assert crashes.unreal_project(games.Game(name='x', folder=r'E:\G\bin', exe_folder=r'E:\G\bin'), True) == ('', '')
    assert crashes.unreal_project(games.Game(name='x'), True) == ('', '')


def test_an_unreal_game_lists_its_crash_folders_and_sends_the_tail_of_its_newest_log(tmp_path):
    game = unreal_game(tmp_path)
    local = tmp_path / 'Local'
    fresh = local / 'Proj' / 'Saved' / 'Crashes' / 'UECC-Windows-ABC_0000'
    fresh.mkdir(parents=True)
    (fresh / 'CrashContext.runtime-xml').write_text('<x/>')
    (fresh / 'UEMinidump.dmp').write_bytes(b'DUMPBYTES')
    stale = local / 'Proj' / 'Saved' / 'Crashes' / 'UECC-Windows-OLD_0000'
    stale.mkdir()
    age(stale, 40)
    beside = Path(game.folder) / 'Proj' / 'Saved' / 'Crashes' / 'UECC-Windows-DEV_0000'   # a development build
    beside.mkdir(parents=True)
    logs_dir = local / 'Proj' / 'Saved' / 'Logs'
    logs_dir.mkdir()
    lines = [f'[2026.10.13-17.03.{n % 60:02d}:000][{n}]LogTemp: line {n} C:\\Users\\{USER}\\x' for n in range(500)]
    (logs_dir / 'Proj.log').write_text('\ufeff' + '\n'.join(lines) + '\n', encoding='utf-8')
    (logs_dir / 'Proj-backup-2026.10.12.log').write_text('an earlier run\n')
    age(logs_dir / 'Proj-backup-2026.10.12.log', 1)
    text = crashes.crashes_text(game, windows=True, run=serve(), dumps_dir=tmp_path / 'none', wer_folders=[],
                                processes=[], local=local)
    assert 'Unreal Engine project Proj' in text and 'UECC-Windows-ABC_0000 | 2 files, ' in text
    assert 'UECC-Windows-DEV_0000' in text and 'UECC-Windows-OLD_0000' not in text   # last 14 days only
    assert 'DUMPBYTES' not in text and 'is included as unreal-latest.log' in text
    [(name, data)] = crashes.unreal_evidence(game, logs.redactor([USER]), True, local)
    body = data.decode()
    assert name == 'unreal-latest.log' and body.splitlines()[0].startswith('[AMDLSS: the last 200 lines of Proj.log')
    assert body.splitlines()[1].endswith('line 300 C:\\Users\\<user>\\x') and body.splitlines()[-1].endswith('<user>\\x')
    assert len(body.splitlines()) == 201 and USER not in body and 'an earlier run' not in body
    assert '\ufeff' not in body
    # a game that is not an Unreal game, or has no Saved folder, says nothing about Unreal
    plain = make_game(tmp_path / 'plain')
    assert crashes.unreal_evidence(plain, str, True, local) == []
    assert 'Unreal' not in crashes.crashes_text(plain, windows=True, run=serve(), dumps_dir=tmp_path / 'none',
                                                wer_folders=[], processes=[], local=local)
    assert crashes.unreal_evidence(unreal_game(tmp_path / 'other'), str, True, tmp_path / 'nowhere') == []


def test_the_unreal_log_is_capped_in_size(tmp_path):
    game = unreal_game(tmp_path)
    saved = Path(game.folder) / 'Proj' / 'Saved' / 'Logs'
    saved.mkdir(parents=True)
    (saved / 'Proj.log').write_text('\n'.join('x' * 5000 for _ in range(150)), encoding='utf-8')
    [(_name, data)] = crashes.unreal_evidence(game, str, True, tmp_path / 'nowhere')
    assert len(data.decode()) <= crashes.UNREAL_LOG_CHARS + 200


def test_on_linux_the_unreal_saved_folder_inside_the_proton_prefix_is_found(tmp_path):
    library = tmp_path / 'Steam' / 'steamapps'
    root = library / 'common' / 'UE'
    exe_folder = root / 'Proj' / 'Binaries' / 'Win64'
    exe_folder.mkdir(parents=True)
    (library / 'appmanifest_77.acf').write_text('"appid" "77"\n"installdir" "UE"\n"name" "UE"\n')
    saved = library / 'compatdata' / '77' / 'pfx' / 'drive_c' / 'users' / 'steamuser' / 'AppData' / 'Local' / 'Proj' / 'Saved'
    (saved / 'Crashes' / 'UECC-Windows-X_0000').mkdir(parents=True)
    game = games.Game(name='UE', folder=root.as_posix(), exe_folder=exe_folder.as_posix())   # a Linux game's paths
    project, places = crashes.unreal_saved_dirs(game, windows=False)
    assert project == 'Proj' and places == [saved]
    text = crashes.crashes_text(game, windows=False, home=tmp_path, proc=tmp_path / 'noproc')
    assert 'Unreal Engine crash folder | ' in text and 'UECC-Windows-X_0000' in text
