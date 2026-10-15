# SPDX-License-Identifier: GPL-2.0-or-later
"""FSR 4.1.1 as a per-game upscaler: which DLL, which games, and the choice kept per game."""
import hashlib
from pathlib import Path

import pytest

from amdlss import fsr4, games, gpu, helixsr, settings
from test_pe import build_dll

AMD_BYTES = build_dll(version=(4, 1, 1, 2740))


@pytest.fixture
def helix(tmp_path, monkeypatch):
    """A HelixSR folder holding a stand-in for AMD's DLL, with the pins pointed at it."""
    folder = tmp_path / 'HelixSR-fake'
    (folder / 'amd-fsr').mkdir(parents=True)
    (folder / 'amd-fsr' / fsr4.DLL).write_bytes(AMD_BYTES)
    (folder / 'amd-fsr' / fsr4.LICENSE).write_text('AMD license\n')
    monkeypatch.setattr(fsr4, 'AMD_SIZE', len(AMD_BYTES))
    monkeypatch.setattr(fsr4, 'AMD_SHA256', hashlib.sha256(AMD_BYTES).hexdigest())
    return folder


def picked(tmp_path, version=(4, 0, 2, 0), name='mine.dll', **options):
    path = tmp_path / name
    path.write_bytes(build_dll(version=version, **options))
    return path


def game(**fields):
    base = dict(name='G', folder='/g', exe_folder='/g', inputs=['DLSS'], fsr_targets=[], can_opti=False,
                route='optiscaler')
    base.update(fields)
    return games.Game(**base)


def ready(tmp_path):
    return fsr4.Source(tmp_path / 'x.dll', "AMD's DLL 4.1.1.2740", '', '')


# --- the DLL ------------------------------------------------------------------------------------------------------

def test_rdna3_and_rdna4_use_amds_dll_from_the_helixsr_folder(helix, tmp_path):
    for family in (gpu.RDNA3, gpu.RDNA4):
        source = fsr4.find_source(family, helix, tmp_path / 'data')
        assert source.path == helix / 'amd-fsr' / fsr4.DLL and not source.problem
        assert source.label == "AMD's DLL 4.1.1.2740"


def test_a_changed_or_missing_amd_dll_is_not_used(helix, tmp_path):
    (helix / 'amd-fsr' / fsr4.DLL).write_bytes(AMD_BYTES + b'!')
    source = fsr4.find_source(gpu.RDNA4, helix, tmp_path / 'data')
    assert source.path is None and 'changed' in source.problem
    (helix / 'amd-fsr' / fsr4.DLL).unlink()
    assert fsr4.find_source(gpu.RDNA4, helix, tmp_path / 'data').path is None
    assert 'HelixSR' in fsr4.find_source(gpu.RDNA4, None, tmp_path / 'data').problem


def test_amds_dll_comes_from_the_scaler_package_when_the_folder_has_none(helix, tmp_path):
    # HelixSR 1.9.0 does not build amd-fsr: the HelixSR Scaler package carries AMD's DLL and its license
    from fake_scaler import make as make_package
    (helix / 'amd-fsr' / fsr4.DLL).unlink()
    data = tmp_path / 'data'
    package = make_package(data)
    (package / fsr4.PACKAGE_DLL).write_bytes(AMD_BYTES)
    (package / fsr4.PACKAGE_LICENSE).write_text('AMD license\n')
    source = fsr4.find_source(gpu.RDNA4, helix, data)
    assert source.path == package / fsr4.PACKAGE_DLL and source.label == "AMD's DLL 4.1.1.2740"
    assert fsr4.license_of(helix, data) == package / fsr4.PACKAGE_LICENSE
    (package / fsr4.PACKAGE_DLL).write_bytes(AMD_BYTES + b'!')
    assert 'changed' in fsr4.find_source(gpu.RDNA4, helix, data).problem
    (package / fsr4.PACKAGE_DLL).unlink()
    assert fsr4.find_source(gpu.RDNA4, helix, data).path is None
    assert fsr4.license_of(helix, data) is None


