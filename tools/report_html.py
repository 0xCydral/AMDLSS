# SPDX-License-Identifier: GPL-2.0-or-later
"""A downloaded report read in plain words: the parsing behind tools/amdlss-logs.py's report.html and index.html.

Report text is untrusted (whatever a sender put in a log arrives here), so the rule of this file is simple: the
parsing works on plain strings, and the rendering escapes every byte of report text it shows (`esc`), puts no
report text in an attribute, and builds no link out of report text. The pages are self-contained (inline style, no
script, nothing fetched), so opening one can only ever show text.
"""
import html
import re
import time

from amdlss.logs import OPTI_NAMES, SESSION_LOG

# Lines that say nothing on any machine: Streamline looking for an NVIDIA updater, fakenvapi without latency data.
NOISE = ('nvngx_update.exe', 'readServerManifest', 'getDriverPath', 'GetLatency: Not enough data',
         'NVAPI_NO_IMPLEMENTATION')
# How a locked game file reads in the installer's output, in English and in a Spanish Windows' words.
LOCKED = ('permissionerror', 'errno 13', 'winerror 5', 'access is denied', 'acceso denegado', 'permission denied')
HEADING = re.compile(r'^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (install|update|remove)(?: \(([^)]+)\))? (.*?) ===$')
EXCEPTION = re.compile(r'[\w.]+(Error|Exception)\s*:')
CODE = re.compile(r'[0-9A-HJKMNP-TV-Z]{8}')


def esc(text):
    """Report text for a page: every character that could start markup is shown, never obeyed."""
    return html.escape(str(text), quote=True)


def is_noise(name, line):
    """True for a line that says nothing: any line of an .ini file, a `;`/`#` comment, or known chatter (NOISE)."""
    if name.lower().endswith('.ini'):
        return True
    if line.strip().startswith((';', '#')):
        return True
    return any(marker in line for marker in NOISE)


def plain_cause(text):
    """The locked-file sentence when `text` says a game file could not be replaced, else None. The file named is
    the DLL the text names (its base name only)."""
    if not any(marker in text.lower() for marker in LOCKED):
        return None
    match = re.search(r'["\']([^"\']+\.dll)["\']', text, re.IGNORECASE) or re.search(r'[\w.-]+\.dll', text)
    held = match.group(1) if match and match.lastindex else (match.group(0) if match else 'a game file')
    held = held.replace('\\', '/').rsplit('/', 1)[-1]
    return (f'The game was still running (or another program held {held}), so AMDLSS could not replace it. '
            'Close the game and try again.')


def explain(line):
    """One plain sentence for a known pattern in `line`, else None."""
    low = line.lower()
    if 'allocator->reset()' in low or 'ffxcreatecontextdescframegenerationswapchainversiondx12' in low:
        return ('Frame generation (OptiScaler) failed after the game rebuilt its window/swapchain (resolution, '
                'fullscreen or Alt+Tab change). Lead, not confirmed.')
    return plain_cause(line)


# --- the shared files --------------------------------------------------------------------------------------------

def parse_system(text):
    """[(label, value)] from system.txt: the OS, each GPU and its driver, the CPU and the RAM, in file order."""
    rows = []
    for line in (text or '').splitlines():
        if line.startswith('--- '):
            break
        if line.startswith(('Windows:', 'Linux:')):
            rows.append(('OS', line.strip()))
        elif line.startswith('GPU'):
            label, _, body = line.partition(':')
            body = body.strip()
            rows.append((label.strip() or 'GPU', body.split(',')[0].strip()))
            version = re.search(r', driver ([^,]+)', body)
            date = re.search(r'driver date (\S+)', body)
            if version:
                rows.append(('Driver', version.group(1).strip() + (f' ({date.group(1)})' if date else '')))
        elif line.startswith(('CPU:', 'RAM:')):
            label, _, value = line.partition(':')
            rows.append((label, value.strip()))
    return rows


def parse_games(text):
    """The games of games.txt: folder in the report, name, install mode, HelixSR version, frame generation
    (True/False, or None when the line does not say)."""
    games = []
    for line in (text or '').splitlines():
        parts = [part.strip() for part in line.split(' | ')]
        if len(parts) < 2 or not parts[0]:
            continue
        game = {'folder': parts[0].rstrip('/'), 'name': parts[1] or parts[0].rstrip('/'),
                'install': 'unknown', 'helixsr': 'unknown', 'framegen': None}
        for part in parts[2:]:
            for prefix, key in (('install: ', 'install'), ('HelixSR: ', 'helixsr')):
                if part.startswith(prefix):
                    game[key] = part[len(prefix):]
            if part.startswith('frame generation: '):
                game['framegen'] = part.endswith('on')
        games.append(game)
    return games


def date_of(created, system_text):
    """When the report arrived (the receiver's time), else when the tester collected it, else unknown."""
    if created:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(created))
    for line in (system_text or '').splitlines():
        if line.startswith('Collected: '):
            return line[len('Collected: '):].strip()
    return 'unknown date'


# --- one game -----------------------------------------------------------------------------------------------------

def cause_of(body):
    """One line of cause out of an action's output: the last exception line (the end of a traceback), an
    `error:` line, else the last line printed."""
    lines = [line.strip() for line in body if line.strip()]
    for line in reversed(lines):
        if EXCEPTION.match(line):
            return line
        if line.lower().startswith('error:'):
            return line[len('error:'):].strip()
    return lines[-1] if lines else ''


