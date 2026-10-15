# SPDX-License-Identifier: GPL-2.0-or-later
"""HelixSR Scaler: the pinned package, its placement (the package's own file list), the record, and Remove."""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from amdlss import fsr4, helixsr, scaler
from fake_helixsr import make, old_install
from fake_scaler import INI, LIST, LISTED, UNLISTED
from fake_scaler import make as make_package


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def save_record(game_folder, record_data):
    scaler.record_path(game_folder).parent.mkdir(parents=True, exist_ok=True)
    scaler.record_path(game_folder).write_text(json.dumps(record_data), encoding='utf-8')


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


def serve(monkeypatch, data, size=None, sha_value=None):
    """Makes scaler.fetch() 'download' `data`, pinned as if it were the real release."""
    monkeypatch.setattr(scaler, 'ZIP_SIZE', len(data) if size is None else size)
    monkeypatch.setattr(scaler, 'ZIP_SHA256', hashlib.sha256(data).hexdigest() if sha_value is None else sha_value)
    monkeypatch.setattr(helixsr, 'download', lambda part, progress, url, size: part.write_bytes(data))


def package_bytes():
    return zip_bytes({f'{scaler.TOP}/{scaler.LIST}': '\n'.join(LISTED) + '\n',
                      f'{scaler.TOP}/{scaler.DLL}': 'the OptiScaler DLL'})


@pytest.fixture
def game(tmp_path):
    folder = tmp_path / 'Game'
    folder.mkdir()
    (folder / 'Game.exe').write_bytes(b'MZ')
    return folder


