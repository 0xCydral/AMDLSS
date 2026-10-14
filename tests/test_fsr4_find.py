# SPDX-License-Identifier: GPL-2.0-or-later
"""Finding the community FSR 4 DLL that RX 6000 cards need, so the user does not have to pick it by hand."""
import hashlib
from pathlib import Path

import pytest

from amdlss import fsr4, games
from test_pe import build_dll

OFFICIAL = build_dll(version=(4, 1, 1, 2740))


@pytest.fixture(autouse=True)
def amd_pin(monkeypatch):
    monkeypatch.setattr(fsr4, 'AMD_SIZE', len(OFFICIAL))
    monkeypatch.setattr(fsr4, 'AMD_SHA256', hashlib.sha256(OFFICIAL).hexdigest())


@pytest.fixture
def where(tmp_path):
    """A game with an exe folder, a Downloads folder, and an AMDLSS data folder."""
    exe = tmp_path / 'Game' / 'bin'
    exe.mkdir(parents=True)
    for name in ('Downloads', 'Desktop', 'Documents', 'data'):
        (tmp_path / name).mkdir()
    game = games.Game(name='Game', folder=str(tmp_path / 'Game'), exe_folder=str(exe), inputs=['DLSS'])
    return game, [tmp_path / name for name in ('Downloads', 'Desktop', 'Documents')], tmp_path / 'data'


def put(folder, name, version=(4, 0, 2, 0), **options):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(build_dll(version=version, **options))
    return folder / name


def find(where):
    game, user, data = where
    return fsr4.discover([game], user, data)


def test_a_dll_in_the_games_my_fsr_folder_is_found(where):
    game, *_ = where
    mine = put(Path(game.exe_folder) / 'HelixSR' / 'my-fsr', 'FSR 4.1.1b.dll', version=(4, 1, 1, 9))
    found = find(where)
    assert found.found.path == mine and found.found.version == '4.1.1.9' and not found.hint


def test_a_dll_in_downloads_is_found_by_its_name(where):
    _game, user, _data = where
    mine = put(user[0] / 'fsr4 int8' / 'bin', fsr4.DLL)
    put(user[0], 'unrelated.dll')                      # a DLL with another name, outside a my-fsr folder: ignored
    assert find(where).found.path == mine


def test_the_folder_of_the_games_own_fsr_dll_is_looked_at_too(where, tmp_path):
    game, *_ = where
    mine = put(tmp_path / 'Game' / 'Engine' / 'Binaries', fsr4.DLL)
    game.fsr_targets.append(str(mine))
    assert find(where).found.path == mine


def test_amds_official_dll_is_not_a_candidate(where):
    _game, user, _data = where
    (user[0] / fsr4.DLL).write_bytes(OFFICIAL)
    result = find(where)
    assert result.found is None and not result.hint


def test_the_highest_file_version_wins(where):
    _game, user, data = where
    put(user[0] / 'a', fsr4.DLL, version=(4, 0, 2, 3))
    best = put(user[1], fsr4.DLL, version=(4, 1, 1, 2))
    put(data / 'old', fsr4.DLL, version=(4, 0, 9, 0))
    assert find(where).found.path == best
    assert find(where).found.version == '4.1.1.2'


def test_a_file_that_is_not_an_fsr_4_dll_is_skipped(where):
    _game, user, _data = where
    put(user[0] / 'a', fsr4.DLL, version=(3, 1, 5, 0))
    put(user[0] / 'b', fsr4.DLL, machine=0x14c, magic=0x10b)
    put(user[0] / 'c', fsr4.DLL, exports=('Other',))
    (user[0] / 'd').mkdir()
    (user[0] / 'd' / fsr4.DLL).write_bytes(b'not a dll')
    assert find(where).found is None


def test_the_scan_stops_four_folders_down(where):
    _game, user, _data = where
    put(user[0] / 'a' / 'b' / 'c' / 'd' / 'e', fsr4.DLL)
    assert find(where).found is None
    near = put(user[0] / 'a' / 'b' / 'c', fsr4.DLL)
    assert find(where).found.path == near


def test_a_big_folder_is_not_walked_forever(where, monkeypatch):
    _game, user, _data = where
    monkeypatch.setattr(fsr4, 'MAX_VISITED', 5)
    for number in range(20):
        (user[0] / f'f{number:02}').mkdir()
    put(user[0] / 'zzz', fsr4.DLL)
    assert find(where).found is None


def test_an_archive_in_downloads_gets_a_hint_not_an_extraction(where):
    _game, user, _data = where
    (user[0] / 'photos.zip').write_bytes(b'PK')
    (user[0] / 'FSR4_int8_community_4.1.1b.7z').write_bytes(b'7z')
    result = find(where)
    assert result.found is None
    assert result.hint == 'Unpack FSR4_int8_community_4.1.1b.7z and AMDLSS will find the DLL.'
    assert (user[0] / 'FSR4_int8_community_4.1.1b.7z').read_bytes() == b'7z'   # untouched
    put(user[0], fsr4.DLL)
    assert find(where).hint == ''   # a DLL was found: no hint


def test_nothing_found_says_nothing(where):
    assert find(where) == fsr4.Discovery(None, '')


def test_a_dll_found_is_kept_and_remembers_where_it_came_from(where):
    _game, user, data = where
    mine = put(user[0], fsr4.DLL, version=(4, 1, 1, 5))
    found = fsr4.auto_find([where[0]], user, data)
    assert found.found.path == mine
    assert fsr4.user_dll(data) and fsr4.user_origin(data) == str(mine)
    put(user[1], fsr4.DLL, version=(4, 1, 1, 8))   # a better one later: the saved choice is not swapped behind the user's back
    assert fsr4.auto_find([where[0]], user, data) == fsr4.Discovery(None, '')
    assert fsr4.user_origin(data) == str(mine)
    fsr4.forget_dll(data)
    assert fsr4.user_origin(data) == '' and fsr4.user_dll(data) is None


def test_a_dll_picked_by_hand_remembers_its_path(where, tmp_path):
    _game, _user, data = where
    mine = put(tmp_path, 'mine.dll')
    fsr4.choose_dll(mine, data)
    assert fsr4.user_origin(data) == str(mine)