def parse_actions(text):
    """What AMDLSS did, from amdlss-install.log: one entry per `=== <date> <action> <game> ===` block, with its
    outcome (installed, removed, restored or failed) and, for a failure, one plain sentence of cause."""
    actions, current = [], None
    for line in (text or '').splitlines():
        match = HEADING.match(line.strip())
        if match:
            current = {'when': match.group(1), 'action': match.group(2), 'route': match.group(3) or '',
                       'game': match.group(4), 'body': []}
            actions.append(current)
        elif current is not None:
            current['body'].append(line)
    for action in actions:
        body = action.pop('body')
        joined = '\n'.join(body)
        failed = ('Traceback (most recent call last)' in joined or plain_cause(joined) is not None
                  or any(line.strip().lower().startswith('error:') for line in body))
        if failed:
            action['outcome'], action['cause'] = 'failed', plain_cause(joined) or cause_of(body)
        elif action['action'] == 'remove':
            action['outcome'] = 'restored' if 'restor' in joined.lower() else 'removed'
            action['cause'] = ''
        else:
            action['outcome'], action['cause'] = 'installed', ''
    return actions


def problem_lines(name, text):
    """The real-error lines of one file: OptiScaler's `[E]` lines, HelixSR log lines that mention an error or a
    failure, and traceback last lines anywhere, with the noise taken out first."""
    base = name.rsplit('/', 1)[-1].lower()
    for line in text.splitlines():
        if not line.strip() or is_noise(name, line):
            continue
        if 'optiscaler' in base and base.endswith('.log') and '[E]' in line:
            yield line
        elif base.startswith('helixsr') and base.endswith('.log') and re.search(r'error|fail', line, re.IGNORECASE):
            yield line
        elif EXCEPTION.match(line.strip()):
            yield line


def problems_in(files):
    """[{'text', 'count', 'why'}]: the problem lines of a game's files, each distinct line once with how often
    it appears (the leading `[time] [E]` stamps do not tell lines apart) and its plain-words reading."""
    found = {}
    for name in sorted(files):
        for line in problem_lines(name, files[name]):
            key = ' '.join(re.sub(r'^(\[[^\]]*\]\s*)+', '', line.strip()).split())
            entry = found.setdefault(key, {'text': key, 'count': 0, 'why': explain(key)})
            entry['count'] += 1
    return list(found.values())


def helix_evidence(files):
    """What HelixSR's own log proves: the `create:` line (render to output size), the frames of every
    `destroy after N frames`, whether any upscaler closed cleanly, and a log cut off by NUL bytes."""
    texts = [text for name, text in files.items()
             if name.rsplit('/', 1)[-1].lower().startswith('helixsr') and name.lower().endswith('.log')]
    create, frames = None, []
    for text in texts:
        for line in text.splitlines():
            if 'create:' in line:
                create = line.strip()
        frames += [int(count) for count in re.findall(r'destroy after (\d+) frames', text)]
    return {'present': bool(texts), 'create': create, 'frames': sum(frames) if frames else None,
            'clean': bool(frames), 'nul': any('\x00' in text for text in texts)}


# --- session timelines -------------------------------------------------------------------------------------------
# One run is the current logs of a game, plus one run per kept history snapshot. The logs carry clock times only
# (`[14:14:17.315930]`), never a date, so a timeline speaks in clock times and the report date (system.txt's
# Collected line, or the receiver's arrival time) is what ties a Windows crash record to a run.

CLOCK = re.compile(r'\[(\d\d:\d\d:\d\d)(?:\.\d+)?\]')
FG_DISPATCH = re.compile(r'(?:FSRFG|XeFG|OptiFG)\w*::Dispatch')
FG_CALLBACK = re.compile(r'(?:FSRFG|XeFG|OptiFG)\w*::DispatchCallback')
FG_ACTIVATE = re.compile(r'(?:FSRFG|XeFG|OptiFG)\w*::Activate\b')
FG_DEACTIVATE = re.compile(r'(?:FSRFG|XeFG|OptiFG)\w*::Deactivate\b')
VRAM_PEAK = re.compile(r'Total VRAM used:\s*([0-9]+(?:\.[0-9]+)?)\s*GB')
STAMP = re.compile(r'(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)')
PROTON_NOTE = ('Proton log: not in this report. To capture one, add PROTON_LOG=1 to the game\'s Steam launch '
               'options (Steam then writes ~/steam-<appid>.log), run the game once, and send logs again.')


def _base(name):
    return name.rsplit('/', 1)[-1].lower()


def _clock(line):
    match = CLOCK.search(line)
    return match.group(1) if match else None


def _short(line, limit=220):
    """One evidence line on one line, cut for display (the raw file below keeps every byte)."""
    return ' '.join(str(line).split())[:limit]


def _first(pattern, text, flags=0):
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def _ini_values(text):
    """{Dx12Upscaler, Enabled, FGInput, FGOutput} from an OptiScaler.ini, each key only in its own section (the
    file holds other Enabled keys elsewhere). A tail-cut OptiScaler.log loses its startup lines; the ini is the
    fallback for what was configured."""
    wanted = {'Upscalers': ('Dx12Upscaler',), 'FrameGen': ('Enabled', 'FGInput', 'FGOutput')}
    found, section = {}, ''
    for line in (text or '').splitlines():
        stripped = line.strip().lstrip(';').lstrip('#').strip() if line.strip().startswith((';', '#')) else line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped[1:-1].strip()
            continue
        if line.strip().startswith((';', '#')) or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if key in wanted.get(section, ()):
            found[key] = value.strip().split(';', 1)[0].strip()
    return found


