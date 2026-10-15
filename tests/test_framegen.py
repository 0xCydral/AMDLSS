# SPDX-License-Identifier: GPL-2.0-or-later
"""The frame generation switch edits a few values in OptiScaler.ini and nothing else, and puts them back."""
import json

import pytest

from amdlss import framegen, games, helixsr

INI = (b'; header\r\n[Upscalers]\r\nDx12Upscaler = fsr31\r\n\r\n[FrameGen]\r\n; FGInput: dlssg, nukems; auto is nofg\r\n'
       b'FGInput=auto\r\n\r\n; output\r\nFGOutput=auto\r\nFTInput=auto\r\n\r\n[FSRFG]\r\nFGInput=decoy\r\n')


@pytest.fixture
def exe(tmp_path):
    folder = tmp_path / 'game'
    folder.mkdir()
    (folder / 'OptiScaler.ini').write_bytes(INI)
    return folder


def ini(exe):
    return (exe / 'OptiScaler.ini').read_bytes()


def test_only_the_two_values_change(exe, tmp_path):
    assert framegen.apply(exe, True, tmp_path)
    expected = INI.replace(b'FGInput=auto', b'FGInput=dlssg').replace(b'FGOutput=auto', b'FGOutput=fsrfg')
    assert ini(exe) == expected  # every other byte, the decoy FGInput in [FSRFG] and the CRLFs included
    assert framegen.is_on(exe)


def test_turning_it_off_puts_the_old_values_back(exe, tmp_path):
    (exe / 'OptiScaler.ini').write_bytes(INI.replace(b'FGInput=auto', b'FGInput = nofg'))  # the game's own spacing
    before = ini(exe)
    framegen.apply(exe, True, tmp_path)
    assert ini(exe) != before
    assert framegen.apply(exe, False, tmp_path)
    assert ini(exe) == before and not framegen.is_on(exe)
    assert not (tmp_path / framegen.RECORD).exists()  # the record goes with the last switch


def test_off_without_a_record_or_after_a_hand_edit_touches_nothing(exe, tmp_path):
    assert not framegen.apply(exe, False, tmp_path) and ini(exe) == INI
    framegen.apply(exe, True, tmp_path)
    edited = ini(exe).replace(b'FGOutput=fsrfg', b'FGOutput=xefg')
    (exe / 'OptiScaler.ini').write_bytes(edited)
    assert not framegen.apply(exe, False, tmp_path) and ini(exe) == edited  # not ours any more


def test_on_twice_keeps_the_first_values(exe, tmp_path):
    framegen.apply(exe, True, tmp_path)
    assert not framegen.apply(exe, True, tmp_path)
    framegen.apply(exe, False, tmp_path)
    assert ini(exe) == INI


def test_a_stale_record_is_not_used_on_a_fresh_ini(exe, tmp_path):
    framegen.apply(exe, True, tmp_path)
    # HelixSR's own uninstall and a new install brought back a different file; the old record must not win
    (exe / 'OptiScaler.ini').write_bytes(INI.replace(b'FGOutput=auto', b'FGOutput=xefg'))
    framegen.apply(exe, True, tmp_path)
    framegen.apply(exe, False, tmp_path)
    assert b'FGOutput=xefg' in ini(exe)


ENABLED = (INI.replace(b'[FrameGen]\r\n', b'[FrameGen]\r\n; true or false - auto is false\r\nEnabled=auto\r\n')
           .replace(b'[FSRFG]\r\n', b'[FSRFG]\r\nEnabled=auto\r\n'))


def test_frame_generation_is_switched_on_in_optiscaler_and_back(exe, tmp_path):
    # OptiScaler 0.9.4 generates nothing while [FrameGen] Enabled is false (its default), and only its overlay menu
    # sets it; the game's own frame generation setting never does.
    (exe / 'OptiScaler.ini').write_bytes(ENABLED)
    assert framegen.apply(exe, True, tmp_path)
    assert ini(exe).count(b'Enabled=true') == 1 and b'[FSRFG]\r\nEnabled=auto' in ini(exe)
    framegen.apply(exe, False, tmp_path)
    assert ini(exe) == ENABLED


PRESERVE = INI.replace(b'FTInput=auto\r\n', b'FTInput=auto\r\nPreserveSwapChain = auto\r\n')


def test_preserve_swapchain_goes_false_and_back(exe, tmp_path):
    (exe / 'OptiScaler.ini').write_bytes(PRESERVE)
    assert framegen.apply(exe, True, tmp_path) and framegen.is_on(exe)
    assert b'PreserveSwapChain = false\r\n' in ini(exe) and b'FGInput=dlssg' in ini(exe)
    expected = PRESERVE.replace(b'FGInput=auto', b'FGInput=dlssg').replace(b'FGOutput=auto', b'FGOutput=fsrfg')
    assert ini(exe) == expected.replace(b'PreserveSwapChain = auto', b'PreserveSwapChain = false')
    assert framegen.apply(exe, False, tmp_path) and ini(exe) == PRESERVE


