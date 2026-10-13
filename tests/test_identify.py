# SPDX-License-Identifier: GPL-2.0-or-later
"""identify(): which game is this folder, from files on disk alone (tmp dirs, the environment and the exe version
reader are all stand-ins)."""
import json
import os
import sys
from pathlib import Path

import pytest

from amdlss import identify
from amdlss.games import Game

REAL_READER = identify.read_product_name   # before the autouse stub below replaces it


@pytest.fixture(autouse=True)
def no_real_machine(monkeypatch, tmp_path):
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path / 'nothing-here'))
    monkeypatch.setattr(identify, 'read_product_name', lambda path: None)


def game_in(root, exe_folder=None, name='Some Game'):
    root.mkdir(parents=True, exist_ok=True)
    (exe_folder or root).mkdir(parents=True, exist_ok=True)
    return Game(name=name, folder=str(root), exe_folder=str(exe_folder or root))


def test_a_steam_manifest_gives_the_app_id_and_the_name(tmp_path):
    common = tmp_path / 'lib' / 'steamapps' / 'common'
    (tmp_path / 'lib' / 'steamapps').mkdir(parents=True)
    (tmp_path / 'lib' / 'steamapps' / 'appmanifest_292030.acf').write_text(
        '"AppState"\n{\n\t"appid"\t\t"292030"\n\t"name"\t\t"The Witcher 3: Wild Hunt"\n\t"installdir"\t\t"The Witcher 3"\n}\n')
    found = identify.identify(game_in(common / 'THE WITCHER 3', common / 'THE WITCHER 3' / 'bin' / 'x64', 'Witcher'))
    assert found.steam_appid == 292030 and found.source == 'steam'
    assert found.titles[:2] == ['The Witcher 3: Wild Hunt', 'Witcher']


def test_windows_paths_are_read_as_windows_paths():
    assert identify.steam_manifest('C:\\Games\\Plain') is None
    assert identify.steam_manifest('D:\\SteamLibrary\\steamapps\\common') is None   # the library, not a game
    assert identify.steam_manifest('') is None
    assert list(identify.ancestors('C:\\Games\\X\\bin\\')) == ['C:\\Games\\X\\bin\\', 'C:\\Games\\X', 'C:\\Games', 'C:\\']


def test_gog_info_in_an_ancestor_folder(tmp_path):
    root = tmp_path / 'GOG Games' / 'Witcher'
    game = game_in(root, root / 'bin' / 'x64')
    (root / 'goggame-1207664663.info').write_text(json.dumps(
        {'gameId': '1207664663', 'rootGameId': '1207664663', 'name': 'The Witcher 3: Wild Hunt - Complete Edition'}))
    (root / 'goggame-1207664664.info').write_text(json.dumps({'gameId': '1207664664', 'rootGameId': '1207664663',
                                                              'name': 'A DLC'}))
    found = identify.identify(game)   # the info is two folders above the exe
    assert (found.gog_id, found.source) == (1207664663, 'gog')
    assert found.titles[0] == 'The Witcher 3: Wild Hunt - Complete Edition' and 'A DLC' not in found.titles
    assert identify.identify(game_in(tmp_path / 'Elsewhere')).gog_id is None


def epic_item(folder, **fields):
    manifests = folder / 'Epic' / 'EpicGamesLauncher' / 'Data' / 'Manifests'
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / f'{len(list(manifests.iterdir()))}.item').write_text(json.dumps(fields))


def test_an_epic_manifest_is_matched_by_install_location(tmp_path, monkeypatch):
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path / 'pd'))
    root = tmp_path / 'Epic Games' / 'AlanWake2'
    game = game_in(root, root / 'Binaries' / 'Win64')
    epic_item(tmp_path / 'pd', InstallLocation=str(tmp_path / 'Epic Games' / 'Other'), DisplayName='Other',
              AppName='o', MainGameAppName='o')
    epic_item(tmp_path / 'pd', InstallLocation=str(root), DisplayName='Alan Wake 2 Soundtrack', AppName='dlc',
              MainGameAppName='aw2')
    epic_item(tmp_path / 'pd', InstallLocation=str(root), DisplayName='Alan Wake 2', AppName='aw2',
              MainGameAppName='aw2')
    epic_item(tmp_path / 'pd', InstallLocation=str(root / 'Binaries' / 'Win64' / 'deeper'), DisplayName='Inside',
              AppName='i', MainGameAppName='i')   # inside the game folder: not the game's own install location
    found = identify.identify(game)
    assert found.source == 'epic' and found.titles[0] == 'Alan Wake 2' and 'Inside' not in found.titles


def test_without_the_epic_folder_nothing_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv('PROGRAMDATA')
    assert identify.epic_title([str(tmp_path)]) == ''