def test_rdna2_needs_a_dll_the_user_picked(helix, tmp_path):
    data = tmp_path / 'data'
    source = fsr4.find_source(gpu.RDNA2, helix, data)
    assert source.path is None and 'Choose FSR 4 DLL' in source.problem
    assert fsr4.choose_dll(picked(tmp_path), data) == '4.0.2.0'
    source = fsr4.find_source(gpu.RDNA2, helix, data)
    assert source.path == data / 'fsr4' / fsr4.DLL and source.label == 'your DLL 4.0.2.0' and source.user
    fsr4.forget_dll(data)
    assert fsr4.find_source(gpu.RDNA2, helix, data).path is None and not fsr4.user_dll(data)


def test_a_picked_dll_overrides_amds_on_rdna3_but_never_rescues_another_card(helix, tmp_path):
    data = tmp_path / 'data'
    fsr4.choose_dll(picked(tmp_path, version=(4, 1, 1, 7)), data)
    assert fsr4.find_source(gpu.RDNA3, helix, data).label == 'your DLL 4.1.1.7'
    assert fsr4.find_source(gpu.OTHER, helix, data).path is None


def test_a_picked_dll_is_checked(tmp_path):
    data = tmp_path / 'data'
    for bad, words in ((picked(tmp_path, name='a.dll', machine=0x14c, magic=0x10b), '64-bit'),
                       (picked(tmp_path, name='b.dll', exports=('Other',)), 'ffxCreateContext'),
                       (picked(tmp_path, name='c.dll', version=(3, 1, 5, 0)), 'starts with 4'),
                       (picked(tmp_path, name='d.dll', resources=False), 'starts with 4')):
        with pytest.raises(helixsr.HelixSRError, match=words):
            fsr4.choose_dll(bad, data)
    (tmp_path / 'e.dll').write_bytes(b'not a dll')
    with pytest.raises(helixsr.HelixSRError, match='Windows'):
        fsr4.choose_dll(tmp_path / 'e.dll', data)
    assert not (data / 'fsr4').exists()   # nothing was kept for a refused file
    assert fsr4.choose_dll(picked(tmp_path, version=(4, 1, 1, 3), name='f.dll'), data) == '4.1.1.3'   # a community 4.1.x


def test_a_kept_dll_that_went_bad_is_ignored(helix, tmp_path):
    data = tmp_path / 'data'
    fsr4.choose_dll(picked(tmp_path), data)
    (data / 'fsr4' / fsr4.DLL).write_bytes(b'broken')
    assert fsr4.user_dll(data) is None


# --- which games --------------------------------------------------------------------------------------------------

def test_a_game_with_an_fsr_dll_gets_the_drop_in_route(tmp_path):
    result = fsr4.availability(game(inputs=[], fsr_targets=['/g/amd_fidelityfx_dx12.dll']), ready(tmp_path))
    assert result.ok and result.routes == ('direct',) and result.short == 'yes'


def test_a_game_with_only_dlss_gets_the_optiscaler_route(tmp_path):
    result = fsr4.availability(game(), ready(tmp_path))
    assert result.ok and result.routes == ('optiscaler',)


def test_a_game_with_both_gets_both_routes(tmp_path):
    result = fsr4.availability(game(fsr_targets=['/g/a.dll'], can_opti=True, route='direct'), ready(tmp_path))
    assert result.routes == ('direct', 'optiscaler')


def test_anti_cheat_says_no_with_the_ban_warning(tmp_path):
    result = fsr4.availability(game(anticheat=['Easy Anti-Cheat']), ready(tmp_path))
    assert not result.ok and result.short == 'anti-cheat'
    assert 'Easy Anti-Cheat' in result.reason and 'banned' in result.reason


def test_a_refused_folder_comes_before_anti_cheat(tmp_path):
    result = fsr4.availability(game(anticheat=['BattlEye'], reason=games.UNSAFE_FOLDER), ready(tmp_path))
    assert not result.ok and result.short == 'folder' and 'other games' in result.reason


def test_a_card_that_cannot_run_it_says_so_before_the_game_is_judged(tmp_path):
    source = fsr4.Source(None, '', "Your graphics card can't run FSR 4; AMD's DLL would run FSR 3.1.5.", 'your card')
    result = fsr4.availability(game(inputs=[]), source)
    assert not result.ok and result.short == 'your card' and "can't run FSR 4" in result.reason


def test_a_game_with_nothing_to_replace_says_no(tmp_path):
    result = fsr4.availability(game(inputs=[]), ready(tmp_path))
    assert not result.ok and result.short == 'nothing to replace'
    hidden = fsr4.availability(game(inputs=[], reason=games.NO_UPSCALER), ready(tmp_path))
    assert not hidden.ok and games.NO_UPSCALER in hidden.reason