def test_a_value_the_user_set_before_is_given_back(exe, tmp_path):
    mine = PRESERVE.replace(b'PreserveSwapChain = auto', b'PreserveSwapChain = true')
    (exe / 'OptiScaler.ini').write_bytes(mine)
    framegen.apply(exe, True, tmp_path)
    assert b'PreserveSwapChain = false' in ini(exe)
    framegen.apply(exe, False, tmp_path)
    assert ini(exe) == mine


def test_a_value_the_user_edited_after_is_not_overwritten_on_off(exe, tmp_path):
    (exe / 'OptiScaler.ini').write_bytes(PRESERVE)
    framegen.apply(exe, True, tmp_path)
    edited = ini(exe).replace(b'PreserveSwapChain = false', b'PreserveSwapChain = true')
    (exe / 'OptiScaler.ini').write_bytes(edited)
    assert framegen.is_on(exe)  # frame generation itself is still on
    assert not framegen.apply(exe, False, tmp_path) and ini(exe) == edited


def test_update_of_a_game_switched_on_by_an_older_amdlss(exe, tmp_path):
    (exe / 'OptiScaler.ini').write_bytes(PRESERVE)
    old = {'FGInput': 'auto', 'FGOutput': 'auto'}  # what beta.14 recorded: no PreserveSwapChain
    on_old = PRESERVE.replace(b'FGInput=auto', b'FGInput=dlssg').replace(b'FGOutput=auto', b'FGOutput=fsrfg')
    (exe / 'OptiScaler.ini').write_bytes(on_old)
    (tmp_path / framegen.RECORD).write_text(json.dumps({framegen._record_key(exe): old}), encoding='utf-8')
    assert framegen.is_on(exe)  # the box shows on, so Update keeps it on
    assert framegen.apply(exe, True, tmp_path)  # Update: the new key is added
    assert b'PreserveSwapChain = false' in ini(exe) and not framegen.apply(exe, True, tmp_path)
    assert framegen.apply(exe, False, tmp_path) and ini(exe) == PRESERVE  # FGInput and FGOutput went back to auto


def test_an_old_on_game_turned_off_leaves_the_new_key_alone(exe, tmp_path):
    on_old = PRESERVE.replace(b'FGInput=auto', b'FGInput=dlssg').replace(b'FGOutput=auto', b'FGOutput=fsrfg')
    (exe / 'OptiScaler.ini').write_bytes(on_old)
    old = {'FGInput': 'auto', 'FGOutput': 'auto'}
    (tmp_path / framegen.RECORD).write_text(json.dumps({framegen._record_key(exe): old}), encoding='utf-8')
    assert framegen.apply(exe, False, tmp_path) and ini(exe) == PRESERVE


def test_forget_drops_the_record(exe, tmp_path):
    framegen.apply(exe, True, tmp_path)
    framegen.forget(exe, tmp_path)
    assert not (tmp_path / framegen.RECORD).exists()


def test_a_file_without_the_lines_is_an_error_not_a_guess(exe, tmp_path):
    (exe / 'OptiScaler.ini').write_bytes(b'[Upscalers]\r\nDx12Upscaler = fsr31\r\n')
    with pytest.raises(helixsr.HelixSRError, match='FGInput'):
        framegen.apply(exe, True, tmp_path)
    assert not framegen.is_on(exe) and not framegen.is_on(exe / 'missing')


def test_the_in_game_hint_follows_the_installed_route_and_the_generator():
    optiscaler = games.Game(name='G', state='optiscaler', route='optiscaler')
    assert "it runs AMD's frame generation" in games.after_text(optiscaler, framegen.AMD)
    assert games.FRAMEGEN_HINT.startswith('Turn on DLSS Frame Generation in the game')
    assert games.FRAMEGEN_HINT not in games.after_text(optiscaler)
    assert "it runs HelixSR's own frame generation" in games.after_text(optiscaler, framegen.DLSS4)
    assert games.FRAMEGEN_DLSS4_HINT not in games.after_text(optiscaler, framegen.AMD)
    direct = games.Game(name='G', state='direct', route='direct')
    assert games.FRAMEGEN_HINT not in games.after_text(direct, framegen.AMD) and 'choose AMD FSR' in games.after_text(direct)
    assert 'choose DLSS' in games.after_text(games.Game(name='G', state='optiscaler', route='direct'))  # state wins


# --- the generator: AMD FSR's, or HelixSR's own DLSS 4 mode -------------------------------------------------------

DLSS4_INI = (INI.replace(b'[FrameGen]\r\n', b'[FrameGen]\r\nFGNvngxReplacement=auto\r\n')
             .replace(b'[Upscalers]\r\n',
                      b'[HelixSR]\r\n; frame generation\r\nFrameGeneration=auto\r\nInt8=auto\r\n\r\n[Upscalers]\r\n'))