def parse_optiscaler(text):
    """One OptiScaler.log read for the timeline: when it loaded, what was configured, whether frame generation
    dispatched (delivered callbacks apart from skipping lines), the VRAM peak and the clean-shutdown marker."""
    lines = (text or '').splitlines()
    clocks = [clock for clock in (_clock(line) for line in lines) if clock]
    dispatches = [line for line in lines if FG_DISPATCH.search(line)]
    callbacks = sum(1 for line in lines if FG_CALLBACK.search(line))
    skipped = sum(1 for line in dispatches if 'skipping' in line.lower())
    delivered_plain = sum(1 for line in dispatches if 'skipping' not in line.lower()
                          and '[E]' not in line and 'error' not in line.lower())
    activates = [line for line in lines if FG_ACTIVATE.search(line)]
    deactivates = [line for line in lines if FG_DEACTIVATE.search(line)]
    peaks = [(float(value), line) for line in lines for value in VRAM_PEAK.findall(line)]
    clean = next((line for line in lines if re.search(r'IsShuttingDown\s*=\s*true', line, re.IGNORECASE)), None)
    enabled = _first(r'FrameGen\.Enabled:\s*(\S+)', text or '')
    return {'present': bool(lines), 'version': _first(r'OptiScaler v([0-9][^\s]*)', text or ''),
            'loads': sum(1 for line in lines if 'OptiScaler v' in line and 'loaded' in line),
            'started': clocks[0] if clocks else None, 'last': clocks[-1] if clocks else None,
            'last_line': _short(lines[-1]) if lines else '',
            'dx12_upscaler': _first(r'Upscalers\.Dx12Upscaler:\s*(\S+)', text or ''),
            'fg_enabled': None if enabled is None else enabled.lower() in ('true', '1', 'on', 'yes'),
            'fg_config': next((line.strip() for line in lines if 'FrameGen.Enabled:' in line), ''),
            'fg_input': _first(r'FrameGen\.FGInput:\s*(\S+)', text or ''),
            'fg_output': _first(r'FrameGen\.FGOutput:\s*(\S+)', text or ''),
            'fg_dispatch_lines': len(dispatches), 'fg_callbacks': callbacks, 'fg_skipped': skipped,
            'fg_delivered': callbacks if callbacks else delivered_plain,
            'fg_first_dispatch': _short(dispatches[0]) if dispatches else '',
            'fg_first_delivered': _short(next((line for line in lines if FG_CALLBACK.search(line)), '')) or
            _short(next((line for line in dispatches if 'skipping' not in line.lower()
                         and '[E]' not in line and 'error' not in line.lower()), '')),
            'activates': len(activates), 'deactivates': len(deactivates),
            'first_activate': _clock(activates[0]) if activates else None,
            'last_deactivate': _clock(deactivates[-1]) if deactivates else None,
            'vram_warnings': sum(1 for line in lines if 'Exceeded VRAM budget' in line),
            'vram_peak': max(peaks)[0] if peaks else None,
            'clean': clean is not None, 'clean_line': _short(clean) if clean else '',
            'clean_at': _clock(clean) if clean else None,
            'truncated': (text or '').startswith('[AMDLSS:')}


def parse_helix(texts):
    """The HelixSR sessions of one run: one per `create:` (a game can recreate its upscaler inside one run, so a
    `destroy` for an early session does not close the last one). `texts` is [(name, text)] of helixsr*.log."""
    sessions, clocks, renders = [], [], []
    for _name, text in texts:
        current = None
        for line in (text or '').splitlines():
            clock = _clock(line)
            if clock:
                clocks.append(clock)
            if 'create:' in line:
                sizes = re.search(r'(\d+x\d+)\s*->\s*(?:upscale\s*)?(\d+x\d+)', line)
                current = {'create': _short(line), 'render': sizes.group(1) if sizes else None,
                           'output': sizes.group(2) if sizes else None,
                           'as_dll': _first(r'\(as ([^)]+\.dll)\)', line), 'max_dispatch': -1,
                           'destroy': None, 'destroy_line': '', 'destroy_at': None}
                sessions.append(current)
                continue
            dispatched = re.search(r'\bdispatch (\d+):', line)
            destroyed = re.search(r'destroy after (\d+) frames', line)
            if (dispatched or destroyed) and current is None:
                current = {'create': '', 'render': None, 'output': None, 'as_dll': None, 'max_dispatch': -1,
                           'destroy': None, 'destroy_line': '', 'destroy_at': None}
                sessions.append(current)
            if dispatched and current is not None:
                current['max_dispatch'] = max(current['max_dispatch'], int(dispatched.group(1)))
                size = re.search(r'render (\d+x\d+)\s*->', line)
                if size:
                    renders.append(size.group(1))
            if destroyed and current is not None:
                current['destroy'] = int(destroyed.group(1))
                current['destroy_line'], current['destroy_at'] = _short(line), clock
                current = None
    last = sessions[-1] if sessions else None
    joined = '\n'.join(text for _name, text in texts)
    return {'present': bool(texts), 'sessions': sessions, 'last': last,
            'started': clocks[0] if clocks else None, 'last_clock': clocks[-1] if clocks else None,
            'renders': sorted(set(renders), key=lambda size: int(size.split('x', 1)[0])),
            'closed': sum(1 for session in sessions if session['destroy'] is not None),
            'destroy_frames': [session['destroy'] for session in sessions if session['destroy'] is not None],
            'clean_last': bool(last and last['destroy'] is not None),
            'ran': bool(sessions and (any(s['destroy'] is not None or s['max_dispatch'] >= 0 for s in sessions)
                                      or 'network running' in joined)),
            'nul': '\x00' in joined, 'truncated': joined.startswith('[AMDLSS:')}


