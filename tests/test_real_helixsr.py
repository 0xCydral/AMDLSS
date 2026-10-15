# SPDX-License-Identifier: GPL-2.0-or-later
"""The real thing, without a real game: HelixSR's own installer and HelixSR Scaler into a FAKE game folder.

Skipped unless AMDLSS_TEST_HELIXSR names a built HelixSR folder (one that holds helixsr_setup.json) and
AMDLSS_TEST_SCALER names the unpacked HelixSR Scaler package (HelixSR-Scaler-1.0.3, the one with OptiScaler.dll);
the Python HelixSR's setup uses must be there (%LOCALAPPDATA%\\HelixSR\\python, or on Linux a python3 with numpy).
The HelixSR folder is copied into the temp folder's amdlss-fake first, so HelixSR's own files are never written; the
Scaler package is only read. The install goes only into the fake game there. AMDLSS_TEST_INSTALLER may name a newer
setup/helixsr_install.py to lay over the copy. AMDLSS_TEST_ROOT replaces the %TEMP%\\amdlss-fake folder. Everything
under that folder is deleted at the end.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from amdlss import framegen, fsr4, games, helixsr, logs, scaler

SOURCE = os.environ.get('AMDLSS_TEST_HELIXSR', '')
SCALER_SOURCE = os.environ.get('AMDLSS_TEST_SCALER', '')
pytestmark = pytest.mark.skipif(
    not (SOURCE and helixsr.built(SOURCE) and helixsr.find_python() is not None and SCALER_SOURCE
         and (Path(SCALER_SOURCE) / scaler.DLL).is_file()),
    reason='AMDLSS_TEST_HELIXSR does not name a built HelixSR folder, or AMDLSS_TEST_SCALER a Scaler package')
FAKE_ROOT = Path(os.environ.get('AMDLSS_TEST_ROOT') or Path(tempfile.gettempdir()) / 'amdlss-fake')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def amd_dll():
    """AMD's real FSR 4 DLL: from the HelixSR folder when that build has it (1.9.0.2), else the Scaler package."""
    legacy = Path(SOURCE) / 'amd-fsr' / fsr4.DLL
    return legacy if legacy.is_file() else Path(SCALER_SOURCE) / 'OptiScaler' / fsr4.DLL


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
    shutil.copyfile(amd_dll(), game / 'amd_fidelityfx_upscaler_dx12.dll')
    yield copy, game.resolve()
    shutil.rmtree(FAKE_ROOT, ignore_errors=True)


def listed(client, game):
    return [g for g in client.list_games([game]) if Path(g.folder) == game][0]


def test_install_and_remove_into_a_fake_game(fake_game):
    copy, game = fake_game
    assert FAKE_ROOT in game.parents  # nothing below may ever target a real game
    before = tree(game)
    fsr_dll = game / 'amd_fidelityfx_upscaler_dx12.dll'
    client = helixsr.HelixSR(copy)
    entry = listed(client, game)
    assert len([g for g in client.list_games([game]) if Path(g.folder) == game]) == 1
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


