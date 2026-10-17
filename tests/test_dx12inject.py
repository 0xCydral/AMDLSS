# SPDX-License-Identifier: GPL-2.0-or-later
"""AMDLSS's DirectX 12 injection: which layout each card gets, what is placed where, the record, Update (the player's
render_scale stays), Remove (every file back, byte for byte), and how a game is routed to Luma UE or to this."""
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

from amdlss import dx12inject, framegen, games, gpu, helixsr, luma, pe, scaler
from fake_helixsr import make as make_helixsr
from fake_luma import make as make_package
from fake_scaler import make as make_scaler_package
from test_pe import build_dll

REPO_INI = Path(__file__).resolve().parent.parent / 'probe' / 'dx12' / 'dlss' / 'amdlss_dx12_dlss.ini'
NVIDIA_CARD = gpu.Gpu('NVIDIA GeForce RTX 4070', '10de', '2786')
AMD_CARD = gpu.Gpu('AMD Radeon RX 7800 XT', '1002', '747e')
IGPU = gpu.Gpu('AMD Radeon(TM) Graphics', '1002', '164e')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


@pytest.fixture(autouse=True)
def bundle(tmp_path, monkeypatch):
    """The bundled add-on is a stand-in; its ini is the repository's real one."""
    shipped = tmp_path / 'shipped'
    shipped.mkdir()
    (shipped / dx12inject.ADDON).write_bytes(b'STAND-IN add-on')
    (shipped / dx12inject.INI).write_bytes(REPO_INI.read_bytes())
    monkeypatch.setattr(dx12inject, 'bundled', lambda name: shipped / name if (shipped / name).is_file() else None)
    return shipped


def world(tmp_path, anticheat=None):
    """A DirectX 12 Unreal game as HelixSR's list reports it (no upscaler of its own), AMDLSS's data folder with both
    stand-in packages, and the fake HelixSR client."""
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


def names(folder):
    return sorted(str(p.relative_to(folder)).replace('\\', '/') for p in Path(folder).rglob('*') if p.is_file())


# --- the layout each card gets -------------------------------------------------------------------------------------

def test_any_geforce_gets_the_add_on_alone_and_every_other_card_gets_scaler():
    assert dx12inject.layout([NVIDIA_CARD]) == dx12inject.NVIDIA_LAYOUT
    assert dx12inject.layout([IGPU, NVIDIA_CARD]) == dx12inject.NVIDIA_LAYOUT      # a GeForce next to an iGPU
    assert dx12inject.layout([AMD_CARD]) == dx12inject.OTHER_LAYOUT
    assert dx12inject.layout([gpu.Gpu('Intel Arc A770', '8086', '56a0')]) == dx12inject.OTHER_LAYOUT
    assert dx12inject.layout([]) == dx12inject.OTHER_LAYOUT                       # unreadable: Scaler runs real DLSS


def test_the_nvidia_layout_is_reshade_the_add_on_its_ini_and_the_dlss_runtime(tmp_path):
    client, entry, data, exe = world(tmp_path)
    before = names(exe)
    lines = []
    after = dx12inject.install_game(client, entry, [], data, on_line=lambda text, final=True: lines.append(text),
                                    cards=[NVIDIA_CARD])
    assert after.state == games.INJECT12 and after.route == games.INJECT12
    assert sorted(set(names(exe)) - set(before)) == sorted(
        ['dxgi.dll', 'amdlss_dx12_dlss.addon64', 'amdlss_dx12_dlss.ini', 'nvngx_dlss.dll',
         'HelixSR/amdlss-dx12dlss.json'])
    assert (exe / 'dxgi.dll').read_bytes() == b'STAND-IN dxgi.dll'                 # Luma's zip carries ReShade as dxgi.dll
    assert (exe / 'nvngx_dlss.dll').read_bytes() == b'STAND-IN nvngx_dlss.dll'
    assert (exe / 'amdlss_dx12_dlss.addon64').read_bytes() == b'STAND-IN add-on'
    assert dx12inject.installed(exe) and not scaler.installed(exe) and not (exe / scaler.INI).exists()
    assert not (exe / luma.RESHADE).exists() and not (exe / luma.ADDON).exists()   # none of Luma's own files
    assert any('NVIDIA layout' in line and 'render_scale=quality' in line for line in lines)
    record = json.loads(dx12inject.record_path(exe).read_text(encoding='utf-8'))
    assert record['layout'] == 'nvidia' and record['luma_zip_sha256'] == luma.ZIP_SHA256


