#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pulls a tester's report from the AMDLSS log receiver (receiver/README.md) and writes it where you can read it.

    AMDLSS_LOGS_URL=https://amdlss-reports.<account>.workers.dev AMDLSS_LOGS_TOKEN=<READ_TOKEN> \\
        python tools/amdlss-logs.py fetch K7M2Q9XD

A report is untrusted: whatever its sender put in it, it is only ever text. The code is checked before any request;
the answer is checked again with the rules the app and the server use (amdlss/reports.py); each file is written as
plain bytes, mode 0600 in folders of mode 0700, to a path resolved and checked to stay inside the report's own folder,
never over an existing file, never marked executable, and nothing in it is run, opened or unpacked. The summary names
each game, lists its files with their sizes and shows the first lines that mention an error, exception, crash or
failure (at most 20 per game).
"""
import argparse
import os
import re
import sys
from pathlib import Path

# `python -I` drops the script's own folder from the path, so the project folder is put there by hand
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amdlss import reports   # noqa: E402
from amdlss.reports import ReportError   # noqa: E402

DEFAULT_OUT = Path.home() / 'amdlss-reports'
PROBLEM = re.compile(r'error|exception|crash|fail', re.IGNORECASE)
MAX_PROBLEMS = 20
LINE_CHARS = 160
SHARED = '(shared)'


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
            found += [(name, number, line) for number, line in enumerate(text.splitlines(), 1) if PROBLEM.search(line)]
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
    except (ReportError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    print('\n'.join(summary(report, code, folder)))
    return 0


def main(argv=None, environ=os.environ):
    parser = argparse.ArgumentParser(prog='amdlss-logs', description="Download a tester's AMDLSS report.")
    commands = parser.add_subparsers(dest='command', required=True)
    pull = commands.add_parser('fetch', help='download one report by its code')
    pull.add_argument('code', help='the code the tester pasted: AMDLSS report <code>')
    pull.add_argument('--url', help="the receiver's address (default: $AMDLSS_LOGS_URL)")
    pull.add_argument('--out', help=f'the folder that gets one folder per report (default: {DEFAULT_OUT})')
    args = parser.parse_args(argv)
    return fetch(args, environ)


if __name__ == '__main__':
    sys.exit(main())
