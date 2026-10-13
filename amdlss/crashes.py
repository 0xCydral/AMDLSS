# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows' own crash record for a game, collected with its logs as crashes.txt.

When a game dies, its logs stop without saying why; the Windows Application log keeps the answer (Event ID
1000, "Application Error": faulting application, faulting module and version, exception code, fault offset,
and Event ID 1001 from Windows Error Reporting). This module reads the last 14 days of those events for the
game's own .exe files with one bounded PowerShell query and writes them in one line each. Minidumps and WER
report folders are listed by name, size and time only: their bytes never leave the PC. On Linux there is no
event log, so the file says so and points at the Proton log (`~/steam-<appid>.log`, written with
`PROTON_LOG=1`), which amdlss.logs collects beside it when present.

Everything is best effort: a missing PowerShell, a timeout or an unreadable folder becomes one line in
crashes.txt and never stops log collection. The query goes through the injectable `run(command)` seam the
running-game check uses (amdlss.helixsr.process_paths), so tests need no PowerShell.
"""
import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import host, identify

CRASH_DAYS = 14
EVENT_IDS = (1000, 1001)
MAX_EVENTS = 500
TIMEOUT = 30
MAX_EXES = 200
MAX_LISTED = 20
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The events come back as base64 of UTF-8 JSON (PowerShell's console encoding would garble accented names), the
# same shape amdlss.logs uses for the graphics card. No matching events is an empty list, never an error:
# Get-WinEvent itself errors when nothing matches, so the query is trapped.
EVENT_SCRIPT = (
    "$start=(Get-Date).AddDays(-{days});"
    "try {{ $ev=@(Get-WinEvent -FilterHashtable @{{LogName='Application'; Id=@(1000,1001); StartTime=$start}} "
    "-MaxEvents {max_events} -ErrorAction Stop) }} catch {{ $ev=@() }};"
    "$out=@(); foreach ($e in $ev) {{ $msg=''; if ($e.Message) {{ $msg=$e.Message.Substring(0, "
    "[Math]::Min(2000, $e.Message.Length)) }}; $props=@(); foreach ($p in $e.Properties) {{ $props += "
    "[string]$p.Value }}; $out += @{{time=$e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); id=$e.Id; "
    "provider=$e.ProviderName; message=$msg; props=$props}} }};"
    "$j=ConvertTo-Json -InputObject $out -Compress -Depth 4; if (-not $j) {{ $j='[]' }};"
    "[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($j))")


def _basename(path):
    return str(path or '').replace('\\', '/').rsplit('/', 1)[-1]


def _stamp(moment):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(moment))


def game_exes(game, depth=4, limit=MAX_EXES):
    """The .exe file names in the game's folder tree (each once, first spelling kept), the names a crash record
    is matched against. At most `depth` folders deep and `limit` names; links are not followed."""
    found, seen = [], set()
    roots = [root for root in (game.folder, game.exe_folder) if root]
    todo = [(root, 0) for root in dict.fromkeys(os.path.normcase(os.path.normpath(root)) for root in roots)]
    while todo and len(found) < limit:
        folder, level = todo.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith('.exe'):
                if entry.name.lower() not in seen:
                    seen.add(entry.name.lower())
                    found.append(entry.name)
                    if len(found) >= limit:
                        break
            elif level < depth and entry.is_dir(follow_symlinks=False):
                todo.append((entry.path, level + 1))
    return found


def event_command(days=CRASH_DAYS):
    """The PowerShell query for the Application log (FilterHashtable, at most MAX_EVENTS of the last `days`)."""
    script = EVENT_SCRIPT.format(days=int(days), max_events=MAX_EVENTS)
    return ['powershell', '-NoProfile', '-NonInteractive', '-Command', script]


def query_events(run=None, days=CRASH_DAYS):
    """(raw events, why not): the Event ID 1000/1001 records as the query sent them ({time, id, provider,
    message, props}), or (None, one line of reason). `run(command)` replaces the PowerShell call and returns
    its output text (tests), as in amdlss.helixsr.process_paths."""
    command = event_command(days)
    try:
        if run is not None:
            out = run(command)
        else:
            done = subprocess.run(command, capture_output=True, timeout=TIMEOUT, creationflags=NO_WINDOW,
                                  env=host.clean_env())
            if done.returncode != 0 and not done.stdout.strip():
                return None, f'PowerShell ended with exit {done.returncode}'
            out = done.stdout.decode('utf-8', errors='replace')
        parsed = json.loads(base64.b64decode(str(out).strip()).decode('utf-8'))
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:   # binascii.Error is a ValueError
        return None, str(error) or type(error).__name__
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        return None, 'PowerShell sent an answer that is not an event list'
    return parsed, None


def _message(patterns, message):
    for pattern in patterns:
        match = re.search(pattern, message or '', re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ''


def parse_event(raw, exe_names):
    """One raw event that names one of `exe_names`, as {when, exe, module, version, exception, offset, event,
    path}, or None. Event 1000's replacement strings (props) hold the fields in a fixed order that does not
    depend on the Windows language; the message text (English or Spanish) and WER's P1/P4 are the fallback."""
    message, props = str(raw.get('message') or ''), [str(value) for value in raw.get('props') or []]
    app = module = version = exception = offset = app_path = module_path = ''
    if str(raw.get('id')) == '1000' and len(props) >= 8 and props[0].lower().endswith('.exe'):
        app, module, version, exception, offset = props[0], props[3], props[4], props[6], props[7]
        app_path, module_path = (props[10] if len(props) > 10 else ''), (props[11] if len(props) > 11 else '')
    else:
        app = _message((r'Faulting application name:\s*([^,\r\n]+)', 'Nombre de la aplicación con errores'
                        r':\s*([^,\r\n]+)', r'\bP1:\s*(\S+\.exe)'), message)
        module = _message((r'Faulting module name:\s*([^,\r\n]+)', r'Nombre del módulo con errores:\s*([^,\r\n]+)',
                           r'\bP4:\s*(\S+)'), message)
        exception = _message((r'Exception code:\s*(\S+)', r'Código de excepción:\s*(\S+)'), message)
        offset = _message((r'Fault offset:\s*(\S+)', r'Desplazamiento de errores:\s*(\S+)'), message)
        app_path = _message((r'Faulting application path:\s*(.+)', r'Ruta de la aplicación con errores:\s*(.+)'),
                            message)
        module_path = _message((r'Faulting module path:\s*(.+)', r'Ruta del módulo con errores:\s*(.+)'), message)
    wanted = {name.lower() for name in exe_names}
    hit = next((name for name in (_basename(app), _basename(app_path)) if name.lower() in wanted), '')
    if not hit and not app:
        known = next((name for name in exe_names
                      if re.search(r'(?<![A-Za-z0-9_.-])' + re.escape(name) + r'(?![A-Za-z0-9_.-])',
                                   message, re.IGNORECASE)), '')
        hit, app = known, known
    if not hit:
        return None
    if ('\\' in module or '/' in module) and not module_path:
        module_path = module
    return {'when': str(raw.get('time') or ''), 'exe': hit, 'module': _basename(module) or 'unknown',
            'version': version, 'exception': exception, 'offset': offset,
            'event': str(raw.get('id') or ''), 'path': module_path}