def module_owner(module, path=''):
    """Whose DLL a faulting module is, in plain words (a `dxgi.dll` inside the game is OptiScaler's name; the
    same name under System32 is Windows'); kernelbase and ntdll usually only carry another DLL's fault."""
    name, where = (module or '').lower(), (path or '').lower()
    if name.startswith(('amd_fidelityfx', 'helixsr')):
        return 'HelixSR'
    if name in OPTI_NAMES:
        return 'Windows' if '\\windows\\' in where or '/windows/' in where else 'OptiScaler'
    if name.startswith('fakenvapi'):
        return 'fakenvapi'
    if name.startswith(('nvngx', '_nvngx', 'sl.')):
        return 'NVIDIA DLSS or Streamline'
    if name.startswith(('amd', 'ati', 'amdxc')):
        return 'AMD driver'
    if name in ('kernelbase.dll', 'ntdll.dll'):
        return 'Windows, often carrying another DLL\'s fault'
    return 'the game itself' if name.endswith('.exe') else 'unidentified'


def parse_crashes(text):
    """crashes.txt read back: the Event ID 1000/1001 entries (one `when | exe | faulting module: ...` line each),
    the dumped-file listings, and whether Windows was checked at all. No file answers present False."""
    if not text:
        return {'present': False, 'entries': [], 'dumps': [], 'wer': [], 'checked': False, 'failed': False}
    entries = []
    for line in text.splitlines():
        if ' | faulting module: ' not in line:
            continue
        fields = {}
        parts = [part.strip() for part in line.split(' | ')]
        for part in parts[2:]:
            key, _, value = part.partition(': ')
            fields[key] = value
        entries.append({'when': parts[0], 'exe': parts[1] if len(parts) > 1 else '',
                        'module': fields.get('faulting module', ''), 'version': fields.get('module version', ''),
                        'exception': fields.get('exception', ''), 'offset': fields.get('offset', ''),
                        'event': fields.get('Event ID', ''), 'path': fields.get('module path', ''),
                        'name': fields.get('event name', '')})
    return {'present': True, 'entries': entries,
            'dumps': [_short(line) for line in text.splitlines() if '.dmp' in line.lower()],
            'wer': [_short(line) for line in text.splitlines() if 'AppCrash_' in line],
            'checked': 'Application log' in text, 'failed': 'could not be read' in text}


def crash_what(entry):
    """What one record says happened. An Event 1001 from a hang names no module; an old AMDLSS wrote WER's
    unread "P5:" label where the module belongs, and that is said as it is, never as a module."""
    if (entry.get('name') or '').lower().startswith('apphang'):
        return 'the game stopped responding (hang)'
    if re.fullmatch(r'P\d+:?', entry['module'] or ''):
        return ('crash recorded by Windows Error Reporting; this AMDLSS version could not read the faulting '
                'module')
    owner = module_owner(entry['module'], entry['path'])
    return f"the game crashed inside {entry['module'] or 'an unnamed module'} ({owner})"


def crash_plain(entry):
    """One crash record in plain words, the faulting module named with its owner."""
    text = f"{entry['when']}: {crash_what(entry)}"
    if entry['exe']:
        text += f": {entry['exe']}"
    if entry['exception']:
        text += f", exception {entry['exception']}"
    if entry['offset']:
        text += f" at offset {entry['offset']}"
    if entry['event']:
        text += f" (Event ID {entry['event']})"
    return text


def report_date_of(files, created=None):
    """The report's day as YYYY-MM-DD (the receiver's arrival time, else system.txt's Collected line), else ''."""
    if created:
        return time.strftime('%Y-%m-%d', time.localtime(created))
    for line in ((files or {}).get('system.txt', '')).splitlines():
        match = re.match(r'Collected: (\d{4}-\d\d-\d\d)', line)
        if match:
            return match.group(1)
    return ''


def stamp_text(stamp):
    """A history folder's stamp (YYYYMMDD-HHMMSS) as a date and time, the stamp itself when it is not one."""
    match = STAMP.fullmatch(stamp or '')
    return f'{match.group(1)}-{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}:{match.group(6)}' \
        if match else str(stamp)


