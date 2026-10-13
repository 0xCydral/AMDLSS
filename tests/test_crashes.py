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
    for needle in ('Get-WinEvent -FilterHashtable', "LogName='Application'", 'Id=@(1000,1001)',
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
    assert text.startswith('Windows crashes for One (Application log, last 14 days, Event ID 1000 and 1001)\n')
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
    assert called == [] and os.name  # the query never ran; collection still produced the file


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