def test_the_scaler_route_with_frame_generation_into_a_fake_game(fake_game, tmp_path, monkeypatch):
    # the OptiScaler route: AMDLSS downloads and places HelixSR Scaler 1.0.3 (the real package, read-only);
    # OptiScaler's own streamline DLLs and HelixSR's built DLL go in, its ini gets AMDLSS's frame generation
    # switches, and Remove puts every game file back byte for byte
    copy, game = fake_game
    assert FAKE_ROOT in game.parents
    before = tree(game)
    monkeypatch.setenv('AMDLSS_SCALER', SCALER_SOURCE)
    client = helixsr.HelixSR(copy)
    entry = listed(client, game)
    assert entry.can_opti and entry.route_choice and entry.route == 'direct' and entry.default_route == 'optiscaler'

    lines = []
    after = scaler.install_game(client, entry, [game], tmp_path / 'data', lambda text, final: lines.append(text))
    assert after.state == 'optiscaler' and after.in_game() == 'DLSS'
    assert scaler.installed(game) and scaler.mark(listed(client, game)).state == 'optiscaler'
    assert (game / 'dxgi.dll').read_bytes() == (Path(SCALER_SOURCE) / 'OptiScaler.dll').read_bytes()
    assert (game / 'OptiScaler' / 'streamline' / 'sl.dlss_g.dll').is_file()
    assert (game / 'HelixSR' / 'helixsr.dll').read_bytes() == (copy / 'amd_fidelityfx_dx12.dll').read_bytes()
    assert (game / 'amd_fidelityfx_upscaler_dx12.dll').read_bytes() == Path(amd_dll()).read_bytes()  # not touched
    assert not (game / 'setup_linux.sh').exists()   # the download's own files are not copied
    ini = (game / 'OptiScaler.ini').read_bytes()
    assert not framegen.is_on(game) and b'FGInput=auto' in ini

    assert framegen.apply(game, True, tmp_path / 'data')   # AMD FSR frame generation, as before
    on = (game / 'OptiScaler.ini').read_bytes()
    assert framegen.is_on(game)
    assert on.replace(b'FGInput=dlssg', b'FGInput=auto').replace(b'FGOutput=fsrfg', b'FGOutput=auto').replace(
        b'PreserveSwapChain=false', b'PreserveSwapChain=auto').replace(b'\r\nEnabled=true', b'\r\nEnabled=auto') == ini
    assert framegen.apply(game, False, tmp_path / 'data') and (game / 'OptiScaler.ini').read_bytes() == ini

    assert framegen.apply(game, True, tmp_path / 'data', framegen.LOGGING)   # the real [Log] section has both keys
    assert framegen.is_on(game, framegen.LOGGING) and b'LogToFile=true' in (game / 'OptiScaler.ini').read_bytes()
    (game / 'OptiScaler.log').write_text('[Info] a log the switch made')
    with zipfile.ZipFile(logs.collect(after, copy, tmp_path / 'data', tmp_path / 'out', {}, windows=False)) as archive:
        names = set(archive.namelist())
    assert {'exe-folder/OptiScaler.log', 'exe-folder/OptiScaler.ini', 'exe-folder/HelixSR/amdlss-scaler.json',
            'files.txt', 'system.txt'} <= names, names

    fsr4.remove_game(client, scaler.mark(listed(client, game)), [game], tmp_path / 'data')
    framegen.forget(game, tmp_path / 'data')
    assert tree(game) == before  # every file back, byte for byte, OptiScaler.ini and the HelixSR folder gone
    assert not (tmp_path / 'data' / framegen.RECORD).exists()


def test_dlss4_frame_generation_on_the_real_scaler_ini(fake_game, tmp_path, monkeypatch):
    # AMDLSS's DLSS 4 switch over the real OptiScaler.ini HelixSR Scaler ships, with the real build's FG files:
    # [HelixSR] FrameGeneration=on, [FrameGen] FGInput=nvngxfg and FGNvngxReplacement=HelixSR, and every value
    # back as it was
    copy, game = fake_game
    monkeypatch.setenv('AMDLSS_SCALER', SCALER_SOURCE)
    assert helixsr.fg_reason(copy) == ''   # helixsr-fg (nvexec64, nvngx_dlssg, _nvngx) and helixsr_fg_setup.json
    client = helixsr.HelixSR(copy)
    scaler.install_game(client, listed(client, game), [game], tmp_path / 'data')
    ini = (game / 'OptiScaler.ini').read_bytes()
    assert b'FrameGeneration=auto' in ini and framegen.generator_of(game) == ''

    assert framegen.apply_generator(game, framegen.DLSS4, tmp_path / 'data')
    data = (game / 'OptiScaler.ini').read_bytes()
    assert framegen.generator_of(game) == framegen.DLSS4
    assert b'FrameGeneration=on' in data and b'FGInput=nvngxfg' in data and b'FGNvngxReplacement=HelixSR' in data

    assert framegen.apply_generator(game, framegen.AMD, tmp_path / 'data')
    amd = (game / 'OptiScaler.ini').read_bytes()
    assert b'FGInput=dlssg' in amd and b'FGOutput=fsrfg' in amd and b'FrameGeneration=auto' in amd
    assert b'FGNvngxReplacement=auto' in amd
    assert framegen.generator_of(game) == framegen.AMD

    assert framegen.apply_generator(game, framegen.DLSS4, tmp_path / 'data')
    assert framegen.apply_generator(game, '', tmp_path / 'data')
    assert (game / 'OptiScaler.ini').read_bytes() == ini and framegen.generator_of(game) == ''


def test_an_earlier_versions_install_is_recognised_and_removed(fake_game):
    # an AMDLSS 1.0.0 install: the old bridge called HelixSR's own installer, whose record lives in
    # HelixSR\helixsr-install.json. The new installer reports it and removes it with its own uninstall_optiscaler.
    copy, game = fake_game
    before = tree(game)
    folder = game / 'HelixSR'
    folder.mkdir()
    (game / 'dxgi.dll').write_bytes(b'OPTISCALER 0.9.4')
    (game / 'OptiScaler.ini').write_bytes(b'[FrameGen]\r\nFGInput=auto\r\nFGOutput=auto\r\n')
    (folder / 'helixsr.dll').write_bytes(b'HELIX\x00HelixSR 1.9.0\x00')
    (folder / 'amd_fidelityfx_framegeneration_dx12.dll').write_bytes(b'AMD FG')
    (folder / 'helixsr-install.json').write_text(json.dumps(
        {'route': 'optiscaler', 'optiscaler': 'dxgi.dll', 'ini': 'created',
         'created': ['dxgi.dll', 'OptiScaler.ini', 'HelixSR/helixsr.dll',
                     'HelixSR/amd_fidelityfx_framegeneration_dx12.dll']}), encoding='utf-8')
    client = helixsr.HelixSR(copy)
    entry = listed(client, game)
    assert entry.state == 'optiscaler' and entry.helixsr == '1.9.0' and entry.installed
    client.remove(entry, [game])
    assert tree(game) == before


