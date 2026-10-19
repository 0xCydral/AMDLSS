# SPDX-License-Identifier: GPL-2.0-or-later
"""AMDLSS's injected route: Luma UE's pins and placement, the record, Remove, and the reasons it is offered."""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from amdlss import framegen, games, helixsr, luma, scaler
from fake_helixsr import make as make_helixsr
from fake_luma import FILES, TOP
from fake_luma import make as make_package
from fake_scaler import make as make_scaler_package
from test_pe import build_dll


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


def serve(monkeypatch, data, size=None, sha_value=None):
    """Makes luma.fetch() 'download' `data`, pinned as if it were the real release."""
    monkeypatch.setattr(luma, 'ZIP_SIZE', len(data) if size is None else size)
    monkeypatch.setattr(luma, 'ZIP_SHA256', hashlib.sha256(data).hexdigest() if sha_value is None else sha_value)
    monkeypatch.setattr(helixsr, 'download', lambda part, progress, url, size: part.write_bytes(data))


def package_bytes():
    return zip_bytes({name: f'zip {name}'.encode() for name in FILES})  # the real zip has no top folder


def save_record(game_folder, record_data):
    luma.record_path(game_folder).parent.mkdir(parents=True, exist_ok=True)
    luma.record_path(game_folder).write_text(json.dumps(record_data), encoding='utf-8')


@pytest.fixture
def game(tmp_path):
    """A game's .exe folder the way the injected route needs it: Binaries\\Win64 with a 64-bit shipping .exe."""
    folder = tmp_path / 'One' / 'Project' / 'Binaries' / 'Win64'
    folder.mkdir(parents=True)
    (folder / 'One-Win64-Shipping.exe').write_bytes(build_dll())
    (folder / 'One.exe').write_bytes(b'MZ')
    return folder


# --- the package --------------------------------------------------------------------------------------------------

def test_fetch_unpacks_the_pinned_zip(tmp_path, monkeypatch):
    serve(monkeypatch, package_bytes())
    folder = luma.fetch(tmp_path)
    assert folder == tmp_path / TOP and luma.extracted(tmp_path)
    assert (folder / luma.SCALER_NAME).read_bytes() == b'zip dxgi.dll'
    assert (folder / luma.SHADERS / 'Unreal Engine' / 'Luma_MotionVec_UE4_Decode.hlsl').is_file()
    assert not (tmp_path / '.download').exists()


def test_fetch_rejects_a_bad_hash(tmp_path, monkeypatch):
    serve(monkeypatch, package_bytes(), sha_value='0' * 64)
    with pytest.raises(helixsr.HelixSRError, match='SHA-256'):
        luma.fetch(tmp_path)
    assert not (tmp_path / TOP).exists() and not list((tmp_path / '.download').glob('*.part'))


def test_fetch_rejects_zip_slip(tmp_path, monkeypatch):
    serve(monkeypatch, zip_bytes({luma.ADDON: 'x', '../evil.txt': 'x'}))
    target = tmp_path / 'target'
    with pytest.raises(helixsr.HelixSRError, match='outside the folder'):
        luma.fetch(target)
    assert not (target / 'evil.txt').exists() and not (target / TOP / luma.ADDON).exists()


def test_fetch_rejects_a_zip_that_is_not_luma(tmp_path, monkeypatch):
    serve(monkeypatch, zip_bytes({'README.txt': 'x'}))
    with pytest.raises(helixsr.HelixSRError, match='does not hold'):
        luma.fetch(tmp_path)


def test_files_rename_only_the_loader(tmp_path):
    package = make_package(tmp_path)
    targets = {name for _source, name in luma.files(package)}
    assert luma.SCALER_NAME not in targets and luma.RESHADE in targets   # OptiScaler keeps dxgi.dll for itself
    assert luma.ADDON in targets and luma.DLSS in targets
    assert f'{luma.SHADERS}/Global/Luma_Copy_PS.hlsl' in targets
    assert {source.name for source, name in luma.files(package) if name == luma.RESHADE} == {luma.SCALER_NAME}


def test_locate_finds_the_override_then_the_data_folder(tmp_path):
    assert luma.locate(tmp_path) is None
    package = make_package(tmp_path)
    assert luma.locate(tmp_path) == package
    other = tmp_path / 'elsewhere'
    make_package(other)
    assert luma.locate(tmp_path, str(other / TOP)) == other / TOP


# --- the placement and the record ---------------------------------------------------------------------------------

def test_install_puts_luma_next_to_the_exe_and_keeps_what_it_replaces(tmp_path, game):
    package = make_package(tmp_path)
    (game / luma.DLSS).write_bytes(b'the game already had one')
    record_data = {'amdlss': '1.2.0', 'luma': luma.BUILD, 'moved': [], 'created': []}
    luma.install_into(game, package, record_data)
    assert (game / luma.RESHADE).read_bytes() == f'STAND-IN {luma.SCALER_NAME}'.encode()
    assert not (game / luma.SCALER_NAME).exists()            # OptiScaler's own name is left free for it
    assert (game / luma.ADDON).is_file() and (game / luma.DLSS).is_file()
    assert (game / luma.SHADERS / 'Global' / 'Luma_Copy_PS.hlsl').is_file()
    assert [item['name'] for item in record_data['moved']] == [luma.DLSS]
    assert (game / record_data['moved'][0]['to']).read_bytes() == b'the game already had one'
    assert all(sha(game / item['name']) == item['sha256'] for item in record_data['created'])


