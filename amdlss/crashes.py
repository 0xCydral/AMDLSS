# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows' own crash record for a game, collected with its logs as crashes.txt.

When a game dies, its logs stop without saying why; the Windows Application log keeps the answer (Event ID
1000, "Application Error": faulting application, faulting module and version, exception code, fault offset,
Event ID 1001 from Windows Error Reporting and Event ID 1002, "Application Hang"). This module reads the last 14
days of those events for the game's own .exe files with one bounded PowerShell query and writes them in one line
each. Minidumps and WER report folders are listed by name, size and time only: their bytes never leave the PC. A
game that ends with no crash record and no log line looks like a normal close, so crashes.txt also says whether the
game was still running when it was collected, and for an Unreal Engine game it lists the project's Saved\\Crashes
folders and adds the tail of its newest Saved\\Logs file (unreal-latest.log). On Linux there is no
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
from datetime import datetime
from pathlib import Path

from . import games as gamelist, helixsr, host, identify

CRASH_DAYS = 14
EVENT_IDS = (1000, 1001, 1002)
MAX_EVENTS = 500
TIMEOUT = 30
MAX_EXES = 200
MAX_LISTED = 20
UNREAL_LOG = 'unreal-latest.log'
UNREAL_LOG_LINES = 200
UNREAL_LOG_BYTES = 1 << 20   # read this much of the log's end, then keep its last lines
UNREAL_LOG_CHARS = 200_000
HELPER_EXES = ('crashreport', 'crashhandler')   # Unreal's crash reporter stays open after a crash
MERGE_SECONDS = 10   # Windows writes Event 1001 about 2 s after the 1000 for the same crash (seen on a real PC)
# WER's own event name -> (P number of the exception code, P number of the offset). Both are in the problem
# signature of Event 1001, but BEX64 swaps them (a real BEX64 record: P7 0000000000032f8e, P8 c0000409).
WER_CRASH = {'appcrash': (7, 8), 'bex64': (8, 7), 'bex': (8, 7)}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The events come back as base64 of UTF-8 JSON (PowerShell's console encoding would garble accented names), the
# same shape amdlss.logs uses for the graphics card. No matching events is an empty list, never an error:
# Get-WinEvent itself errors when nothing matches, so the query is trapped.
EVENT_SCRIPT = (
    "$start=(Get-Date).AddDays(-{days});"
    "try {{ $ev=@(Get-WinEvent -FilterHashtable @{{LogName='Application'; Id=@(1000,1001,1002); StartTime=$start}} "
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
    is matched against. At most `depth` folders deep and `limit` names; links are not followed. A drive root
    listed as the folder is never walked: only the .exe folder's tree counts, so no report names every
    executable on a drive."""
    found, seen = [], set()
    roots = [root for root in (game.folder, game.exe_folder) if root and not gamelist.is_root_folder(root)]
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


def _wer_fields(props):
    """The fields of an Event 1001 from its replacement strings, which do not depend on the Windows language:
    [0] fault bucket, [1] bucket type, [2] event name, [3] response, [4] cab id, [5..14] P1..P10 (the order of
    the event text's "Problem signature"). P1 is the application name; APPCRASH and BEX64 add P4 the faulting
    module, P5 its version and the exception code and offset (WER_CRASH); AppHang* has no faulting module.
    None when P1 is not an .exe name."""
    def p(number):
        return props[4 + number] if len(props) > 4 + number else ''

    name = props[2] if len(props) > 2 else ''
    if not p(1).lower().endswith('.exe'):
        return None
    found = {'app': p(1), 'name': name, 'hang': name.lower().startswith('apphang')}
    if name.lower() in WER_CRASH:
        code, where = WER_CRASH[name.lower()]
        found.update(module=p(4), version=p(5), exception=p(code), offset=p(where))
    return found


def parse_event(raw, exe_names):
    """One raw event that names one of `exe_names`, as {when, exe, module, version, exception, offset, event,
    path} (plus `name`, WER's event name, for an Event 1001 whose replacement strings were read), or None.
    The replacement strings (props) hold the fields in a fixed order that does not depend on the Windows
    language (Event 1000: _wer_fields for 1001); the message text (English or Spanish) is the fallback."""
    message, props = str(raw.get('message') or ''), [str(value) for value in raw.get('props') or []]
    app = module = version = exception = offset = app_path = module_path = name = ''
    wer = _wer_fields(props) if str(raw.get('id')) == '1001' else None
    if wer:
        app, name = wer['app'], wer['name']
        module, version, exception, offset = (wer.get(key, '') for key in ('module', 'version', 'exception', 'offset'))
    elif str(raw.get('id')) == '1000' and len(props) >= 8 and props[0].lower().endswith('.exe'):
        app, module, version, exception, offset = props[0], props[3], props[4], props[6], props[7]
        app_path, module_path = (props[10] if len(props) > 10 else ''), (props[11] if len(props) > 11 else '')
    elif str(raw.get('id')) == '1002' and props and props[0].lower().endswith('.exe'):
        # Application Hang: [0] exe name, [1] version, then (Windows 7 to 10) process id, start and end time, path,
        # report id; newer Windows names AppName, AppVersion, ExeFileName (the path), HangType. The path is found
        # by its shape because the position differs.
        app = props[0]
        app_path = next((value for value in props[2:] if re.search(r'[\\/]', value) and value.lower().endswith('.exe')),
                        '')
    else:
        app = _message((r'Faulting application name:\s*([^,\r\n]+)', 'Nombre de la aplicación con errores'
                        r':\s*([^,\r\n]+)', r'\bP1:[ \t]*(\S+\.exe)'), message)
        module = _message((r'Faulting module name:\s*([^,\r\n]+)', r'Nombre del módulo con errores:\s*([^,\r\n]+)'),
                          message)
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
    if str(raw.get('id')) == '1002':
        name = 'AppHang'
    if ('\\' in module or '/' in module) and not module_path:
        module_path = module
    entry = {'when': str(raw.get('time') or ''), 'exe': hit, 'module': _basename(module) or 'unknown',
             'version': version, 'exception': exception, 'offset': offset,
             'event': str(raw.get('id') or ''), 'path': module_path}
    if name:
        entry['name'] = name
    return entry


def _seconds(entry):
    try:
        return datetime.strptime(entry['when'], '%Y-%m-%d %H:%M:%S').timestamp()
    except ValueError:
        return None


def _repeats_a_1000(entry, entries):
    """True for an Event 1001 (not a hang) that Windows wrote for the Event 1000 of the same exe a moment
    before or after: one crash, shown once, with the 1000's richer record."""
    if entry['event'] != '1001' or entry.get('name', '').lower().startswith('apphang') or _seconds(entry) is None:
        return False
    return any(other['event'] == '1000' and other['exe'].lower() == entry['exe'].lower()
               and _seconds(other) is not None and abs(_seconds(other) - _seconds(entry)) <= MERGE_SECONDS
               for other in entries)


def entries_from(raw, exe_names):
    """The parsed crash records for `exe_names` out of raw events already queried, oldest first, an Event 1001
    that repeats an Event 1000 left out."""
    entries = [entry for entry in (parse_event(item, exe_names) for item in raw or []) if entry]
    entries = [entry for entry in entries if not _repeats_a_1000(entry, entries)]
    return sorted(entries, key=lambda entry: entry['when'])


def crash_entries(exe_names, run=None, days=CRASH_DAYS):
    """(entries, why not): the parsed crash records for `exe_names`, oldest first."""
    raw, why = query_events(run, days)
    return ([], why) if raw is None else (entries_from(raw, exe_names), None)


def _clean(value):
    return ' '.join(str(value or '').replace('|', ' ').split())


def format_entry(entry):
    """One crash record as one crashes.txt line, the shape tools/report_html.py reads back."""
    parts = [_clean(entry['when']), _clean(entry['exe']),
             f"faulting module: {_clean(entry['module']) or 'unknown'}",
             f"module version: {_clean(entry['version'])}", f"exception: {_clean(entry['exception'])}",
             f"offset: {_clean(entry['offset'])}", f"Event ID: {_clean(entry['event'])}",
             f"module path: {_clean(entry['path'])}"]
    if entry.get('name'):
        parts.append(f"event name: {_clean(entry['name'])}")
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


# --- was the game still running? ----------------------------------------------------------------------------------

def running_text(game, windows=None, run=None, processes=None, proc=None):
    """'yes', 'no' or 'unknown': whether a process of the game was running when this was collected. It is the
    check Install uses (helixsr.running_from), except that Unreal's crash reporter, which stays open after a crash,
    does not count. `processes` is helixsr.process_paths() already asked (one query for a whole report).
    A drive root listed as the folder would match every process on the drive: only the .exe folder counts."""
    folder = game.exe_folder if gamelist.is_root_folder(game.folder) else (game.folder or game.exe_folder)
    if not folder:
        return 'unknown'
    try:
        if host.is_windows(windows):
            paths = helixsr.process_paths(run) if processes is None else processes
            if not paths:   # a PowerShell that failed, not a PC with no processes
                return 'unknown'
            return 'yes' if any(helixsr.inside(path, folder, True) and not _basename(path).lower().startswith(HELPER_EXES)
                                for path in paths) else 'no'
        return 'yes' if helixsr.running_from(folder, False, proc=proc) else 'no'
    except OSError:
        return 'unknown'


# --- Unreal Engine games ------------------------------------------------------------------------------------------
# An Unreal game that dies handles it itself: Windows records nothing, but the engine keeps a crash folder and a log.
# UE puts them in Saved\ under <Project>, named by the exe's place (...\<Project>\Binaries\Win64\<exe>): in
# %LOCALAPPDATA%\<Project>\Saved for a shipped game, in <game folder>\<Project>\Saved for a development build.

def unreal_project(game, windows=None):
    """(project name, project folder) from the exe folder `...\\<Project>\\Binaries\\Win64`, else ('', '')."""
    pathmod = host.path_module(windows)
    for folder in (game.exe_folder, game.folder):
        path = str(folder or '').rstrip('\\/')
        while path:
            head, tail = pathmod.split(path)
            if tail.lower() == 'binaries' and pathmod.basename(head) and pathmod.basename(head).lower() != 'engine':
                return pathmod.basename(head), head
            if not tail or head == path:
                break
            path = head.rstrip('\\/')
    return '', ''


def local_appdata(game, windows=None):
    """%LOCALAPPDATA%; on Linux the one inside the game's Proton prefix when the game sits in a Steam library."""
    if host.is_windows(windows):
        return host.data_home(True)
    library, steamapps, _game = (game.folder or '').partition('/steamapps/')
    appid = steam_appid(game)
    if appid is None or not steamapps:
        return None
    local = Path(library) / 'steamapps' / 'compatdata' / str(appid) / 'pfx' / 'drive_c' / 'users' / 'steamuser' \
        / 'AppData' / 'Local'
    return local if local.is_dir() else None


def unreal_saved_dirs(game, windows=None, local=None):
    """(project, [Saved folders that exist]) for an Unreal game; ('', []) for any other. `local` replaces
    %LOCALAPPDATA% (tests)."""
    project, root = unreal_project(game, windows)
    if not project:
        return '', []
    base = local_appdata(game, windows) if local is None else local
    places = ([Path(base) / project / 'Saved'] if base else []) + [Path(root) / 'Saved']
    return project, [place for place in places if place.is_dir()]


def _tree(path, limit=200):
    """(files, bytes) in a folder, looking at no more than `limit` files."""
    files = size = 0
    for root, _folders, names in os.walk(path):
        for name in names:
            try:
                size += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
            files += 1
            if files >= limit:
                return files, size
    return files, size


def list_unreal_crashes(saved_dirs, days=CRASH_DAYS):
    """[(time, name, files, bytes, folder)] of what Saved\\Crashes holds from the last `days`, newest first. Only
    names, sizes and times: a dump in there is never read."""
    cutoff, found = time.time() - days * 86400, []
    for saved in saved_dirs:
        try:
            entries = list(os.scandir(Path(saved) / 'Crashes'))
        except OSError:
            continue
        for entry in entries:
            try:
                when = entry.stat().st_mtime
                files, size = _tree(entry.path) if entry.is_dir(follow_symlinks=False) else (1, entry.stat().st_size)
            except OSError:
                continue
            if when >= cutoff:
                found.append((when, entry.name, files, size, str(Path(saved) / 'Crashes')))
    return sorted(found, reverse=True)[:MAX_LISTED]


def unreal_log_path(game, windows=None, local=None):
    """The newest *.log in the project's Saved\\Logs folders, or None (not an Unreal game, or no log)."""
    newest, best = None, -1.0
    for saved in unreal_saved_dirs(game, windows, local)[1]:
        try:
            for entry in os.scandir(Path(saved) / 'Logs'):
                if entry.is_file() and entry.name.lower().endswith('.log') and entry.stat().st_mtime > best:
                    newest, best = Path(entry.path), entry.stat().st_mtime
        except OSError:
            continue
    return newest


def unreal_evidence(game, redact, windows=None, local=None):
    """[(unreal-latest.log, bytes)]: the last UNREAL_LOG_LINES lines of the newest Unreal log, redacted and cut to
    UNREAL_LOG_CHARS; [] when there is none. It says in its first line which file it is and when it was written."""
    path = unreal_log_path(game, windows, local)
    if path is None:
        return []
    try:
        with open(path, 'rb') as stream:
            size = path.stat().st_size
            stream.seek(max(0, size - UNREAL_LOG_BYTES))
            text = stream.read().decode('utf-8-sig', errors='replace')
        written = _stamp(path.stat().st_mtime)
    except OSError:
        return []
    lines = text.splitlines()[-UNREAL_LOG_LINES:]
    body = '\n'.join(lines)[-UNREAL_LOG_CHARS:]
    head = f'[AMDLSS: the last {len(lines)} lines of {path.name}, written {written}]\n'
    return [(UNREAL_LOG, redact(head + body + '\n').encode('utf-8'))]


def unreal_lines(game, windows=None, local=None, days=CRASH_DAYS):
    """The crashes.txt lines for an Unreal game (none for any other): its crash folders, one machine-readable line
    each (tools/report_html.py reads them back)."""
    project, saved = unreal_saved_dirs(game, windows, local)
    if not project:
        return []
    lines = ['', f'Unreal Engine project {project}: crash folders of the last {int(days)} days, listed only (not sent):']
    crashes = list_unreal_crashes(saved, days)
    lines += [f'Unreal Engine crash folder | {_stamp(when)} | {_clean(name)} | {files} files, {size} bytes | {_clean(where)}'
              for when, name, files, size, where in crashes] or ['  (none found)']
    lines.append(f'The newest Saved\\Logs file is included as {UNREAL_LOG}.' if unreal_log_path(game, windows, local)
                 else f'No Saved\\Logs file was found in {len(saved)} Saved folder(s).')
    return lines


def proton_log_path(game, home=None):
    """~/steam-<appid>.log (what Proton writes with PROTON_LOG=1) when it exists, else None. `home` replaces
    the home folder (tests)."""
    appid = steam_appid(game)
    if appid is None:
        return None
    path = Path(home or Path.home()) / f'steam-{appid}.log'
    return path if path.is_file() else None


def crashes_text(game, windows=None, run=None, dumps_dir=None, wer_folders=None, home=None, days=CRASH_DAYS,
                 events=None, processes=None, local=None, proc=None):
    """crashes.txt for one game (unredacted: the caller replaces the user name) (see the module text).
    `events` is a query_events() answer and `processes` a helixsr.process_paths() answer, shared across games, so one
    report runs PowerShell once for each. `local` replaces %LOCALAPPDATA% and `proc` /proc (tests)."""
    running = f'Running when collected: {running_text(game, windows, run, processes, proc)}'
    if not host.is_windows(windows):
        appid, proton = steam_appid(game), proton_log_path(game, home)
        lines = [f'Windows crashes for {game.name} (not collected on Linux)', running,
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
        return '\n'.join(lines + unreal_lines(game, windows, local, days)) + '\n'
    exes = game_exes(game)
    lines = [f'Windows crashes for {game.name} (Application log, last {int(days)} days, '
             f"Event ID {', '.join(str(number) for number in EVENT_IDS[:-1])} and {EVENT_IDS[-1]})",
             f"Game executables checked: {', '.join(exes) if exes else '(none found in the game folder)'}", running, '']
    if not exes:
        lines.append('No .exe file was found in the game folder, so the event log was not searched.')
    else:
        if events is None:
            entries, why = crash_entries(exes, run, days)
        else:
            raw, why = events
            entries = [] if raw is None else entries_from(raw, exes)
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
    return '\n'.join(lines + unreal_lines(game, windows, local, days)) + '\n'


def crashes_evidence(game, redact, windows=None, run=None, dumps_dir=None, wer_folders=None, home=None,
                     days=CRASH_DAYS, events=None, processes=None, local=None, proc=None):
    """('crashes.txt', bytes) for Collect logs and Send logs, the user name already replaced. A failure to even
    build the text becomes the file's one line: collecting logs never stops for crash evidence."""
    try:
        text = crashes_text(game, windows, run, dumps_dir, wer_folders, home, days, events, processes, local, proc)
    except Exception as error:   # noqa: BLE001 (best effort by contract: see the module text)
        text = (f'Windows crashes for {game.name}\n'
                f'The Windows event log could not be read: {error}. Collection continued without it.\n')
    return 'crashes.txt', redact(text).encode('utf-8')