def test_an_xbox_gdk_folder_is_found_under_its_content_folder(fake_game):
    # HelixSR 1.9.0 scans 16 folders deep (Unreal Engine plugins sit 8 to 10 deep), so a GDK game's own files,
    # deep inside Content, are found: the game is listed under its Content folder and its config name, and no
    # widened-over-other-games entry is made any more.
    copy, _game = fake_game
    drive = FAKE_ROOT / 'drive'
    content = drive / 'xcaja' / 'Valor Mortis' / 'Content'
    win64 = content / 'ValorMortis' / 'Binaries' / 'Win64'
    win64.mkdir(parents=True)
    (win64 / 'ValorMortisMSG-Win64-Shipping.exe').write_bytes(b'M' * 500000)
    (win64 / 'GameChat2.dll').write_bytes(b'MZ')
    (content / 'MicrosoftGame.config').write_text(
        '<Game><Identity Name="ValorMortis"/><ShellVisuals DefaultDisplayName="Valor Mortis"/></Game>')
    deep = content / 'a' / 'b' / 'c' / 'd' / 'e' / 'f' / 'g' / 'h'
    deep.mkdir(parents=True)
    (deep / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'own fsr')
    other = drive / 'OtherGame'
    other.mkdir(parents=True)
    (other / 'Other-Win64-Shipping.exe').write_bytes(b'M' * 1000)
    shutil.copyfile(amd_dll(), other / 'amd_fidelityfx_upscaler_dx12.dll')
    before = tree(other)
    client = helixsr.HelixSR(copy)
    rows = games.classify(client.list_games([content, other]), [str(content), str(other)])
    valor = next(g for g in rows if Path(g.exe_folder) == win64.resolve())
    assert valor.folder == str(content.resolve()) and valor.name == 'Valor Mortis'
    assert valor.reason == '' and valor.can_install and valor.fsr31        # the deep FSR 3.1 DLL is found now
    assert [str(t) for t in valor.fsr_targets] == [str(deep / 'amd_fidelityfx_upscaler_dx12.dll')]
    assert tree(other) == before


def fsr4_world(fake_game, tmp_path, monkeypatch):
    """The real client, AMD's real DLL (from the Scaler package on 1.9.0), and the fake game as HelixSR lists it."""
    from amdlss import fsr4, gpu
    copy, game = fake_game
    assert FAKE_ROOT in game.parents
    monkeypatch.setenv('AMDLSS_SCALER', SCALER_SOURCE)
    client = helixsr.HelixSR(copy)
    source = fsr4.find_source(gpu.RDNA4, copy, tmp_path / 'data')
    assert source.path and source.label == "AMD's DLL 4.1.1.2740"   # the pinned SHA-256 matches the real file

    def listed_():
        entry = [g for g in client.list_games([game]) if Path(g.folder) == game][0]
        entry.fsr4 = fsr4.availability(entry, source)
        return fsr4.mark(entry)

    def go(want, route=None, entry=None):
        entry = entry or listed_()
        entry.fsr4 = fsr4.availability(entry, source)
        return fsr4.install_game(client, entry, [game], want, route, source, fsr4.license_of(copy, tmp_path / 'data'),
                                 tmp_path / 'data')

    return client, game, listed_, go, source


def test_fsr4_on_the_optiscaler_route_and_back_with_the_real_helixsr(fake_game, tmp_path, monkeypatch):
    from amdlss import fsr4
    client, game, listed, go, source = fsr4_world(fake_game, tmp_path, monkeypatch)
    before = tree(game)
    assert listed().fsr4.ok and listed().fsr4.routes == ('direct', 'optiscaler')

    after = go('helixsr', 'optiscaler')
    helix_ini = (game / 'OptiScaler.ini').read_bytes()
    assert after.state == 'optiscaler' and after.upscaler == '' and scaler.installed(game)
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


def test_fsr4_drop_in_and_switching_with_the_real_helixsr(fake_game, tmp_path, monkeypatch):
    from amdlss import fsr4
    client, game, listed, go, source = fsr4_world(fake_game, tmp_path, monkeypatch)
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
