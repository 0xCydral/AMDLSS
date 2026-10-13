# SPDX-License-Identifier: GPL-2.0-or-later
"""Send logs: what a report may hold (and that Python and the receiver's JavaScript agree), how one report carries every
game and is cut to fit, and ReportClient against a local server."""
import json
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from amdlss import games as gamelist, logs, reports
from amdlss.reports import ReportClient, ReportError
from test_logs import INFO, USER, fake  # noqa: F401  (the fixture: a game folder with logs under a fake profile)

GOOD = {'app': 'AMDLSS', 'version': '0.1.0-beta.9', 'game': 'Fake Game', 'files': {'files.txt': 'ok\n'}}
GOOD_NAMES = ['files.txt', 'system.txt', 'amdlss-install.log', 'Far-Cry-6/exe-folder/OptiScaler.log',
              'Far-Cry-6/exe-folder/HelixSR/helixsr-install.json', 'fsr-dll-folder-2/helixsr.ini', 'a/b/c.LOG',
              'a/b/c/d/e.log', 'Game/history/20261013-141500/exe-folder/HelixSR/helixsr.log', 'a/b/c/d/e/f/g.log',
              'crashes.txt', 'Game/crashes.txt', 'Game/proton.log', 'a' * 156 + '.log']
BAD_NAMES = ['', '../x.log', 'a/../x.log', '/etc/passwd.txt', '.hidden.log', 'a/.x.log', 'a/./x.log', 'a//x.log',
             'a/b/c/d/e/f/g/h.log', 'a\\b.log', 'C:/x.log', 'x.log ', 'x .log', 'x.log.', 'x..log', 'noext', '.log',
             'x.exe', 'x.dll', 'x.log.exe', 'x.sh', 'x.zip', 'x.log/', 'a./b.log', 'nul.log', 'COM1.txt', 'a/aux.ini',
             'caf\u00e9.log', 'x\x00.log', 'x\n.log', 'a' * 157 + '.log', '__proto__']
GOOD_TEXTS = ['a\tb\r\nc\n', 'caf\u00e9 \u2014 \U0001F600', '']
BAD_TEXTS = ['a\x00b', '\x01', '\x08', '\x0b', '\x0c', '\x1b[31m', '\x7f', '\x9b', 'x\ud800']


def entries(**files):
    return {**GOOD, 'files': files}


# --- the rules ---------------------------------------------------------------------------------------------------

def test_the_names_of_a_zip_of_logs_are_accepted_and_odd_ones_are_not():
    assert [reports.name_problem(n) for n in GOOD_NAMES] == [None] * len(GOOD_NAMES)
    for name in BAD_NAMES + [42, None]:
        assert reports.name_problem(name), repr(name)


def test_a_name_has_at_most_seven_parts():
    history = 'Game/history/20261013-141500/exe-folder/HelixSR/helixsr.log'
    assert reports.name_problem(history) is None and reports.name_problem('a/b/c/d/e/f/g.log') is None
    assert 'more than 7 parts' in reports.name_problem('a/b/c/d/e/f/g/h.log')


def test_text_may_hold_tab_newline_and_carriage_return_and_no_other_control_character():
    assert [reports.text_problem(t) for t in GOOD_TEXTS] == [None] * len(GOOD_TEXTS)
    for text in BAD_TEXTS + [5, None, b'x']:
        assert reports.text_problem(text), repr(text)


def test_a_report_is_exactly_app_version_game_and_files():
    assert reports.validate(GOOD) is GOOD
    many = lambda count: {f'f{i}.txt': 'x' for i in range(count)}   # noqa: E731
    bad = [None, [], 'x', {}, {**GOOD, 'extra': 1}, {**GOOD, 'app': 'Other'}, {k: v for k, v in GOOD.items() if k != 'game'},
           {**GOOD, 'files': {}}, {**GOOD, 'files': []}, {**GOOD, 'files': many(201)}, entries(**{'../x.log': 'x'}),
           entries(**{'a.exe': 'x'}), entries(**{'a.log': 'x\x00'}), entries(**{'a.log': 5}),
           entries(**{'a/b/c/d/e/f/g/h.log': 'x'})]
    bad += [{**GOOD, key: value} for key in ('version', 'game') for value in ('', 'x' * 121, 'a\nb', 'a\x00b', 5, None)]
    for report in bad:
        with pytest.raises(ReportError):
            reports.validate(report)
    reports.validate({**GOOD, 'files': many(200), 'game': 'x' * 120, 'version': 'Caf\u00e9'})
    reports.validate(entries(**{'a/b/c/d/e.log': 'x'}))
    reports.validate(entries(**{'Game/history/20261013-141500/exe-folder/HelixSR/helixsr.log': 'x'}))


