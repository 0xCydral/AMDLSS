# SPDX-License-Identifier: GPL-2.0-or-later
"""The game list: parsing HelixSR's output, states, the warning texts and the anti-cheat check."""
import json

from amdlss import games

PRINTED = '''[helixsr-setup] looking for games with DLSS, XeSS or FSR ...
    1. Aniimo                                       FSR 3.1 (HelixSR replaces the game's FSR DLL: select AMD FSR in the game)
    2. Far Cry 6                                    DLSS / XeSS / FSR 2 (through OptiScaler: select DLSS in the game)
    3. Sven Co-op                                   HelixSR installed
    4. Some Game                                    DLSS (HelixSR as the game's DLSS, experimental: select DLSS in the game)
  12. This Name Is Longer Than The Forty Five Chars Pad HelixSR installed
Close your games first.
'''


def test_printed_list_is_parsed():
    rows = games.parse_list_output(PRINTED)
    assert [(g.index, g.name) for g in rows] == [(1, 'Aniimo'), (2, 'Far Cry 6'), (3, 'Sven Co-op'), (4, 'Some Game'),
                                                (12, 'This Name Is Longer Than The Forty Five Chars Pad')]
    assert rows[0].route == 'direct' and rows[0].fsr31 and rows[0].state == 'none'
    assert rows[1].inputs == ['DLSS', 'XeSS', 'FSR 2'] and rows[1].route == 'optiscaler'
    assert rows[2].installed and rows[2].state == 'unknown'
    assert rows[3].state == 'dlss' and not rows[3].can_install


def test_unrecognised_or_empty_output_gives_no_games():
    assert games.parse_list_output('') == []
    assert games.parse_list_output('    1. Odd   something new HelixSR says') == []
    assert games.parse_list_output('[helixsr-setup] no game with DLSS, XeSS or FSR found') == []


def test_bridge_output_uses_the_last_sentinel_line_and_ignores_noise():
    first = games.SENTINEL + json.dumps([{'name': 'Old'}])
    last = games.SENTINEL + json.dumps([{'index': 2, 'name': 'Né', 'state': 'optiscaler', 'inputs': ['DLSS'],
                                         'fsr31': False, 'route': 'optiscaler', 'folder': 'C:\\g'}])
    rows = games.parse_bridge_output(f'warning\n{first}\nnoise\n{last}\n')
    assert len(rows) == 1 and rows[0].name == 'Né' and rows[0].state_text == 'OptiScaler'
    assert games.parse_bridge_output(games.SENTINEL + '{not json') is None
    assert games.parse_bridge_output('') is None


def test_state_and_route_texts():
    drop = games.Game(name='G', state='direct', route='direct', fsr31=True, inputs=['DLSS'])
    assert drop.state_text == 'FSR drop-in' and drop.supports == 'FSR 3.1, DLSS' and drop.installed
    assert drop.in_game() == 'AMD FSR'
    opti = games.Game(name='G', route='optiscaler', inputs=['DLSS'])
    assert opti.state_text == 'not installed' and opti.in_game() == 'DLSS' and not opti.installed


def test_after_text_says_what_to_choose():
    assert 'choose AMD FSR' in games.after_text(games.Game(name='G', route='direct'))
    text = games.after_text(games.Game(name='G', route='optiscaler'))
    assert 'choose DLSS' in text and 'Insert' in text


def test_plan_text_warns_and_names_the_files():
    text = games.plan_text(games.Game(name='G', route='direct', fsr_targets=['C:\\G\\amd_fidelityfx_upscaler_dx12.dll']))
    assert 'amd_fidelityfx_upscaler_dx12.dll' in text and 'Close the game first' in text and 'anti-cheat' in text
    assert 'found anti-cheat files' not in text
    flagged = games.plan_text(games.Game(name='G', anticheat=['BattlEye']))
    assert 'found anti-cheat files' in flagged and 'BattlEye' in flagged


def test_match_by_folder_or_unique_name():
    fresh = [games.Game(name='A', folder='C:\\Games\\A'), games.Game(name='B'), games.Game(name='B')]
    assert games.match_game(fresh, games.Game(name='x', folder='c:/games/a/')) is fresh[0]
    assert games.match_game(fresh, games.Game(name='B')) is None  # two with one name: not guessed
    assert games.match_game(fresh, games.Game(name='A')) is fresh[0]


def test_overlap_is_whole_folders():
    assert games.overlaps('C:\\G\\A', 'C:\\G\\A\\Binaries')
    assert games.overlaps('C:\\G\\A\\Binaries', 'C:\\G\\A')
    assert not games.overlaps('C:\\G\\A', 'C:\\G\\AB')


def test_anticheat_names_are_whole_words():
    assert games.anticheat_of('EasyAntiCheat_Setup.exe') == 'Easy Anti-Cheat'
    assert games.anticheat_of('beservice.exe') == 'BattlEye'
    assert games.anticheat_of('vgk.sys') == 'Riot Vanguard'
    assert games.anticheat_of('faceitems.pak') is None
    assert games.anticheat_of('Binaries') is None


def test_anticheat_scan_finds_nested_files_only_within_depth(tmp_path):
    deep = tmp_path / 'a' / 'b'
    deep.mkdir(parents=True)
    (deep / 'EasyAntiCheat_EOS.exe').write_bytes(b'')
    assert games.scan_anticheat(tmp_path) == ['Easy Anti-Cheat']
    assert games.scan_anticheat(tmp_path, depth=0) == []
    assert games.scan_anticheat(tmp_path / 'missing') == []
