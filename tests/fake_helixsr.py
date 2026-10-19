# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for HelixSR's setup folder, so tests can run AMDLSS's whole path (bridge, list parsing, install and
remove command lines) with no HelixSR and no game. make() writes the HelixSR 2.0.0 layout: the DLL in HelixSR-fsrfg/,
setup/helixsr_games.py (the game module AMDLSS's bridge imports) and setup/helixsr_bridge.py (the JSON command line
that installs the drop-in and removes), and a setup/helixsr_install.py that refuses to run, like AMDLSS must never
run the real one (its games step installs into every game under --yes and does not list). make(legacy=True) writes
the HelixSR 1.9.3 layout instead (an older folder not updated yet): the DLL at the root and one
setup/helixsr_install.py, which has the same
module-level names and command line as HelixSR 1.9.3's installer (`games`, `install`, `uninstall`, `say`,
`version_of`, `is_helixsr`, `original`, `FOLDER`/`MARKER`/`OWN`/`CORE`, Game.route/scaler_installed/old_install()/
installed()/direct_installed()/current()/label()/ways(); `list`, `install N`, `uninstall N`, `--folder PATH`,
`--fg on|off`). Like the real one: a game's route is 'direct' (FSR 3.1), 'scaler' (DLSS/XeSS/FSR 2, or Scaler
already there), 'blocked' (those plus anti-cheat), 'dlss'/'optiscaler' (an earlier version's install, which can
only be removed) or None. The drop-in is file renames plus the install record next to the game's FSR DLL; a Scaler
game gets dxgi.dll, OptiScaler.ini and its HelixSR DLL (HelixSR-fsrfg\\amd_fidelityfx_dx12.dll in the 2.0.0 layout,
HelixSR\\helixsr.dll in the 1.9.3 one) next to its .exe plus HelixSR-Scaler.files, the record `uninstall N` reads to
put everything back - see scaler_install(), which writes one.
"""
import json
from pathlib import Path

STUB = '''
import json, os, re, sys
from pathlib import Path

MODERN = @MODERN@   # the HelixSR 2.0.0 layout

DLL = 'amd_fidelityfx_dx12.dll'
DLL_SR = 'amd_fidelityfx_upscaler_dx12.dll'
FOLDER, MARKER, BACKUP = 'HelixSR', 'helixsr-install.json', 'backup'
OWN, OWN_SR, CORE = 'helixsr.dll', 'helixsr_upscaler.dll', 'nvngx.dll'
FINI = 'helixsr.ini'
SCALER_FILES, SCALER_BACKUP = 'HelixSR-Scaler.files', 'HelixSR-Scaler-backup'
UNSUPPORTED = 'not supported by the drop-in: use HelixSR Desktop (HelixSR Scaler)'
WAY = {'direct': 'FSR drop-in', 'scaler': 'HelixSR Scaler', 'optiscaler': 'OptiScaler, earlier version',
       'dlss': 'DLSS loader, earlier version'}
INI = ('[FrameGen]\\r\\n; comment\\r\\nFGInput=auto\\r\\nFGOutput=auto\\r\\n\\r\\n[Other]\\r\\nKey = 1\\r\\n\\r\\n'
       '[Log]\\r\\nLogToFile=auto\\r\\nLogLevel=auto\\r\\n\\r\\n[Libraries]\\r\\nFfxDx12Path=auto\\r\\n'
       'FfxDx12SRPath=auto\\r\\n')


def original(t):
    return t.with_name(t.stem + '.original.dll')


def is_helixsr(t):
    try:
        return t.read_bytes().startswith(b'HELIX')
    except OSError:
        return False


def say(msg):
    print(f'[helixsr-setup] {msg}', flush=True)


def sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def version_of(dll):
    if not Path(dll).is_file():
        return '?'
    found = re.search(rb'\\x00HelixSR (\\d+\\.\\d+\\.\\d+)\\x00', Path(dll).read_bytes()[:4 << 20])
    return found.group(1).decode() if found else '?'


def read_marker(d):
    try:
        return json.loads((Path(d) / MARKER).read_text())
    except (OSError, ValueError):
        return {}


def network_ready(here):
    return True


class Game:
    def __init__(self, name, folder, spec):
        fsr, inputs = spec[0], spec[1]
        anticheat = list(spec[2]) if len(spec) > 2 else []
        self.name, self.folder = name, Path(folder)
        self.fsr_targets = [self.folder / DLL_SR] if fsr else []
        self.inputs, self.target = inputs, self.folder
        marker = self.folder / FOLDER / MARKER
        rec = read_marker(self.folder / FOLDER)
        self.dlss_installed = rec.get('route') == 'dlss'
        self.opti_installed = marker.exists() and not self.dlss_installed
        self.scaler_installed = (self.folder / SCALER_FILES).is_file()
        self.anticheat, self.chosen = anticheat, False
        if self.scaler_installed:
            self.route = 'scaler'      # an update keeps the way the game has
        elif fsr:
            self.route = 'direct'
        elif (self.inputs or self.fsr_targets) and self.target:
            self.route = 'blocked' if self.anticheat else 'scaler'
        elif self.dlss_installed:
            self.route = 'dlss'
        elif self.opti_installed:
            self.route = 'optiscaler'
        else:
            self.route = None

    def ways(self):
        """The ways this game can get, like the real one: 'direct' (FSR 3.1) and 'scaler' (an .exe folder and an
        upscaler HelixSR Scaler can take over, no anti-cheat; or Scaler already there)."""
        out = []
        if self.fsr_targets:
            out.append('direct')
        if self.target and (self.inputs or self.fsr_targets) and (not self.anticheat or self.scaler_installed):
            out.append('scaler')
        return out

    def old_install(self):
        """'dlss' / 'optiscaler' for an install of an earlier version (can only be removed), else None."""
        return 'dlss' if self.dlss_installed else 'optiscaler' if self.opti_installed else None

    def direct_installed(self):
        return any(is_helixsr(t) for t in self.fsr_targets)

    def current(self):
        """The way HelixSR is in this game now: 'scaler', 'direct', 'dlss' or 'optiscaler', else None."""
        if self.scaler_installed:
            return 'scaler'
        return self.old_install() or ('direct' if self.direct_installed() else None)

    def installed(self):
        return self.scaler_installed or bool(self.old_install()) or self.direct_installed()

    def label(self):
        if self.route == 'blocked':
            return ' / '.join(self.inputs) + ' only, anti-cheat: HelixSR Scaler is not installed in games with anti-cheat (' + ', '.join(self.anticheat) + ')'
        if self.route == 'scaler':
            return ' / '.join(self.inputs or ['FSR 3.1']) + ': HelixSR Scaler (select DLSS in the game)'
        if self.fsr_targets:
            return "FSR 3.1: FSR drop-in (HelixSR replaces the game's FSR DLL: select AMD FSR in the game)"
        return ' / '.join(self.inputs) + ' only'


def walk(folder, depth):
    return (False, [])   # a folder that holds nothing HelixSR can use


def overlaps(a, b):
    a, b = Path(a), Path(b)
    return a == b or a in b.parents or b in a.parents


def clean_path(f):
    return str(f)


def games_from(folders, listed=()):
    # like HelixSR: a folder with nothing it can use ("Plain...") is dropped with a message
    return [Game(Path(f).name, f, (True, ['DLSS'])) for f in folders
            if Path(f).is_dir() and not Path(f).name.startswith('Plain')]


def games(extra):
    here = Path(__file__).resolve().parent.parent
    rows = json.loads((here / 'games.json').read_text())
    out = [g for g in (Game(r['name'], r['folder'], (r['fsr'], r['inputs'], r.get('anticheat') or []))
                       for r in rows) if g.route]
    return out + games_from(extra, out)


def remove_ini(d, made):
    ini = Path(d) / FINI
    if ini.exists() and (not made or sha(ini) == made):
        ini.unlink()


def uninstall_old(g):
    """An earlier version's install (AMDLSS 1.0.0's bridge too): its own record in HelixSR\\\\helixsr-install.json
    is the truth, like the real installer's uninstall_optiscaler."""
    folder, target = g.folder / FOLDER, g.folder
    rec = read_marker(folder)
    for name in rec.get('created', []):
        (target / name).unlink(missing_ok=True)
    for name in rec.get('moved', []):
        src, dst = folder / BACKUP / name, target / name
        if src.exists():
            os.replace(src, dst)
    opti = rec.get('optiscaler', 'dxgi.dll')
    if rec.get('ini') == 'created' or not (folder / BACKUP / 'OptiScaler.ini').exists():
        (target / 'OptiScaler.ini').unlink(missing_ok=True)
    (target / opti).unlink(missing_ok=True)
    for name in (MARKER, OWN, OWN_SR, 'amd_fidelityfx_framegeneration_dx12.dll', 'helixsr.log'):
        (folder / name).unlink(missing_ok=True)
    for d in (folder / BACKUP, folder):
        try:
            d.rmdir()
        except OSError:
            pass