def test_the_ini_is_the_shipped_one_with_render_scale_quality(tmp_path):
    client, entry, data, exe = world(tmp_path)
    dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    shipped, placed = REPO_INI.read_text(), (exe / dx12inject.INI).read_text()
    assert 'render_scale=game' in shipped and 'render_scale=quality' in placed
    assert placed == shipped.replace('render_scale=game', 'render_scale=quality')   # nothing else changed


def test_the_other_layout_is_scaler_as_dxgi_reshade_as_reshade64_and_loadreshade(tmp_path):
    client, entry, data, exe = world(tmp_path)
    before = names(exe)
    after = dx12inject.install_game(client, entry, [], data, cards=[AMD_CARD])
    assert after.state == games.INJECT12
    added = set(names(exe)) - set(before)
    assert {'dxgi.dll', 'ReShade64.dll', 'amdlss_dx12_dlss.addon64', 'amdlss_dx12_dlss.ini', 'OptiScaler.ini',
            'HelixSR/helixsr.dll', 'HelixSR/amdlss-dx12dlss.json', 'HelixSR/amdlss-scaler.json'} <= added
    assert 'nvngx_dlss.dll' not in added and not any(name.startswith('Luma') for name in added)
    assert (exe / 'dxgi.dll').read_bytes() == b'STAND-IN OptiScaler.dll'            # Scaler took dxgi.dll
    assert (exe / 'ReShade64.dll').read_bytes() == b'STAND-IN dxgi.dll'             # ReShade from Luma's zip
    ini = (exe / scaler.INI).read_bytes()
    assert b'LoadReshade=true' in ini and b'LoadReshade=auto' not in ini
    assert dx12inject.installed(exe) and scaler.installed(exe)


# --- Remove and Update ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize('card', [NVIDIA_CARD, AMD_CARD])
def test_remove_puts_every_file_back_byte_for_byte(tmp_path, card):
    client, entry, data, exe = world(tmp_path)
    (exe / 'dxgi.dll').write_bytes(b'the game shipped its own dxgi.dll')          # a pre-existing file in the way
    (exe / 'nvngx_dlss.dll').write_bytes(b'the game shipped its own DLSS')
    (exe / 'ReShade64.dll').write_bytes(b'the player had ReShade here')
    before = tree(exe)
    dx12inject.install_game(client, entry, [], data, cards=[card])
    assert (exe / 'dxgi.dll').read_bytes() != b'the game shipped its own dxgi.dll'
    backup = exe / scaler.FOLDER / scaler.BACKUP
    assert (backup / 'dxgi.dll').read_bytes() == b'the game shipped its own dxgi.dll'
    lines = []
    fresh = dx12inject.remove_game(client, dx12inject.mark(scaler.mark(client.list_games()[0])), [], data,
                                   on_line=lambda text, final=True: lines.append(text))
    assert fresh.state == 'none' and not dx12inject.installed(exe) and not scaler.installed(exe)
    assert tree(exe) == before and not (exe / scaler.FOLDER).exists()
    assert not (data / framegen.RECORD).exists()


def test_remove_deletes_the_ini_the_tab_rewrote_and_the_add_ons_runtime_files(tmp_path):
    client, entry, data, exe = world(tmp_path)
    before = tree(exe)
    dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    (exe / dx12inject.INI).write_bytes(b'render_scale=performance\n')              # the add-on's tab saved it
    (exe / 'amdlss_dx12_dlss.log').write_text('log')
    (exe / 'tsr_1ff10c3ce95d3bcb.dxil').write_bytes(b'dxil')
    (exe / 'ngx').mkdir()
    (exe / 'ngx' / 'nvngx.log').write_text('ngx')
    assert dx12inject.remove(exe) == []
    assert tree(exe) == before and not (exe / 'ngx').exists()


def test_remove_keeps_a_file_the_user_changed(tmp_path):
    client, entry, data, exe = world(tmp_path)
    dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    (exe / 'dxgi.dll').write_bytes(b'the user put another ReShade build here')
    notes = dx12inject.remove(exe)
    assert (exe / 'dxgi.dll').read_bytes() == b'the user put another ReShade build here'
    assert any('kept' in note and 'dxgi.dll' in note for note in notes), notes
    assert not (exe / dx12inject.ADDON).exists()