def crash_entries(exe_names, run=None, days=CRASH_DAYS):
    """(entries, why not): the parsed crash records for `exe_names`, oldest first."""
    raw, why = query_events(run, days)
    if raw is None:
        return [], why
    entries = [entry for entry in (parse_event(item, exe_names) for item in raw) if entry]
    return sorted(entries, key=lambda entry: entry['when']), None


def _clean(value):
    return ' '.join(str(value or '').replace('|', ' ').split())


def format_entry(entry):
    """One crash record as one crashes.txt line, the shape tools/report_html.py reads back."""
    parts = [_clean(entry['when']), _clean(entry['exe']),
             f"faulting module: {_clean(entry['module']) or 'unknown'}",
             f"module version: {_clean(entry['version'])}", f"exception: {_clean(entry['exception'])}",
             f"offset: {_clean(entry['offset'])}", f"Event ID: {_clean(entry['event'])}",
             f"module path: {_clean(entry['path'])}"]
    return ' | '.join(parts)


def list_crash_dumps(exe_names, folder=None, windows=None):
    """[(name, size, time)] of the minidumps for `exe_names` in %LOCALAPPDATA%\\CrashDumps, newest first. The
    dumps themselves are never read. `folder` replaces the place (tests)."""
    if folder is None:
        if not host.is_windows(windows):
            return []
        folder = host.data_home(True) / 'CrashDumps'
    try:
        dumps = [entry for entry in os.scandir(folder)
                 if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith('.dmp')
                 and any(entry.name.lower().startswith(name.lower()) for name in exe_names)]
        dumps.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
        return [(entry.name, entry.stat().st_size, _stamp(entry.stat().st_mtime))
                for entry in dumps[:MAX_LISTED]]
    except OSError:
        return []


