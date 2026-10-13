#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pulls testers' reports from the AMDLSS log receiver (receiver/README.md) and writes them where you can read them.

    python tools/amdlss-logs.py sync            every report that is not here yet, then open ~/amdlss-reports/index.html
    python tools/amdlss-logs.py fetch K7M2Q9XD  one report by its code

`sync` reads the read token from AMDLSS_LOGS_TOKEN, else from ~/.config/amdlss-logs/token (where the desktop entry
keeps it), and the address from AMDLSS_LOGS_URL, else the live receiver. It downloads each report the output folder
does not have, writes a readable report.html into each report's folder and an index.html over all of them
(tools/report_html.py does the reading; report text is escaped everywhere). `fetch` keeps its stricter habits:
address and token from the environment or --url, and it writes the same report.html next to the files.

A report is untrusted: whatever its sender put in it, it is only ever text. The code is checked before any request;
the answer is checked again with the rules the app and the server use (amdlss/reports.py); each file is written as
plain bytes, mode 0600 in folders of mode 0700, to a path resolved and checked to stay inside the report's own folder,
never over an existing file, never marked executable, and nothing in it is run, opened or unpacked. The summary names
each game, lists its files with their sizes and shows the first lines that mention an error, exception, crash or
failure (at most 20 per game), with .ini files, comments and known noise left out.

The supported reader is Linux: the desktop front end is tools/amdlss-logs-gui.sh, the token file lives under
~/.config, and the 0600/0700 modes are POSIX permissions. On Windows the script warns and still runs, but
os.chmod cannot set those modes there, so downloaded reports get the permissions of the folder they land in.
"""
import argparse
import os
import re
import sys
from pathlib import Path

# `python -I` drops the script's own folder from the path, so the project folder and this one are put there by hand
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import report_html   # noqa: E402
from amdlss import reports   # noqa: E402
from amdlss.reports import ReportError   # noqa: E402

DEFAULT_OUT = Path.home() / 'amdlss-reports'
PROBLEM = re.compile(r'error|exception|crash|fail', re.IGNORECASE)
MAX_PROBLEMS = 20
LINE_CHARS = 160
SHARED = '(shared)'


def platform_warning(name=None):
    """What a Windows run is told before anything else, or '' on the supported system (see the module text:
    the 0600/0700 modes are POSIX permissions and Windows os.chmod cannot set them). `name` replaces os.name
    (tests)."""
    if (os.name if name is None else name) != 'nt':
        return ''
    return ('this report reader is supported on Linux: on Windows it cannot set the 0600 file and 0700 folder '
            'permissions it sets there, so downloaded reports get the permissions of the folder they land in')


def make_dir(path):
    """Makes `path` (its parent exists) readable by this user only; a folder that is already there is left alone."""
    if not path.is_dir():
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)


def plan(report, root):
    """[(path, bytes)] of the files of `report` under `root`, each path resolved and checked to stay inside it, and
    free of clashes (the same name in another case, a file that is also a folder). ReportError otherwise."""
    root, files, keys, folders = Path(os.path.abspath(root)), [], set(), set()
    for name, text in report['files'].items():
        path = (root / name).resolve()
        try:
            parts = path.relative_to(root.resolve()).parts
        except ValueError:
            raise ReportError(f'{name} would be written outside the report folder')
        key = tuple(part.lower() for part in parts)
        if key in keys:
            raise ReportError(f'{name} has the same name as another file in the report')
        keys.add(key)
        folders.update(key[:depth] for depth in range(1, len(key)))
        files.append((path, text.encode('utf-8')))
    if keys & folders:
        raise ReportError('a name in the report is both a file and a folder')
    return files


def write_report(report, base, code):
    """Writes `report` into <base>/<code>/ and returns that folder. An existing folder is an error: nothing is
    overwritten."""
    folder = Path(base) / code
    if folder.exists():
        raise ReportError(f'{folder} already exists: nothing was overwritten')
    files = plan(report, folder)
    if not Path(base).is_dir():
        Path(base).mkdir(parents=True, mode=0o700)
        os.chmod(base, 0o700)
    make_dir(folder)
    root = folder.resolve()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    for path, data in files:
        for parent in reversed([p for p in path.parents if p != root and root in p.parents]):
            make_dir(parent)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
    return folder


def one_line(text):
    return ' '.join(text.split())[:LINE_CHARS]


def grouped(files):
    """{game folder: {name: text}} in report order; the files outside a game's folder are '(shared)'."""
    groups = {}
    for name, text in files.items():
        groups.setdefault(name.split('/')[0] + '/' if '/' in name else SHARED, {})[name] = text
    return dict(sorted(groups.items(), key=lambda item: item[0] != SHARED))


