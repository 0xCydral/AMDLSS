# SPDX-License-Identifier: GPL-2.0-or-later
"""Send logs: the evidence of Collect logs for every game that has any, in one upload to the AMDLSS log receiver
(receiver/, a Cloudflare Worker), which answers a short code the tester pastes in chat. ReportClient is the only code
that talks to that server.

A report is text only: `{"app", "version", "game", "files": {name: text}}`, under 8 MiB. Each game's files sit in a
folder named after the game (`Far-Cry-6/exe-folder/OptiScaler.log`); system.txt and games.txt (one line per game: name,
folder, install mode, HelixSR version) sit at the top. The rules for what a name or a text may hold are the receiver's
(receiver/validate.js) written again in Python, and tools/amdlss-logs.py applies them to what it downloads. Nobody gets
an executable through this path: the names are a fixed allowlist of text extensions, the content is text with no
control characters, and the fetch tool never runs or unpacks anything.

Over the budget, every game gets a fair share and its largest files go in as their last lines with a one-line note on
top (OptiScaler.log can be 800 KB or more), so one big log cannot push another game out. REPORT_URL stays empty until
the receiver is deployed; then the window offers the zip instead.
"""
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import VERSION, framegen, fsr4, helixsr, logs

DEFAULT_LOGS_URL = 'https://amdlss-reports.amdlss-reports.workers.dev'   # the receiver's address (receiver/README.md)
REPORT_URL = DEFAULT_LOGS_URL   # where Send logs uploads; empty: sending is not set up in this build
TIMEOUT = 30
MAX_BODY = 8 << 20
MAX_FILES = 200
MAX_SEGMENTS = 7
MAX_NAME_CHARS = 160
MAX_LABEL_CHARS = 120
MAX_FILE_CHARS = 2 << 20      # one file may not take more than a quarter of the budget
MAX_ANSWER = 64 << 10
MAX_LIST = 1 << 20        # the report list: one short entry per stored report
EXTENSIONS = ('log', 'ini', 'json', 'txt')
KEYS = {'app', 'version', 'game', 'files'}
# kept first when there are more files than MAX_FILES
OWN_FILES = ('files.txt', logs.SESSION_LOG, logs.CRASHES, logs.crashes.UNREAL_LOG, helixsr.SETUP_LOG)
CODE = re.compile(r'[0-9A-HJKMNP-TV-Z]{8}')
# A name part is letters, digits, "_", "." and "-", and it neither starts nor ends with ".".
SEGMENT = re.compile(r'[A-Za-z0-9_-](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?')
DEVICE = re.compile(r'(?:con|prn|aux|nul|com[0-9]|lpt[0-9])', re.IGNORECASE)   # Windows device names
# NUL, the C0 controls but tab, line feed and carriage return, DEL and the C1 controls (a terminal obeys those).
CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')
ANY_CONTROL = re.compile('[\x00-\x1f\x7f-\x9f]')


class ReportError(Exception):
    """One line, in words a tester can read, on why a report was not sent (or not accepted)."""


def valid_code(code):
    return isinstance(code, str) and CODE.fullmatch(code) is not None


def report_text(code):
    """What the tester pastes in chat."""
    return f'AMDLSS report {code}'


# --- what a report may hold (receiver/validate.js says the same) --------------------------------------------------

def name_problem(name):
    """Why `name` may not be a file name in a report, or None."""
    if not isinstance(name, str) or not name or len(name) > MAX_NAME_CHARS:
        return 'a file name is empty or too long'
    parts = name.split('/')
    if len(parts) > MAX_SEGMENTS:
        return f'a file name has more than {MAX_SEGMENTS} parts'
    for part in parts:
        if not SEGMENT.fullmatch(part) or '..' in part:
            return 'a file name has characters that are not allowed'
        if DEVICE.fullmatch(part.split('.')[0]):
            return 'a file name is a reserved device name'
    dot = parts[-1].rfind('.')
    if dot < 1 or parts[-1][dot + 1:].lower() not in EXTENSIONS:
        return 'a file name does not end in .log, .ini, .json or .txt'
    return None