def list_wer_reports(exe_names, folders=None, windows=None):
    """[(name, time)] of the Windows Error Reporting AppCrash folders for `exe_names`, newest first, listed
    only. `folders` replaces the two WER places (tests)."""
    if folders is None:
        if not host.is_windows(windows):
            return []
        wer = host.data_home(True) / 'Microsoft' / 'Windows' / 'WER'
        folders = [wer / 'ReportArchive', wer / 'ReportQueue']
    found = []
    for folder in folders:
        try:
            found += [entry for entry in os.scandir(folder)
                      if entry.is_dir(follow_symlinks=False)
                      and any(entry.name.lower().startswith('appcrash_' + name.lower()) for name in exe_names)]
        except OSError:
            continue
    try:
        found.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
        return [(entry.name, _stamp(entry.stat().st_mtime)) for entry in found[:MAX_LISTED]]
    except OSError:
        return []


def steam_appid(game):
    """The game's Steam app id from its library manifest, or None (amdlss.identify reads the same files)."""
    for folder in (game.folder, game.exe_folder):
        if folder:
            found = identify.steam_manifest(folder)
            if found:
                return found[0]
    return None


def proton_log_path(game, home=None):
    """~/steam-<appid>.log (what Proton writes with PROTON_LOG=1) when it exists, else None. `home` replaces
    the home folder (tests)."""
    appid = steam_appid(game)
    if appid is None:
        return None
    path = Path(home or Path.home()) / f'steam-{appid}.log'
    return path if path.is_file() else None


def crashes_text(game, windows=None, run=None, dumps_dir=None, wer_folders=None, home=None, days=CRASH_DAYS):
    """crashes.txt for one game (unredacted: the caller replaces the user name) (see the module text)."""
    if not host.is_windows(windows):
        appid, proton = steam_appid(game), proton_log_path(game, home)
        lines = [f'Windows crashes for {game.name} (not collected on Linux)',
                 'Windows event log records exist only on Windows. On Linux the Proton log is the crash '
                 'evidence:']
        if proton:
            lines.append(f'the Proton log for Steam app {appid} is included as proton.log.')
        elif appid is not None:
            lines.append(f'no Proton log was found at ~/steam-{appid}.log. Add PROTON_LOG=1 to the game\'s '
                         'Steam launch options, run the game once, and send logs again.')
        else:
            lines.append('AMDLSS could not tell this game\'s Steam app id from its folder, so no Proton log '
                         'was looked for. Add PROTON_LOG=1 to the game\'s Steam launch options, run the game '
                         'once, and send logs again.')
        return '\n'.join(lines) + '\n'
    exes = game_exes(game)
    lines = [f'Windows crashes for {game.name} (Application log, last {int(days)} days, '
             f'Event ID {EVENT_IDS[0]} and {EVENT_IDS[1]})',
             f"Game executables checked: {', '.join(exes) if exes else '(none found in the game folder)'}", '']
    if not exes:
        lines.append('No .exe file was found in the game folder, so the event log was not searched.')
    else:
        entries, why = crash_entries(exes, run, days)
        if why:
            lines.append(f'The Windows event log could not be read: {why}. Collection continued without it.')
        elif entries:
            lines += [format_entry(entry) for entry in entries]
        else:
            lines.append("No matching crash was recorded for this game's executables.")
    dumps = list_crash_dumps(exes, dumps_dir, windows) if exes else []
    lines += ['', 'Crash dumps listed only (not sent):']
    lines += [f'  {name}  {size} bytes  {when}' for name, size, when in dumps] or \
        ["  (none found for this game's executables)"]
    reports = list_wer_reports(exes, wer_folders, windows) if exes else []
    lines += ['', 'Windows Error Reporting folders listed only (not sent):']
    lines += [f'  {name}  {when}' for name, when in reports] or ["  (none found for this game's executables)"]
    return '\n'.join(lines) + '\n'


def crashes_evidence(game, redact, windows=None, run=None, dumps_dir=None, wer_folders=None, home=None,
                     days=CRASH_DAYS):
    """('crashes.txt', bytes) for Collect logs and Send logs, the user name already replaced. A failure to even
    build the text becomes the file's one line: collecting logs never stops for crash evidence."""
    try:
        text = crashes_text(game, windows, run, dumps_dir, wer_folders, home, days)
    except Exception as error:   # noqa: BLE001 (best effort by contract: see the module text)
        text = (f'Windows crashes for {game.name}\n'
                f'The Windows event log could not be read: {error}. Collection continued without it.\n')
    return 'crashes.txt', redact(text).encode('utf-8')