def test_xbox_microsoftgame_config_then_appxmanifest(tmp_path):
    root = tmp_path / 'XboxGames' / 'Forza'
    content = root / 'Content'
    game = game_in(root, content)
    (content / 'MicrosoftGame.config').write_text(
        '<?xml version="1.0" encoding="utf-8"?><Game configVersion="1"><Identity Name="x"/>'
        '<ShellVisuals DefaultDisplayName="ms-resource:AppName" Square150x150Logo="a.png"/>'
        '<Title>Forza Horizon 5</Title></Game>')
    assert identify.identify(game).titles[0] == 'Forza Horizon 5'
    (content / 'MicrosoftGame.config').write_text(
        '<Game><ShellVisuals DefaultDisplayName="Forza Horizon 5 Edition"/><Title>Other</Title></Game>')
    assert identify.xbox_title([str(content)]) == 'Forza Horizon 5 Edition'
    (content / 'MicrosoftGame.config').unlink()
    manifest = ('<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"><Properties>'
                '<DisplayName>{}</DisplayName></Properties></Package>')
    (content / 'appxmanifest.xml').write_text(manifest.format('ms-resource:AppName'))
    assert identify.xbox_title([str(content)]) == ''   # a resource reference is not a name
    (content / 'appxmanifest.xml').write_text(manifest.format('Halo Infinite'))
    assert identify.xbox_title([str(content)]) == 'Halo Infinite'
    (content / 'appxmanifest.xml').write_text('<Package><not closed')
    assert identify.xbox_title([str(content)]) == ''


def test_ea_installerdata(tmp_path):
    root = tmp_path / 'EA Games' / 'FIFA'
    game = game_in(root)
    (root / '__Installer').mkdir()
    (root / '__Installer' / 'installerdata.xml').write_text(
        '<DiPManifest><gameTitles><gameTitle locale="de_DE">Fussball</gameTitle>'
        '<gameTitle locale="en_US">EA SPORTS FC 24</gameTitle></gameTitles></DiPManifest>')
    found = identify.identify(game)
    assert found.source == 'ea' and found.titles[0] == 'EA SPORTS FC 24'


@pytest.mark.parametrize('product', ['Unreal Engine', 'UE4', 'UnrealGame', 'Microsoft\u00ae Windows\u00ae Operating System',
                                     '', 'Unreal Engine 5', 'Unity', 'Ubisoft Connect 174.2.13389 installer',
                                     'EA app', 'Battle.net Launcher'])
def test_a_generic_product_name_is_discarded(tmp_path, monkeypatch, product):
    game = game_in(tmp_path / 'G')
    (tmp_path / 'G' / 'Game.exe').write_bytes(b'MZ')
    monkeypatch.setattr(identify, 'read_product_name', lambda path: product)
    assert identify.identify(game).titles == ['Some Game', 'G']


def test_the_product_name_comes_from_the_biggest_ordinary_exe(tmp_path, monkeypatch):
    game = game_in(tmp_path / 'G')
    for name, size in (('Game.exe', 500), ('Game-Win64-Shipping.exe', 5000), ('CrashReportClient.exe', 90000),
                       ('UnityCrashHandler64.exe', 90000), ('Launcher.exe', 90000), ('unins000.exe', 90000),
                       ('vc_redist.x64.exe', 90000), ('Helper.exe', 90000), ('notes.txt', 90000)):
        (tmp_path / 'G' / name).write_bytes(b'M' * size)
    asked = []
    monkeypatch.setattr(identify, 'read_product_name', lambda path: asked.append(path.name) or 'Alan Wake 2')
    found = identify.identify(game)
    assert asked == ['Game-Win64-Shipping.exe'] and found.source == 'exe' and found.titles[0] == 'Alan Wake 2'
    assert identify.main_exe(tmp_path / 'empty') is None


def test_installers_next_to_the_game_are_not_the_main_exe(tmp_path):
    # Far Cry 6 and Wildlands ship UbisoftConnectInstaller.exe in the game folder (seen on a real Steam library)
    for name, size in (('UbisoftConnectInstaller.exe', 900000), ('UplayInstaller.exe', 800000),
                       ('UEPrereqSetup_x64.exe', 700000), ('GRW.exe', 1000)):
        (tmp_path / name).write_bytes(b'M' * size)
    assert identify.main_exe(tmp_path).name == 'GRW.exe'


@pytest.mark.skipif(os.name != 'nt', reason='needs Windows version.dll')
def test_on_windows_the_version_reader_names_a_real_exe():
    assert REAL_READER(sys.executable) == 'Python'
    assert REAL_READER(str(Path(sys.executable).with_name('missing.exe'))) is None


@pytest.mark.skipif(os.name == 'nt', reason='the real reader runs on Windows')
def test_off_windows_the_version_reader_gives_nothing():
    assert REAL_READER('Game.exe') is None


@pytest.mark.parametrize('folder, title', [('TheWitcher3_WildHunt', 'The Witcher 3 Wild Hunt'),
                                           ('Cyberpunk2077', 'Cyberpunk 2077'), ('FINAL FANTASY XVI', 'FINAL FANTASY XVI'),
                                           ('Where-Winds.Meet', 'Where Winds Meet'), ('  Hades  ', 'Hades')])
def test_folder_names_become_titles(folder, title):
    assert identify.tidy_folder_name(folder) == title


def test_titles_keep_their_priority_and_lose_duplicates(tmp_path):
    root = tmp_path / 'FinalFantasyXVI'
    game = game_in(root, name='FINAL FANTASY XVI\u2122')
    (root / 'goggame-1.info').write_text(json.dumps({'gameId': '1', 'name': 'Final Fantasy XVI'}))
    found = identify.identify(game)
    # the launcher's spelling first; HelixSR's name and the folder say the same thing and are dropped
    assert found.titles == ['Final Fantasy XVI'] and found.gog_id == 1
    plain = identify.identify(game_in(tmp_path / 'Other' / 'MyGame_2', name='Zed'))
    assert plain.titles == ['Zed', 'My Game 2'] and plain.source == 'name'
    assert identify.identify(Game(name='')).titles == [] and identify.identify(Game(name='')).source == 'name'