def helix_folder(tmp_path, fg=True):
    folder = tmp_path / 'HelixSR-1.9.0'
    folder.mkdir()
    (folder / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.9.0\x00')
    if fg:
        fgdir = folder / helixsr.FG_FOLDER
        fgdir.mkdir()
        for name in helixsr.FG_DLLS:
            (fgdir / name).write_bytes(f'MZ {name}'.encode())
        (folder / helixsr.FG_SETUP).write_text('{}', encoding='utf-8')
    return folder


def record(game_folder, name='dxgi.dll'):
    return {'amdlss': '1.1.0', 'scaler': scaler.VERSION, 'name': name, 'moved': [], 'created': []}


# --- the package --------------------------------------------------------------------------------------------------

def test_fetch_extracts_the_pinned_package(tmp_path, monkeypatch):
    serve(monkeypatch, package_bytes())
    folder = scaler.fetch(tmp_path)
    assert folder == tmp_path / scaler.TOP and scaler.extracted(tmp_path)
    assert (folder / scaler.DLL).read_bytes() == b'the OptiScaler DLL'
    assert not (tmp_path / '.download').exists()


def test_fetch_rejects_a_bad_hash(tmp_path, monkeypatch):
    serve(monkeypatch, package_bytes(), sha_value='0' * 64)
    with pytest.raises(helixsr.HelixSRError, match='SHA-256'):
        scaler.fetch(tmp_path)
    assert not (tmp_path / scaler.TOP).exists()
    assert not list((tmp_path / '.download').glob('*.part'))


def test_fetch_rejects_zip_slip(tmp_path, monkeypatch):
    serve(monkeypatch, zip_bytes({f'{scaler.TOP}/{scaler.LIST}': 'x', '../evil.txt': 'x'}))
    target = tmp_path / 'target'
    with pytest.raises(helixsr.HelixSRError, match='outside the folder'):
        scaler.fetch(target)
    assert not (tmp_path / 'evil.txt').exists() and not (target / scaler.TOP).exists()


def test_fetch_rejects_a_package_that_is_not_one(tmp_path, monkeypatch):
    serve(monkeypatch, zip_bytes({f'{scaler.TOP}/README.txt': 'x'}))
    with pytest.raises(helixsr.HelixSRError, match='does not hold'):
        scaler.fetch(tmp_path)


def test_files_reads_the_packages_own_list(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    (package / LIST).write_text('# a comment\n\nOptiScaler.dll\nsetup_linux.sh\n'
                                'OptiScaler/streamline/sl.dlss_g.dll\n')
    assert scaler.files(package) == ['OptiScaler.dll', 'OptiScaler/streamline/sl.dlss_g.dll']  # own files skipped


def test_locate_finds_the_override_then_the_data_folder(tmp_path):
    assert scaler.locate(tmp_path) is None
    package = tmp_path / scaler.TOP
    package.mkdir()
    (package / scaler.DLL).write_bytes(b'x')
    (package / LIST).write_text('OptiScaler.dll\n')
    assert scaler.locate(tmp_path) == package
    other = tmp_path / 'elsewhere'
    other.mkdir()
    (other / scaler.DLL).write_bytes(b'x')
    (other / LIST).write_text('OptiScaler.dll\n')
    assert scaler.locate(tmp_path, str(other)) == other


# --- the placement ------------------------------------------------------------------------------------------------

def test_install_places_the_listed_files_and_keeps_what_it_replaces(tmp_path, game):
    package = make_package(tmp_path)
    helix = helix_folder(tmp_path)
    (game / 'OptiScaler.ini').write_bytes(b'the settings the user already had')
    record_data = record(game)
    scaler.install_into(package, game, helix, 'dxgi.dll', record_data)
    assert (game / 'dxgi.dll').read_bytes() == b'STAND-IN OptiScaler.dll'
    assert (game / 'OptiScaler' / 'streamline' / 'sl.dlss_g.dll').read_bytes() == b'STAND-IN OptiScaler/streamline/sl.dlss_g.dll'
    assert (game / 'Licenses' / 'OptiScaler_LICENSE.txt').is_file()
    assert (game / 'HelixSR' / 'helixsr.dll').read_bytes() == (helix / helixsr.DLL).read_bytes()
    assert (game / 'HelixSR' / 'helixsr-fg' / 'nvngx_dlssg.dll').is_file()
    for name in UNLISTED:   # the download's own files: named by the list, never copied into the game
        assert not (game / name).exists()
    moved = record_data['moved']
    assert [item['name'] for item in moved] == ['OptiScaler.ini']
    assert moved[0]['to'] == f'HelixSR/{scaler.BACKUP}/OptiScaler.ini'
    assert (game / moved[0]['to']).read_bytes() == b'the settings the user already had'
    assert record_data['created'] and all((game / item['name']).is_file() for item in record_data['created'])
    assert all(sha(game / item['name']) == item['sha256'] for item in record_data['created'])


def test_install_without_frame_generation_copies_no_fg_files(tmp_path, game):
    package = make_package(tmp_path)
    helix = helix_folder(tmp_path, fg=False)
    scaler.install_into(package, game, helix, 'dxgi.dll', record(game))
    assert (game / 'HelixSR' / 'helixsr.dll').is_file()
    assert not (game / 'HelixSR' / helixsr.FG_FOLDER).exists()


def test_remove_puts_the_games_own_files_back(tmp_path, game):
    package = make_package(tmp_path)
    helix = helix_folder(tmp_path)
    (game / 'OptiScaler.ini').write_bytes(b'the settings the user already had')
    before = tree(game)
    record_data = record(game)
    scaler.install_into(package, game, helix, 'dxgi.dll', record_data)
    save_record(game, record_data)
    assert scaler.installed(game)
    assert scaler.remove(game) == []
    assert tree(game) == before and not scaler.installed(game)


def test_remove_keeps_a_file_the_user_changed(tmp_path, game):
    package = make_package(tmp_path)
    helix = helix_folder(tmp_path)
    record_data = record(game)
    scaler.install_into(package, game, helix, 'dxgi.dll', record_data)
    save_record(game, record_data)
    (game / 'dxgi.dll').write_bytes(b'the user put another DLL here')
    notes = scaler.remove(game)
    assert (game / 'dxgi.dll').read_bytes() == b'the user put another DLL here'   # kept, not deleted
    assert any('kept' in note and 'dxgi.dll' in note for note in notes), notes
    assert not (game / 'HelixSR').exists()                                        # the rest is gone


def test_name_for_uses_scalers_own_order(tmp_path, game):
    assert scaler._name_for(game) == 'dxgi.dll'
    (game / 'dxgi.dll').write_bytes(b'the game has its own')
    assert scaler._name_for(game) == 'winmm.dll'
    for name in scaler.NAMES:
        (game / name).write_bytes(b'x')
    assert scaler._name_for(game) == 'dxgi.dll'


def test_plan_text_names_the_package_and_what_remove_does(game):
    text = scaler.plan_text(type('G', (), {'exe_folder': str(game)})())
    assert 'HelixSR Scaler 1.0.3' in text and 'OptiScaler 0.9.4' in text and scaler.BACKUP in text


# --- install_game, the game's HelixSR folder and an earlier install ------------------------------------------------

def world(tmp_path):
    one = tmp_path / 'One'
    one.mkdir()
    (one / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'GAME ORIGINAL')
    folder = make(tmp_path, [{'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']}])
    data = tmp_path / 'data'
    make_package(data)
    client = helixsr.HelixSR(folder, python=sys.executable, running=lambda game: False)
    return client, one, data


def test_install_game_then_update_then_remove(tmp_path):
    client, one, data = world(tmp_path)
    entry = client.list_games()[0]
    before = tree(one)
    after = scaler.install_game(client, entry, [], data)
    assert after.state == 'optiscaler' and scaler.installed(one)
    assert (one / 'dxgi.dll').read_bytes() == b'STAND-IN OptiScaler.dll'
    assert (one / 'amd_fidelityfx_upscaler_dx12.dll').read_bytes() == b'GAME ORIGINAL'   # the drop-in was not used
    assert (one / 'OptiScaler.ini').read_bytes() == INI                                   # the package's own ini
    assert scaler.mark(client.list_games()[0]).state == 'optiscaler'   # a fresh list sees the record
    # an update keeps the settings the user made in OptiScaler.ini
    (one / 'OptiScaler.ini').write_bytes(b'[FrameGen]\r\nFGInput=nvngxfg\r\n')
    after = scaler.install_game(client, entry, [], data)
    assert (one / 'OptiScaler.ini').read_bytes() == b'[FrameGen]\r\nFGInput=nvngxfg\r\n'
    assert scaler.installed(one) and after.state == 'optiscaler'
    # Remove through the app's own path puts every file back
    fsr4.remove_game(client, scaler.mark(client.list_games()[0]), [], data)
    assert tree(one) == before and not scaler.installed(one)


def test_install_game_takes_an_amdlss_100_install_out_first(tmp_path):
    client, one, data = world(tmp_path)
    old_install(one)   # marker, dxgi.dll, OptiScaler.ini, HelixSR\helixsr.dll: AMDLSS 1.0.0's bridge
    assert (one / 'dxgi.dll').read_bytes() == b'OPTISCALER 0.9.4'
    scaler.install_game(client, client.list_games()[0], [], data)
    assert (one / 'dxgi.dll').read_bytes() == b'STAND-IN OptiScaler.dll'   # ours now
    assert (one / 'HelixSR' / scaler.RECORD).is_file() and scaler.installed(one)
    assert not (one / 'HelixSR' / 'helixsr-install.json').exists()          # the old record is gone


def test_install_game_fetches_when_the_package_is_missing(tmp_path, monkeypatch):
    client, _one, data = world(tmp_path)
    import shutil
    shutil.rmtree(data)
    seen = []
    monkeypatch.setattr(scaler, 'fetch', lambda target, progress=None: seen.append(str(target)) or
                        scaler.install_folder(target))
    with pytest.raises(helixsr.HelixSRError, match='was not installed'):
        scaler.install_game(client, client.list_games()[0], [], data)   # the fetched folder is empty: a failure
    assert seen == [str(data)]


def test_install_helixsr_fetches_the_scaler_package_too(tmp_path, monkeypatch):
    """AMD's FSR 4 DLL now lives in the Scaler package, so Install HelixSR fetches it as well and FSR 4.1.1 works
    right after it, without an OptiScaler install. A failed Scaler download must not undo the HelixSR install."""
    monkeypatch.setattr(helixsr, 'fetch', lambda target, progress=None: None)
    monkeypatch.setattr(helixsr, 'build', lambda folder, on_line=None, data_dir=None: None)
    monkeypatch.setattr(helixsr, 'install_folder', lambda target: tmp_path / 'HelixSR-1.9.0')
    serve(monkeypatch, package_bytes())
    data = tmp_path / 'data'
    assert helixsr.install_helixsr(data, consented=True) == tmp_path / 'HelixSR-1.9.0'
    assert scaler.extracted(data)   # the package is there: AMD's FSR 4 DLL has somewhere to come from

    def no_network(target, progress=None):
        raise helixsr.HelixSRError('no network')

    monkeypatch.setattr(scaler, 'fetch', no_network)
    lines = []
    folder = helixsr.install_helixsr(tmp_path / 'other', consented=True,
                                     on_line=lambda text, final=False: lines.append(text))
    assert folder == tmp_path / 'HelixSR-1.9.0' and not scaler.extracted(tmp_path / 'other')
    assert lines == ['HelixSR Scaler was not downloaded (no network): FSR 4.1.1 will not be available until it is']


def test_a_failure_halfway_puts_every_file_back(tmp_path, monkeypatch):
    client, one, data = world(tmp_path)
    before = tree(one)
    package = data / scaler.TOP
    (package / LIST).write_text('\n'.join(LISTED) + '\nOptiScaler/missing.dll\n', encoding='utf-8')
    with pytest.raises(helixsr.HelixSRError, match='was not installed'):
        scaler.install_game(client, client.list_games()[0], [], data)
    assert tree(one) == before and not scaler.installed(one)
    assert not (one / 'HelixSR').exists()


def test_a_running_game_is_not_touched(tmp_path):
    client, one, data = world(tmp_path)
    client.running = lambda game: True
    with pytest.raises(helixsr.HelixSRError, match='still running'):
        scaler.install_game(client, client.list_games()[0], [], data)
    assert not scaler.installed(one) and not (one / 'dxgi.dll').exists()