# The same file with frame generation off: the explicit off values, never Scaler's auto.
DLSS4_OFF_INI = DLSS4_INI.replace(b'FrameGeneration=auto', b'FrameGeneration=off').replace(b'FGInput=auto',
                                                                                          b'FGInput=nofg')


@pytest.fixture
def opti(tmp_path):
    """A game folder with the OptiScaler.ini HelixSR Scaler ships: [HelixSR] and FGNvngxReplacement included."""
    folder = tmp_path / 'game-dlss4'
    folder.mkdir()
    (folder / framegen.INI).write_bytes(DLSS4_INI)
    return folder


def test_the_dlss4_generator_sets_three_values_and_off_writes_the_explicit_off_values(opti, tmp_path):
    assert framegen.generator_of(opti) == ''
    assert framegen.apply_generator(opti, framegen.DLSS4, tmp_path)
    data = (opti / framegen.INI).read_bytes()
    assert framegen.generator_of(opti) == framegen.DLSS4
    assert b'FrameGeneration=on' in data and b'FGInput=nvngxfg' in data and b'FGNvngxReplacement=HelixSR' in data
    assert b'Enabled=true' not in data and b'FGOutput=auto' in data   # AMD FSR's switch is left alone
    assert framegen.apply_generator(opti, '', tmp_path)
    off = (opti / framegen.INI).read_bytes()
    assert framegen.generator_of(opti) == ''
    # off is written out: Scaler's auto would run HelixSR's frame generation as soon as helixsr-fg is there
    assert off == DLSS4_OFF_INI and b'FrameGeneration=off' in off and b'FGInput=nofg' in off and b'nvngxfg' not in off
    assert not framegen.apply_generator(opti, '', tmp_path)   # already off: nothing changes


def test_switching_generators_restores_exactly_what_the_file_held(opti, tmp_path):
    assert framegen.apply_generator(opti, framegen.AMD, tmp_path)
    amd = (opti / framegen.INI).read_bytes()
    assert framegen.generator_of(opti) == framegen.AMD
    assert b'FGInput=dlssg' in amd and b'FGOutput=fsrfg' in amd
    assert framegen.apply_generator(opti, framegen.DLSS4, tmp_path)
    dlss4 = (opti / framegen.INI).read_bytes()
    assert framegen.generator_of(opti) == framegen.DLSS4
    assert b'FGInput=nvngxfg' in dlss4 and b'FGNvngxReplacement=HelixSR' in dlss4 and b'FrameGeneration=on' in dlss4
    assert b'FGOutput=fsrfg' not in dlss4 and b'Enabled=true' not in dlss4   # AMD FSR's values went back first
    assert framegen.apply_generator(opti, framegen.AMD, tmp_path)            # and back: byte for byte as before
    assert (opti / framegen.INI).read_bytes() == amd
    assert framegen.apply_generator(opti, '', tmp_path)
    assert (opti / framegen.INI).read_bytes() == DLSS4_OFF_INI   # off: the explicit off values, never auto


def test_dlss4_values_without_a_record_go_to_off(opti, tmp_path):
    # a hand-edited ini, or another tool's: the DLSS 4 values are in it and the record is not. They must not be left
    # pointing at HelixSR's frame generation files, and Scaler's auto would run them as soon as the files are there,
    # so the off values are written.
    assert framegen.apply_generator(opti, framegen.DLSS4, tmp_path)
    (tmp_path / framegen.RECORD).unlink()
    assert framegen.generator_of(opti) == framegen.DLSS4
    assert framegen.clear_dlss4(opti, tmp_path)
    assert (opti / framegen.INI).read_bytes() == DLSS4_OFF_INI and framegen.generator_of(opti) == ''
    assert not framegen.clear_dlss4(opti, tmp_path)   # nothing left of ours: nothing to do, nothing to write
    assert (opti / framegen.INI).read_bytes() == DLSS4_OFF_INI


def test_dlss4_needs_its_lines_in_the_ini(exe, tmp_path):
    # an older OptiScaler.ini (no [HelixSR], no FGNvngxReplacement): DLSS 4 cannot be set up in it; AMD's switch
    # still works
    assert framegen.generator_of(exe) == '' and framegen.apply_generator(exe, framegen.AMD, tmp_path)
    with pytest.raises(helixsr.HelixSRError, match='HelixSR'):
        framegen.apply_generator(exe, framegen.DLSS4, tmp_path)


def test_generator_names_for_reports():
    assert framegen.name_of(framegen.AMD) == 'AMD FSR' and framegen.name_of(framegen.DLSS4) == 'DLSS 4 (HelixSR)'
    assert framegen.name_of('') == 'off' and framegen.name_of('?') == 'off'
