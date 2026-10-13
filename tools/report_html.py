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
    """Everything the pages show, out of one report: the system rows and, per game, what happened, the problems
    found, HelixSR's evidence and the plain-words status. A report without games.txt falls back to its folders."""
    files = report.get('files') or {}
    games = parse_games(files.get('games.txt', ''))
    if not games:
        games = [{'folder': folder, 'name': folder, 'install': 'unknown', 'helixsr': 'unknown', 'framegen': None}
                 for folder in sorted({name.split('/')[0] for name in files if '/' in name})]
    for game in games:
        own = {name: text for name, text in files.items() if name.startswith(game['folder'] + '/')}
        game['actions'] = parse_actions(own.get(f"{game['folder']}/{SESSION_LOG}", ''))
        game['problems'] = problems_in(own)
        game['helix'] = helix_evidence(own)
        game['third_party'] = third_party_optiscaler(own, game['install'])
        game['status'], game['level'] = status_of(game, game['actions'], game['problems'], game['helix'])
    return {'code': code, 'created': created, 'date': date_of(created, files.get('system.txt', '')),
            'version': report.get('version') or '?', 'system': parse_system(files.get('system.txt', '')),
            'games': games, 'files': files}


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
