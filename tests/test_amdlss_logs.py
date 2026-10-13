# SPDX-License-Identifier: GPL-2.0-or-later
"""tools/amdlss-logs.py: what it checks before a request, and that a report only ever becomes plain, private text
files inside its own folder."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_reports import GOOD, server  # noqa: F401  (the fixture: a local stand-in for the receiver)

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / 'tools' / 'amdlss-logs.py'
CODE = 'ABCD2345'


def load():
    spec = importlib.util.spec_from_file_location('amdlss_logs_tool', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load()


def report(**files):
    return {**GOOD, 'game': '2 games: Far Cry 6, Quiet', 'files': files}


FULL = report(**{
    'games.txt': 'Far-Cry-6/ | Far Cry 6 | C:\\Users\\<user>\\x | install: drop-in DLL | HelixSR: 1.9.0\nQuiet/ | Quiet\n',
    'system.txt': 'AMDLSS 0.1.0\nCPU: x\n',
    'Far-Cry-6/files.txt': 'Game: Far Cry 6\n',
    'Far-Cry-6/exe-folder/OptiScaler.log': '[Info] start\n[Error] shader FAILED to compile\nok\nUnhandled Exception: boom\n',
    'Far-Cry-6/exe-folder/HelixSR/helixsr.log': 'network ready\r\nCRASH at 0x0\r\n',
    'Quiet/files.txt': 'Game: Quiet\n',
    'Quiet/exe-folder/OptiScaler.log': '[Info] nothing wrong\n',
})


def run(args, env=None, **options):
    return subprocess.run([sys.executable, '-I', str(TOOL), *args], capture_output=True, text=True, timeout=60,
                          env={**os.environ, 'AMDLSS_LOGS_URL': '', 'AMDLSS_LOGS_TOKEN': '', **(env or {})}, **options)


# --- before any request ------------------------------------------------------------------------------------------

def test_a_bad_code_exits_non_zero_without_a_request(server):  # noqa: F811
    stub = server((200, FULL))
    env = {'AMDLSS_LOGS_URL': stub.url, 'AMDLSS_LOGS_TOKEN': 'tok'}
    for code in ('../x', 'abc', 'ILOU0000', '..%2f..%2fx', 'ABCD2345/../x'):
        done = run(['fetch', code], env)
        assert done.returncode != 0 and 'not a report code' in done.stderr, code
    assert stub.seen == []


def test_the_documented_check_runs_under_isolated_python():
    done = run(['fetch', '../x'])
    assert done.returncode == 2 and done.stdout == '' and 'not a report code' in done.stderr


def test_a_missing_token_or_address_is_a_usage_error_without_a_request(server, tmp_path):  # noqa: F811
    stub = server((200, FULL))
    for env in ({}, {'AMDLSS_LOGS_URL': stub.url}, {'AMDLSS_LOGS_TOKEN': 'tok'}):
        done = run(['fetch', CODE, '--out', str(tmp_path)], env)
        assert done.returncode == 2 and 'AMDLSS_LOGS_TOKEN' in done.stderr
    assert stub.seen == [] and not any(tmp_path.iterdir())


def test_the_url_flag_and_a_lower_case_code_work_end_to_end_under_isolated_python(server, tmp_path):  # noqa: F811
    stub = server((200, FULL))
    done = run(['fetch', CODE.lower(), '--url', stub.url, '--out', str(tmp_path)], {'AMDLSS_LOGS_TOKEN': 'tok'})
    assert done.returncode == 0, done.stderr
    assert stub.seen[0][0] == f'/reports/{CODE}' and stub.auth == ['Bearer tok']
    assert (tmp_path / CODE / 'Far-Cry-6' / 'exe-folder' / 'OptiScaler.log').is_file()


# --- what is written ---------------------------------------------------------------------------------------------

def fetch(tmp_path, server_url, capsys, answer=None, code=CODE, out=None):  # noqa: F811
    status = tool.main(['fetch', code, '--url', server_url, '--out', str(out or tmp_path)], {'AMDLSS_LOGS_TOKEN': 'tok'})
    shown = capsys.readouterr()
    return status, shown.out, shown.err


@pytest.mark.skipif(os.name == 'nt', reason='0600/0700 modes are the Linux reader contract: tools/amdlss-logs.py '
                                            'is supported on Linux (tools/amdlss-logs-gui.sh, ~/.config token file) '
                                            'and Windows os.chmod cannot set POSIX modes; the script warns there')
def test_every_file_lands_in_the_reports_own_folder_private_and_not_executable(server, tmp_path, capsys):  # noqa: F811
    stub = server((200, FULL))
    status, out, err = fetch(tmp_path, stub.url, capsys)
    assert status == 0 and err == ''
    folder = tmp_path / CODE
    written = sorted(str(p.relative_to(folder)).replace(os.sep, '/') for p in folder.rglob('*') if p.is_file())
    assert written == sorted([*FULL['files'], 'report.html'])   # the report's files, plus the readable page
    assert 'Far Cry 6' in (folder / 'report.html').read_text(encoding='utf-8')
    for path in folder.rglob('*'):
        mode = path.stat().st_mode & 0o777
        assert mode == (0o700 if path.is_dir() else 0o600), (path, oct(mode))   # nothing is executable, nothing is shared
    assert (folder / 'Far-Cry-6' / 'exe-folder' / 'HelixSR' / 'helixsr.log').read_bytes() == b'network ready\r\nCRASH at 0x0\r\n'
    assert not (tmp_path / '..' / 'x').exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [CODE]


def test_a_name_that_leaves_the_folder_is_refused_even_if_the_server_sent_it(server, tmp_path, capsys):  # noqa: F811
    outside = tmp_path / 'out'
    outside.mkdir()
    for name in ('../evil.txt', 'a/../../evil.log', '/etc/evil.log', 'ok/../../evil.ini', 'C:/evil.log', 'a\\b.log',
                 'tool.exe', 'run.sh', 'x.log\x00.exe', 'link/../../x.txt'):
        stub = server((200, report(**{name: 'x', 'ok.txt': 'fine'})))
        status, out, err = fetch(tmp_path, stub.url, capsys, out=outside)
        assert status == 1 and err.startswith('error: '), name
    assert not (tmp_path / 'evil.txt').exists() and not (tmp_path / 'evil.log').exists()
    assert list(outside.iterdir()) == []   # a refused report writes nothing, not even its folder


def test_plan_resolves_every_path_and_stays_inside(tmp_path):
    folder = tmp_path / CODE
    planned = tool.plan(report(**{'G/exe-folder/a.log': 'x', 'system.txt': 'y'}), folder)
    root = folder.resolve()
    assert all(root in path.parents for path, _data in planned)
    for bad in ({'a.log': 'x', 'A.LOG': 'y'}, {'a.log': 'x', 'a.log/b.log': 'y'}, {'G/a.log': 'x', 'g/A.log': 'y'}):
        with pytest.raises(tool.ReportError):
            tool.plan(report(**bad), folder)
    escaped = {'files': {'../../escape.log': 'x'}}
    with pytest.raises(tool.ReportError, match='outside'):
        tool.plan(escaped, folder)


def test_a_symlink_in_the_way_is_not_followed(tmp_path):
    folder = tmp_path / CODE
    folder.mkdir()
    target = tmp_path / 'elsewhere'
    target.mkdir()
    try:
        (folder / 'G').symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('no symlinks here')
    with pytest.raises(tool.ReportError, match='outside'):
        tool.plan(report(**{'G/x.log': 'x'}), folder)


def test_an_existing_report_folder_is_never_overwritten(server, tmp_path, capsys):  # noqa: F811
    stub = server((200, FULL))
    assert fetch(tmp_path, stub.url, capsys)[0] == 0
    mine = tmp_path / CODE / 'system.txt'
    mine.write_text('my notes')
    status, out, err = fetch(tmp_path, stub.url, capsys)
    assert status == 1 and 'already exists' in err and mine.read_text() == 'my notes'


def test_an_existing_file_is_not_overwritten_even_inside_a_new_folder(tmp_path):
    folder = tmp_path / CODE
    planned = tool.plan(report(**{'a.log': 'new'}), folder)
    tool.make_dir(folder)
    (folder / 'a.log').write_text('old')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with pytest.raises(FileExistsError):
        os.open(planned[0][0], flags, 0o600)
    assert (folder / 'a.log').read_text() == 'old'


def test_nothing_in_a_report_is_run_or_unpacked(server, tmp_path, capsys):  # noqa: F811
    hostile = report(**{'a.json': '{"__import__": "os"}', 'b.txt': '#!/bin/sh\nrm -rf ~\n', 'c.ini': '[x]\n',
                        'd.log': 'PK not a zip', 'e.txt': 'MZ not an exe'})
    stub = server((200, hostile))
    assert fetch(tmp_path, stub.url, capsys)[0] == 0
    for path in (tmp_path / CODE).iterdir():
        assert path.stat().st_mode & 0o111 == 0 and path.is_file()
    assert (tmp_path / CODE / 'b.txt').read_text() == '#!/bin/sh\nrm -rf ~\n'


def test_a_server_answer_that_breaks_the_rules_writes_nothing(server, tmp_path, capsys):  # noqa: F811
    for answer in (b'not json', {**FULL, 'extra': 1}, report(**{'a.log': 'x\x00y'}), report(**{'a/b/c/d/e/f/g/h.log': 'x'}),
                   report(**{'x.dll': 'x'}), report()):
        stub = server((200, answer))
        status, out, err = fetch(tmp_path, stub.url, capsys)
        assert status == 1 and err.startswith('error: ') and out == ''
    assert list(tmp_path.iterdir()) == []


def test_a_refused_token_and_an_unknown_code_say_so(server, tmp_path, capsys):  # noqa: F811
    assert 'token was refused' in fetch(tmp_path, server((401, {'error': 'x'})).url, capsys)[2]
    assert 'no report has that code' in fetch(tmp_path, server((404, {'error': 'x'})).url, capsys)[2]
    assert list(tmp_path.iterdir()) == []


def test_the_default_folder_is_amdlss_reports_in_the_home_folder():
    assert tool.DEFAULT_OUT == Path.home() / 'amdlss-reports'


def test_a_windows_run_is_warned_that_the_linux_permissions_cannot_be_set():
    assert 'supported on Linux' in tool.platform_warning('nt') and '0600' in tool.platform_warning('nt')
    assert tool.platform_warning('posix') == ''


# --- the summary -------------------------------------------------------------------------------------------------

def test_the_summary_groups_by_game_with_sizes_and_the_first_problem_lines(server, tmp_path, capsys):  # noqa: F811
    stub = server((200, FULL))
    status, out, err = fetch(tmp_path, stub.url, capsys)
    lines = out.splitlines()
    assert lines[0] == f'Report {CODE} saved to {tmp_path / CODE}'
    assert lines[1] == 'AMDLSS 0.1.0-beta.9: 2 games: Far Cry 6, Quiet'
    assert 'games.txt:' in lines and any('Far-Cry-6/ | Far Cry 6' in line for line in lines)
    headings = [line for line in lines if line.startswith('== ')]
    assert headings == ['== (shared) (2 files)', '== Far-Cry-6/ (3 files)', '== Quiet/ (2 files)']
    size = len(FULL['files']['Far-Cry-6/exe-folder/OptiScaler.log'].encode())
    assert any(line.strip() == f'{size} bytes  Far-Cry-6/exe-folder/OptiScaler.log' for line in lines)
    assert '    Far-Cry-6/exe-folder/OptiScaler.log:2: [Error] shader FAILED to compile' in lines
    assert '    Far-Cry-6/exe-folder/OptiScaler.log:4: Unhandled Exception: boom' in lines
    assert '    Far-Cry-6/exe-folder/HelixSR/helixsr.log:2: CRASH at 0x0' in lines
    quiet = lines[lines.index('== Quiet/ (2 files)'):]
    assert not any('mention' in line or 'Quiet/exe-folder/OptiScaler.log:' in line for line in quiet)


def test_each_game_shows_at_most_20_problem_lines_so_a_noisy_one_cannot_hide_the_rest():
    noisy = report(**{'Noisy/a.log': ''.join(f'error {n}\n' for n in range(50)), 'Calm/a.log': 'crash once\n'})
    lines = tool.summary(noisy, CODE, Path('/x'))
    shown = [line for line in lines if line.startswith('    Noisy/a.log:')]
    assert len(shown) == 20 and shown[0].endswith('error 0') and shown[-1].endswith('error 19')
    assert '  lines that mention error, exception, crash or fail: 20 of 50' in lines
    assert '    Calm/a.log:1: crash once' in lines


def test_the_summary_leaves_ini_files_comments_and_known_noise_out():
    noisy = report(**{'G/exe-folder/OptiScaler.ini': 'this error is inside an ini\n',
                      'G/exe-folder/OptiScaler.log': ('; Might cause crashes, especially with Async\n'
                                                      '[E] nvngx_update.exe failed\nGetLatency: Not enough data\n'
                                                      'a real failure here\n')})
    lines = tool.summary(noisy, CODE, Path('/x'))
    shown = [line for line in lines if ': ' in line and line.startswith('    G/')]
    assert shown == ['    G/exe-folder/OptiScaler.log:4: a real failure here']


def test_a_long_line_is_cut_for_the_summary():
    lines = tool.summary(report(**{'G/a.log': 'error ' + 'x' * 500 + '\n'}), CODE, Path('/x'))
    [shown] = [line for line in lines if line.startswith('    G/a.log:1:')]
    assert len(shown) < 200


# --- sync ---------------------------------------------------------------------------------------------------------

OTHER = report(**{'games.txt': 'Quiet/ | Quiet | C:\\Q | install: none | HelixSR: unknown\n', 'Quiet/files.txt': 'x\n'})
LISTED = {'reports': [{'code': CODE, 'created': 2_000_000_000}, {'code': 'WXYZ6789', 'created': 1_900_000_000}]}


def sync_it(tmp_path, url, environ, capsys):
    status = tool.main(['sync', '--url', url, '--out', str(tmp_path)], environ)
    shown = capsys.readouterr()
    return status, shown.out, shown.err


@pytest.mark.skipif(os.name == 'nt', reason='0600 modes are the Linux reader contract: tools/amdlss-logs.py '
                                            'is supported on Linux (tools/amdlss-logs-gui.sh, ~/.config token file) '
                                            'and Windows os.chmod cannot set POSIX modes; the script warns there')
def test_sync_downloads_every_new_report_once_and_builds_the_pages(server, tmp_path, capsys):  # noqa: F811
    environ = {'AMDLSS_LOGS_TOKEN': 'tok', 'XDG_CONFIG_HOME': str(tmp_path / 'cfg')}
    stub = server((200, LISTED), (200, FULL), (200, OTHER))
    status, out, err = sync_it(tmp_path, stub.url, environ, capsys)
    assert status == 0 and err == '', err
    assert '2 new reports downloaded' in out
    assert [seen[0] for seen in stub.seen] == ['/reports', f'/reports/{CODE}', '/reports/WXYZ6789']
    for code, name in ((CODE, 'Far Cry 6'), ('WXYZ6789', 'Quiet')):
        page_text = (tmp_path / code / 'report.html').read_text(encoding='utf-8')
        assert name in page_text and (tmp_path / code / 'report.html').stat().st_mode & 0o777 == 0o600
    index = (tmp_path / 'index.html').read_text(encoding='utf-8')
    assert index.index(f'{CODE}/report.html') < index.index('WXYZ6789/report.html')   # newest first
    kept = (tmp_path / CODE / 'system.txt').read_bytes()
    again = server((200, LISTED))   # a second sync asks for the list and downloads nothing
    status, out, err = sync_it(tmp_path, again.url, environ, capsys)
    assert status == 0 and '0 new reports' in out and '2 already here' in out
    assert [seen[0] for seen in again.seen] == ['/reports']
    assert (tmp_path / CODE / 'system.txt').read_bytes() == kept


def test_sync_reads_the_token_from_the_saved_file_and_needs_one(server, tmp_path, capsys):  # noqa: F811
    config = tmp_path / 'cfg'
    environ = {'XDG_CONFIG_HOME': str(config)}
    stub = server((200, {'reports': []}))
    status, out, err = sync_it(tmp_path / 'out', stub.url, environ, capsys)   # no token anywhere yet
    assert status == 2 and 'AMDLSS_LOGS_TOKEN' in err and stub.seen == []
    saved = config / 'amdlss-logs' / 'token'
    saved.parent.mkdir(parents=True)
    saved.write_text('tok-from-file\n', encoding='utf-8')
    status, out, err = sync_it(tmp_path / 'out', stub.url, environ, capsys)
    assert status == 0 and stub.auth == ['Bearer ' + 'tok-from-file']   # the file's token, whitespace gone
    assert 'tok-from-file' not in out + err   # and it is never shown
    assert (tmp_path / 'out' / 'index.html').is_file()


def test_sync_under_isolated_python_against_the_stub(server, tmp_path):  # noqa: F811
    stub = server((200, {'reports': [{'code': CODE, 'created': 2_000_000_000}]}), (200, FULL))
    done = run(['sync', '--out', str(tmp_path)],
               {'AMDLSS_LOGS_URL': stub.url, 'AMDLSS_LOGS_TOKEN': 'tok', 'HOME': str(tmp_path / 'home')})
    assert done.returncode == 0, done.stderr
    assert '1 new report downloaded' in done.stdout
    assert (tmp_path / 'index.html').is_file() and (tmp_path / CODE / 'report.html').is_file()
    refused = server((401, {'error': 'x'}))
    done = run(['sync', '--out', str(tmp_path)],
               {'AMDLSS_LOGS_URL': refused.url, 'AMDLSS_LOGS_TOKEN': 'wrong', 'HOME': str(tmp_path / 'home')})
    assert done.returncode == 1 and 'read token was refused' in done.stderr
