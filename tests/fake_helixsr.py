# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for HelixSR's setup folder, so tests can run AMDLSS's whole path (bridge, list parsing, install and
remove command lines) with no HelixSR and no game. make() writes setup/helixsr_install.py, which has the same
module-level names and command line as HelixSR's (games, original, is_helixsr, Game.* attributes; `list`,
`install N`, `uninstall N`, `--folder PATH`) and does the FSR drop-in with plain file renames."""
import json
from pathlib import Path

STUB = '''
import json, os, sys
from pathlib import Path


def original(t):
    return t.with_name(t.stem + '.original.dll')


def is_helixsr(t):
    try:
        return t.read_bytes().startswith(b'HELIX')
    except OSError:
        return False


class Game:
    def __init__(self, name, folder, fsr, inputs):
        self.name, self.folder = name, Path(folder)
        self.fsr_targets = [self.folder / 'amd_fidelityfx_upscaler_dx12.dll'] if fsr else []
        self.inputs, self.target = inputs, self.folder
        self.route = 'direct' if fsr else 'optiscaler'
        self.dlss_installed = False
        self.opti_installed = False

    def installed(self):
        return any(original(t).exists() and is_helixsr(t) for t in self.fsr_targets)

    def label(self):
        if self.route == 'direct':
            return "FSR 3.1 (HelixSR replaces the game's FSR DLL: select AMD FSR in the game)"
        return ' / '.join(self.inputs) + ' (through OptiScaler: select DLSS in the game)'


def games(extra):
    here = Path(__file__).resolve().parent.parent
    rows = json.loads((here / 'games.json').read_text())
    out = [Game(r['name'], r['folder'], r['fsr'], r['inputs']) for r in rows]
    out += [Game(Path(f).name, f, True, ['DLSS']) for f in extra]
    return out


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
        print(f'[helixsr-setup] {g.name}', flush=True)
        for t in g.fsr_targets:
            if cmd == 'install':
                os.replace(t, original(t))
                t.write_bytes(b'HELIX stand-in')
            else:
                os.replace(original(t), t)
        print('[helixsr-setup] done', flush=True)
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
