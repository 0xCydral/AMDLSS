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
        b'PreserveSwapChain=false', b'PreserveSwapChain=auto').replace(b'\r\nEnabled=true', b'\r\nEnabled=auto') == ini
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


def fsr4_world(fake_game, tmp_path):
    """The real client, AMD's real DLL from this HelixSR folder, and the fake game as HelixSR lists it."""
    from amdlss import fsr4, gpu
    copy, game = fake_game
    assert FAKE_ROOT in game.parents
    client = helixsr.HelixSR(copy)
    source = fsr4.find_source(gpu.RDNA4, copy, tmp_path / 'data')
    assert source.path and source.label == "AMD's DLL 4.1.1.2740"   # the pinned SHA-256 matches the real file

    def listed():
        entry = [g for g in client.list_games([game]) if Path(g.folder) == game][0]
        entry.fsr4 = fsr4.availability(entry, source)
        return fsr4.mark(entry)

    def go(want, route=None, entry=None):
        entry = entry or listed()
        entry.fsr4 = fsr4.availability(entry, source)
        return fsr4.install_game(client, entry, [game], want, route, source, fsr4.license_of(copy), tmp_path / 'data')

    return client, game, listed, go, source


def test_fsr4_on_the_optiscaler_route_and_back_with_the_real_helixsr(fake_game, tmp_path):
    from amdlss import fsr4
    client, game, listed, go, source = fsr4_world(fake_game, tmp_path)
    before = tree(game)
    assert listed().fsr4.ok and listed().fsr4.routes == ('direct', 'optiscaler')

    after = go('helixsr', 'optiscaler')
    helix_ini = (game / 'OptiScaler.ini').read_bytes()
    assert after.state == 'optiscaler' and after.upscaler == ''
    after = go('fsr4', entry=after)
    assert after.state == 'optiscaler' and after.upscaler == 'fsr4' and after.state_text == 'FSR 4.1.1 (OptiScaler)'
    ini = (game / 'OptiScaler.ini').read_bytes()
    assert fsr4.in_use(game) and ini != helix_ini
    assert re.search(rb'(?m)^UpscalerIndex\s*=\s*auto', ini)   # left alone
    for name in fsr4.OPTI_COPIES:
        assert sha(game / 'HelixSR' / 'fsr4' / name) == hashlib.sha256(source.path.read_bytes()).hexdigest()
    assert (game / 'HelixSR' / 'fsr4' / fsr4.LICENSE).is_file()
    assert framegen.read_values(ini, fsr4.libraries(game)) == fsr4.libraries(game).values
    assert listed().upscaler == 'fsr4'                                  # a fresh scan sees it

    after = go('helixsr', entry=after)                                  # switching back restores HelixSR's values
    assert after.upscaler == '' and not (game / 'HelixSR' / 'fsr4').exists()
    assert (game / 'OptiScaler.ini').read_bytes() == helix_ini

    after = go('fsr4', entry=after)
    fsr4.remove_game(client, after, [game], tmp_path / 'data')
    assert tree(game) == before                                         # every file back, the fsr4 folder gone too
    assert not (tmp_path / 'data' / framegen.RECORD).exists()


def test_fsr4_drop_in_and_switching_with_the_real_helixsr(fake_game, tmp_path):
    from amdlss import fsr4
    client, game, listed, go, source = fsr4_world(fake_game, tmp_path)
    dll = game / fsr4.DLL
    dll.write_bytes(b'THE GAMES OWN FSR 3.1 DLL')
    before = tree(game)
    original = game / 'amd_fidelityfx_upscaler_dx12.original.dll'

    after = go('fsr4', 'direct')
    assert after.state == 'fsr4' and after.in_game() == 'AMD FSR' and listed().state == 'fsr4'
    assert dll.read_bytes() == source.path.read_bytes() and original.read_bytes() == b'THE GAMES OWN FSR 3.1 DLL'
    assert (game / fsr4.MARKER).is_file() and (game / fsr4.LICENSE).is_file()
    with pytest.raises(helixsr.HelixSRError, match='FSR 4.1.1'):
        client.install(listed(), [game])                                # HelixSR refuses to install over ours

    after = go('helixsr', entry=after)                                  # ours is removed, then HelixSR goes in
    assert after.state == 'direct' and re.search(rb'HelixSR \d+\.\d+\.\d+', dll.read_bytes()[:4 << 20])
    assert not (game / fsr4.MARKER).exists() and original.read_bytes() == b'THE GAMES OWN FSR 3.1 DLL'

    after = go('fsr4', entry=after)                                     # HelixSR is removed, then ours goes in
    assert after.state == 'fsr4' and dll.read_bytes() == source.path.read_bytes()
    assert not (game / 'helixsr-install.json').exists()

    fsr4.remove_game(client, after, [game], tmp_path / 'data')
    assert tree(game) == before
