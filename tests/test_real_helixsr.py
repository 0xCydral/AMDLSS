# SPDX-License-Identifier: GPL-2.0-or-later
"""The real thing, without a real game: HelixSR's own installer (both routes) into a FAKE game folder.

Skipped unless AMDLSS_TEST_HELIXSR names a built HelixSR folder (one that holds helixsr_setup.json); the Python
HelixSR's setup uses must be there (%LOCALAPPDATA%\\HelixSR\\python, or on Linux a python3 with numpy). The folder
is copied into the temp folder's amdlss-fake first, so HelixSR's
own files are never written, and the install goes only into the fake game there. AMDLSS_TEST_INSTALLER may name a
newer setup/helixsr_install.py to lay over the copy. AMDLSS_TEST_ROOT replaces the %TEMP%\\amdlss-fake folder.
Everything under that folder is deleted at the end.
"""
import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from amdlss import framegen, games, helixsr, logs

SOURCE = os.environ.get('AMDLSS_TEST_HELIXSR', '')
pytestmark = pytest.mark.skipif(not (SOURCE and helixsr.built(SOURCE) and helixsr.find_python() is not None),
                                reason='AMDLSS_TEST_HELIXSR does not name a built HelixSR folder')
FAKE_ROOT = Path(os.environ.get('AMDLSS_TEST_ROOT') or Path(tempfile.gettempdir()) / 'amdlss-fake')


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


def test_optiscaler_route_with_frame_generation_into_a_fake_game(fake_game, tmp_path):
    copy, game = fake_game
    assert FAKE_ROOT in game.parents
    before = tree(game)
    client = helixsr.HelixSR(copy)
    entry = [g for g in client.list_games([game]) if Path(g.folder) == game][0]
    assert entry.can_opti and entry.route_choice and entry.route == 'direct' and entry.default_route == 'optiscaler'

    lines = []
    after = client.install(entry, [game], lambda text, final: lines.append(text), route='optiscaler')
    assert after.state == 'optiscaler' and after.in_game() == 'DLSS'
    fsr_dll = 'amd_fidelityfx_upscaler_dx12.dll'
    assert sha(game / fsr_dll) == before[fsr_dll]  # the drop-in was not used: the game's FSR DLL is untouched
    for name in ('dxgi.dll', 'OptiScaler.ini', 'fakenvapi.dll', 'HelixSR/helixsr-install.json',
                 'HelixSR/amd_fidelityfx_framegeneration_dx12.dll'):
        assert (game / name).is_file(), name
    assert any('Done. Start the game' in line for line in lines), lines
    assert [g for g in client.list_games([game]) if Path(g.folder) == game][0].state == 'optiscaler'

    ini = (game / 'OptiScaler.ini').read_bytes()
    assert framegen.apply(game, True, tmp_path)
    on = (game / 'OptiScaler.ini').read_bytes()
    assert framegen.is_on(game)
    assert on.replace(b'FGInput=dlssg', b'FGInput=auto').replace(b'FGOutput=fsrfg', b'FGOutput=auto').replace(
        b'PreserveSwapChain = false', b'PreserveSwapChain = auto') == ini
    assert b'FGInput=dlssg' in on and b'FGOutput=fsrfg' in on
    assert framegen.apply(game, False, tmp_path) and (game / 'OptiScaler.ini').read_bytes() == ini

    assert framegen.apply(game, True, tmp_path, framegen.LOGGING)   # the real [Log] section has both keys
    assert framegen.is_on(game, framegen.LOGGING) and b'LogToFile=true' in (game / 'OptiScaler.ini').read_bytes()
    (game / 'OptiScaler.log').write_text('[Info] a log the switch made')
    with zipfile.ZipFile(logs.collect(after, copy, tmp_path / 'data', tmp_path / 'out', {}, windows=False)) as archive:
        names = set(archive.namelist())
    assert {'exe-folder/OptiScaler.log', 'exe-folder/OptiScaler.ini', 'exe-folder/HelixSR/helixsr-install.json',
            'files.txt', 'system.txt'} <= names, names

    framegen.apply(game, True, tmp_path)
    listed =[g for g in client.list_games([game]) if Path(g.folder) == game][0]
    client.remove(listed, [game])
    framegen.forget(game, tmp_path)
    assert tree(game) == before  # every file back, byte for byte, OptiScaler.ini and the HelixSR folder gone


def test_an_xbox_gdk_folder_helixsr_widens_is_corrected_and_refused(fake_game):
    # 7B47A27X with HelixSR itself: its by-hand scan climbs from Content (no FSR DLL within its reach) to a
    # folder over other games. AMDLSS shows the GDK game under its config name and Content folder, refused.
    copy, _game = fake_game
    drive = FAKE_ROOT / 'drive'
    content = drive / 'xcaja' / 'Valor Mortis' / 'Content'
    win64 = content / 'ValorMortis' / 'Binaries' / 'Win64'
    win64.mkdir(parents=True)
    (win64 / 'ValorMortisMSG-Win64-Shipping.exe').write_bytes(b'M' * 500000)
    (win64 / 'GameChat2.dll').write_bytes(b'MZ')
    (content / 'MicrosoftGame.config').write_text(
        '<Game><Identity Name="ValorMortis"/><ShellVisuals DefaultDisplayName="Valor Mortis"/></Game>')
    deep = content / 'a' / 'b' / 'c' / 'd' / 'e' / 'f' / 'g' / 'h'   # its own FSR, past the installer's reach
    deep.mkdir(parents=True)
    (deep / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'own fsr')
    other = drive / 'OtherGame'
    other.mkdir(parents=True)
    (other / 'Other-Win64-Shipping.exe').write_bytes(b'M' * 1000)
    shutil.copyfile(Path(SOURCE) / 'amd-fsr' / 'amd_fidelityfx_upscaler_dx12.dll',
                    other / 'amd_fidelityfx_upscaler_dx12.dll')
    before = tree(other)
    client = helixsr.HelixSR(copy)
    rows = games.classify(client.list_games([content, other]), [str(content), str(other)])
    valor = next(g for g in rows if Path(g.exe_folder) == win64.resolve())
    assert valor.folder == str(content.resolve()) and valor.name == 'Valor Mortis'
    assert valor.reason == games.UNSAFE_FOLDER and not valor.can_install
    with pytest.raises(helixsr.HelixSRError, match='folder covers other games'):
        client.install(valor, [content, other])
    assert tree(other) == before