def test_update_keeps_the_players_render_scale(tmp_path):
    client, entry, data, exe = world(tmp_path)
    before = tree(exe)
    dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    assert dx12inject.kept_scale(exe) == b'quality'
    shipped = (exe / dx12inject.INI).read_text()
    (exe / dx12inject.INI).write_text(shipped.replace('render_scale=quality', 'render_scale=performance'))
    again = dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    assert again.state == games.INJECT12
    assert 'render_scale=performance' in (exe / dx12inject.INI).read_text()
    record = json.loads(dx12inject.record_path(exe).read_text(encoding='utf-8'))
    assert all(sha(exe / item['name']) == item['sha256'] for item in record['created'])   # the record is true again
    dx12inject.remove_game(client, again, [], data)
    assert tree(exe) == before                                                      # one install, one Remove: clean


def test_update_on_the_other_layout_and_a_card_change_leave_one_consistent_install(tmp_path):
    client, entry, data, exe = world(tmp_path)
    before = tree(exe)
    dx12inject.install_game(client, entry, [], data, cards=[AMD_CARD])
    dx12inject.install_game(client, entry, [], data, cards=[AMD_CARD])             # Update: same layout
    assert (exe / 'dxgi.dll').read_bytes() == b'STAND-IN OptiScaler.dll' and scaler.installed(exe)
    dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])          # a GeForce was put in the PC
    assert (exe / 'dxgi.dll').read_bytes() == b'STAND-IN dxgi.dll' and not scaler.installed(exe)
    assert not (exe / 'ReShade64.dll').exists() and not (exe / scaler.INI).exists()
    dx12inject.remove_game(client, entry, [], data)
    assert tree(exe) == before


def test_an_add_on_older_than_the_shipped_one_asks_for_update(tmp_path, bundle):
    client, entry, data, exe = world(tmp_path)
    assert not dx12inject.addon_outdated(exe)                                      # nothing installed
    dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    assert not dx12inject.addon_outdated(exe)                                      # just installed: current
    (bundle / dx12inject.ADDON).write_bytes(b'STAND-IN add-on, next release')
    assert dx12inject.addon_outdated(exe)                                          # AMDLSS carries a newer one
    dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    assert not dx12inject.addon_outdated(exe)                                      # Update placed it


def test_kept_scale_ignores_junk_and_ini_bytes_touch_only_that_line(tmp_path):
    exe = tmp_path
    assert dx12inject.kept_scale(exe) == b'quality'                                # no ini yet
    (exe / dx12inject.INI).write_bytes(b'enabled=1\r\nrender_scale = 58 \r\n')
    assert dx12inject.kept_scale(exe) == b'58'
    (exe / dx12inject.INI).write_bytes(b'render_scale=\\evil\\1\n')
    assert dx12inject.kept_scale(exe) == b'quality'
    source = tmp_path / 'a.ini'
    source.write_bytes(b'; render_scale=game is a comment\r\nrender_scale=game\r\nother=1\r\n')
    assert dx12inject.ini_bytes(source, b'dlaa') == b'; render_scale=game is a comment\r\nrender_scale=dlaa\r\nother=1\r\n'


# --- refusals and failures -----------------------------------------------------------------------------------------

def test_an_add_on_that_is_not_built_stops_before_anything_changes(tmp_path, monkeypatch):
    client, entry, data, exe = world(tmp_path)
    before = tree(exe)
    monkeypatch.setattr(dx12inject, 'bundled', lambda name: None)
    with pytest.raises(helixsr.HelixSRError, match='build.sh'):
        dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    assert tree(exe) == before and not dx12inject.installed(exe)


def test_the_source_tree_looks_in_out_for_the_add_on_and_next_to_the_build_for_the_ini(tmp_path, monkeypatch):
    monkeypatch.undo()                       # the real bundled(): no stand-in
    assert dx12inject.bundled(dx12inject.INI) == REPO_INI
    out = REPO_INI.parent / 'out' / dx12inject.ADDON
    assert dx12inject.bundled(dx12inject.ADDON) == (out if out.is_file() else None)


def test_a_bundle_folder_is_read_when_frozen(tmp_path, monkeypatch):
    monkeypatch.undo()
    folder = tmp_path / dx12inject.BUNDLE
    folder.mkdir()
    (folder / dx12inject.ADDON).write_bytes(b'x')
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    assert dx12inject.bundled(dx12inject.ADDON) == folder / dx12inject.ADDON
    assert dx12inject.bundled(dx12inject.INI) is None


