# SPDX-License-Identifier: GPL-2.0-or-later
"""The real thing, without a real game: HelixSR's own installer (route f) into a FAKE game folder.

Skipped unless AMDLSS_TEST_HELIXSR names a built HelixSR folder (one that holds helixsr_setup.json); HelixSR's
Python must be in %LOCALAPPDATA%\\HelixSR\\python. The folder is copied into %TEMP%\\amdlss-fake first, so HelixSR's
own files are never written, and the install goes only into the fake game there. AMDLSS_TEST_INSTALLER may name a
newer setup/helixsr_install.py to lay over the copy. Everything under %TEMP%\\amdlss-fake is deleted at the end.
"""
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from amdlss import helixsr

SOURCE = os.environ.get('AMDLSS_TEST_HELIXSR', '')
pytestmark = pytest.mark.skipif(not (SOURCE and helixsr.built(SOURCE) and helixsr.helixsr_python().is_file()),
                                reason='AMDLSS_TEST_HELIXSR does not name a built HelixSR folder')
FAKE_ROOT = Path(tempfile.gettempdir()) / 'amdlss-fake'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


@pytest.fixture
def fake_game():
    shutil.rmtree(FAKE_ROOT, ignore_errors=True)
    FAKE_ROOT.mkdir()
    copy = FAKE_ROOT / 'HelixSR'
    shutil.copytree(SOURCE, copy)
    if os.environ.get('AMDLSS_TEST_INSTALLER'):
        shutil.copyfile(os.environ['AMDLSS_TEST_INSTALLER'], copy / 'setup' / 'helixsr_install.py')
    game = FAKE_ROOT / 'FakeGame'
    game.mkdir()
    (game / 'FakeGame.exe').write_bytes(b'MZ dummy executable')
    (game / 'nvngx_dlss.dll').write_bytes(b'dummy dlss')
    shutil.copyfile(Path(SOURCE) / 'amd-fsr' / 'amd_fidelityfx_upscaler_dx12.dll',
                    game / 'amd_fidelityfx_upscaler_dx12.dll')
    yield copy, game.resolve()
    shutil.rmtree(FAKE_ROOT, ignore_errors=True)


def test_install_and_remove_into_a_fake_game(fake_game):
    copy, game = fake_game
    assert FAKE_ROOT in game.parents  # nothing below may ever target a real game
    before = tree(game)
    fsr_dll = game / 'amd_fidelityfx_upscaler_dx12.dll'
    client = helixsr.HelixSR(copy)
    listed = [g for g in client.list_games([game]) if Path(g.folder) == game]
    assert len(listed) == 1
    entry = listed[0]
    assert entry.state == 'none' and entry.route == 'direct' and entry.fsr31 and 'DLSS' in entry.inputs

    lines = []
    after = client.install(entry, [game], lambda text, final: lines.append(text))
    assert after.state == 'direct' and after.in_game() == 'AMD FSR'
    assert re.search(rb'HelixSR \d+\.\d+\.\d+', fsr_dll.read_bytes()[:4 << 20])  # HelixSR is in place
    backup = game / 'amd_fidelityfx_upscaler_dx12.original.dll'
    assert sha(backup) == before['amd_fidelityfx_upscaler_dx12.dll']  # the game's own file is kept
    assert (game / 'helixsr-install.json').is_file()
    assert any('installed' in line for line in lines), lines

    client.remove(after, [game])
    assert not backup.exists() and not (game / 'helixsr-install.json').exists()
    assert tree(game) == before  # every file back, byte for byte, and nothing left behind