def timeline_of(files, label='Latest run', report_date='', crashes=None, drop_in=False):
    """One run read plainly: start and last clock, route and resolution, frame generation, VRAM, errors, how it
    ended, and Yes/No/Unknown for HelixSR ran, frame generation worked and the game crashed, each with its
    evidence line. `files` holds only this run's files; `crashes` is parse_crashes() of the game's crashes.txt;
    `drop_in` is what the install record says (games.txt, the install log) for a run whose own logs do not
    name the route: a drop-in run's HelixSR log can end in a session with no `create:` line."""
    opti_text = '\n'.join(text for name, text in files.items()
                          if _base(name).startswith('optiscaler') and _base(name).endswith('.log'))
    helix_texts = [(name, text) for name, text in files.items()
                   if _base(name).startswith('helixsr') and _base(name).endswith('.log')]
    fake_text = '\n'.join(text for name, text in files.items()
                          if _base(name).startswith('fakenvapi') and _base(name).endswith('.log'))
    ini = {}
    for name, text in files.items():
        if _base(name).startswith('optiscaler') and _base(name).endswith('.ini'):
            ini.update(_ini_values(text))
    opti, helix = parse_optiscaler(opti_text), parse_helix(helix_texts)
    fake_clocks = [clock for clock in (_clock(line) for line in fake_text.splitlines()) if clock]
    enabled = opti['fg_enabled']
    if enabled is None and 'Enabled' in ini:
        enabled = ini['Enabled'].lower() in ('true', '1', 'on', 'yes')
    fg_input = opti['fg_input'] or ini.get('FGInput')
    fg_output = opti['fg_output'] or ini.get('FGOutput')
    dx12 = opti['dx12_upscaler'] or ini.get('Dx12Upscaler')
    # The run starts with OptiScaler when it is there, else with HelixSR: a fakenvapi.log can be a leftover of
    # an earlier route (Monster Hunter in TFTKA6A3 keeps one from 13:20 beside a 14:17 drop-in run).
    started = opti['started'] or helix['started'] or (fake_clocks[0] if fake_clocks else None)
    lasts = [value for value in (opti['last'], helix['last_clock'], fake_clocks[-1] if fake_clocks else None) if value]
    last_session = helix['last'] or {}
    if opti['present']:
        route = 'OptiScaler route'
    elif drop_in or any((s['as_dll'] or '').lower().startswith('amd_fidelityfx') for s in helix['sessions']):
        route = 'FSR drop-in'
    elif helix['present']:
        route = 'HelixSR'
    else:
        route = 'unknown'
    ended = 'clean' if (opti['clean'] if opti['present'] else helix['clean_last']) else \
        'stops' if (opti['present'] or helix['present'] or fake_text) else 'unknown'
    ended_evidence = opti['clean_line'] if opti['clean'] else last_session.get('destroy_line', '')
    entries = (crashes or {}).get('entries') or []

    def seconds(clock):
        try:
            hours, minutes, secs = (int(part) for part in clock.split(':'))
            return hours * 3600 + minutes * 60 + secs
        except (ValueError, AttributeError):
            return None

    low = seconds(started) - 600 if seconds(started) is not None else None
    high = seconds(max(lasts)) + 600 if lasts and seconds(max(lasts)) is not None else None
    mine = [entry for entry in entries if report_date and entry['when'].startswith(report_date)
            and (seconds(entry['when'][11:19]) is None or low is None
                 or low <= seconds(entry['when'][11:19]) <= (high if high is not None else 24 * 3600))]
    if mine or (entries and not report_date and ended != 'clean'):
        hit = (mine or entries)[-1]
        crashed = {'verdict': 'Yes', 'evidence': crash_plain(hit)}
    elif ended == 'clean':
        crashed = {'verdict': 'No', 'evidence': ended_evidence or 'a clean shutdown marker is in the log'}
    elif ended == 'stops':
        crashed = {'verdict': 'Unknown', 'evidence': 'the log just stops, which fits a crash or a kill; '
                                                     'Windows recorded no matching crash for this run'
                   if crashes and crashes.get('checked') else 'the log just stops, which fits a crash or a kill'}
    else:
        crashed = {'verdict': 'Unknown', 'evidence': 'no HelixSR, OptiScaler or fakenvapi log in this report'}
    note = ''
    if entries and crashed['verdict'] != 'Yes':
        latest = entries[-1]
        note = (f"Windows also recorded {len(entries)} crash(es) of this game in the last 14 days; the latest is "
                f"{latest['when']}: {crash_what(latest)}. See Windows crashes below.")
    if opti['present'] and opti['fg_delivered'] > 0:
        framegen = {'verdict': 'Yes', 'evidence': (
            f"{opti['fg_delivered']} frames delivered by {'FSR ' if 'FSRFG' in opti['fg_first_delivered'] else ''}"
            f"frame generation, {opti['activates']} activation{'' if opti['activates'] == 1 else 's'}")}
    elif opti['present'] and enabled:
        framegen = {'verdict': 'No', 'evidence': opti['fg_first_dispatch'] or opti['fg_config'] or
                    'frame generation on, no dispatch line in OptiScaler.log'}
    elif opti['present'] and enabled is False:
        framegen = {'verdict': 'No', 'evidence': opti['fg_config'] or 'FrameGen Enabled is off in OptiScaler.ini'}
    elif route == 'FSR drop-in':
        create = next((s['create'] for s in reversed(helix['sessions']) if s['create']), '')
        framegen = {'verdict': 'No', 'evidence': ('the FSR drop-in route has no OptiScaler and no frame '
                                                  f"generation: {create}" if create else
                                                  'the FSR drop-in has no frame generation')}
    else:
        framegen = {'verdict': 'Unknown', 'evidence': 'no OptiScaler log in this report'}
    if helix['ran']:
        evidence = next((s['create'] for s in helix['sessions'] if s['create']), '') or \
            next((s['destroy_line'] for s in helix['sessions'] if s['destroy_line']), '') or \
            'dispatch lines in helixsr.log'
        ran = {'verdict': 'Yes', 'evidence': evidence}
    elif helix['present']:
        ran = {'verdict': 'No', 'evidence': 'a HelixSR log is present but holds no create, dispatch or destroy line'}
    else:
        ran = {'verdict': 'Unknown', 'evidence': 'no HelixSR log in this report'}
    run_files = {name: text for name, text in files.items() if _base(name) != SESSION_LOG}
    return {'label': label, 'started': started, 'last': max(lasts) if lasts else None,
            'route': route, 'opti': opti, 'helix': helix, 'dx12_upscaler': dx12, 'fg_enabled': enabled,
            'fg_input': fg_input, 'fg_output': fg_output, 'ended': ended, 'ended_evidence': ended_evidence,
            'ended_at': opti['clean_at'] or last_session.get('destroy_at'),
            'helix_ran': ran, 'framegen': framegen, 'crashed': crashed, 'crash_note': note,
            'errors': problems_in(run_files), 'fake_present': bool(fake_text),
            'truncated': opti['truncated'] or helix['truncated']}


