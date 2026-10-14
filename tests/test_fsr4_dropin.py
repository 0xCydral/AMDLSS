# SPDX-License-Identifier: GPL-2.0-or-later
"""The FSR 4.1.1 drop-in: AMDLSS swaps the game's FSR DLL itself, records it, and puts every file back exactly."""
import hashlib
import json
import shutil

import pytest

from amdlss import fsr4, games, helixsr
from amdlss.helixsr import HelixSRError

UPSCALER = fsr4.DLL
OLD = 'amd_fidelityfx_dx12.dll'
FSR4_BYTES, LOADER_BYTES, GAME_UP, GAME_OLD = b'FSR4 DLL', b'AMD LOADER', b'GAME UPSCALER', b'GAME FSR3 DLL'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    source = tmp_path / 'amd' / UPSCALER
    source.parent.mkdir()
    source.write_bytes(FSR4_BYTES)
    license_file = tmp_path / 'amd' / fsr4.LICENSE
    license_file.write_text('AMD license\n')
    monkeypatch.setattr(fsr4, 'LOADER_SIZE', len(LOADER_BYTES))
    monkeypatch.setattr(fsr4, 'LOADER_SHA256', hashlib.sha256(LOADER_BYTES).hexdigest())
    asked = []

    def fake_download(part, progress, url, size):
        asked.append(url)
        part.write_bytes(LOADER_BYTES)

    monkeypatch.setattr(helixsr, 'download', fake_download)
    return data, fsr4.Source(source, "AMD's DLL", '', ''), license_file, asked


def game_folder(tmp_path, name='Game', files=None):
    folder = tmp_path / name
    folder.mkdir()
    for file, data in (files or {UPSCALER: GAME_UP}).items():
        (folder / file).write_bytes(data)
    return folder


