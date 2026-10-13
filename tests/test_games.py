# SPDX-License-Identifier: GPL-2.0-or-later
"""The game list: parsing HelixSR's output, states, the warning texts and the anti-cheat check."""
import json
import ntpath
import os
import posixpath

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
    # Windows paths read as Windows paths (case and separators folded), on either host
    assert games.match_game(fresh, games.Game(name='x', folder='c:/games/a/'), ntpath) is fresh[0]
    assert games.match_game(fresh, games.Game(name='B')) is None  # two with one name: not guessed
    assert games.match_game(fresh, games.Game(name='A')) is fresh[0]
    posix = [games.Game(name='A', folder='/games/A')]
    assert games.match_game(posix, games.Game(name='x', folder='/games/A/'), posixpath) is posix[0]
    assert games.match_game(posix, games.Game(name='x', folder='/games/a'), posixpath) is None  # case matters


def test_overlap_is_whole_folders():
    assert games.overlaps('C:\\G\\A', 'C:\\G\\A\\Binaries', ntpath)
    assert games.overlaps('C:\\G\\A\\Binaries', 'C:\\G\\A', ntpath)
    assert not games.overlaps('C:\\G\\A', 'C:\\G\\AB', ntpath)
    assert games.overlaps('/g/a', '/g/a/bin', posixpath) and games.overlaps('/g/a/bin', '/g/a', posixpath)
    assert not games.overlaps('/g/a', '/g/ab', posixpath) and not games.overlaps('/Games/A', '/games/a', posixpath)


def test_anticheat_names_are_whole_words():
    assert games.anticheat_of('EasyAntiCheat_Setup.exe') == 'Easy Anti-Cheat'
    assert games.anticheat_of('beservice.exe') == 'BattlEye'
    assert games.anticheat_of('vgk.sys') == 'Riot Vanguard'
    assert games.anticheat_of('faceitems.pak') is None
    assert games.anticheat_of('Binaries') is None


def test_ace_is_caught_by_the_dlls_it_puts_next_to_the_game():
    # Delta Force ships ACE-IDW64.dll in its game folder and no AntiCheatExpert folder there (tester report, 2026-10-13)
    assert games.anticheat_of('ACE-IDW64.dll') == 'ACE anti-cheat'
    assert games.anticheat_of('ACE-Base64.dll') == 'ACE anti-cheat'
    assert games.anticheat_of('space-base.pak') is None and games.anticheat_of('race-idle.dat') is None


def test_anticheat_scan_finds_nested_files_only_within_depth(tmp_path):
    deep = tmp_path / 'a' / 'b'
    deep.mkdir(parents=True)
    (deep / 'EasyAntiCheat_EOS.exe').write_bytes(b'')
    assert games.scan_anticheat(tmp_path) == ['Easy Anti-Cheat']
    assert games.scan_anticheat(tmp_path, depth=0) == []
    assert games.scan_anticheat(tmp_path / 'missing') == []


# --- which games the list shows ---------------------------------------------------------------------------------

def installed_game(tmp_path, name, exe=True, **fields):
    """A Game whose folder exists (with a .exe unless exe=False), as HelixSR's bridge would report it."""
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    if exe:
        (folder / f'{name}.exe').write_bytes(b'MZ')
    fields.setdefault('inputs', ['DLSS'])
    return games.Game(name=name, folder=str(folder), exe_folder=str(folder) if exe else '', **fields)


def test_a_game_with_an_exe_and_something_to_work_with_is_ready(tmp_path):
    ready = installed_game(tmp_path, 'Ready')
    fsr_only = installed_game(tmp_path, 'FsrOnly', inputs=[], fsr31=True, fsr_targets=['x.dll'])
    rows = games.classify([ready, fsr_only])
    assert [g.reason for g in rows] == ['', ''] and games.count_text(rows) == '2 games ready, 0 hidden'
    assert all(games.visible(g) for g in rows) and ready.can_install


