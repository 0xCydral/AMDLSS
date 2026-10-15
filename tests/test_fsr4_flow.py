# SPDX-License-Identifier: GPL-2.0-or-later
"""Install, switch and remove FSR 4.1.1 through a fake HelixSR: every route, and every way back."""
import hashlib
import sys
from pathlib import Path

import pytest

from amdlss import framegen, fsr4, games, helixsr, scaler
from amdlss.helixsr import HelixSRError
from fake_helixsr import make
from fake_scaler import make as make_package
from test_pe import build_dll

AMD = build_dll()
DLL = fsr4.DLL
ORIGINAL = 'amd_fidelityfx_upscaler_dx12.original.dll'


@pytest.fixture
def world(tmp_path, monkeypatch):
    one, two = tmp_path / 'One', tmp_path / 'Two'
    for folder in (one, two):
        folder.mkdir()
        (folder / DLL).write_bytes(b'GAME ORIGINAL')
    three = tmp_path / 'Three'
    three.mkdir()
    folder = make(tmp_path, [{'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']},
                             {'name': 'Two', 'folder': str(two), 'fsr': True, 'inputs': []},
                             {'name': 'Three', 'folder': str(three), 'fsr': False, 'inputs': ['XeSS']}])
    amd = tmp_path / 'amd'
    (amd).mkdir()
    (amd / DLL).write_bytes(AMD)
    (amd / fsr4.LICENSE).write_text('AMD license\n')
    monkeypatch.setattr(fsr4, 'AMD_SIZE', len(AMD))
    monkeypatch.setattr(fsr4, 'AMD_SHA256', hashlib.sha256(AMD).hexdigest())
    client = helixsr.HelixSR(folder, python=sys.executable, running=lambda game: False)
    source = fsr4.Source(amd / DLL, "AMD's DLL", '', '')
    data = tmp_path / 'data'
    make_package(data)   # HelixSR Scaler's package: the OptiScaler route installs it now
    return client, source, amd / fsr4.LICENSE, data, {'One': one, 'Two': two, 'Three': three}


def listed(client, name):
    game = next(g for g in client.list_games() if g.name == name)
    game.fsr4 = fsr4.availability(game, fsr4.Source(Path('x'), '', '', ''))
    scaler.mark(game)
    fsr4.mark(game)
    return game


def go(world, name, want, route=None, game=None):
    client, source, license_file, data, _ = world
    game = game or listed(client, name)
    game.fsr4 = fsr4.availability(game, source)
    return fsr4.install_game(client, game, [], want, route, source, license_file, data)


def test_fsr4_drop_in_then_remove_restores_the_game(world):
    client, _s, _l, data, folders = world
    after = go(world, 'Two', 'fsr4')
    assert after.state == 'fsr4' and after.upscaler == 'fsr4' and after.installed
    assert (folders['Two'] / DLL).read_bytes() == AMD and (folders['Two'] / ORIGINAL).read_bytes() == b'GAME ORIGINAL'
    assert listed(client, 'Two').state == 'fsr4'      # a fresh scan still sees it
    fsr4.remove_game(client, listed(client, 'Two'), [], data)
    assert sorted(p.name for p in folders['Two'].iterdir()) == [DLL]
    assert (folders['Two'] / DLL).read_bytes() == b'GAME ORIGINAL' and not listed(client, 'Two').installed


def test_fsr4_through_optiscaler_points_the_ini_and_remove_undoes_it(world):
    client, _s, _l, data, folders = world
    one = folders['One']
    before = {p.name: p.read_bytes() for p in one.iterdir() if p.is_file()}
    after = go(world, 'One', 'fsr4', 'optiscaler')
    assert after.state == 'optiscaler' and after.upscaler == 'fsr4' and after.state_text == 'FSR 4.1.1 (OptiScaler)'
    assert scaler.installed(one) and (one / 'dxgi.dll').is_file() and fsr4.in_use(one)
    assert (one / 'HelixSR' / 'fsr4' / 'fsr4.dll').read_bytes() == AMD
    assert (one / DLL).read_bytes() == b'GAME ORIGINAL'       # the game's own FSR DLL is not touched on this route
    fsr4.remove_game(client, after, [], data)
    assert not scaler.installed(one) and not (one / 'OptiScaler.ini').exists()
    assert not (one / 'HelixSR').exists() and not (data / framegen.RECORD).exists()
    assert {p.name: p.read_bytes() for p in one.iterdir() if p.is_file()} == before   # byte for byte