def listing(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def install(setup, *targets, **options):
    data, source, license_file, _ = setup
    return fsr4.install_dropin([str(t) for t in targets], source, data, license_file, **options)


def test_an_upscaler_dll_is_swapped_and_restored(setup, tmp_path):
    folder = game_folder(tmp_path)
    before = listing(folder)
    install(setup, folder / UPSCALER)
    assert listing(folder) == {UPSCALER: FSR4_BYTES, 'amd_fidelityfx_upscaler_dx12.original.dll': GAME_UP,
                               fsr4.LICENSE: b'AMD license\n', fsr4.MARKER: listing(folder)[fsr4.MARKER]}
    record = json.loads((folder / fsr4.MARKER).read_text())
    assert record['moved'] == [{'name': UPSCALER, 'to': 'amd_fidelityfx_upscaler_dx12.original.dll',
                                'sha256': hashlib.sha256(GAME_UP).hexdigest()}]
    assert {item['name'] for item in record['created']} == {UPSCALER, fsr4.LICENSE}
    assert fsr4.remove_dropin([str(folder)]) == []
    assert listing(folder) == before


def test_an_old_style_loader_is_replaced_by_amds_loader_with_the_upscaler_beside_it(setup, tmp_path):
    folder = game_folder(tmp_path, files={OLD: GAME_OLD})
    before = listing(folder)
    install(setup, folder / OLD)
    now = listing(folder)
    assert now[OLD] == LOADER_BYTES and now[UPSCALER] == FSR4_BYTES and now['amd_fidelityfx_dx12.original.dll'] == GAME_OLD
    assert fsr4.remove_dropin([str(folder)]) == []
    assert listing(folder) == before


def test_an_upscaler_file_that_sat_beside_the_old_loader_is_kept_as_original(setup, tmp_path):
    folder = game_folder(tmp_path, files={OLD: GAME_OLD, UPSCALER: GAME_UP})
    before = listing(folder)
    install(setup, folder / OLD)
    assert listing(folder)['amd_fidelityfx_upscaler_dx12.original.dll'] == GAME_UP
    fsr4.remove_dropin([str(folder)])
    assert listing(folder) == before


def test_both_names_as_targets_are_handled_once(setup, tmp_path):
    folder = game_folder(tmp_path, files={OLD: GAME_OLD, UPSCALER: GAME_UP})
    before = listing(folder)
    install(setup, folder / OLD, folder / UPSCALER)
    now = listing(folder)
    assert now[OLD] == LOADER_BYTES and now[UPSCALER] == FSR4_BYTES
    fsr4.remove_dropin([str(folder)])
    assert listing(folder) == before


def test_each_folder_gets_its_own_record(setup, tmp_path):
    one, two = game_folder(tmp_path, 'One'), game_folder(tmp_path, 'Two', {OLD: GAME_OLD})
    install(setup, one / UPSCALER, two / OLD)
    assert (one / fsr4.MARKER).is_file() and (two / fsr4.MARKER).is_file()
    assert fsr4.dropin_folders([str(one), str(two), str(tmp_path)]) == [str(one), str(two)]
    fsr4.remove_dropin([str(one), str(two)])
    assert not fsr4.dropin_folders([str(one), str(two)])


def test_a_picked_dll_alone_needs_no_license(setup, tmp_path):
    data, source, license_file, _ = setup
    folder = game_folder(tmp_path)
    fsr4.install_dropin([str(folder / UPSCALER)], source._replace(user=True), data, license_file)
    assert fsr4.LICENSE not in listing(folder)
    fsr4.remove_dropin([str(folder)])
    assert listing(folder) == {UPSCALER: GAME_UP}


def test_the_loader_is_downloaded_once_and_only_when_a_game_needs_it(setup, tmp_path):
    data, _source, _license, asked = setup
    install(setup, game_folder(tmp_path, 'A')  / UPSCALER)
    assert asked == [] and not (data / fsr4.USER_FOLDER / fsr4.LOADER).exists()
    install(setup, game_folder(tmp_path, 'B', {OLD: GAME_OLD}) / OLD)
    install(setup, game_folder(tmp_path, 'C', {OLD: GAME_OLD}) / OLD)
    assert asked == [fsr4.LOADER_URL]
    assert (data / fsr4.USER_FOLDER / fsr4.LOADER).read_bytes() == LOADER_BYTES


def test_a_loader_that_does_not_match_its_pin_is_refused_and_deleted(setup, tmp_path, monkeypatch):
    data, *_ = setup
    monkeypatch.setattr(helixsr, 'download', lambda part, progress, url, size: part.write_bytes(b'x' * len(LOADER_BYTES)))
    folder = game_folder(tmp_path, files={OLD: GAME_OLD})
    with pytest.raises(HelixSRError, match='SHA-256'):
        install(setup, folder / OLD)
    assert listing(folder) == {OLD: GAME_OLD}
    assert not list((data / fsr4.USER_FOLDER).glob('*')) if (data / fsr4.USER_FOLDER).exists() else True


def test_helixsrs_drop_in_here_blocks_ours(setup, tmp_path):
    folder = game_folder(tmp_path)
    (folder / helixsr.MARKER_FILE).write_text('{}')
    before = listing(folder)
    with pytest.raises(HelixSRError, match='remove HelixSR first'):
        install(setup, folder / UPSCALER)
    assert listing(folder) == before


def test_ours_blocks_helixsr(setup, tmp_path):
    folder = game_folder(tmp_path)
    install(setup, folder / UPSCALER)
    game = games.Game(name='G', folder=str(folder), fsr_targets=[str(folder / UPSCALER)])
    with pytest.raises(HelixSRError, match='FSR 4.1.1'):
        helixsr.HelixSR.need_no_fsr4(game)


def test_an_install_over_ours_or_over_an_existing_original_changes_nothing(setup, tmp_path):
    folder = game_folder(tmp_path, files={UPSCALER: GAME_UP, 'amd_fidelityfx_upscaler_dx12.original.dll': b'OLDER'})
    before = listing(folder)
    with pytest.raises(HelixSRError, match='original'):
        install(setup, folder / UPSCALER)
    assert listing(folder) == before
    clean = game_folder(tmp_path, 'Clean')
    install(setup, clean / UPSCALER)
    with pytest.raises(HelixSRError, match='already'):
        install(setup, clean / UPSCALER)


def test_a_failure_halfway_puts_everything_back(setup, tmp_path, monkeypatch):
    one, two = game_folder(tmp_path, 'One'), game_folder(tmp_path, 'Two')
    before_one, before_two = listing(one), listing(two)
    real, calls = shutil.copyfile, []

    def flaky(source, target, *args, **kwargs):
        calls.append(target)
        if len(calls) == 3:
            raise OSError('disk full')
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(fsr4.shutil, 'copyfile', flaky)
    with pytest.raises(HelixSRError, match='disk full'):
        install(setup, one / UPSCALER, two / UPSCALER)
    assert listing(one) == before_one and listing(two) == before_two


def test_a_file_the_user_replaced_by_hand_is_kept_and_reported(setup, tmp_path):
    folder = game_folder(tmp_path)
    install(setup, folder / UPSCALER)
    (folder / UPSCALER).write_bytes(b'THE USER PUT THIS HERE')
    notes = fsr4.remove_dropin([str(folder)])
    assert len(notes) == 1 and UPSCALER in notes[0] and 'kept' in notes[0]
    assert (folder / UPSCALER).read_bytes() == b'THE USER PUT THIS HERE'
    assert (folder / 'amd_fidelityfx_upscaler_dx12.original.dll').read_bytes() == GAME_UP   # still there to restore by hand
    assert not (folder / fsr4.MARKER).exists() and not (folder / fsr4.LICENSE).exists()


def test_a_missing_installed_file_is_not_an_error_on_remove(setup, tmp_path):
    folder = game_folder(tmp_path)
    install(setup, folder / UPSCALER)
    (folder / UPSCALER).unlink()
    assert fsr4.remove_dropin([str(folder)]) == []
    assert (folder / UPSCALER).read_bytes() == GAME_UP


def test_a_damaged_record_is_not_guessed_at(setup, tmp_path):
    folder = game_folder(tmp_path)
    (folder / fsr4.MARKER).write_text('{not json')
    with pytest.raises(HelixSRError, match='record'):
        fsr4.remove_dropin([str(folder)])
    assert fsr4.dropin_folders([str(folder)]) == [str(folder)]