def test_a_steam_leftover_whose_folder_is_gone_is_hidden_as_not_installed(tmp_path):
    leftover = games.Game(name='Gone', folder=str(tmp_path / 'Gone'), exe_folder=str(tmp_path / 'Gone'),
                          inputs=['DLSS'])
    games.classify([leftover])
    assert leftover.reason == games.NOT_INSTALLED == 'not installed'
    assert not games.visible(leftover) and games.visible(leftover, show_all=True) and not leftover.can_install


def test_a_folder_without_an_exe_is_hidden_as_not_installed(tmp_path):
    no_exe = installed_game(tmp_path, 'NoExe', exe=False)
    stale_exe = installed_game(tmp_path, 'Stale')
    stale_exe.exe_folder = str(tmp_path / 'Stale' / 'bin')  # HelixSR's exe folder is not on disk
    games.classify([no_exe, stale_exe])
    assert no_exe.reason == stale_exe.reason == games.NOT_INSTALLED


def test_a_game_with_no_dlss_fsr_or_xess_is_hidden_with_that_reason(tmp_path):
    plain = installed_game(tmp_path, 'Minecraft', inputs=[])
    games.classify([plain])
    assert plain.reason == 'no DLSS, FSR 3.1 or XeSS found' and not games.visible(plain)
    assert not plain.can_install


def test_an_installed_game_is_kept_whatever_the_check_says(tmp_path):
    kept = games.Game(name='Kept', folder=str(tmp_path / 'Gone'), state='optiscaler')  # no folder, no exe, no inputs
    games.classify([kept])
    assert kept.reason == '' and games.visible(kept) and kept.installed
    assert games.count_text([kept]) == '1 games ready, 0 hidden'


def test_a_game_known_only_from_the_printed_list_is_not_judged():
    printed = games.parse_list_output(PRINTED)
    games.classify(printed)
    assert all(g.reason == '' for g in printed)


def test_a_folder_added_by_hand_that_fails_is_shown_with_its_reason(tmp_path):
    bare = installed_game(tmp_path, 'Bare', inputs=[])  # HelixSR gave a row for it, with nothing to work with
    rows = games.classify([bare], [str(bare.folder), str(tmp_path / 'Typo')])
    assert [g.manual for g in rows] == [True, True]
    assert rows[0].reason == games.NO_UPSCALER and rows[1].reason == games.NO_FOLDER == 'folder not found'
    assert rows[1].name == 'Typo' and rows[1].folder == str(tmp_path / 'Typo') and not rows[1].can_install
    assert all(games.visible(g) for g in rows)  # shown without "Show all games"
    assert games.count_text(rows) == '0 games ready, 0 hidden'  # shown, so not counted as hidden


def test_a_folder_added_by_hand_that_is_already_listed_is_not_added_twice(tmp_path):
    ready = installed_game(tmp_path, 'Ready')
    inside = str(tmp_path / 'Ready' / 'bin')  # a folder inside a listed game is that game, as for HelixSR
    rows = games.classify([ready], [str(ready.folder), str(ready.folder) + os.sep, inside])
    assert rows == [ready] and ready.manual and ready.reason == ''


def test_the_status_line_counts_ready_and_hidden(tmp_path):
    rows = games.classify([installed_game(tmp_path, 'A'), installed_game(tmp_path, 'B', inputs=[]),
                           games.Game(name='C', folder=str(tmp_path / 'C'), inputs=['DLSS'])])
    assert games.count_text(rows) == '1 games ready, 2 hidden'
    assert [g.name for g in rows if games.visible(g)] == ['A']
    assert [g.name for g in rows if games.visible(g, show_all=True)] == ['A', 'B', 'C']


def test_bridge_json_keeps_an_empty_route_for_a_game_helixsr_does_not_list():
    row = {'index': 0, 'name': 'Nope', 'folder': 'C:\\g', 'route': '', 'inputs': [], 'state': 'none'}
    assert games.from_json([row])[0].route == '' and games.from_json([{'name': 'x'}])[0].route == 'optiscaler'