def test_anti_cheat_luma_and_a_missing_data_folder_are_refused(tmp_path):
    client, entry, data, exe = world(tmp_path, anticheat=['BattlEye'])
    with pytest.raises(helixsr.HelixSRError, match='anti-cheat'):
        dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    entry.anticheat = []
    with pytest.raises(helixsr.HelixSRError, match='data folder'):
        dx12inject.install_game(client, entry, [], None, cards=[NVIDIA_CARD])
    luma.record_path(exe).parent.mkdir(parents=True)
    luma.record_path(exe).write_text(json.dumps({'moved': [], 'created': []}), encoding='utf-8')
    with pytest.raises(helixsr.HelixSRError, match='Luma UE is installed'):
        dx12inject.install_game(client, entry, [], data, cards=[NVIDIA_CARD])
    assert not (exe / dx12inject.ADDON).exists()


def test_a_failed_scaler_install_leaves_the_game_as_it_was(tmp_path, monkeypatch):
    client, entry, data, exe = world(tmp_path)
    (exe / 'ReShade64.dll').write_bytes(b'the player had ReShade here')
    before = tree(exe)

    def explode(*_args, **_kwargs):
        raise helixsr.HelixSRError('the Scaler package is missing')

    monkeypatch.setattr(scaler, 'install_game', explode)
    with pytest.raises(helixsr.HelixSRError, match='as they were'):
        dx12inject.install_game(client, entry, [], data, cards=[AMD_CARD])
    assert tree(exe) == before and not dx12inject.installed(exe)


def test_mark_reads_the_record(tmp_path):
    folder = tmp_path / 'Game'
    folder.mkdir()
    entry = games.Game(name='One', folder=str(folder), exe_folder=str(folder))
    assert dx12inject.mark(entry).state == 'none'
    dx12inject.record_path(folder).parent.mkdir()
    dx12inject.record_path(folder).write_text(json.dumps({'moved': [], 'created': []}), encoding='utf-8')
    assert dx12inject.mark(entry).state == games.INJECT12 and entry.route == games.INJECT12
    assert 'DirectX 12' in games.STATE_TEXT[games.INJECT12]


# --- what the window says ------------------------------------------------------------------------------------------

def test_the_texts_say_what_will_happen_for_each_layout(tmp_path):
    entry = games.Game(name='One', folder=str(tmp_path), exe_folder=str(tmp_path))
    nvidia, other = (dx12inject.plan_text(entry, [card]) for card in (NVIDIA_CARD, AMD_CARD))
    for plan in (nvidia, other):
        assert 'Echoes of Aincrad' in plan and 'experimental' in plan and 'SHA-256' in plan
        assert games.WARNINGS in plan and scaler.BACKUP in plan and 'TSR' in plan
    assert 'nvngx_dlss.dll' in nvidia and 'HelixSR Scaler' not in nvidia
    assert 'HelixSR Scaler' in other and 'LoadReshade' in other and 'ReShade64.dll' in other
    after = dx12inject.after_text(entry, dx12inject.OTHER_LAYOUT)
    assert 'Home' in after and 'render_scale=quality' in after and 'next time the game starts' in after
    assert 'Echoes of Aincrad' in after and 'anti-cheat' in after and 'original files' in after
    assert 'real DLSS' in dx12inject.after_text(entry, dx12inject.NVIDIA_LAYOUT)
    assert dx12inject.BUILD_COMMAND in dx12inject.not_built_text()
    assert 'HelixSR Scaler' in dx12inject.download_text(0, scaler.ZIP_SIZE) and 'ReShade' in dx12inject.download_text(0, 1)
    assert 'DirectX 11' in dx12inject.choice_note(entry) and 'DirectX 12' in dx12inject.choice_note(entry)


# --- routing: Luma UE for DirectX 11, this for DirectX 12 -----------------------------------------------------------

def with_imports(data, imports=(), delayed=()):
    """`data` (a build_dll PE) with an import table naming `imports` and a delay-load table naming `delayed`: a third
    section at file 0xB00 (RVA 0x3000)."""
    image = bytearray(data)
    struct.pack_into('<H', image, 0x84 + 2, 3)
    section = 0x98 + 240 + 80
    image[section:section + 8] = b'.idata'.ljust(8, b'\0')
    struct.pack_into('<IIII', image, section + 8, 0x200, 0x3000, 0x200, 0xB00)
    directories = 0x98 + 112
    struct.pack_into('<II', image, directories + 8, 0x3000, 0x80)
    struct.pack_into('<II', image, directories + 8 * 13, 0x3080, 0x80)
    cursor = 0x3100
    for table, size, name_at, wanted in ((0x3000, 20, 12, imports), (0x3080, 32, 4, delayed)):
        for index, dll in enumerate(wanted):
            struct.pack_into('<I', image, 0xB00 + (table - 0x3000) + size * index + name_at, cursor)
            image[0xB00 + (cursor - 0x3000):0xB00 + (cursor - 0x3000) + len(dll) + 1] = dll.encode() + b'\0'
            cursor += len(dll) + 1
    return bytes(image)