def test_the_record_lives_in_the_games_helixsr_folder(tmp_path, game):
    assert luma.record_path(game) == game / scaler.FOLDER / luma.RECORD   # the Scaler install's folder, removed with it
    assert not luma.installed(game)
    save_record(game, {'amdlss': '1.2.0', 'moved': [], 'created': []})
    assert luma.installed(game)


def test_remove_puts_the_games_own_files_back(tmp_path, game):
    package = make_package(tmp_path)
    (game / luma.DLSS).write_bytes(b'the game already had one')
    before = tree(game)
    record_data = {'amdlss': '1.2.0', 'luma': luma.BUILD, 'moved': [], 'created': []}
    luma.install_into(game, package, record_data)
    save_record(game, record_data)
    assert luma.remove(game) == []
    assert tree(game) == before and not luma.installed(game)
    assert sorted(p.name for p in game.iterdir()) == sorted(before)   # nothing of ours and no empty folder left


def test_remove_keeps_a_file_the_user_changed(tmp_path, game):
    package = make_package(tmp_path)
    record_data = {'amdlss': '1.2.0', 'luma': luma.BUILD, 'moved': [], 'created': []}
    luma.install_into(game, package, record_data)
    save_record(game, record_data)
    (game / luma.RESHADE).write_bytes(b'the user put another ReShade build here')
    notes = luma.remove(game)
    assert (game / luma.RESHADE).read_bytes() == b'the user put another ReShade build here'   # kept, not deleted
    assert any('kept' in note and luma.RESHADE in note for note in notes), notes
    assert not (game / luma.ADDON).exists() and not (game / luma.SHADERS).exists()            # the rest is gone


def test_mark_reads_the_record(tmp_path):
    folder = tmp_path / 'Game'
    folder.mkdir()
    entry = games.Game(name='One', folder=str(folder), exe_folder=str(folder))
    assert luma.mark(entry) is entry and entry.state == 'none'
    save_record(folder, {'amdlss': '1.2.0', 'moved': [], 'created': []})
    assert luma.mark(entry).state == games.INJECT and entry.route == games.INJECT   # the Scaler record alone: plain
    assert 'Luma UE' in games.STATE_TEXT[games.INJECT]


# --- what the window says ------------------------------------------------------------------------------------------

def test_the_texts_say_what_will_happen_and_what_to_check(tmp_path, game):
    entry = games.Game(name='One', folder=str(game), exe_folder=str(game))
    plan = luma.plan_text(entry)
    assert luma.FULL_NAME in plan and luma.BUILD in plan and 'DirectX 11' in plan
    assert 'SHA-256' in plan and 'MIT' in plan and scaler.BACKUP in plan and games.WARNINGS in plan
    assert '-dx11' in plan                      # how to get the game onto DirectX 11, before anything is installed
    after = luma.after_text(entry)
    assert 'Home' in after and 'DLSS' in after and 'CreateD3D12DeviceForLuma=false' in after
    assert 'DontCreateD3D12DeviceForLuma' not in after   # HelixSR Scaler 1.1.0 reads the positive name (CreateD3D12DeviceForLuma)
    assert 'no settings' in after and 'DirectX 12' in after and '-dx11' in after   # the empty tab, and what to do
    assert 'experimental' in after and 'original files' in after
    assert luma.FULL_NAME in luma.ROW_NOTE and 'no upscaler' in luma.ROW_NOTE
    assert luma.NAME in luma.download_text(0, luma.ZIP_SIZE)
    assert 'HelixSR Scaler' in luma.download_text(0, scaler.ZIP_SIZE)   # Scaler's pinned size tells the two apart


# --- the layout it is offered in -----------------------------------------------------------------------------------

def test_only_an_unreal_shipping_folder_with_a_64_bit_exe_counts(tmp_path, game):
    entry = games.Game(name='One', folder=str(game), exe_folder=str(game))
    assert games.inject_layout(entry)
    assert games.shipping_exe(str(game)) == str(game / 'One-Win64-Shipping.exe')
    assert not games.inject_layout(games.Game(name='One', exe_folder=str(game.parent)))   # not Binaries\Win64 itself
    (game / 'One-Win64-Shipping.exe').write_bytes(build_dll(machine=0x14c, magic=0x10b))  # a 32-bit build
    assert not games.inject_layout(entry)
    (game / 'One-Win64-Shipping.exe').unlink()
    assert not games.inject_layout(entry)                                                 # nothing to hook