def summary(report, code, folder):
    """The lines printed after a fetch: one block per game with its files, sizes and the first problem lines."""
    lines = [f'Report {code} saved to {folder}', f'AMDLSS {report["version"]}: {report["game"]}']
    games = report['files'].get('games.txt')
    if games:
        lines += ['', 'games.txt:'] + [f'  {one_line(line)}' for line in games.splitlines()[:50]]
    for group, files in grouped(report['files']).items():
        lines += ['', f'== {group} ({len(files)} file{"" if len(files) == 1 else "s"})']
        found = []
        for name, text in files.items():
            lines.append(f'  {len(text.encode("utf-8")):>9} bytes  {name}')
            found += [(name, number, line) for number, line in enumerate(text.splitlines(), 1)
                      if PROBLEM.search(line) and not report_html.is_noise(name, line)]
        if found:
            lines.append(f'  lines that mention error, exception, crash or fail: {min(len(found), MAX_PROBLEMS)} of '
                         f'{len(found)}')
        lines += [f'    {name}:{number}: {one_line(line)}' for name, number, line in found[:MAX_PROBLEMS]]
    return lines


def fetch(args, environ=os.environ):
    code = args.code.strip().upper()
    if not reports.valid_code(code):
        print(f'error: {args.code!r} is not a report code (8 characters of 0-9 and A-Z, never I, L, O or U)',
              file=sys.stderr)
        return 2
    url, token = args.url or environ.get('AMDLSS_LOGS_URL', ''), environ.get('AMDLSS_LOGS_TOKEN', '')
    if not url or not token:
        print('error: set AMDLSS_LOGS_URL (or --url) and AMDLSS_LOGS_TOKEN', file=sys.stderr)
        return 2
    try:
        report = reports.ReportClient(url).fetch(code, token)
        folder = write_report(report, args.out or DEFAULT_OUT, code)
        write_report_page(report, folder, code)
    except (ReportError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    print('\n'.join(summary(report, code, folder)))
    return 0


# --- sync: every new report, readable at a glance ---------------------------------------------------------------

def token_file(environ):
    """Where the desktop entry keeps the read token: $XDG_CONFIG_HOME/amdlss-logs/token (else ~/.config/...)."""
    home = Path(environ.get('HOME') or Path.home())
    return Path(environ.get('XDG_CONFIG_HOME') or home / '.config') / 'amdlss-logs' / 'token'


def read_token(environ):
    """The read token for sync: AMDLSS_LOGS_TOKEN, else the token file, stripped; '' when neither is there. The
    value is never printed or logged anywhere."""
    token = (environ.get('AMDLSS_LOGS_TOKEN') or '').strip()
    if token:
        return token
    try:
        return token_file(environ).read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def write_html(path, text):
    """Writes one of our own generated pages: mode 0600, never executable, a symlink in the way is not followed."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(text.encode('utf-8'))
    os.chmod(path, 0o600)


def write_report_page(report, folder, code, created=None):
    """report.html next to a report's files; returns the analysis the index needs."""
    analysis = report_html.analyse(report, code, created)
    write_html(Path(folder) / 'report.html', report_html.render_report(analysis))
    return analysis


def report_from_folder(folder):
    """The report of a folder downloaded earlier, rebuilt from its text files (report.html is ours, not evidence;
    a name the receiver would refuse is skipped, a symlink is not followed)."""
    root, files = Path(folder), {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file() or path.name == 'report.html':
            continue
        name = path.relative_to(root).as_posix()
        if not reports.name_problem(name):
            files[name] = path.read_text(encoding='utf-8', errors='replace')
    system = files.get('system.txt', '')
    version = system.splitlines()[0][len('AMDLSS '):].strip() if system.startswith('AMDLSS ') else '?'
    return {'app': 'AMDLSS', 'version': version or '?', 'game': '', 'files': files}


def write_index(base, created_by_code):
    """index.html over every report folder in `base`, newest first (the receiver's arrival time when known, else
    the folder's own time); a folder without a report.html gets one. Returns the analyses, in page order."""
    base, entries = Path(base), []
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        if folder.is_symlink() or not folder.is_dir() or not reports.valid_code(folder.name):
            continue
        created = created_by_code.get(folder.name)
        analysis = report_html.analyse(report_from_folder(folder), folder.name, created)
        if not (folder / 'report.html').is_file():
            write_html(folder / 'report.html', report_html.render_report(analysis))
        entries.append((created if created is not None else folder.stat().st_mtime, analysis))
    entries.sort(key=lambda entry: entry[0], reverse=True)
    analyses = [analysis for _when, analysis in entries]
    write_html(base / 'index.html', report_html.render_index(analyses))
    return analyses


def sync(args, environ=os.environ):
    url = args.url or environ.get('AMDLSS_LOGS_URL') or reports.DEFAULT_LOGS_URL
    token = read_token(environ)
    if not token:
        print(f'error: set AMDLSS_LOGS_TOKEN or save the read token in {token_file(environ)}', file=sys.stderr)
        return 2
    base = Path(args.out or DEFAULT_OUT)
    client = reports.ReportClient(url)
    try:
        listed = client.list(token)
    except ReportError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    if not base.is_dir():
        base.mkdir(parents=True, mode=0o700)
        os.chmod(base, 0o700)
    fresh, failed = [], []
    for entry in listed:
        if (base / entry['code']).exists():
            continue   # already downloaded: nothing is ever overwritten
        try:
            report = client.fetch(entry['code'], token)
            write_report_page(report, write_report(report, base, entry['code']), entry['code'], entry['created'])
            fresh.append(entry['code'])
        except (ReportError, OSError) as error:
            failed.append(f"{entry['code']}: {error}")
    write_index(base, {entry['code']: entry['created'] for entry in listed})
    print(f'{len(fresh)} new report{"" if len(fresh) == 1 else "s"} downloaded, '
          f'{len(listed) - len(fresh) - len(failed)} already here: {base / "index.html"}')
    for line in failed:
        print(f'error: {line}', file=sys.stderr)
    return 1 if failed else 0


def main(argv=None, environ=os.environ):
    parser = argparse.ArgumentParser(prog='amdlss-logs', description="Download testers' AMDLSS reports.")
    commands = parser.add_subparsers(dest='command', required=True)
    pull = commands.add_parser('fetch', help='download one report by its code')
    pull.add_argument('code', help='the code the tester pasted: AMDLSS report <code>')
    pull.add_argument('--url', help="the receiver's address (default: $AMDLSS_LOGS_URL)")
    pull.add_argument('--out', help=f'the folder that gets one folder per report (default: {DEFAULT_OUT})')
    get_all = commands.add_parser('sync', help='download every report that is not here yet and rebuild index.html')
    get_all.add_argument('--url', help="the receiver's address (default: $AMDLSS_LOGS_URL, else the live receiver)")
    get_all.add_argument('--out', help=f'the folder that gets one folder per report (default: {DEFAULT_OUT})')
    args = parser.parse_args(argv)
    return fetch(args, environ) if args.command == 'fetch' else sync(args, environ)


if __name__ == '__main__':
    if warning := platform_warning():
        print(f'warning: {warning}', file=sys.stderr)
    sys.exit(main())