NODE_CHECK = """
import { MAX_BODY_BYTES, MAX_FILES, MAX_SEGMENTS, nameProblem, textProblem } from './receiver/validate.js';
const cases = JSON.parse(process.argv[1]);
console.log(JSON.stringify({
  names: cases.names.map((n) => nameProblem(n) === null),
  texts: cases.texts.map((t) => textProblem(t) === null),
  limits: [MAX_BODY_BYTES, MAX_FILES, MAX_SEGMENTS],
}));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='no node')
def test_python_and_the_receivers_javascript_agree_on_every_name_text_and_limit():
    names, texts = GOOD_NAMES + BAD_NAMES, GOOD_TEXTS + [t for t in BAD_TEXTS if t != 'x\ud800']
    root = Path(reports.__file__).resolve().parent.parent
    done = subprocess.run(['node', '--input-type=module', '-e', NODE_CHECK, json.dumps({'names': names, 'texts': texts})],
                          cwd=root, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    said = json.loads(done.stdout)
    assert said['names'] == [reports.name_problem(n) is None for n in names]
    assert said['texts'] == [reports.text_problem(t) is None for t in texts]
    assert said['limits'] == [reports.MAX_BODY, reports.MAX_FILES, reports.MAX_SEGMENTS]


NODE_REPORTS = """
import { validateReport } from './receiver/validate.js';
console.log(JSON.stringify(JSON.parse(process.argv[1]).map((report) => validateReport(report).ok)));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='no node')
def test_the_receiver_accepts_what_the_app_builds_and_the_two_sides_share_the_file_limits(fake):  # noqa: F811
    many = lambda count: {f'f{i}.txt': 'x' for i in range(count)}   # noqa: E731
    built = build(fake, add_game(fake, 'Second Game', log='x'))
    cases = [built, entries(**many(200)), entries(**many(201)), entries(**{'a/b/c/d/e.log': 'x'}),
             entries(**{'a/b/c/d/e/f/g/h.log': 'x'}), {**built, 'game': 'x' * 121},
             entries(**{'Game/history/20261013-141500/exe-folder/HelixSR/helixsr.log': 'x'})]
    root = Path(reports.__file__).resolve().parent.parent
    done = subprocess.run(['node', '--input-type=module', '-e', NODE_REPORTS, json.dumps(cases)], cwd=root,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [reports.problem_of(case) is None for case in cases] == \
        [True, True, False, True, False, False, True]


# --- building the report -----------------------------------------------------------------------------------------

def add_game(fake_game, name, state='direct', log='', helixsr='1.9.0'):
    """A second game next to the fixture's: its own folder with an OptiScaler.log of `log` (none when empty)."""
    root = fake_game[3].parent / 'Users' / USER / 'Games' / name
    root.mkdir(parents=True)
    if log:
        (root / 'OptiScaler.log').write_text(log, 'utf-8', newline='')   # exact bytes: text mode rewrites \n on Windows
    return gamelist.Game(name=name, folder=str(root), exe_folder=str(root), state=state, helixsr=helixsr)


def build(fake_game, *more):
    game, setup, data, _out = fake_game
    return reports.build_payload([game, *more], setup, data, INFO)


def test_the_report_holds_the_files_of_every_game_under_its_folder_and_the_system_once(fake):  # noqa: F811
    second = add_game(fake, 'Second Game', log=f'[Info] C:\\Users\\{USER}\\Second\n')
    report = build(fake, second)
    assert reports.problem_of(report) is None
    assert sorted(report['files']) == [
        'Fake-Game/exe-folder/HelixSR/helixsr-install.json', 'Fake-Game/exe-folder/HelixSR/helixsr.log',
        'Fake-Game/exe-folder/OptiScaler.ini', 'Fake-Game/exe-folder/OptiScaler.log',
        'Fake-Game/exe-folder/fakenvapi.ini', 'Fake-Game/files.txt', 'Fake-Game/fsr-dll-folder/helixsr.log',
        'Second-Game/exe-folder/OptiScaler.log', 'Second-Game/files.txt', 'games.txt', 'system.txt']
    assert report['app'] == 'AMDLSS' and report['version'] == reports.VERSION
    assert report['game'] == '2 games: Fake: Game?, Second Game'
    assert 'C:\\Users\\<user>\\Games\\Fake Game\\bin' in report['files']['Fake-Game/exe-folder/OptiScaler.log']
    assert report['files']['Second-Game/exe-folder/OptiScaler.log'] == '[Info] C:\\Users\\<user>\\Second\n'
    assert 'GPU 1: AMD Radeon RX 7800 XT' in report['files']['system.txt']
    assert USER not in reports.body_of(report).decode()
    assert len(reports.body_of(report)) < reports.MAX_BODY


def test_one_game_names_the_report_after_itself(fake):  # noqa: F811
    assert build(fake)['game'] == 'Fake: Game?'
    fake[0].name = 'Tab\tand\nline \x1b[0m' + 'x' * 300
    label = build(fake)['game']
    assert len(label) == reports.MAX_LABEL_CHARS and '\n' not in label and '\x1b' not in label
    fake[0].name = '\n\t'
    assert build(fake)['game'] == 'unknown'


def test_games_txt_has_one_line_per_game(fake):  # noqa: F811
    opti = add_game(fake, 'Opti Game', state='optiscaler', helixsr='1.8.1')
    (Path(opti.folder) / 'dxgi.dll').write_bytes(b'MZ OptiScaler 0.9.4')
    (Path(opti.folder) / 'OptiScaler.ini').write_bytes(b'[FrameGen]\r\nFGInput=dlssg\r\nFGOutput=fsrfg\r\n')
    plain = add_game(fake, 'Plain Game', state='none', log='[Info] left over\n', helixsr='')
    lines = build(fake, opti, plain)['files']['games.txt'].splitlines()
    assert len(lines) == 3
    fields = [[f.strip() for f in line.split(' | ')] for line in lines]
    assert fields[0][:2] == ['Fake-Game/', 'Fake: Game?'] and 'install: OptiScaler as dxgi.dll' in fields[0]
    assert 'HelixSR: unknown' in fields[0] and 'frame generation: off' in fields[0]   # the fixture's ini has no FG values
    assert fields[1][:2] == ['Opti-Game/', 'Opti Game'] and fields[1][2].endswith('Opti Game')
    assert fields[1][3:] == ['install: OptiScaler as dxgi.dll', 'HelixSR: 1.8.1', 'frame generation: on']
    assert fields[2][3:] == ['install: none', 'HelixSR: unknown']   # no OptiScaler.ini: nothing said about frame generation
    assert USER not in '\n'.join(lines) and '<user>' in lines[1]
    assert reports.install_mode(add_game(fake, 'D', state='direct')) == 'drop-in DLL'


def test_a_game_with_nothing_to_report_is_skipped(fake):  # noqa: F811
    empty = add_game(fake, 'Empty Game', state='none')
    (Path(empty.folder) / 'unrelated.txt').write_text('x')
    (Path(empty.folder) / 'OptiScaler.ini').write_text('[Log]\n')   # a settings file alone is not a finding
    installed = add_game(fake, 'Installed Game', state='direct')   # installed, no log yet: reported
    logged = add_game(fake, 'Logged Game', state='none', log='x')
    recorded = add_game(fake, 'Recorded Game', state='none')
    logs.GameLog(fake[2], recorded.folder).write('=== install ===')
    games = [fake[0], empty, installed, logged, recorded]
    assert [g.name for g in reports.reportable(games, fake[2])] == ['Fake: Game?', 'Installed Game', 'Logged Game', 'Recorded Game']
    names = {n.split('/')[0] for n in reports.build_payload(games, fake[1], fake[2], INFO)['files']}
    assert names == {'Fake-Game', 'Installed-Game', 'Logged-Game', 'Recorded-Game', 'games.txt', 'system.txt'}
    assert 'amdlss-install.log' in ' '.join(build(fake, recorded)['files'])
    with pytest.raises(ReportError, match='anything to report'):
        reports.build_payload([empty], None, fake[2], INFO)
    with pytest.raises(ReportError, match='anything to report'):
        reports.build_payload([], None, fake[2], INFO)


def test_folder_names_follow_the_zip_slug_and_are_unique_and_valid():
    names = ["Assassin's Creed: Shadows", 'MONSTER HUNTER STORIES 3', 'Game', 'game', 'GAME', 'Nul', 'Con.X', 'A...B',
             '???', '', 'x' * 100]
    folders = reports.folder_names([gamelist.Game(name=n) for n in names])
    assert folders[:3] == ['Assassin-s-Creed-Shadows', 'MONSTER-HUNTER-STORIES-3', 'Game']
    assert folders[3:5] == ['game-2', 'GAME-3']   # a Windows folder is not told apart by case
    assert folders[5:8] == ['game-Nul', 'game-Con.X', 'A.B'] and folders[8:10] == ['game-4', 'game-5']
    assert folders[10] == 'x' * 40 and folders[0] == logs.slug("Assassin's Creed: Shadows")
    assert len({f.lower() for f in folders}) == len(folders)
    for folder in folders:
        assert reports.name_problem(f'{folder}/files.txt') is None, folder


def test_files_are_decoded_as_utf8_with_replacement_and_stripped_of_control_characters(fake):  # noqa: F811
    exe = Path(fake[0].exe_folder)
    (exe / 'OptiScaler.log').write_bytes(b'ok \xff\xfe\x80 bad\x00 nul\x1b[31m red\x7f \r\n\ttab\n')
    assert build(fake)['files']['Fake-Game/exe-folder/OptiScaler.log'] == 'ok \ufffd\ufffd\ufffd bad nul[31m red \r\n\ttab\n'
    (exe / 'OptiScaler.ini').write_bytes('\ufeff[Log]\nx=caf\u00e9\n'.encode('utf-16'))
    assert build(fake)['files']['Fake-Game/exe-folder/OptiScaler.ini'].lstrip('\ufeff') == '[Log]\nx=caf\u00e9\n'


def test_a_name_the_receiver_would_refuse_is_left_out(fake):  # noqa: F811
    (Path(fake[0].exe_folder) / 'OptiScaler copy.log').write_text('spaces in a name')
    report = build(fake)
    assert not any('copy' in name for name in report['files']) and reports.problem_of(report) is None
    assert 'OptiScaler copy.log' not in reports.confirm_text([fake[0]], fake[2])


def test_a_huge_log_goes_in_as_its_tail_and_the_rest_stays_whole(fake):  # noqa: F811
    exe = Path(fake[0].exe_folder)
    lines = ''.join(f'[Info] line {n} hooked "x" \t\n' for n in range(400_000))   # about 11 MB; escapes make it larger
    (exe / 'OptiScaler.log').write_text(lines + 'THE LAST LINE\n', 'utf-8', newline='')
    report = build(fake)
    assert len(reports.body_of(report)) <= reports.MAX_BODY and reports.problem_of(report) is None
    text = report['files']['Fake-Game/exe-folder/OptiScaler.log']
    assert text.startswith('[AMDLSS: the first ') and 'characters of this file are left out' in text.splitlines()[0]
    assert text.endswith('THE LAST LINE\n') and text.splitlines()[1].startswith('[Info] line ')   # cut at a line start
    assert report['files']['Fake-Game/exe-folder/HelixSR/helixsr.log'] == 'network ready\n'
    assert report['files']['Fake-Game/fsr-dll-folder/helixsr.log'] == 'fsr drop-in log\n'


def test_a_big_log_in_one_game_does_not_push_another_game_out(fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(reports, 'MAX_BODY', 1 << 20)
    monkeypatch.setattr(reports, 'MAX_FILE_CHARS', 1 << 20)
    exe = Path(fake[0].exe_folder)
    (exe / 'OptiScaler.log').write_text('a' * 800_000 + '\nEND-A\n', newline='')
    (exe / 'HelixSR' / 'helixsr.log').write_text('h' * 300_000 + '\nEND-H\n', newline='')
    big = add_game(fake, 'Big Game', log='b' * 800_000 + '\nEND-B\n')
    small = add_game(fake, 'Small Game', log='tiny log\n')
    files = build(fake, big, small)['files']
    assert len(reports.body_of({**GOOD, 'files': files})) <= 1 << 20
    assert files['Small-Game/exe-folder/OptiScaler.log'] == 'tiny log\n'   # a small game keeps everything
    for name, end in (('Fake-Game/exe-folder/OptiScaler.log', 'END-A\n'), ('Big-Game/exe-folder/OptiScaler.log', 'END-B\n')):
        assert files[name].startswith('[AMDLSS: the first ') and files[name].endswith(end)
    kept = {game: sum(len(t) for n, t in files.items() if n.startswith(game + '/')) for game in ('Fake-Game', 'Big-Game')}
    assert 0.8 < kept['Fake-Game'] / kept['Big-Game'] < 1.25   # the two big games end up with about the same
    # inside a game the largest file is cut first: the 300 KB log keeps more of itself than the 800 KB one
    assert 'END-H' in files['Fake-Game/exe-folder/HelixSR/helixsr.log']
    assert len(files['Fake-Game/exe-folder/HelixSR/helixsr.log']) > 200_000


def test_many_games_with_big_logs_all_arrive_within_the_real_budget(fake):  # noqa: F811
    more = [add_game(fake, f'Game {n}', log=('x' * 99 + '\n') * 25_000 + f'END-{n}\n') for n in range(5)]   # 2.5 MB each
    exe = Path(fake[0].exe_folder)
    (exe / 'OptiScaler.log').write_text(('y' * 99 + '\n') * 25_000 + 'END-0\n', newline='')
    report = build(fake, *more)
    assert len(reports.body_of(report)) <= reports.MAX_BODY == 8 << 20
    logs_of = {n.split('/')[0]: t for n, t in report['files'].items() if n.endswith('exe-folder/OptiScaler.log')}
    assert sorted(logs_of) == ['Fake-Game', 'Game-0', 'Game-1', 'Game-2', 'Game-3', 'Game-4']
    for folder, text in logs_of.items():
        assert text.startswith('[AMDLSS:') and text.endswith('END-0\n' if folder == 'Fake-Game' else f'END-{folder[-1]}\n')
    sizes = [len(t) for t in logs_of.values()]
    assert max(sizes) < 1.1 * min(sizes)


def test_one_file_may_not_take_more_than_its_share(fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(reports, 'MAX_FILE_CHARS', 1000)
    (Path(fake[0].exe_folder) / 'OptiScaler.log').write_text('x' * 5000 + '\nEND\n', newline='')
    text = build(fake)['files']['Fake-Game/exe-folder/OptiScaler.log']
    assert text.startswith('[AMDLSS: the first ') and text.endswith('END\n') and len(text) < 1200


def test_a_report_that_cannot_be_made_small_enough_is_an_error(fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(reports, 'MAX_BODY', 300)
    with pytest.raises(ReportError, match='too large'):
        build(fake)


def test_more_than_200_files_keep_each_games_files_txt_and_the_top_level_ones(fake):  # noqa: F811
    exe = Path(fake[0].exe_folder)
    for number in range(210):
        (exe / f'optiscaler-{number:03}.log').write_text('x')
    report = build(fake)
    assert len(report['files']) == reports.MAX_FILES
    assert {'system.txt', 'games.txt', 'Fake-Game/files.txt'} <= set(report['files'])
    assert reports.problem_of(report) is None


def test_the_confirm_text_lists_the_games_and_how_many_files_each_sends(fake):  # noqa: F811
    second = add_game(fake, 'Second Game', log='x')
    empty = add_game(fake, 'Empty Game', state='none')
    text = reports.confirm_text([fake[0], second, empty], fake[2])
    assert 'Fake: Game?: 7 files' in text   # the six files found, plus files.txt
    assert 'Second Game: 2 files' in text and 'Empty Game' not in text
    assert 'AMDLSS log server' in text and '30 days' in text and '<user>' in text and text.endswith('Send logs?')
    assert 'system.txt' in text and 'games.txt' in text


# --- the client --------------------------------------------------------------------------------------------------

class Server:
    """A local stand-in for the receiver: answers GET and POST from `script` (one entry per request, the last one
    repeats) and records what it was sent."""

    def __init__(self, *script):
        self.script, self.seen, self.auth = list(script), [], []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                outer.auth.append(self.headers.get('Authorization'))
                outer.seen.append((self.path, self.headers.get('Content-Type'), self.headers.get('User-Agent'), body))
                step = outer.script[min(len(outer.seen), len(outer.script)) - 1]
                if step == 'drop':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                status, answer = step
                data = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    made = []

    def start(*script):
        made.append(Server(*script))
        return made[-1]

    yield start
    for one in made:
        one.close()


def test_a_good_report_is_posted_as_json_and_the_code_comes_back(server):
    stub = server((200, {'code': 'ABCD2345'}))
    assert ReportClient(stub.url).send(GOOD) == 'ABCD2345'
    [(path, kind, agent, body)] = stub.seen
    assert path == '/reports' and kind == 'application/json' and agent == f'AMDLSS/{reports.VERSION}'
    assert json.loads(body) == GOOD
    assert ReportClient(stub.url + '/').send(GOOD) == 'ABCD2345' and stub.seen[-1][0] == '/reports'


def test_a_full_report_goes_through_the_client_as_built(fake, server):  # noqa: F811
    stub = server((200, {'code': 'ABCD2345'}))
    report = build(fake, add_game(fake, 'Second Game', log='x'))
    assert ReportClient(stub.url).send(report) == 'ABCD2345'
    assert json.loads(stub.seen[0][3]) == report


def test_a_refusal_is_one_readable_error_and_is_not_tried_again(server):
    stub = server((400, {'error': 'a file name has characters\nthat are not allowed'}))
    with pytest.raises(ReportError, match=r'refused the report \(HTTP 400: a file name has characters that are not allowed\)'):
        ReportClient(stub.url).send(GOOD)
    assert len(stub.seen) == 1
    busy = server((429, {'error': 'x'}))
    with pytest.raises(ReportError, match='busy'):
        ReportClient(busy.url).send(GOOD)
    odd = server((500, b'<html>oops</html>'))
    with pytest.raises(ReportError, match=r'\(HTTP 500\)'):
        ReportClient(odd.url).send(GOOD)


def test_an_answer_that_is_not_a_code_is_an_error(server):
    for answer in (b'not json', {'code': 'short'}, {'code': 'abcd2345'}, {'code': 'ABCDEFGI'}, {'nope': 1}, [1]):
        stub = server((200, answer))
        with pytest.raises(ReportError, match='not a report code'):
            ReportClient(stub.url).send(GOOD)
    with pytest.raises(ReportError, match='more than it should'):
        ReportClient(server((200, b'x' * 70000)).url).send(GOOD)


def test_a_dropped_connection_is_tried_once_more(server):
    stub = server('drop', (200, {'code': 'ABCD2345'}))
    told = []
    assert ReportClient(stub.url, log=told.append).send(GOOD) == 'ABCD2345'
    assert len(stub.seen) == 2 and len(told) == 1
    stub = server('drop')
    with pytest.raises(ReportError, match='could not reach the log server'):
        ReportClient(stub.url).send(GOOD)
    assert len(stub.seen) == 2


def test_no_connection_is_a_report_error():
    with socket.socket() as closed:
        closed.bind(('127.0.0.1', 0))
        port = closed.getsockname()[1]
    with pytest.raises(ReportError, match='could not reach the log server'):
        ReportClient(f'http://127.0.0.1:{port}', timeout=2).send(GOOD)


def test_without_an_address_or_with_a_plain_http_one_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(reports, 'REPORT_URL', '')
    assert not ReportClient().configured
    with pytest.raises(ReportError, match='not set up'):
        ReportClient().send(GOOD)
    monkeypatch.setattr(reports, 'REPORT_URL', 'https://logs.example.test')
    assert ReportClient().configured and ReportClient().endpoint() == 'https://logs.example.test/reports'
    for url in ('http://logs.example.test', 'ftp://x', 'logs.example.test'):
        with pytest.raises(ReportError, match='https'):
            ReportClient(url).send(GOOD)


def test_an_invalid_or_oversized_report_never_leaves_the_machine(server, monkeypatch):
    stub = server((200, {'code': 'ABCD2345'}))
    with pytest.raises(ReportError, match='control characters'):
        ReportClient(stub.url).send(entries(**{'a.log': 'x\x00'}))
    monkeypatch.setattr(reports, 'MAX_BODY', 50)
    with pytest.raises(ReportError, match='too large'):
        ReportClient(stub.url).send(GOOD)
    assert stub.seen == []


def test_a_report_is_fetched_with_the_token_and_checked_again(server):
    stub = server((200, GOOD))
    assert ReportClient(stub.url).fetch('ABCD2345', 'sekret-token') == GOOD
    assert stub.seen[0][0] == '/reports/ABCD2345' and stub.auth == ['Bearer sekret-token']
    for answer in (b'not json', entries(**{'../x.log': 'x'}), {**GOOD, 'extra': 1}, [], entries(**{'a.exe': 'x'})):
        with pytest.raises(ReportError):
            ReportClient(server((200, answer)).url).fetch('ABCD2345', 'sekret-token')
    with pytest.raises(ReportError, match='token was refused'):
        ReportClient(server((401, {'error': 'x'})).url).fetch('ABCD2345', 'wrong')
    with pytest.raises(ReportError, match='no report has that code'):
        ReportClient(server((404, {'error': 'x'})).url).fetch('ABCD2345', 'sekret-token')
    with pytest.raises(ReportError, match='more than it should'):
        ReportClient(server((200, b'x' * ((8 << 20) + 1))).url).fetch('ABCD2345', 'sekret-token')


def test_fetch_checks_the_code_and_the_token_before_any_request(server):
    stub = server((200, GOOD))
    for code in ('../x', 'abcd2345', 'ABCD234', 'ILOU0000', '', None):
        with pytest.raises(ReportError, match='not a report code'):
            ReportClient(stub.url).fetch(code, 'tok')
    for token in ('', None, 'a b', 'tok\r\nX-Evil: 1', 'tok\u00e9'):
        with pytest.raises(ReportError, match='read token'):
            ReportClient(stub.url).fetch('ABCD2345', token)
    with pytest.raises(ReportError, match='no log server address'):
        ReportClient('').fetch('ABCD2345', 'tok')
    assert stub.seen == []


def test_the_report_list_is_read_with_the_token_and_checked_entry_by_entry(server):
    listed = {'reports': [{'code': 'ABCD2345', 'created': 300}, {'code': 'EFGH6789', 'created': None}]}
    stub = server((200, listed))
    assert ReportClient(stub.url).list('sekret-token') == listed['reports']
    assert stub.seen[0][0] == '/reports' and stub.auth == ['Bearer ' + 'sekret-token']
    assert ReportClient(server((200, {'reports': []})).url).list('sekret-token') == []
    for answer in (b'not json', [], {'reports': {}}, {'reports': [{'code': 'bad', 'created': 1}]},
                   {'reports': [{'code': 'ABCD2345', 'created': 'now'}]}, {'reports': [{'created': 1}]},
                   {'reports': [{'code': 'ABCD2345'}]}):
        with pytest.raises(ReportError):
            ReportClient(server((200, answer)).url).list('sekret-token')
    with pytest.raises(ReportError, match='token was refused'):
        ReportClient(server((401, {'error': 'x'})).url).list('wrong')


def test_list_checks_the_token_and_the_address_before_any_request(server):
    stub = server((200, {'reports': []}))
    for token in ('', None, 'a b', 'tok\r\nX-Evil: 1'):
        with pytest.raises(ReportError, match='read token'):
            ReportClient(stub.url).list(token)
    with pytest.raises(ReportError, match='no log server address'):
        ReportClient('').list('tok')
    assert stub.seen == []


def test_the_default_address_is_the_live_receiver():
    assert reports.DEFAULT_LOGS_URL == 'https://amdlss-reports.amdlss-reports.workers.dev'
    assert reports.REPORT_URL == reports.DEFAULT_LOGS_URL


def test_the_code_to_paste():
    assert reports.report_text('ABCD2345') == 'AMDLSS report ABCD2345'
    assert reports.valid_code('ABCD2345') and not any(map(reports.valid_code, ['abcd2345', 'ABCD234', 'ABCD23456', 'ILOU0000', None]))