def test_mark_inject_offers_the_route_and_refuses_anti_cheat(tmp_path, game):
    plain = games.Game(name='One', folder=str(game), exe_folder=str(game), reason=games.NO_UPSCALER)
    assert games.mark_inject(plain) is plain and plain.reason == '' and plain.route == games.INJECT
    guarded = games.Game(name='One', folder=str(game), exe_folder=str(game), reason=games.NO_UPSCALER,
                         anticheat=['BattlEye'])
    assert games.mark_inject(guarded).reason == games.INJECT_ANTICHEAT and guarded.route != games.INJECT
    upscaled = games.Game(name='One', folder=str(game), exe_folder=str(game), reason=games.NO_UPSCALER,
                          inputs=['DLSS'])
    assert games.mark_inject(upscaled).reason == games.NO_UPSCALER      # it has one of its own: not this route
    elsewhere = games.Game(name='One', folder=str(tmp_path), exe_folder=str(tmp_path), reason=games.NO_UPSCALER)
    assert games.mark_inject(elsewhere).reason == games.NO_UPSCALER     # not an Unreal shipping build


def test_an_injection_already_there_stays_offered_with_anti_cheat(tmp_path, game):
    """The refusal is about installing. An install that is in the game must stay offered, so Remove stays reachable."""
    guarded = games.Game(name='One', folder=str(game), exe_folder=str(game), reason=games.NO_UPSCALER,
                         anticheat=['BattlEye'])
    assert games.mark_inject(guarded, placed=True).reason == '' and guarded.route == games.INJECT


# --- install_game and remove_game ----------------------------------------------------------------------------------

def world(tmp_path, anticheat=None):
    """A game as HelixSR's list reports it (its .exe folder is the Unreal Binaries\\Win64 one, with no upscaler of
    its own), AMDLSS's data folder with both stand-in packages, and the fake HelixSR client."""
    exe = tmp_path / 'One' / 'Project' / 'Binaries' / 'Win64'
    exe.mkdir(parents=True)
    (exe / 'One-Win64-Shipping.exe').write_bytes(build_dll())
    folder = make_helixsr(tmp_path, [{'name': 'One', 'folder': str(exe), 'fsr': False, 'inputs': []}])
    data = tmp_path / 'data'
    make_package(data)
    make_scaler_package(data)
    client = helixsr.HelixSR(folder, python=sys.executable, running=lambda game: False)
    entry = client.list_games()[0]
    entry.anticheat = list(anticheat or [])
    return client, entry, data, exe


def test_install_game_installs_luma_then_scaler_and_sets_loadreshade(tmp_path):
    client, entry, data, exe = world(tmp_path)
    before = tree(exe)
    lines = []
    after = luma.install_game(client, entry, [], data, on_line=lambda text, final=True: lines.append(text))
    assert after.state == games.INJECT and after.route == games.INJECT
    assert (exe / luma.RESHADE).read_bytes() == f'STAND-IN {luma.SCALER_NAME}'.encode()
    assert (exe / scaler.NAMES[0]).read_bytes() == b'STAND-IN OptiScaler.dll'   # OptiScaler took dxgi.dll itself
    assert (exe / luma.SHADERS / 'Global' / 'Luma_Copy_PS.hlsl').is_file()
    assert luma.installed(exe) and scaler.installed(exe)
    ini = (exe / scaler.INI).read_bytes()
    assert b'LoadReshade=true' in ini and b'LoadReshade=auto' not in ini and ini.count(b'LoadReshade') == 1
    assert any('injected a DLSS call' in line for line in lines)
    assert any('LoadReshade=true' in line for line in lines)
    # Update: the same install again, keeping the settings the user made in OptiScaler.ini
    (exe / scaler.INI).write_bytes(ini + b'; the user added a line\r\n')
    again = luma.install_game(client, entry, [], data)
    assert b'the user added a line' in (exe / scaler.INI).read_bytes()
    assert luma.installed(exe) and again.state == games.INJECT
    # Remove, through the window's own path, puts every original file back
    fresh = luma.remove_game(client, scaler.mark(client.list_games()[0]), [], data)
    assert fresh.state == 'none' and not luma.installed(exe) and not scaler.installed(exe)
    assert tree(exe) == before
    assert not (data / framegen.RECORD).exists()   # the ini record goes with the install


def test_install_game_refuses_a_game_with_anti_cheat(tmp_path):
    client, entry, data, exe = world(tmp_path, anticheat=['BattlEye'])
    with pytest.raises(helixsr.HelixSRError, match='anti-cheat'):
        luma.install_game(client, entry, [], data)
    assert not (exe / luma.RESHADE).exists() and not luma.installed(exe)


def test_install_game_needs_a_data_folder(tmp_path):
    client, entry, _data, _exe = world(tmp_path)
    with pytest.raises(helixsr.HelixSRError, match='data folder'):
        luma.install_game(client, entry, [], None)


def test_a_failed_scaler_install_leaves_the_game_as_it_was(tmp_path, monkeypatch):
    """The Luma files go in first: if the Scaler install after them fails, none of them stays behind."""
    client, entry, data, exe = world(tmp_path)
    before = tree(exe)

    def explode(*_args, **_kwargs):
        raise helixsr.HelixSRError('the Scaler package is missing')

    monkeypatch.setattr(scaler, 'install_game', explode)
    with pytest.raises(helixsr.HelixSRError, match='as they were'):
        luma.install_game(client, entry, [], data)
    assert tree(exe) == before and not luma.installed(exe)
