# SPDX-License-Identifier: GPL-2.0-or-later
"""Reports know FSR 4.1.1: system.txt names the card class, games.txt and files.txt name the upscaler, and the report
page says "FSR 4.1.1 (AMD's DLL ...)" instead of treating HelixSR as missing."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from amdlss import fsr4, games, gpu, logs, reports
from test_logs import INFO, USER, fake  # noqa: F401 (the fixture)
from test_pe import build_dll
from test_reports import add_game, build

AMD = build_dll(version=(4, 1, 1, 2740))
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def amd_pin(monkeypatch):
    monkeypatch.setattr(fsr4, 'AMD_SIZE', len(AMD))
    monkeypatch.setattr(fsr4, 'AMD_SHA256', hashlib.sha256(AMD).hexdigest())


def page():
    spec = importlib.util.spec_from_file_location('report_html', ROOT / 'tools' / 'report_html.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dropin_game(fake, name='Drop Game', user_dll=False):  # noqa: F811
    game = add_game(fake, name, state='none', helixsr='')
    folder = Path(game.folder)
    (folder / fsr4.DLL).write_bytes(b'GAME DLL')
    game.fsr_targets = [str(folder / fsr4.DLL)]
    amd = folder.parent / 'amd'
    amd.mkdir(exist_ok=True)
    (amd / fsr4.DLL).write_bytes(AMD if not user_dll else build_dll(version=(4, 1, 1, 9)))
    (amd / fsr4.LICENSE).write_text('AMD license\n')
    source = fsr4.Source(amd / fsr4.DLL, 'your DLL 4.1.1.9' if user_dll else "AMD's DLL 4.1.1.2740", '', '', user_dll)
    fsr4.install_dropin(game.fsr_targets, source, folder.parent / 'data', amd / fsr4.LICENSE)
    fsr4.mark(game)
    return game


# --- system.txt ---------------------------------------------------------------------------------------------------

def test_system_txt_names_the_card_class():
    info = {'gpu': [{'Name': 'AMD Radeon RX 7900 XTX', 'PNPDeviceID': 'PCI\\VEN_1002&DEV_744C&SUBSYS_1', 'DriverVersion': '1'}]}
    assert 'FSR 4 card: rdna3 (RX 7000 (RDNA 3))' in logs.system_text(None, info, windows=True)
    only_nvidia = {'gpu': [{'Name': 'NVIDIA GeForce RTX 4090', 'PNPDeviceID': 'PCI\\VEN_10DE&DEV_2684&X'}]}
    assert 'FSR 4 card: other' in logs.system_text(None, only_nvidia, windows=True)
    assert 'FSR 4 card: could not be read' in logs.system_text(None, {}, windows=True)


def test_the_windows_query_asks_for_the_pnp_device_id():
    assert 'PNPDeviceID' in logs.SYSTEM_SCRIPT


def test_linux_system_info_carries_the_pci_ids(monkeypatch):
    monkeypatch.setattr(gpu, 'read_sysfs', lambda *a, **k: [gpu.Gpu('', '1002', '7550')])
    info = logs.system_info(windows=False)
    assert 'FSR 4 card: rdna4' in logs.system_text(None, info, windows=False)


# --- the install record shows in games.txt and files.txt -----------------------------------------------------------

def test_games_txt_says_the_upscaler_of_a_drop_in(fake):  # noqa: F811
    game = dropin_game(fake)
    fields = [f.strip() for f in build(fake, game)['files']['games.txt'].splitlines()[1].split(' | ')]
    assert 'install: drop-in DLL' in fields and 'HelixSR: none' in fields
    assert "upscaler: FSR 4.1.1 (AMD's DLL 4.1.1.2740)" in fields


def test_games_txt_says_the_upscaler_of_an_optiscaler_route(fake, tmp_path):  # noqa: F811
    game = add_game(fake, 'Opti Fsr', state='optiscaler', helixsr='1.9.0')
    folder = Path(game.folder)
    (folder / 'dxgi.dll').write_bytes(b'MZ OptiScaler 0.9.4')
    (folder / 'OptiScaler.ini').write_bytes(b'[Libraries]\r\nFfxDx12Path=auto\r\nFfxDx12SRPath=auto\r\n')
    mine = tmp_path / 'mine.dll'
    mine.write_bytes(build_dll(version=(4, 1, 1, 9)))
    fsr4.use_in_optiscaler(folder, fsr4.Source(mine, 'your DLL 4.1.1.9', '', '', True), None, tmp_path / 'data')
    fsr4.mark(game)
    fields = [f.strip() for f in build(fake, game)['files']['games.txt'].splitlines()[1].split(' | ')]
    assert 'upscaler: FSR 4.1.1 (your DLL 4.1.1.9)' in fields and 'HelixSR: 1.9.0' in fields
    assert 'install: OptiScaler as dxgi.dll' in fields


def test_a_game_on_helixsr_says_nothing_new(fake):  # noqa: F811
    plain = add_game(fake, 'Helix Game', state='direct')
    assert 'upscaler:' not in build(fake, plain)['files']['games.txt']


def test_files_txt_names_the_upscaler_the_dlls_and_whether_fsr4_is_possible(fake):  # noqa: F811
    game = dropin_game(fake)
    game.fsr4 = fsr4.Availability(True, ('direct',), '', 'yes')
    text = logs.files_text(game)
    assert "Upscaler: FSR 4.1.1 (AMD's DLL 4.1.1.2740)" in text and 'FSR 4.1.1: yes' in text
    line = next(l for l in text.splitlines() if l.startswith(fsr4.DLL))
    assert "AMD's FSR 4.1.1 DLL" in line
    assert next(l for l in text.splitlines() if l.startswith('amd_fidelityfx_upscaler_dx12.original.dll')).rstrip().endswith('not HelixSR')
    game.fsr4 = fsr4.Availability(False, (), 'Anti-cheat found', 'anti-cheat')
    assert 'FSR 4.1.1: no, anti-cheat' in logs.files_text(game)


def test_the_record_beside_the_dll_travels_with_the_logs(fake):  # noqa: F811
    game = dropin_game(fake)
    assert any(name.endswith(fsr4.MARKER) for name, _path in logs.gather(game))
    assert not reports.name_problem('Game/fsr-dll-folder/' + fsr4.MARKER)


# --- the report page ----------------------------------------------------------------------------------------------

def report_for(upscaler):
    return {'app': 'AMDLSS', 'version': '0.1.0-beta.17', 'game': 'G', 'files': {
        'system.txt': 'AMDLSS 0.1.0-beta.17\nWindows: Windows 11\nGPU 1: AMD Radeon RX 7800 XT, driver 1, driver date 2026-01-01\n'
                      'FSR 4 card: rdna3 (RX 7000 (RDNA 3))\nCPU: x\nRAM: 1 GB\nCollected: 2026-10-14 10:00:00\n',
        'games.txt': 'G/ | G | D:\\G | install: drop-in DLL | HelixSR: none' + (f' | upscaler: {upscaler}' if upscaler else '') + '\n',
        'G/files.txt': 'Game: G\n'}}


def test_the_page_reads_the_card_class_and_the_upscaler():
    module = page()
    system = module.parse_system(report_for('x')['files']['system.txt'])
    assert ('FSR 4 card', 'rdna3 (RX 7000 (RDNA 3))') in system
    parsed = module.parse_games(report_for("FSR 4.1.1 (AMD's DLL 4.1.1.2740)")['files']['games.txt'])
    assert parsed[0]['upscaler'] == "FSR 4.1.1 (AMD's DLL 4.1.1.2740)"
    assert module.parse_games(report_for('')['files']['games.txt'])[0]['upscaler'] == ''


def test_a_fsr4_game_is_not_reported_as_missing_helixsr():
    module = page()
    analysis = module.analyse(report_for("FSR 4.1.1 (AMD's DLL 4.1.1.2740)"), 'ABCD2345')
    game = analysis['games'][0]
    assert game['status'] == 'Working' and game['level'] == 'green'
    html = module.render_report(analysis)
    assert "FSR 4.1.1 (AMD&#x27;s DLL 4.1.1.2740)" in html or "FSR 4.1.1 (AMD's DLL 4.1.1.2740)" in html
    assert 'Did FSR 4.1.1 run?' in html and 'Did HelixSR run?' not in html
    assert any('Did FSR 4.1.1 run? Unknown' in line for line in module.timeline_lines(game['timeline']))
    assert 'FSR 4 card' in html and 'rdna3' in html


def test_a_helixsr_game_keeps_its_old_questions():
    module = page()
    html = module.render_report(module.analyse(report_for(''), 'ABCD2345'))
    assert 'Did HelixSR run?' in html and '<th>Upscaler</th>' not in html
