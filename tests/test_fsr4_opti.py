# SPDX-License-Identifier: GPL-2.0-or-later
"""FSR 4.1.1 on the OptiScaler route: OptiScaler.ini is pointed at the FSR 4 DLL and put back, and nothing else moves."""
import pytest

from amdlss import framegen, fsr4, games, host
from amdlss.helixsr import HelixSRError

HELIX_INI = (b'[Upscalers]\r\nDx12Upscaler = fsr31\r\n\r\n[FSR]\r\nUpscalerIndex=auto\r\n\r\n[Libraries]\r\n'
             b'FfxDx12Path = Z:\\game\\HelixSR\\helixsr.dll\r\nFfxDx12SRPath = Z:\\game\\HelixSR\\helixsr_upscaler.dll\r\n'
             b'FfxDx12FGPath = Z:\\game\\HelixSR\\amd_fidelityfx_framegeneration_dx12.dll\r\n')


@pytest.fixture
def setup(tmp_path):
    exe = tmp_path / 'game'
    (exe / 'HelixSR').mkdir(parents=True)
    (exe / 'OptiScaler.ini').write_bytes(HELIX_INI)
    amd = tmp_path / 'amd'
    amd.mkdir()
    (amd / fsr4.DLL).write_bytes(b'FSR4 DLL')
    (amd / fsr4.LICENSE).write_text('AMD license\n')
    return exe, fsr4.Source(amd / fsr4.DLL, "AMD's DLL", '', ''), amd / fsr4.LICENSE, tmp_path / 'data'


def ini(exe):
    return (exe / 'OptiScaler.ini').read_bytes()


def test_the_ini_points_at_two_copies_and_nothing_else_changes(setup):
    exe, source, license_file, data = setup
    assert fsr4.use_in_optiscaler(exe, source, license_file, data)
    folder = exe / 'HelixSR' / 'fsr4'
    assert sorted(p.name for p in folder.iterdir()) == [fsr4.LICENSE, 'fsr4.dll', 'fsr4_upscaler.dll']
    assert (folder / 'fsr4.dll').read_bytes() == (folder / 'fsr4_upscaler.dll').read_bytes() == b'FSR4 DLL'
    text = ini(exe).decode()
    for key, name in (('FfxDx12Path', 'fsr4.dll'), ('FfxDx12SRPath', 'fsr4_upscaler.dll')):
        assert f'{key} = {fsr4.ini_path(folder / name)}\r\n' in text
    assert 'UpscalerIndex=auto' in text and 'FfxDx12FGPath = Z:\\game\\HelixSR\\amd' in text   # untouched
    assert 'Dx12Upscaler = fsr31' in text


def test_two_different_files_back_the_two_roles(setup):
    exe, source, license_file, data = setup
    fsr4.use_in_optiscaler(exe, source, license_file, data)
    values = framegen.read_values(ini(exe), fsr4.libraries(exe))
    assert values['FfxDx12Path'] != values['FfxDx12SRPath']


def test_switching_back_restores_what_helixsr_wrote_and_removes_the_copies(setup):
    exe, source, license_file, data = setup
    fsr4.use_in_optiscaler(exe, source, license_file, data)
    assert fsr4.in_use(exe)
    assert fsr4.restore_optiscaler(exe, data)
    assert ini(exe) == HELIX_INI and not fsr4.in_use(exe)
    assert not (exe / 'HelixSR' / 'fsr4').exists() and not (data / framegen.RECORD).exists()
    assert not fsr4.restore_optiscaler(exe, data)   # nothing left to do


def test_using_it_twice_changes_nothing_the_second_time(setup):
    exe, source, license_file, data = setup
    assert fsr4.use_in_optiscaler(exe, source, license_file, data)
    once = ini(exe)
    assert not fsr4.use_in_optiscaler(exe, source, license_file, data)
    assert ini(exe) == once


def test_a_helixsr_update_rewrites_the_ini_and_we_point_it_again(setup):
    exe, source, license_file, data = setup
    fsr4.use_in_optiscaler(exe, source, license_file, data)
    updated = HELIX_INI + b'; a newer HelixSR wrote this\r\n'
    (exe / 'OptiScaler.ini').write_bytes(updated)
    assert not fsr4.in_use(exe)
    assert fsr4.use_in_optiscaler(exe, source, license_file, data)
    fsr4.restore_optiscaler(exe, data)
    assert ini(exe) == updated


def test_a_picked_dll_brings_no_amd_license(setup):
    exe, source, license_file, data = setup
    fsr4.use_in_optiscaler(exe, source._replace(user=True), license_file, data)
    assert not (exe / 'HelixSR' / 'fsr4' / fsr4.LICENSE).exists()


def test_a_bad_ini_leaves_no_copies_behind(setup):
    exe, source, license_file, data = setup
    (exe / 'OptiScaler.ini').write_bytes(b'[Upscalers]\r\nDx12Upscaler = fsr31\r\n')
    with pytest.raises(HelixSRError, match='FfxDx12Path'):
        fsr4.use_in_optiscaler(exe, source, license_file, data)
    assert not (exe / 'HelixSR' / 'fsr4').exists()


def test_forgetting_a_game_drops_every_record_including_ours(setup):
    exe, source, license_file, data = setup
    fsr4.use_in_optiscaler(exe, source, license_file, data)
    framegen.forget(exe, data)
    assert not (data / framegen.RECORD).exists()


def test_mark_reads_what_is_installed(setup, tmp_path):
    exe, source, license_file, data = setup
    plain = games.Game(name='G', folder=str(exe), exe_folder=str(exe), state='optiscaler')
    fsr4.mark(plain)
    assert plain.upscaler == '' and plain.state_text == 'OptiScaler'
    fsr4.use_in_optiscaler(exe, source, license_file, data)
    fsr4.mark(plain)
    assert plain.upscaler == 'fsr4' and plain.state == 'optiscaler' and plain.state_text == 'FSR 4.1.1 (OptiScaler)'
    drop = tmp_path / 'drop'
    drop.mkdir()
    (drop / fsr4.DLL).write_bytes(b'x')
    here = games.Game(name='D', folder=str(drop), fsr_targets=[str(drop / fsr4.DLL)], state='none')
    fsr4.mark(here)
    assert not here.installed
    (drop / fsr4.MARKER).write_text('{}')
    fsr4.mark(here)
    assert here.state == 'fsr4' and here.upscaler == 'fsr4' and here.installed
    helix = games.Game(name='H', folder=str(drop), fsr_targets=[str(drop / fsr4.DLL)], state='direct')
    fsr4.mark(helix)
    assert helix.state == 'direct' and helix.upscaler == ''


def test_the_ini_path_is_written_the_way_helixsr_writes_it(tmp_path):
    shown = fsr4.ini_path(tmp_path / 'x.dll')
    if host.WINDOWS:
        assert shown == str((tmp_path / 'x.dll').resolve())
    else:
        assert shown == 'Z:' + str((tmp_path / 'x.dll').resolve()).replace('/', '\\')