def test_the_card_line_reads_yes_or_no_and_why(tmp_path):
    assert fsr4.availability(game(), ready(tmp_path)).card_text == 'FSR 4.1.1: yes'
    assert fsr4.availability(game(anticheat=['BattlEye']), ready(tmp_path)).card_text == 'FSR 4.1.1: no, anti-cheat'


# --- the choice and the state ------------------------------------------------------------------------------------

def test_the_choice_is_kept_per_game_folder(tmp_path):
    assert settings.load_upscaler(tmp_path, '/games/A') == 'helixsr'
    settings.save_upscaler(tmp_path, '/games/A', 'fsr4')
    settings.save_upscaler(tmp_path, '/games/B', 'helixsr')
    assert settings.load_upscaler(tmp_path, '/games/A') == 'fsr4'
    assert settings.load_upscaler(tmp_path, '/games/B') == 'helixsr'
    settings.save_beta(tmp_path, True)   # the other settings stay
    assert settings.load_upscaler(tmp_path, '/games/A') == 'fsr4'
    settings.save_upscaler(tmp_path, '/games/A', 'nonsense')
    assert settings.load_upscaler(tmp_path, '/games/A') == 'helixsr'


def test_a_game_installed_with_fsr4_says_so():
    drop = game(state='fsr4', upscaler='fsr4')
    assert drop.installed and drop.state_text == 'FSR 4.1.1 drop-in' and drop.in_game() == 'AMD FSR'
    opti = game(state='optiscaler', upscaler='fsr4')
    assert opti.state_text == 'FSR 4.1.1 (OptiScaler)' and opti.in_game() == 'DLSS'
    assert game(state='optiscaler').state_text == 'OptiScaler'


# --- what the window says -----------------------------------------------------------------------------------------

def test_the_plan_names_the_files_the_loader_download_and_the_license(tmp_path):
    source = fsr4.Source(tmp_path / 'x.dll', "AMD's DLL 4.1.1.2740", '', '')
    old = game(fsr_targets=['/g/amd_fidelityfx_dx12.dll'], route='direct', inputs=[])
    text = fsr4.plan_text(old, 'direct', source, tmp_path / 'data')
    assert 'amd_fidelityfx_dx12.dll' in text and '*.original.dll' in text and 'AMD\'s DLL 4.1.1.2740' in text
    assert 'downloads AMD\'s FSR loader' in text and 'license' in text and 'Close the game first' in text
    new = game(fsr_targets=['/g/amd_fidelityfx_upscaler_dx12.dll'], route='direct', inputs=[])
    assert 'loader' not in fsr4.plan_text(new, 'direct', source, tmp_path / 'data')
    (tmp_path / 'data' / fsr4.USER_FOLDER).mkdir(parents=True)
    (tmp_path / 'data' / fsr4.USER_FOLDER / fsr4.LOADER).write_bytes(b'x')
    assert 'loader' not in fsr4.plan_text(old, 'direct', source, tmp_path / 'data')   # already kept
    assert 'HelixSR is removed first' in fsr4.plan_text(game(state='direct', fsr_targets=['/g/a.dll']), 'direct', source,
                                                         tmp_path)
    assert 'OptiScaler' in fsr4.plan_text(game(), 'optiscaler', source, tmp_path)


def test_the_after_text_says_what_to_pick_in_the_game(tmp_path):
    source = fsr4.Source(tmp_path / 'x.dll', 'your DLL 4.1.1.9', '', '')
    drop = fsr4.after_text(game(name='Fable'), 'direct', source)
    assert 'FSR 4.1.1 is installed in Fable' in drop and 'choose AMD FSR' in drop and 'your DLL 4.1.1.9' in drop
    opti = fsr4.after_text(game(name='Fable'), 'optiscaler', source, generator='amd')
    assert 'choose DLSS' in opti and 'FFX Upscaler' in opti and 'Insert' in opti and 'watermark' in opti
    assert games.FRAMEGEN_HINT in opti and games.FRAMEGEN_HINT not in fsr4.after_text(game(), 'optiscaler', source)
    assert games.FRAMEGEN_DLSS4_HINT in fsr4.after_text(game(), 'optiscaler', source, generator='dlss4')