def text_problem(text):
    """Why `text` may not be a file's content, or None."""
    if not isinstance(text, str):
        return 'a file is not text'
    if CONTROL.search(text):
        return 'a file holds control characters'
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        return 'a file is not valid Unicode text'
    return None


def label_problem(label, what):
    if not isinstance(label, str) or not 0 < len(label) <= MAX_LABEL_CHARS:
        return f'{what} must be a text of 1 to {MAX_LABEL_CHARS} characters'
    if ANY_CONTROL.search(label) or text_problem(label):
        return f'{what} is not printable text'
    return None


def problem_of(report):
    """Why `report` is not an acceptable report, or None."""
    if not isinstance(report, dict) or set(report) != KEYS:
        return 'the report must have exactly app, version, game and files'
    if report['app'] != 'AMDLSS':
        return 'app must be AMDLSS'
    problem = label_problem(report['version'], 'version') or label_problem(report['game'], 'game')
    files = report['files']
    if problem or not isinstance(files, dict):
        return problem or 'files must be an object'
    if not 0 < len(files) <= MAX_FILES:
        return f'files must hold 1 to {MAX_FILES} files'
    for name, text in files.items():
        if problem := name_problem(name) or text_problem(text):
            return problem
    return None


def validate(report):
    """`report` itself, or ReportError saying why it is not acceptable."""
    if problem := problem_of(report):
        raise ReportError(problem)
    return report


# --- building one from the same sources as the zip ----------------------------------------------------------------