def test_a_game_with_only_dlss_inputs_goes_through_optiscaler(world):
    _client, _s, _l, _data, folders = world
    after = go(world, 'Three', 'fsr4')
    assert after.upscaler == 'fsr4' and after.state == 'optiscaler'
    assert scaler.installed(folders['Three']) and (folders['Three'] / 'dxgi.dll').is_file()


def test_switching_a_helixsr_drop_in_to_fsr4_removes_helixsr_first(world):
    client, _s, _l, data, folders = world
    after = go(world, 'Two', 'helixsr')
    assert after.state == 'direct' and (folders['Two'] / DLL).read_bytes().startswith(b'HELIX')
    after = go(world, 'Two', 'fsr4', game=after)
    assert after.state == 'fsr4' and (folders['Two'] / DLL).read_bytes() == AMD
    assert (folders['Two'] / ORIGINAL).read_bytes() == b'GAME ORIGINAL'   # HelixSR's backup was put back, then ours was made


def test_switching_from_fsr4_back_to_helixsr(world):
    client, _s, _l, data, folders = world
    after = go(world, 'Two', 'fsr4')
    after = go(world, 'Two', 'helixsr', game=after)
    assert after.state == 'direct' and after.upscaler == ''
    assert (folders['Two'] / DLL).read_bytes().startswith(b'HELIX')
    assert not (folders['Two'] / fsr4.MARKER).exists() and not (folders['Two'] / fsr4.LICENSE).exists()
    fsr4.remove_game(client, after, [], data)
    assert (folders['Two'] / DLL).read_bytes() == b'GAME ORIGINAL'


def test_switching_an_optiscaler_game_between_the_upscalers_keeps_optiscaler(world):
    client, _s, _l, data, folders = world
    one = folders['One']
    after = go(world, 'One', 'helixsr', 'optiscaler')
    assert after.state == 'optiscaler' and after.upscaler == ''
    before = (one / 'OptiScaler.ini').read_bytes()
    after = go(world, 'One', 'fsr4', game=after)
    assert after.upscaler == 'fsr4' and scaler.installed(one)
    after = go(world, 'One', 'helixsr', game=after)
    assert after.upscaler == '' and after.state == 'optiscaler'
    assert (one / 'OptiScaler.ini').read_bytes() == before and not (one / 'HelixSR' / 'fsr4').exists()


def test_it_refuses_what_the_game_check_refused(world):
    client, source, license_file, data, folders = world
    game = listed(client, 'Two')
    game.fsr4 = fsr4.availability(games.Game(name='x', anticheat=['BattlEye']), source)
    with pytest.raises(HelixSRError, match='nti-cheat'):
        fsr4.install_game(client, game, [], 'fsr4', None, source, license_file, data)
    assert (folders['Two'] / DLL).read_bytes() == b'GAME ORIGINAL'


def test_a_route_the_game_cannot_use_is_refused(world):
    with pytest.raises(HelixSRError, match='route'):
        go(world, 'Two', 'fsr4', 'optiscaler')


def test_a_running_game_is_not_touched(world):
    client, _s, _l, data, folders = world
    client.running = lambda game: True
    with pytest.raises(HelixSRError, match='still running'):
        go(world, 'Two', 'fsr4')
    assert (folders['Two'] / DLL).read_bytes() == b'GAME ORIGINAL'


def test_helixsr_will_not_install_over_an_fsr4_drop_in(world):
    client, *_ = world
    after = go(world, 'Two', 'fsr4')
    with pytest.raises(HelixSRError, match='FSR 4.1.1'):
        client.install(after)
