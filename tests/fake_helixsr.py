# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for HelixSR's setup folder, so tests can run AMDLSS's whole path (bridge, list parsing, install and
remove command lines) with no HelixSR and no game. make() writes setup/helixsr_install.py, which has the same
module-level names and command line as HelixSR 1.9.0's installer (`games`, `install`, `uninstall`, `say`,
`version_of`, `is_helixsr`, `original`, `FOLDER`/`MARKER`/`OWN`/`CORE`, Game.route/old_install()/installed();
`list`, `install N`, `uninstall N`, `--folder PATH`). Like the real one: the FSR drop-in is file renames plus the
install record next to the game's FSR DLL; games without an FSR 3.1 DLL are 'unsupported' (HelixSR Scaler is
AMDLSS's); an install of an earlier version (marker in HelixSR\\helixsr-install.json) can only be removed — see
old_install(), which writes one.
"""
import json
from pathlib import Path

STUB = '''
import json, os, re, sys
from pathlib import Path

DLL = 'amd_fidelityfx_dx12.dll'
DLL_SR = 'amd_fidelityfx_upscaler_dx12.dll'
FOLDER, MARKER, BACKUP = 'HelixSR', 'helixsr-install.json', 'backup'
OWN, OWN_SR, CORE = 'helixsr.dll', 'helixsr_upscaler.dll', 'nvngx.dll'
FINI = 'helixsr.ini'
UNSUPPORTED = 'not supported by the drop-in: use HelixSR Desktop (HelixSR Scaler)'
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
        fsr, inputs = spec
        self.name, self.folder = name, Path(folder)
        self.fsr_targets = [self.folder / DLL_SR] if fsr else []
        self.inputs, self.target = inputs, self.folder
        marker = self.folder / FOLDER / MARKER
        rec = read_marker(self.folder / FOLDER)
        self.dlss_installed = rec.get('route') == 'dlss'
        self.opti_installed = marker.exists() and not self.dlss_installed
        self.chosen = False
        if fsr:
            self.route = 'direct'
        elif self.dlss_installed:
            self.route = 'dlss'
        elif self.opti_installed:
            self.route = 'optiscaler'
        else:
            self.route = 'unsupported' if (self.inputs and self.target) else None

    def old_install(self):
        """'dlss' / 'optiscaler' for an install of an earlier version (can only be removed), else None."""
        return 'dlss' if self.dlss_installed else 'optiscaler' if self.opti_installed else None

    def installed(self):
        return bool(self.old_install()) or any(is_helixsr(t) for t in self.fsr_targets)

    def label(self):
        if self.fsr_targets:
            return "FSR 3.1 (HelixSR replaces the game's FSR DLL: select AMD FSR in the game)"
        return ' / '.join(self.inputs) + ' only: ' + UNSUPPORTED


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
    out = [g for g in (Game(r['name'], r['folder'], (r['fsr'], r['inputs'])) for r in rows) if g.route]
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


def install(g, here):
    if not g.fsr_targets:
        raise SystemExit(f'{g.name} has no FSR 3.1 DLL: {UNSUPPORTED}')
    if g.old_install():
        say('  removing the install of an earlier version first')
        uninstall_old(g)
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
    gl = games(extra)
    if cmd == 'list':
        for i, g in enumerate(gl, 1):
            print(f'  {i:3}. {g.name:45} {"HelixSR installed" if g.installed() else g.label()}', flush=True)
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


def make(root, games):
    """A fake HelixSR folder under `root`; `games` is a list of {name, folder, fsr, inputs}. The folder holds a
    built DLL and helixsr_setup.json, so it doubles as the source Scaler copies HelixSR's DLL from."""
    folder = Path(root) / 'HelixSR-fake'
    (folder / 'setup').mkdir(parents=True)
    (folder / 'setup' / 'helixsr_install.py').write_text(STUB, encoding='utf-8')
    (folder / 'games.json').write_text(json.dumps(games), encoding='utf-8')
    (folder / 'amd_fidelityfx_dx12.dll').write_bytes(b'MZ\x00HelixSR 1.9.0\x00')
    (folder / 'helixsr_setup.json').write_text('{}', encoding='utf-8')
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