def uninstall_scaler(t):
    """HelixSR 1.9.3's own Scaler uninstall: HelixSR-Scaler.files names the files it put there; the games own
    files come back out of HelixSR-Scaler-backup."""
    t, backup = Path(t), Path(t) / SCALER_BACKUP
    try:
        rec = json.loads((t / SCALER_FILES).read_text())
    except (OSError, ValueError):
        return
    for name in rec.get('files', []):
        (t / name).unlink(missing_ok=True)
    if backup.is_dir():
        for f in sorted(backup.rglob('*')):
            if f.is_file():
                dst = t / f.relative_to(backup)
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(f, dst)
        for d in sorted((p for p in backup.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            backup.rmdir()
        except OSError:
            pass
    (t / SCALER_FILES).unlink(missing_ok=True)
    for folder in (t / FOLDER, t / 'HelixSR-fsrfg'):
        try:
            folder.rmdir()
        except OSError:
            pass


def install_scaler(t):
    """HelixSR 1.9.3's own Scaler install in its own layout: the DLL next to the game's .exe, HelixSR's DLL in its
    HelixSR folder, and HelixSR-Scaler.files, the record its uninstall reads (the real one also copies the whole
    package - OptiScaler/, streamline/, Licenses/; the bridge only ever reads the record)."""
    t = Path(t)
    (t / 'dxgi.dll').write_bytes(b'OPTISCALER 0.9.4')
    (t / 'OptiScaler.ini').write_bytes(INI.encode())
    own = 'HelixSR-fsrfg/' + DLL if MODERN else FOLDER + '/' + OWN
    (t / own).parent.mkdir(parents=True, exist_ok=True)
    (t / own).write_bytes(b'HELIX\\x00HelixSR ' + (b'2.0.0' if MODERN else b'1.9.3') + b'\\x00')
    (t / SCALER_FILES).write_text(json.dumps({'name': 'dxgi.dll', 'version': '1.1.0' if MODERN else '1.0.6',
                                              'files': ['dxgi.dll', 'OptiScaler.ini', own],
                                              'by_hand': False}, indent=1))


def install(g, here):
    """HelixSR 1.9.3: the game's route picks the way - the FSR drop-in, or HelixSR Scaler where the game has no
    FSR 3.1 DLL (or Scaler already was the way)."""
    route = g.route if g.route in ('direct', 'scaler') else ('direct' if g.fsr_targets else 'scaler')
    if route == 'direct' and not g.fsr_targets:
        raise SystemExit(f'{g.name} has no FSR 3.1 DLL: {UNSUPPORTED}')
    if route == 'scaler' and g.anticheat and not g.scaler_installed:
        raise SystemExit(f'{g.name} has anti-cheat: HelixSR Scaler is not installed there')
    if g.old_install():
        say('  removing the install of an earlier version first')
        uninstall_old(g)
    g.route = route
    if route == 'scaler':
        install_scaler(g.folder)
        say(f'  installed HelixSR Scaler in {g.folder}')
        return
    for t in g.fsr_targets:
        if not original(t).exists() and not is_helixsr(t):
            os.replace(t, original(t))
        t.write_bytes(b'HELIX stand-in')
        (t.parent / MARKER).write_text(json.dumps({'helixsr': version_of(here / DLL), 'files': [t.name],
                                                   'route': 'direct'}, indent=1))
    say(f'  installed in {g.folder}')


def uninstall(g):
    if g.old_install():
        uninstall_old(g)
    if g.scaler_installed:
        uninstall_scaler(g.folder)
    for t in g.fsr_targets:
        orig = original(t)
        if is_helixsr(t):
            if orig.exists():
                os.replace(orig, t)
            else:
                t.unlink()
            remove_ini(t.parent, read_marker(t.parent).get('ini_made'))
            (t.parent / MARKER).unlink(missing_ok=True)


def done_message(todo=()):
    say('Done. Start the game and pick AMD FSR in its graphics settings.')


def main(argv):
    here, cmd, rest = Path(argv[0]), argv[1], argv[2:]
    extra = []
    while '--folder' in rest:
        i = rest.index('--folder')
        extra.append(rest[i + 1])
        del rest[i:i + 2]
    if '--fg' in rest:   # the real installer takes `--fg on|off`; AMDLSS never sends it
        i = rest.index('--fg')
        del rest[i:i + 2]
    gl = games(extra)
    if cmd == 'list':
        for i, g in enumerate(gl, 1):
            now = g.current()
            print(f'  {i:3}. {g.name:45} ' + (f'HelixSR installed ({WAY[now]})' if now else g.label()), flush=True)
    elif cmd in ('install', 'uninstall'):
        g = gl[int(rest[0]) - 1]
        say(g.name)
        install(g, here) if cmd == 'install' else uninstall(g)
        say('done')
    else:
        raise SystemExit('usage')


if __name__ == '__main__':
    main(sys.argv[1:])
'''


# HelixSR 2.0.0's helixsr_bridge.py, reduced to what AMDLSS runs: `RELEASE install --game DIR --way fsr` and
# `RELEASE remove --game DIR`, one JSON object out ({protocol, ok, error, messages}), game found by its folder.
BRIDGE_STUB = '''
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helixsr_games as h


def main(argv):
    out, messages = {'protocol': 1, 'ok': False}, []
    h.say = lambda m: messages.append(str(m).strip())
    try:
        release, verb = Path(argv[0]), argv[1]
        folder = Path(argv[argv.index('--game') + 1])
        g = next((g for g in h.games([]) if Path(g.folder) == folder), None) or (h.games_from([str(folder)]) or [None])[0]
        if g is None:
            raise SystemExit(f'no game (.exe) found in {folder}')
        if verb == 'install':
            if argv[argv.index('--way') + 1] != 'fsr':
                raise SystemExit('the fake bridge installs the FSR drop-in only')
            g.route = 'direct'
            h.install(g, release / 'HelixSR-fsrfg')
        else:
            h.uninstall(g)
        out['ok'] = True
    except SystemExit as error:
        out['error'] = str(error.code)
    out['messages'] = messages
    print(json.dumps(out))
    return 0 if out['ok'] else 1


sys.exit(main(sys.argv[1:]))
'''


def make(root, games, legacy=False):
    """A fake HelixSR folder under `root`; `games` is a list of {name, folder, fsr, inputs[, anticheat]}. The
    folder holds a built DLL and helixsr_setup.json, so it doubles as the source Scaler copies HelixSR's DLL
    from. `legacy` makes the 1.9.3 layout (DLL at the root, setup/helixsr_install.py is the module and the command
    line) instead of 2.0.0's."""
    folder = Path(root) / 'HelixSR-fake'
    (folder / 'setup').mkdir(parents=True)
    (folder / 'games.json').write_text(json.dumps(games), encoding='utf-8')
    (folder / 'helixsr_setup.json').write_text('{}', encoding='utf-8')
    stub = STUB.replace('@MODERN@', str(not legacy))
    if legacy:
        (folder / 'setup' / 'helixsr_install.py').write_text(stub, encoding='utf-8')
        (folder / 'amd_fidelityfx_dx12.dll').write_bytes(b'MZ\x00HelixSR 1.9.3\x00')
        return folder
    (folder / 'setup' / 'helixsr_games.py').write_text(stub, encoding='utf-8')
    (folder / 'setup' / 'helixsr_bridge.py').write_text(BRIDGE_STUB, encoding='utf-8')
    (folder / 'setup' / 'helixsr_install.py').write_text(   # the games step: AMDLSS must never run it
        "raise SystemExit('the 2.0 games step installs into every game under --yes: AMDLSS must not run it')\n",
        encoding='utf-8')
    (folder / 'HelixSR-fsrfg').mkdir()
    (folder / 'HelixSR-fsrfg' / 'amd_fidelityfx_dx12.dll').write_bytes(b'MZ\x00HelixSR 2.0.0\x00')
    return folder


def old_install(game_folder, route='optiscaler'):
    """An OptiScaler install of an earlier version, next to a game's .exe, the way the real one records it: the
    files, HelixSR\\helixsr.dll, and HelixSR\\helixsr-install.json with route/created so `uninstall N` removes it.
    Returns (the record, every file it put there, in creation order)."""
    target = Path(game_folder)
    folder = target / 'HelixSR'
    folder.mkdir(parents=True, exist_ok=True)
    record = {'route': route, 'optiscaler': 'dxgi.dll', 'ini': 'created',
              'created': ['dxgi.dll', 'OptiScaler.ini', 'HelixSR/helixsr.dll',
                          'HelixSR/amd_fidelityfx_framegeneration_dx12.dll']}
    (target / 'dxgi.dll').write_bytes(b'OPTISCALER 0.9.4')
    (target / 'OptiScaler.ini').write_bytes(
        b'[FrameGen]\r\n; earlier version\r\nFGInput=auto\r\nFGOutput=auto\r\n\r\n[Libraries]\r\nFfxDx12Path=auto\r\n'
        b'FfxDx12SRPath=auto\r\n')
    (folder / 'helixsr.dll').write_bytes(b'HELIX\x00HelixSR 1.9.0\x00')
    (folder / 'amd_fidelityfx_framegeneration_dx12.dll').write_bytes(b'AMD FG')
    (folder / 'helixsr-install.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
    return record, list(record['created'])


def scaler_install(game_folder):
    """A HelixSR 2.0.0 Scaler install next to a game's .exe, the way its own setup records it: dxgi.dll,
    OptiScaler.ini, HelixSR-fsrfg\\amd_fidelityfx_dx12.dll and HelixSR-Scaler.files (the record the bridge's remove
    reads). AMDLSS 1.0.0's older bridge recorded such installs in HelixSR\\helixsr-install.json instead (see
    old_install). Returns the record."""
    target = Path(game_folder)
    folder = target / 'HelixSR-fsrfg'
    folder.mkdir(parents=True, exist_ok=True)
    (target / 'dxgi.dll').write_bytes(b'OPTISCALER 10.0.0')
    (target / 'OptiScaler.ini').write_bytes(b'[FrameGen]\r\nFGInput=auto\r\n')
    (folder / 'amd_fidelityfx_dx12.dll').write_bytes(b'HELIX\x00HelixSR 2.0.0\x00')
    record = {'name': 'dxgi.dll', 'version': '1.1.0',
              'files': ['dxgi.dll', 'OptiScaler.ini', 'HelixSR-fsrfg/amd_fidelityfx_dx12.dll'], 'by_hand': False}
    (target / 'HelixSR-Scaler.files').write_text(json.dumps(record, indent=1), encoding='utf-8')
    return record


def age(folder, version='1.8.1'):
    """Turns a make() folder into one an earlier AMDLSS built and the window has not updated yet: the 1.9 layout (the
    DLL at the root, setup/helixsr_install.py as the module and the command line, no bridge), at `version`."""
    folder = Path(folder)
    setup = folder / 'setup'
    games = setup / 'helixsr_games.py'
    if games.is_file():
        (setup / 'helixsr_install.py').write_text(games.read_text(encoding='utf-8').replace('MODERN = True', 'MODERN = False'),
                                                  encoding='utf-8')
        games.unlink()
        (setup / 'helixsr_bridge.py').unlink()
    (folder / 'HelixSR-fsrfg' / 'amd_fidelityfx_dx12.dll').unlink(missing_ok=True)
    try:
        (folder / 'HelixSR-fsrfg').rmdir()
    except OSError:
        pass
    (folder / 'amd_fidelityfx_dx12.dll').write_bytes(f'MZ\x00HelixSR {version}\x00'.encode())
    return folder