def body_of(report):
    """The bytes that go over the wire."""
    return json.dumps(report, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def decode(data):
    """A file's text: UTF-8 (or UTF-16 with a BOM, which redact_bytes keeps) with what cannot be read as U+FFFD, and
    without the control characters a report may not hold."""
    text = data.decode('utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8', errors='replace')
    return CONTROL.sub('', text)


def label_of(text):
    """`text` as a one-line label of at most MAX_LABEL_CHARS characters."""
    return ' '.join(ANY_CONTROL.sub(' ', text).split())[:MAX_LABEL_CHARS] or 'unknown'


def tail_of(text, excess):
    """`text` without its first `excess` characters or more (the cut moves on to the next line start), with a note
    on top. The note's own length is part of what the cut makes up for."""
    cut = min(len(text), excess + 100)
    newline = text.find('\n', cut, cut + 400)
    if newline != -1:
        cut = newline + 1
    return f'[AMDLSS: the first {cut} characters of this file are left out so the report fits]\n' + text[cut:]


def size_of(name, text):
    """What one file adds to the body: its name and text as JSON, the colon and the comma."""
    return sum(len(json.dumps(part, ensure_ascii=False).encode('utf-8')) for part in (name, text)) + 2


def trim_group(files, names, limit):
    """Cuts the largest of `names` in `files` to their tails until together they are within `limit` bytes (or there
    is nothing left to cut)."""
    sizes = {name: size_of(name, files[name]) for name in names}
    while (excess := sum(sizes.values()) - limit) > 0:
        name = max(sizes, key=sizes.get)
        if len(files[name]) < 200:
            return
        files[name] = tail_of(files[name], excess)
        sizes[name] = size_of(name, files[name])


def fit(report):
    """`report` cut to under MAX_BODY bytes. Each game (a folder; the top-level files are one more group) gets an
    equal share of the room, a small group keeping what it does not use for the others, and inside a group the
    largest files are cut first. ReportError when even that is not enough."""
    files = report['files']
    for name, text in files.items():
        if len(text) > MAX_FILE_CHARS:
            files[name] = tail_of(text, len(text) - MAX_FILE_CHARS)
    groups = {}
    for name in files:
        groups.setdefault(name.split('/')[0] if '/' in name else '', []).append(name)
    sizes = {key: sum(size_of(name, files[name]) for name in names) for key, names in groups.items()}
    room, order = MAX_BODY - len(body_of({**report, 'files': {}})), sorted(groups, key=sizes.get)
    for position, key in enumerate(order):
        share = room // (len(order) - position)
        if sizes[key] > share:   # this group and every larger one are cut to the same share
            for larger in order[position:]:
                trim_group(files, groups[larger], share)
            break
        room -= sizes[key]
    while (excess := len(body_of(report)) - MAX_BODY) > 0:   # whatever the shares left (they count commas generously)
        name = max(files, key=lambda key: len(files[key]))
        if len(files[name]) < 200:
            raise ReportError('the report is too large to send')
        files[name] = tail_of(files[name], excess)
    return report


# --- every game in one report -------------------------------------------------------------------------------------

def has_report(game, data_dir):
    """True when `game` has anything to report: HelixSR or OptiScaler is in it, one of their logs is in its folders,
    AMDLSS has a record of its installs, or kept history remains after a remove took the current logs away."""
    own = logs.session_log_path(data_dir, game.folder) if game.folder else None
    return (game.installed or bool(own and own.is_file())
            or any(name.endswith('.log') for name, _ in logs.gather(game))
            or bool(logs.history_snapshots(data_dir, game.folder)))


def reportable(games, data_dir):
    return [game for game in games if has_report(game, data_dir)]


def folder_names(games):
    """One folder name per game, from logs.slug() (the zip's rule) made valid for a report and unique ignoring case."""
    taken, found = set(), []
    for game in games:
        base = re.sub(r'\.{2,}', '.', logs.slug(game.name)).strip('-.') or 'game'
        if DEVICE.fullmatch(base.split('.')[0]):
            base = 'game-' + base
        name, number = base, 1
        while name.lower() in taken:
            number += 1
            name = f'{base}-{number}'
        taken.add(name.lower())
        found.append(name)
    return found


def install_mode(game):
    """'drop-in DLL', 'OptiScaler as dxgi.dll' (whichever of OptiScaler's names is in place) or 'none'."""
    if game.state == 'optiscaler':
        exe = Path(game.exe_folder or game.folder)
        dll = next((n for n in logs.OPTI_NAMES if (exe / n).is_file() and logs.owner_of(exe / n) == 'OptiScaler'), '')
        return f'OptiScaler as {dll}' if dll else 'OptiScaler'
    return {'direct': 'drop-in DLL', 'fsr4': 'drop-in DLL', 'dlss': 'DLSS loader (experimental)',
            'inject': 'DLSS injected with Luma UE (OptiScaler as dxgi.dll)',
            'none': 'none'}.get(game.state, 'unknown')


def games_text(games, folders):
    """games.txt: one line per game, `folder in the report | name | game folder | install mode | HelixSR | frame
    generation` (the last only when the game has an OptiScaler.ini)."""
    lines = []
    for game, folder in zip(games, folders):
        fields = [f'{folder}/', game.name, game.folder, f'install: {install_mode(game)}',
                  f'HelixSR: {"none" if game.state == "fsr4" else game.helixsr or "unknown"}']
        if game.upscaler == 'fsr4':
            fields.append(f'upscaler: {fsr4.NAME} ({fsr4.describe(game)})')
        if game.exe_folder and framegen.ini_path(game.exe_folder).is_file():
            fields.append(f'frame generation: {framegen.name_of(framegen.generator_of(game.exe_folder))}')
        lines.append(ANY_CONTROL.sub(' ', ' | '.join(fields)))
    return '\n'.join(lines) + '\n'


def build_payload(games, helixsr_folder, data_dir, info=None, windows=None, crash_run=None):
    """The report for the games that have anything to report: system.txt and games.txt, then each game's files
    (logs.game_evidence(), kept history included) under its own folder, those with an allowed name only, cut
    to fit. `crash_run` is the crash query's PowerShell seam (tests)."""
    games = reportable(games, data_dir)
    if not games:
        raise ReportError('no game has anything to report yet')
    redact, folders = logs.redactor(), folder_names(games)
    held = [logs.system_evidence(helixsr_folder, redact, info),
            ('games.txt', redact(games_text(games, folders)).encode('utf-8'))] \
        + logs.setup_log_evidence(data_dir, redact)
    on_windows = logs.host.is_windows(windows)
    shared = logs.crashes.query_events(crash_run) if on_windows else None
    processes = helixsr.process_paths(crash_run) if on_windows else None
    for game, folder in zip(games, folders):
        held += [(f'{folder}/{name}', data)
                 for name, data in logs.game_evidence(game, data_dir, redact, windows, crash_run, shared, processes)]
    held = [(name, data) for name, data in held if not name_problem(name)]

    def rank(item):   # stable within a rank: AMDLSS's own writes, the current logs, then kept history
        name = item[0]
        if '/history/' in name:
            return 2
        return 1 if '/' in name and name.rsplit('/', 1)[1] not in OWN_FILES else 0

    held.sort(key=rank)
    files = {name: decode(data) for name, data in held[:MAX_FILES]}
    names = [game.name for game in games]
    label = names[0] if len(names) == 1 else f'{len(names)} games: {", ".join(names)}'
    return fit({'app': 'AMDLSS', 'version': VERSION, 'game': label_of(label), 'files': files})


def confirm_text(games, data_dir):
    """What the window asks before it sends anything: the games, and how many files each sends."""
    lines = ['AMDLSS sends the logs of these games to the AMDLSS log server:', '']
    for game in reportable(games, data_dir):
        count = sum(1 for name in logs.contents(game, data_dir) if name != 'system.txt' and not name_problem(name))
        lines.append(f'  {game.name}: {count} file{"" if count == 1 else "s"}')
    lines += ['', "Per game: the HelixSR, OptiScaler and fakenvapi logs and settings found in the game's folders, "
              'files.txt (names, sizes, dates and versions of its .dll and .exe files), crashes.txt (on Windows, '
              "Windows' record of this game's crashes and the names of its crash dumps, never the dumps "
              'themselves; for an Unreal Engine game also its crash folder names and the last 200 lines of its newest '
              'log, unreal-latest.log), the logs AMDLSS kept before earlier installs (history/), and, when AMDLSS '
              'installed into it, what AMDLSS printed then. Once for all: system.txt (the system, graphics card and '
              'driver, CPU, RAM, '
              'the AMDLSS and HelixSR versions) and games.txt (each game, its folder and install mode).',
              '', 'Your user name is replaced by <user> everywhere. Nothing else from your user folder is included. '
              'The server keeps the report for 30 days and then deletes it. You get a short code to paste in the '
              'chat.', '', 'Send logs?']
    return '\n'.join(lines)


# --- the server ---------------------------------------------------------------------------------------------------

class ReportClient:
    """send(report) uploads a report and returns its code; anything that goes wrong (no address set, no connection,
    an HTTP error, an answer that is not a code) is one ReportError. It is the only code that builds the receiver's
    URLs. A connection error is tried once more; an HTTP answer never is."""

    def __init__(self, url=None, timeout=TIMEOUT, log=None):
        self.url = REPORT_URL if url is None else url
        self.timeout, self.log = timeout, log

    @property
    def configured(self):
        return bool(self.url)

    def endpoint(self):
        parts = urllib.parse.urlsplit(self.url)
        if parts.scheme != 'https' and not (parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost')):
            raise ReportError('the log server address is not an https address')
        return self.url.rstrip('/') + '/reports'

    def send(self, report):
        if not self.configured:
            raise ReportError('sending logs is not set up in this build')
        validate(report)
        body = body_of(report)
        if len(body) > MAX_BODY:
            raise ReportError('the report is too large to send')
        request = urllib.request.Request(self.endpoint(), data=body, method='POST', headers={
            'Content-Type': 'application/json', 'User-Agent': f'AMDLSS/{VERSION}'})
        return self.code_of(self.exchange(request, MAX_ANSWER))

    @staticmethod
    def checked_token(token):
        """`token`, or ReportError when it could never be the server's READ_TOKEN (or would break the header)."""
        if not re.fullmatch('[\x21-\x7e]+', token or ''):
            raise ReportError('the read token is empty or has characters a token never has')
        return token

    def fetch(self, code, token):
        """The report stored under `code` (the developer's side: `token` is the server's READ_TOKEN), checked with
        the same rules as an upload before anyone writes it anywhere."""
        if not self.configured:
            raise ReportError('no log server address is set')
        if not valid_code(code):
            raise ReportError('that is not a report code')
        request = urllib.request.Request(f'{self.endpoint()}/{code}', headers={
            'Authorization': f'Bearer {self.checked_token(token)}', 'Accept': 'application/json',
            'User-Agent': f'AMDLSS/{VERSION}'})
        answer = self.exchange(request, MAX_BODY)
        try:
            return validate(json.loads(answer))
        except ValueError:
            raise ReportError('the log server sent an answer that is not JSON')

    def list(self, token):
        """[{'code', 'created'}] of every report the server holds, newest first as the server sent them
        (`created` is Unix seconds, or None for a report stored before the server kept it). The developer's
        side, like fetch(): anything about the answer that does not fit is one ReportError."""
        if not self.configured:
            raise ReportError('no log server address is set')
        request = urllib.request.Request(self.endpoint(), headers={
            'Authorization': f'Bearer {self.checked_token(token)}', 'Accept': 'application/json',
            'User-Agent': f'AMDLSS/{VERSION}'})
        try:
            parsed = json.loads(self.exchange(request, MAX_LIST))
        except ValueError:
            raise ReportError('the log server sent an answer that is not JSON')
        entries = parsed.get('reports') if isinstance(parsed, dict) else None
        if not isinstance(entries, list):
            raise ReportError('the log server sent a report list that cannot be read')
        found = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'code', 'created'}:
                raise ReportError('the log server sent a report list that cannot be read')
            if not valid_code(entry['code']) or not (entry['created'] is None or type(entry['created']) is int):
                raise ReportError('the log server sent a report list that cannot be read')
            found.append({'code': entry['code'], 'created': entry['created']})
        return found

    def exchange(self, request, limit):
        """The body of the answer to `request` (a connection error is tried once more). More than `limit` bytes,
        or an HTTP error, is a ReportError."""
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    answer = response.read(limit + 1)
                break
            except urllib.error.HTTPError as error:   # before OSError: it is one
                raise ReportError(self.refusal(error, request.get_method()))
            except (OSError, http.client.HTTPException) as error:
                if attempt == 2:
                    raise ReportError(f'could not reach the log server ({error})')
                if self.log:
                    self.log(f'logs: {error}; trying once more')
        if len(answer) > limit:
            raise ReportError('the log server sent more than it should')
        return answer

    @staticmethod
    def refusal(error, method='POST'):
        if error.code == 429:
            return 'the log server is busy: try again in a minute'
        if method == 'GET' and error.code in (401, 404):
            return ('the read token was refused' if error.code == 401 else
                    'no report has that code (the server deletes reports after 30 days)')
        try:
            reason = json.loads(error.read(2048)).get('error')
        except (OSError, ValueError, AttributeError):
            reason = None
        reason = label_of(reason) if isinstance(reason, str) else ''
        return f'the log server refused the report (HTTP {error.code}{": " + reason if reason else ""})'

    @staticmethod
    def code_of(answer):
        try:
            code = json.loads(answer)['code']
        except (ValueError, KeyError, TypeError):
            code = None
        if not valid_code(code):
            raise ReportError('the log server sent an answer that is not a report code')
        return code