def unreal(tmp_path, imports=(), delayed=(), name='Game'):
    """A Binaries\\Win64 folder with a 64-bit shipping .exe linking the given DLLs; returns the game."""
    exe = tmp_path / name / 'Project' / 'Binaries' / 'Win64'
    exe.mkdir(parents=True)
    (exe / 'Project-Win64-Shipping.exe').write_bytes(with_imports(build_dll(), imports, delayed))
    return games.Game(name=name, folder=str(tmp_path / name), exe_folder=str(exe), reason=games.NO_UPSCALER)


def test_pe_imports_reads_the_import_and_the_delay_load_tables(tmp_path):
    path = tmp_path / 'a.exe'
    path.write_bytes(with_imports(build_dll(), ['KERNEL32.dll', 'D3D11.dll'], ['d3d12.dll']))
    assert pe.imports(path) == {'kernel32.dll', 'd3d11.dll', 'd3d12.dll'}
    path.write_bytes(build_dll())
    assert pe.imports(path) == frozenset()                                         # no import table at all
    path.write_bytes(b'not a program' * 50)
    with pytest.raises(pe.PEError):
        pe.imports(path)


def test_a_game_that_links_only_direct3d_12_is_routed_to_the_add_on(tmp_path):
    game = unreal(tmp_path, ['KERNEL32.dll'], ['d3d12.dll'])
    assert games.unreal_rhi(game)[0] == games.RHI_DX12
    assert games.mark_inject(game) is game and game.route == games.INJECT12 and game.reason == ''
    assert game.rhi == games.RHI_DX12 and not game.inject_choice and 'Direct3D 12' in game.rhi_why


def test_a_game_that_links_only_direct3d_11_stays_on_luma(tmp_path):
    game = unreal(tmp_path, ['d3d11.dll', 'dxgi.dll'])
    games.mark_inject(game)
    assert game.route == games.INJECT and game.rhi == games.RHI_DX11 and not game.inject_choice


def test_a_game_with_both_or_neither_shows_both_routes_with_the_reason(tmp_path):
    both = unreal(tmp_path, ['d3d11.dll'], ['d3d12.dll'], name='Both')
    games.mark_inject(both)
    assert both.route == games.INJECT and both.rhi == '' and both.inject_choice
    assert 'either DirectX 11 or DirectX 12' in both.rhi_why
    neither = unreal(tmp_path, ['KERNEL32.dll'], name='Neither')
    games.mark_inject(neither)
    assert neither.route == games.INJECT and neither.inject_choice and 'nothing in' in neither.rhi_why
    neither.state = games.INJECT12                                                 # installed: no choice any more
    assert not neither.inject_choice


def test_default_engine_ini_beats_the_imports_and_a_sm6_cache_decides_when_they_do_not(tmp_path):
    game = unreal(tmp_path, ['d3d11.dll'], ['d3d12.dll'], name='Ini')
    config = Path(game.exe_folder).parent.parent / 'Config'
    config.mkdir()
    (config / 'DefaultEngine.ini').write_text('[/Script/WindowsTargetPlatform.WindowsTargetSettings]\n'
                                              'DefaultGraphicsRHI=DefaultGraphicsRHI_DX11\n')
    assert games.unreal_rhi(game)[0] == games.RHI_DX11
    (config / 'DefaultEngine.ini').write_text('DefaultGraphicsRHI = DefaultGraphicsRHI_DX12\n')
    assert games.unreal_rhi(game)[0] == games.RHI_DX12 and 'DefaultEngine.ini' in games.unreal_rhi(game)[1]
    cached = unreal(tmp_path, ['d3d11.dll'], ['d3d12.dll'], name='Cached')
    caches = Path(cached.exe_folder).parent.parent / 'Content' / 'PipelineCaches' / 'Windows'
    caches.mkdir(parents=True)
    (caches / 'Project_PCD3D_SM5.stable.upipelinecache').write_bytes(b'x')
    assert games.unreal_rhi(cached)[0] == ''                                       # SM5 runs on both APIs
    (caches / 'Project_PCD3D_SM6.stable.upipelinecache').write_bytes(b'x')
    assert games.unreal_rhi(cached)[0] == games.RHI_DX12
    deep = unreal(tmp_path, name='Deep')
    saved = Path(deep.exe_folder).parent.parent / 'Saved' / 'a' / 'b' / 'c' / 'd'
    saved.mkdir(parents=True)
    (saved / 'Project_PCD3D_SM6.upipelinecache').write_bytes(b'x')
    assert games.unreal_rhi(deep)[0] == ''                                         # too deep to be a cache folder


