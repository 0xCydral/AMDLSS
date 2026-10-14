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


def test_the_in_game_hint_follows_the_installed_route():
    optiscaler = games.Game(name='G', state='optiscaler', route='optiscaler')
    assert "it runs AMD's frame generation" in games.after_text(optiscaler, True)
    assert games.FRAMEGEN_HINT.startswith('Turn on DLSS Frame Generation in the game')
    assert games.FRAMEGEN_HINT not in games.after_text(optiscaler)
    direct = games.Game(name='G', state='direct', route='direct')
    assert games.FRAMEGEN_HINT not in games.after_text(direct, True) and 'choose AMD FSR' in games.after_text(direct)
    assert 'choose DLSS' in games.after_text(games.Game(name='G', state='optiscaler', route='direct'))  # state wins
