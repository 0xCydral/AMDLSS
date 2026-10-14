# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for HelixSR's setup folder, so tests can run AMDLSS's whole path (bridge, list parsing, install and
remove command lines) with no HelixSR and no game. make() writes setup/helixsr_install.py, which has the same
module-level names and command line as HelixSR's (games, install, say, done_message, original, is_helixsr,
Game.* attributes; `list`, `install N`, `uninstall N`, `--folder PATH`). The FSR drop-in is plain file renames; the
OptiScaler route writes a marker file and an OptiScaler.ini next to the game, and uninstall deletes them."""
import json
from pathlib import Path

STUB = '''
import json, os, re, sys
from pathlib import Path

DLL = 'amd_fidelityfx_dx12.dll'
MARKER, FOLDER, OWN, CORE = 'helixsr-install.json', 'HelixSR', 'helixsr.dll', 'nvngx.dll'
INI = ('[FrameGen]\\r\\n; comment\\r\\nFGInput=auto\\r\\nFGOutput=auto\\r\\n\\r\\n[Other]\\r\\nKey = 1\\r\\n\\r\\n[Log]\\r\\nLogToFile=auto\\r\\nLogLevel=auto\\r\\n\\r\\n[Libraries]\\r\\nFfxDx12Path=auto\\r\\nFfxDx12SRPath=auto\\r\\n')


def original(t):
    return t.with_name(t.stem + '.original.dll')


def is_helixsr(t):
    try:
        return t.read_bytes().startswith(b'HELIX')
    except OSError:
        return False


def say(msg):
    print(f'[helixsr-setup] {msg}', flush=True)


def version_of(dll):
    if not Path(dll).is_file():
        return '?'
    found = re.search(rb'\\x00HelixSR (\\d+\\.\\d+\\.\\d+)\\x00', Path(dll).read_bytes()[:4 << 20])
    return found.group(1).decode() if found else '?'


def network_ready(here):
    return True


class Game:
    def __init__(self, name, folder, spec):
        fsr, inputs = spec
        self.name, self.folder = name, Path(folder)
        self.fsr_targets = [self.folder / 'amd_fidelityfx_upscaler_dx12.dll'] if fsr else []
        self.inputs, self.target = inputs, self.folder
        self.can_opti = bool(fsr and inputs)
        self.dlss_installed = False
        self.opti_installed = (self.folder / 'opti.marker').exists()
        self.chosen = self.opti_installed
        self.route = 'direct' if fsr and not self.chosen else 'optiscaler' if inputs else None

    def installed(self):
        return self.opti_installed or any(original(t).exists() and is_helixsr(t) for t in self.fsr_targets)

    def label(self):
        if self.route == 'direct':
            return "FSR 3.1 (HelixSR replaces the game's FSR DLL: select AMD FSR in the game)"
        return ' / '.join(self.inputs) + ' (through OptiScaler: select DLSS in the game)'


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


def install(g, here):
    if g.route == 'optiscaler':
        (g.folder / 'opti.marker').write_text('chosen' if g.chosen else '')
        if not (g.folder / 'OptiScaler.ini').exists():
            (g.folder / 'OptiScaler.ini').write_bytes(INI.encode())
        say(f'  installed in {g.folder} (OptiScaler)')
        return
    for t in g.fsr_targets:
        os.replace(t, original(t))
        t.write_bytes(b'HELIX stand-in')


def uninstall(g):
    if g.opti_installed:
        (g.folder / 'opti.marker').unlink()
        (g.folder / 'OptiScaler.ini').unlink(missing_ok=True)
        return
    for t in g.fsr_targets:
        os.replace(original(t), t)


def done_message(todo=()):
    say('Done. Start the game and pick this in its graphics settings:')
    for g in todo:
        say(f'  {g.name}: ' + ('DLSS' if g.route == 'optiscaler' else 'AMD FSR'))


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
    """A fake HelixSR folder under `root`; `games` is a list of {name, folder, fsr, inputs}."""
    folder = Path(root) / 'HelixSR-fake'
    (folder / 'setup').mkdir(parents=True)
    (folder / 'setup' / 'helixsr_install.py').write_text(STUB, encoding='utf-8')
    (folder / 'games.json').write_text(json.dumps(games), encoding='utf-8')
    return folder
