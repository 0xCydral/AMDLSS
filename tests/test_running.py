# SPDX-License-Identifier: GPL-2.0-or-later
"""The game is still running: the process check before Install, Update and Remove, and a locked game file said in
plain words instead of a traceback (a tester's install and remove both died on a dxgi.dll the running game held)."""
import os

import pytest

from amdlss import games as gamelist, helixsr

PERMISSION = r'''PermissionError: [WinError 5] Acceso denegado: "E:\SteamLibrary\steamapps\common\Assassin's Creed Shadows\dxgi.dll"'''
MESSAGE = "Close Shadows first. It is still running, so its files can't be replaced."


def test_a_locked_file_in_a_failed_run_says_close_the_game():
    for text in (PERMISSION, 'PermissionError: [Errno 13] Permission denied', 'error: Access is denied',
                 'Traceback\n[WinError 5] something'):
        assert helixsr.plain_failure(text, 'Shadows') == MESSAGE, text
    assert helixsr.plain_failure('some other failure', 'Shadows') is None
    assert helixsr.still_running_message('Shadows') == MESSAGE


def test_inside_compares_paths_the_way_the_system_does():
    assert helixsr.inside(r'E:\Games\One\bin\game.exe', r'E:\Games\One', windows=True)
    assert helixsr.inside(r'e:\games\one\GAME.EXE', 'E:\\Games\\One\\', windows=True)
    assert not helixsr.inside(r'E:\Games\OneTwo\game.exe', r'E:\Games\One', windows=True)
    assert helixsr.inside('/games/one/game', '/games/one', windows=False)
    assert not helixsr.inside('/games/onetwo/game', '/games/one', windows=False)


def test_on_windows_the_process_paths_decide():
    folder = r'E:\Games\One'
    assert helixsr.running_from(folder, windows=True,
                                run=lambda command: 'C:\\Windows\\explorer.exe\nE:\\Games\\One\\bin\\game.exe\n')
    assert not helixsr.running_from(folder, windows=True,
                                    run=lambda command: 'C:\\Windows\\explorer.exe\nE:\\Games\\Two\\game.exe\n')

    def broken(command):
        raise OSError('no powershell')

    assert not helixsr.running_from(folder, windows=True, run=broken)   # a failed query never blocks an install
    assert not helixsr.running_from('', windows=True, run=lambda command: 'E:\\Games\\One\\game.exe\n')


def test_on_linux_the_exe_link_and_the_cmdline_decide(tmp_path):
    game = tmp_path / 'One'
    game.mkdir()
    (game / 'game.exe').write_bytes(b'MZ')
    proc = tmp_path / 'proc'
    (proc / '123').mkdir(parents=True)
    (proc / '124').mkdir()
    (proc / '124' / 'cmdline').write_bytes(b'/usr/bin/other\x00')
    os.symlink(game / 'game.exe', proc / '123' / 'exe')
    (proc / '123' / 'cmdline').write_bytes(b'game.exe\x00')
    assert helixsr.running_from(str(game), windows=False, proc=proc)
    # Proton: the process is wine outside the folder and only the command line names the game
    (proc / '123' / 'exe').unlink()
    os.symlink('/usr/bin/wine', proc / '123' / 'exe')
    (proc / '123' / 'cmdline').write_bytes(('wine\x00' + str(game / 'game.exe') + '\x00').encode())
    assert helixsr.running_from(str(game), windows=False, proc=proc)
    assert helixsr.game_running(gamelist.Game(name='One', folder=str(game)), windows=False, proc=proc)
    (proc / '123' / 'cmdline').write_bytes(b'wine\x00Z:\\elsewhere\\game.exe\x00')
    assert not helixsr.running_from(str(game), windows=False, proc=proc)
    assert not helixsr.running_from(str(game), windows=False, proc=tmp_path / 'no-proc')


def test_install_and_remove_refuse_while_the_game_runs(tmp_path, monkeypatch):
    app = helixsr.HelixSR(tmp_path / 'helixsr', python='python', running=lambda game: True)

    def asked(extras=()):
        raise AssertionError('the installer was asked')

    monkeypatch.setattr(app, 'list_games', asked)
    game = gamelist.Game(name='Shadows', folder=str(tmp_path / 'Shadows'), state='optiscaler')
    with pytest.raises(helixsr.HelixSRError, match='still running'):
        app.install(game)
    with pytest.raises(helixsr.HelixSRError, match='still running'):
        app.remove(game)


def test_a_failed_installer_run_with_a_locked_file_says_close_the_game(tmp_path, monkeypatch):
    game = gamelist.Game(name='Shadows', folder=str(tmp_path / 'Shadows'), index=1, route='optiscaler')
    app = helixsr.HelixSR(tmp_path / 'helixsr', python='python', running=lambda game: False)
    monkeypatch.setattr(app, 'list_games', lambda extras=(): [game])
    locked = (1, ['Traceback (most recent call last):', PERMISSION])
    monkeypatch.setattr(helixsr, 'run_streaming', lambda command, cwd, on_line=None, **rest: locked)
    with pytest.raises(helixsr.HelixSRError) as caught:
        app.install(game)
    assert str(caught.value) == MESSAGE   # the traceback stays in the game's install log, not in the window
    monkeypatch.setattr(helixsr, 'run_streaming', lambda *args, **rest: (3, ['boom']))
    with pytest.raises(helixsr.HelixSRError, match=r'\(exit 3\): boom'):
        app.install(game)