def timeline_lines(run, verdicts=True):
    """One run as short plain lines, the same text on the page (escaped there) and in the fetch summary. With
    `verdicts` off the three Did-you lines are left out: the page shows them in its Answers table instead."""
    opti, helix, lines = run['opti'], run['helix'], []
    started = f"Started {run['started']}" if run['started'] else 'Start time unknown (no timed log line)'
    if opti['version']:
        started += f" (OptiScaler v{opti['version']} loaded)"
    if run['last']:
        started += f". Last log line {run['last']}"
    lines.append(started + '.')
    if run['truncated']:
        lines.append('A log in this run was cut to its tail to fit the report, so its first lines are missing.')
    if opti['loads'] > 1:
        lines.append(f"OptiScaler loaded {opti['loads']} times in this log; the counts below cover the whole log.")
    last = helix['last'] or {}
    upscaler = f"Upscaler: {run['route']}"
    if run['dx12_upscaler']:
        upscaler += f", Dx12Upscaler {run['dx12_upscaler']}"
    if last.get('render'):
        upscaler += f". HelixSR render {last['render']} -> output {last['output']}"
        if last.get('as_dll'):
            upscaler += f" (as {last['as_dll']})"
    if len(helix['renders']) > 1:
        upscaler += f"; dispatch render sizes {', '.join(helix['renders'])}"
    lines.append(upscaler + '.')
    if helix['sessions']:
        frames = []
        if helix['destroy_frames']:
            total = sum(helix['destroy_frames'])
            frames.append(f"{total} frame{'' if total == 1 else 's'} in {helix['closed']} closed session(s)")
        if last.get('destroy') is None and last.get('max_dispatch', -1) >= 0:
            frames.append(f"last session reached dispatch {last['max_dispatch']} "
                          f"(at least {last['max_dispatch'] + 1} frames) with no destroy line")
        if frames:
            lines.append(f"HelixSR sessions: {len(helix['sessions'])}; {'; '.join(frames)}.")
    if opti['present']:
        config = ', '.join(part for part in (
            f"FrameGen.Enabled {'true' if run['fg_enabled'] else 'false'}" if run['fg_enabled'] is not None else '',
            f"FGInput {run['fg_input']}" if run['fg_input'] else '',
            f"FGOutput {run['fg_output']}" if run['fg_output'] else '') if part)
        other = opti['fg_dispatch_lines'] - opti['fg_callbacks'] - opti['fg_skipped'] if opti['fg_callbacks'] else 0
        counts = (f"{opti['fg_dispatch_lines']} FSRFG dispatch lines "
                  f"({opti['fg_delivered']} delivered, {opti['fg_skipped']} skipping"
                  f"{f', {other} other' if other > 0 else ''}), "
                  f"{opti['activates']} activates and {opti['deactivates']} deactivates")
        active = ''
        if opti['first_activate']:
            active = f", first active {opti['first_activate']}"
        if opti['last_deactivate']:
            active += f", last deactivated {opti['last_deactivate']}"
        lines.append(f"Frame generation: {config + '. ' if config else ''}{counts}{active}.")
        vram = f"VRAM: {opti['vram_warnings']} budget warnings"
        if opti['vram_peak'] is not None:
            vram += f", peak {opti['vram_peak']:.2f} GB"
        lines.append(vram + '.')
    if run['ended'] == 'clean':
        lines.append('How it ended: clean shutdown' + (f" at {run['ended_at']}" if run['ended_at'] else '') + '.')
    elif run['ended'] == 'stops':
        lines.append('How it ended: the log just stops' + (f" at {run['last']}" if run['last'] else '') +
                     ' (likely crash or kill).')
    else:
        lines.append('How it ended: unknown, no log of the run is in this report.')
    if verdicts:
        for question, answer in (('Did HelixSR run?', run['helix_ran']),
                                 ('Did frame generation work?', run['framegen']),
                                 ('Did the game crash?', run['crashed'])):
            lines.append(f"{question} {answer['verdict']}. Evidence: {_short(answer['evidence'])}")
    if run['crash_note']:
        lines.append(run['crash_note'])
    if run['errors']:
        total = sum(error['count'] for error in run['errors'])
        lines.append(f"Real errors: {len(run['errors'])} distinct line{'' if len(run['errors']) == 1 else 's'} "
                     f"({total} total). First: {_short(run['errors'][0]['text'])}")
    else:
        lines.append('Real errors: none found in these logs.')
    return lines


def drop_in_before(actions, moment):
    """True when the install log says the FSR drop-in was in place just before `moment`. A history snapshot is
    taken as an install, update or remove starts, so the action that took it (the last one at or before the
    stamp) is left out."""
    earlier = [action for action in actions if action['when'] <= moment][:-1]
    last = next((action for action in reversed(earlier) if action['outcome'] != 'failed'), None)
    return bool(last and last['action'] != 'remove' and last['route'] == 'direct')


def runs_of(files, folder, report_date=''):
    """(timelines, crashes, proton present) for one game folder of a report: the latest run first, then each
    kept history snapshot newest first. History files never join the latest run. A run whose logs do not name
    its route gets it from the install record: games.txt for the latest run, the install log for a kept one."""
    prefix = folder + '/'
    install = next((game['install'] for game in parse_games((files or {}).get('games.txt', ''))
                    if game['folder'] == folder), '')
    actions = parse_actions((files or {}).get(prefix + SESSION_LOG, ''))
    crashes = parse_crashes((files or {}).get(prefix + 'crashes.txt', ''))
    current, history = {}, {}
    for name, text in (files or {}).items():
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if rest.startswith('history/') and '/' in rest[len('history/'):]:
            history.setdefault(rest.split('/')[1], {})[name] = text
        else:
            current[name] = text
    runs = [timeline_of(current, 'Latest run', report_date, crashes, install == 'drop-in DLL')]
    for stamp in sorted(history, reverse=True):
        runs.append(timeline_of(history[stamp], f'Earlier run kept {stamp_text(stamp)}',
                                stamp_text(stamp)[:10], None, drop_in_before(actions, stamp_text(stamp))))
    proton = any(_base(name) == 'proton.log' for name in current)
    return runs, crashes, proton


