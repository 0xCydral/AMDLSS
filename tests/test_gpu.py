# SPDX-License-Identifier: GPL-2.0-or-later
"""The graphics card check for FSR 4: PCI IDs, names, the Windows query and the Linux sysfs read, all on fixed input."""
import json

from amdlss import gpu


def card(name='', device='', vendor='1002'):
    return gpu.Gpu(name, vendor, device)


def test_known_device_ids_give_their_family():
    assert gpu.classify_one(card(device='7550')) == gpu.RDNA4      # RX 9070 XT
    assert gpu.classify_one(card(device='7590')) == gpu.RDNA4      # RX 9060 XT
    assert gpu.classify_one(card(device='744c')) == gpu.RDNA3      # RX 7900 XTX
    assert gpu.classify_one(card(device='747E')) == gpu.RDNA3      # RX 7800 XT, upper case
    assert gpu.classify_one(card(device='73bf')) == gpu.RDNA2      # RX 6800 XT
    assert gpu.classify_one(card(device='743f')) == gpu.RDNA2      # RX 6500 XT


def test_integrated_radeon_graphics_are_other():
    assert gpu.classify_one(card('AMD Radeon 780M Graphics', '15bf')) == gpu.OTHER
    assert gpu.classify_one(card('AMD Radeon(TM) 890M Graphics', '150e')) == gpu.OTHER
    assert gpu.classify_one(card('AMD Radeon RX Vega 11 Graphics', '15d8')) == gpu.OTHER


def test_an_unknown_amd_id_falls_back_to_the_name():
    assert gpu.classify_one(card('AMD Radeon RX 9070 XT', '9999')) == gpu.RDNA4
    assert gpu.classify_one(card('AMD Radeon RX 9060', '')) == gpu.RDNA4
    assert gpu.classify_one(card('AMD Radeon AI PRO R9700', '')) == gpu.RDNA4
    assert gpu.classify_one(card('AMD Radeon RX 7900 XTX', '')) == gpu.RDNA3
    assert gpu.classify_one(card('AMD Radeon PRO W7800', '')) == gpu.RDNA3
    assert gpu.classify_one(card('AMD Radeon RX 6700 XT', '')) == gpu.RDNA2
    assert gpu.classify_one(card('AMD Radeon RX 580 Series', '')) == gpu.OTHER


def test_other_makers_are_other_whatever_the_name_says():
    assert gpu.classify_one(card('NVIDIA GeForce RTX 4090', '2684', '10de')) == gpu.OTHER
    assert gpu.classify_one(card('Intel(R) Arc(TM) A770', '56a0', '8086')) == gpu.OTHER
    assert gpu.classify_one(card('Imaginary RX 7900', '', '10de')) == gpu.OTHER


def test_the_best_card_wins_and_no_card_is_other():
    cards = [card('Intel(R) UHD Graphics', '9a49', '8086'), card('AMD Radeon RX 7800 XT', '747e'),
             card('AMD Radeon 780M Graphics', '15bf')]
    assert gpu.classify(cards) == gpu.RDNA3
    assert gpu.classify([card(device='73bf'), card(device='7550')]) == gpu.RDNA4
    assert gpu.classify([]) == gpu.OTHER


def test_windows_ids_are_read_from_the_pnp_device_id():
    entries = [{'Name': 'AMD Radeon RX 7900 XTX', 'PNPDeviceID': 'PCI\\VEN_1002&DEV_744C&SUBSYS_0E0D1002&REV_C8\\4&1'},
               {'Name': 'Microsoft Basic Display Adapter', 'PNPDeviceID': None}]
    found = gpu.from_info(entries)
    assert found[0] == gpu.Gpu('AMD Radeon RX 7900 XTX', '1002', '744c')
    assert found[1] == gpu.Gpu('Microsoft Basic Display Adapter', '', '')
    assert gpu.classify(found) == gpu.RDNA3
    assert gpu.from_info(entries[0]) == [found[0]]   # PowerShell prints one card as a bare object


def test_the_windows_query_goes_through_powershell(monkeypatch):
    seen = []

    def run(command):
        seen.append(command)
        return json.dumps({'Name': 'AMD Radeon RX 9070 XT', 'PNPDeviceID': 'PCI\\VEN_1002&DEV_7550&SUBSYS_1'})

    found = gpu.detect(windows=True, run=run)
    assert gpu.classify(found) == gpu.RDNA4
    assert seen[0][0] == 'powershell' and 'Win32_VideoController' in seen[0][-1]
    assert gpu.detect(windows=True, run=lambda command: 'not json') == []
    assert gpu.detect(windows=True, run=lambda command: '') == []


def test_linux_reads_sysfs(tmp_path):
    for name, vendor, device in (('card0', '0x10de', '0x2684'), ('card1', '0x1002', '0x744c')):
        folder = tmp_path / name / 'device'
        folder.mkdir(parents=True)
        (folder / 'vendor').write_text(vendor + '\n')
        (folder / 'device').write_text(device + '\n')
    (tmp_path / 'card1-DP-1').mkdir()           # a connector, not a card
    (tmp_path / 'renderD128').mkdir()
    found = gpu.read_sysfs(tmp_path)
    assert found == [gpu.Gpu('', '10de', '2684'), gpu.Gpu('', '1002', '744c')]
    assert gpu.classify(found) == gpu.RDNA3
    assert gpu.read_sysfs(tmp_path / 'missing') == []
    assert gpu.detect(windows=False, sysfs=tmp_path) == found


def test_every_class_has_words_for_the_window():
    for family in (gpu.RDNA4, gpu.RDNA3, gpu.RDNA2, gpu.OTHER):
        assert gpu.TEXT[family]