def test_an_unreadable_exe_is_never_claimed_and_anti_cheat_still_refuses(tmp_path):
    game = unreal(tmp_path, ['d3d12.dll'])
    (Path(game.exe_folder) / 'Project-Win64-Shipping.exe').write_bytes(build_dll())   # a PE, but no import table
    assert games.unreal_rhi(game)[0] == ''
    guarded = unreal(tmp_path, ['d3d12.dll'], name='Guarded')
    guarded.anticheat = ['BattlEye']
    assert games.mark_inject(guarded).reason == games.INJECT_ANTICHEAT and guarded.route != games.INJECT12
    placed = unreal(tmp_path, ['d3d12.dll'], name='Placed')                           # an install is there: Remove stays
    placed.anticheat = ['BattlEye']
    assert games.mark_inject(placed, placed=True).route == games.INJECT12 and placed.reason == ''


def steam_game(tmp_path, imports=(['d3d11.dll'], ['d3d12.dll'])):
    """A Steam Unreal game (Echoes of Aincrad's shape: both graphics DLLs linked, nothing cached in the game folder)
    under <library>/steamapps/common, with its manifest; returns (game, the Proton prefix's Local folder)."""
    library = tmp_path / 'lib' / 'steamapps'
    root = library / 'common' / 'Echoes'
    exe = root / 'EchoesofAincrad' / 'Binaries' / 'Win64'
    exe.mkdir(parents=True)
    (exe / 'EchoesofAincrad-Win64-Shipping.exe').write_bytes(with_imports(build_dll(), *imports))
    (library / 'appmanifest_2244210.acf').write_text('"appid" "2244210"\n"installdir" "Echoes"\n"name" "Echoes"\n')
    local = library / 'compatdata' / '2244210' / 'pfx' / 'drive_c' / 'users' / 'steamuser' / 'AppData' / 'Local'
    game = games.Game(name='Echoes', folder=str(root), exe_folder=str(exe), reason=games.NO_UPSCALER)
    return game, local


def test_a_sm6_cache_in_the_players_proton_prefix_makes_it_dx12(tmp_path):
    game, local = steam_game(tmp_path)
    assert games.unreal_rhi(game)[0] == ''                                         # both DLLs, nothing cached yet
    saved = local / 'EchoesofAincrad' / 'Saved'
    saved.mkdir(parents=True)
    (saved / 'EchoesofAincrad_PCD3D_SM5.upipelinecache').write_bytes(b'x')
    assert games.unreal_rhi(game)[0] == ''                                         # SM5 says nothing
    (saved / 'EchoesofAincrad_PCD3D_SM6.upipelinecache').write_bytes(b'x')
    rhi, why = games.unreal_rhi(game)
    assert rhi == games.RHI_DX12 and 'Shader Model 6' in why
    games.mark_inject(game)
    assert game.route == games.INJECT12 and not game.inject_choice
    other = local / 'OtherProject' / 'Saved'                                       # another project's cache is not ours
    other.mkdir(parents=True)
    (saved / 'EchoesofAincrad_PCD3D_SM6.upipelinecache').unlink()
    (other / 'Other_PCD3D_SM6.upipelinecache').write_bytes(b'x')
    assert games.unreal_rhi(game)[0] == ''


def test_a_sm6_cache_in_local_appdata_is_found_on_windows(tmp_path, monkeypatch):
    game, _local = steam_game(tmp_path)
    appdata = tmp_path / 'AppData' / 'Local'
    (appdata / 'EchoesofAincrad' / 'Saved').mkdir(parents=True)
    (appdata / 'EchoesofAincrad' / 'Saved' / 'EchoesofAincrad_PCD3D_SM6.upipelinecache').write_bytes(b'x')
    monkeypatch.setattr(games.host, 'is_windows', lambda windows=None: True if windows is None else bool(windows))
    monkeypatch.setenv('LOCALAPPDATA', str(appdata))
    monkeypatch.setattr(games.host, 'path_module', lambda windows=None: __import__('posixpath'))   # tmp paths are posix
    assert games.unreal_rhi(game)[0] == games.RHI_DX12