def third_party_optiscaler(files, install):
    """'OptiScaler <version> from another install...' when files.txt shows an OptiScaler DLL in the game while
    AMDLSS installed nothing there, else None."""
    if install != 'none':
        return None
    text = next((text for name, text in files.items() if name.rsplit('/', 1)[-1] == 'files.txt'), '')
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0].lower() in OPTI_NAMES and line.rstrip().endswith('OptiScaler'):
            version = next((field for field in fields if re.fullmatch(r'\d+(\.\d+)+', field)), '')
            shown = f'OptiScaler {version} ' if version else 'OptiScaler '
            return f"{shown}from another install is in this folder (not AMDLSS's)"
    return None


def status_of(game, actions, problems, helix):
    """(plain-words status, badge colour) for one game: a failed last action is red, an install with problems
    or no clean shutdown is amber, a clean install is green."""
    if actions and actions[-1]['outcome'] == 'failed':
        return ('Remove failed' if actions[-1]['action'] == 'remove' else 'Install failed'), 'red'
    if game['install'] == 'none':
        if any(action['outcome'] in ('removed', 'restored') for action in actions):
            return 'Removed', 'amber'
        return 'Not installed by AMDLSS', 'amber'
    if problems or helix['nul'] or (helix['present'] and not helix['clean']):
        return 'Installed, with problems', 'amber'
    return 'Working', 'green'


def analyse(report, code, created=None):
    """Everything the pages show, out of one report: the system rows and, per game, the session timelines, what
    happened, the problems found, HelixSR's evidence, Windows crashes and the plain-words status. Problems and
    HelixSR's evidence read the latest run only (kept history has its own timelines). A report without
    games.txt falls back to its folders."""
    files = report.get('files') or {}
    system = parse_system(files.get('system.txt', ''))
    linux = any(label == 'OS' and value.startswith('Linux:') for label, value in system)
    day = report_date_of(files, created)
    games = parse_games(files.get('games.txt', ''))
    if not games:
        games = [{'folder': folder, 'name': folder, 'install': 'unknown', 'helixsr': 'unknown', 'framegen': None}
                 for folder in sorted({name.split('/')[0] for name in files if '/' in name})]
    for game in games:
        own = {name: text for name, text in files.items() if name.startswith(game['folder'] + '/')}
        current = {name: text for name, text in own.items()
                   if not name.startswith(game['folder'] + '/history/')}
        runs, crashes, proton = runs_of(files, game['folder'], day)
        game['runs'], game['timeline'], game['crashes'], game['proton'] = runs, runs[0], crashes, proton
        game['proton_note'] = 'Proton log: included (proton.log).' if proton else PROTON_NOTE if linux else ''
        game['actions'] = parse_actions(own.get(f"{game['folder']}/{SESSION_LOG}", ''))
        game['problems'] = problems_in(current)
        game['helix'] = helix_evidence(current)
        game['third_party'] = third_party_optiscaler(current, game['install'])
        game['status'], game['level'] = status_of(game, game['actions'], game['problems'], game['helix'])
    return {'code': code, 'created': created, 'date': date_of(created, files.get('system.txt', '')),
            'version': report.get('version') or '?', 'system': system, 'games': games, 'files': files}


# --- the pages ----------------------------------------------------------------------------------------------------

STYLE = """
body { background: #16181d; color: #e6e6e6; font: 15px/1.5 system-ui, "Segoe UI", sans-serif; margin: 2rem auto;
       max-width: 56rem; padding: 0 1rem; }
h1 { font-size: 1.5rem; } h2 { font-size: 1.2rem; margin: 0 0 .5rem; } h3 { font-size: 1rem; margin: 1.2rem 0 .4rem;
       color: #b9bec9; }
.meta, .dim { color: #9aa0ad; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { border: 1px solid #2c3038; padding: .3rem .7rem; text-align: left; }
th { color: #b9bec9; font-weight: 600; }
.card { background: #1d2026; border: 1px solid #2c3038; padding: 1rem 1.2rem; margin: 1.2rem 0; }
.badge { border: 1px solid #2c3038; border-radius: 999px; display: inline-block; font-size: .82rem;
         margin: 0 0 .4rem .6rem; padding: .05rem .6rem; vertical-align: middle; }
.badge.green { background: #17351d; color: #8fd49a; } .badge.amber { background: #3a2d12; color: #e8c37a; }
.badge.red { background: #3d1717; color: #f09a9a; }
.cause, .why, .warn { color: #e8c37a; }
ul { margin: .3rem 0; padding-left: 1.4rem; }
details { margin: .6rem 0; } summary { cursor: pointer; color: #b9bec9; }
pre { background: #111318; border: 1px solid #2c3038; overflow: auto; padding: .7rem; white-space: pre-wrap;
      word-break: break-word; font-size: .82rem; }
code { font-size: .9em; }
a { color: #9ec1f0; }
"""


def _page(title, body):
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<title>{esc(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n')


def _game_card(game):
    out = ['<section class="card">',
           f'<h2>{esc(game["name"])}<span class="badge {game["level"]}">{esc(game["status"])}</span></h2>']
    rows = [('Install', game['install']), ('HelixSR', game['helixsr'])]
    if game['framegen'] is not None:
        rows.append(('Frame generation', 'on' if game['framegen'] else 'off'))
    out.append('<table>' + ''.join(f'<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>'
                                   for label, value in rows) + '</table>')
    if game['third_party']:
        out.append(f'<p class="warn">{esc(game["third_party"])}</p>')
    latest = game['timeline']
    out.append('<h3>Answers (latest run)</h3>')
    answers = [('Did HelixSR run?', latest['helix_ran']), ('Did frame generation work?', latest['framegen']),
               ('Did the game crash?', latest['crashed'])]
    out.append('<table>' + ''.join(
        f'<tr><th>{esc(question)}</th><td>{esc(answer["verdict"])}</td>'
        f'<td><code>{esc(_short(answer["evidence"]))}</code></td></tr>'
        for question, answer in answers) + '</table>')
    if latest['crash_note']:
        out.append(f'<p class="warn">{esc(latest["crash_note"])}</p>')
    out.append('<h3>Session timeline</h3>')
    for run in game['runs']:
        out.append(f'<p><strong>{esc(run["label"])}</strong></p>')
        out.append('<ul>' + ''.join(f'<li>{esc(line)}</li>'
                                     for line in timeline_lines(run, verdicts=run is not latest)) + '</ul>')
    if game['proton_note']:
        out.append(f'<p class="dim">{esc(game["proton_note"])}</p>')
    crashes = game['crashes']
    if crashes['present']:
        out.append('<h3>Windows crashes</h3>')
        if crashes['entries']:
            out.append('<ul>' + ''.join(f'<li>{esc(crash_plain(entry))}</li>'
                                         for entry in crashes['entries']) + '</ul>')
        elif crashes['failed']:
            out.append('<p class="warn">The Windows event log could not be read when these logs were '
                       'collected, so a crash can be neither proved nor ruled out from Windows. '
                       'See crashes.txt in the raw files.</p>')
        elif crashes['checked']:
            out.append('<p>No crash of this game\'s executables in the Windows Application log '
                       '(last 14 days, Event ID 1000 and 1001).</p>')
        else:
            out.append('<p class="dim">Windows crash records are collected on Windows only. '
                       'See crashes.txt in the raw files.</p>')
        for line in crashes['dumps'] + crashes['wer']:
            out.append(f'<p class="dim">Listed only, not sent: {esc(line)}</p>')
    out.append('<h3>What happened</h3>')
    if game['actions']:
        out.append('<ul>')
        for action in game['actions']:
            route = f' ({action["route"]})' if action['route'] else ''
            line = f'<li>{esc(action["when"])}: {esc(action["action"])}{esc(route)}: {esc(action["outcome"])}'
            if action['cause']:
                line += f'<br><span class="cause">{esc(action["cause"])}</span>'
            out.append(line + '</li>')
        out.append('</ul>')
    else:
        out.append('<p class="dim">AMDLSS has no install record for this game in the report.</p>')
    out.append('<h3>Problems found</h3>')
    if game['problems']:
        out.append('<ul>')
        for problem in game['problems']:
            count = f' (x{problem["count"]})' if problem['count'] > 1 else ''
            line = f'<li><code>{esc(problem["text"])}</code>{count}'
            if problem['why']:
                line += f'<br><span class="why">{esc(problem["why"])}</span>'
            out.append(line + '</li>')
        out.append('</ul>')
    else:
        out.append('<p>No errors found.</p>')
    out.append('<h3>HelixSR evidence</h3>')
    helix = game['helix']
    if not helix['present']:
        out.append('<p class="dim">No HelixSR log in this report.</p>')
    else:
        if helix['create']:
            out.append(f'<p><code>{esc(helix["create"])}</code></p>')
        if helix['clean']:
            out.append(f'<p>{helix["frames"]} frames, clean shutdown.</p>')
        else:
            shown = f'{helix["frames"]} frames. ' if helix['frames'] else ''
            out.append(f'<p>{shown}No clean shutdown: the log has no destroy line, so the game died or was '
                       'killed.</p>')
        if helix['nul']:
            out.append('<p class="warn">The log holds NUL bytes: log cut off, likely a crash or forced '
                       'close.</p>')
    out.append('</section>')
    return '\n'.join(out)


def render_report(analysis):
    """One report as a page: the reading first, then every raw file under a collapsed heading."""
    out = [f'<h1>AMDLSS report {esc(analysis["code"])}</h1>',
           f'<p class="meta">{esc(analysis["date"])} &middot; AMDLSS {esc(analysis["version"])}</p>']
    if analysis['system']:
        out.append('<table>' + ''.join(f'<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>'
                                       for label, value in analysis['system']) + '</table>')
    out += [_game_card(game) for game in analysis['games']]
    out.append('<h2>Raw files</h2>')
    for name in sorted(analysis['files']):
        out.append(f'<details><summary>{esc(name)}</summary><pre>{esc(analysis["files"][name])}</pre></details>')
    return _page(f'AMDLSS report {analysis["code"]}', '\n'.join(out))


def render_index(analyses):
    """Every downloaded report, newest first as given, one relative link and one badge per game."""
    out = ['<h1>AMDLSS reports</h1>']
    if not analyses:
        out.append('<p class="dim">No reports downloaded yet.</p>')
    out.append('<ul>')
    for analysis in analyses:
        code = analysis['code']
        link = (f'<a href="{esc(code)}/report.html">{esc(code)}</a>' if CODE.fullmatch(code or '')
                else esc(code))
        badges = ''.join(f'<span class="badge {game["level"]}">{esc(game["name"])}: {esc(game["status"])}</span>'
                         for game in analysis['games'])
        count = len(analysis['games'])
        out.append(f'<li>{link} <span class="dim">{esc(analysis["date"])} &middot; {count} '
                   f'game{"" if count == 1 else "s"}</span>{badges}</li>')
    out.append('</ul>')
    return _page('AMDLSS reports', '\n'.join(out))
