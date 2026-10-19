# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux side, checked on either host: every call names its system (windows=, pathmod= or the path itself),
so the Windows run under wine proves the same lines. Nothing here touches the network or a real game."""
import hashlib
import io
import os
from pathlib import Path

import pytest

from amdlss import games, helixsr, host, identify, logs, settings, ui, update

EXE = b'new amdlss linux'
SHA = hashlib.sha256(EXE).hexdigest()


# --- the data folder -------------------------------------------------------------------------------------------

def test_the_linux_data_folder_is_the_xdg_one(tmp_path, monkeypatch):
    monkeypatch.delenv('AMDLSS_DATA_DIR', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    assert settings.data_dir(windows=False) == tmp_path / 'xdg' / 'AMDLSS'
    monkeypatch.delenv('XDG_DATA_HOME')
    assert settings.data_dir(windows=False).parts[-3:] == ('.local', 'share', 'AMDLSS')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    assert settings.data_dir(windows=True) == tmp_path / 'local' / 'AMDLSS'
    monkeypatch.setenv('AMDLSS_DATA_DIR', str(tmp_path / 'elsewhere'))   # the override wins on both
    assert settings.data_dir(windows=False) == settings.data_dir(windows=True) == tmp_path / 'elsewhere'
    assert helixsr.helixsr_data(windows=True) == tmp_path / 'local' / 'HelixSR'
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    assert helixsr.helixsr_data(windows=False) == tmp_path / 'xdg' / 'HelixSR'
    assert helixsr.portable_python().parts[-4:] == ('HelixSR', 'python', 'bin', 'python3')


# --- HelixSR's setup and its Python ----------------------------------------------------------------------------

def test_the_linux_setup_is_the_shell_script_and_the_consent_says_so():
    assert helixsr.setup_command(windows=False, dlss='n.dll') == ['bash', 'helixsr-setup.sh', '--no-fg', '--dlss', 'n.dll']
    linux, windows = helixsr.consent_text(windows=False), helixsr.consent_text(windows=True)
    assert 'portable' in linux and 'Proton' in linux and '%LOCALAPPDATA%' not in linux
    assert '%LOCALAPPDATA%\\HelixSR' in windows
    assert helixsr.NVIDIA_LICENSE_URL in linux   # AMDLSS downloads NVIDIA's DLL for the setup, so both texts ask first


@pytest.fixture
def pythons(tmp_path, monkeypatch):
    """find_python()'s two Linux candidates as stand-ins; the test says which of them have numpy."""
    portable, system = tmp_path / 'portable-python3', tmp_path / 'usr-python3'
    monkeypatch.setattr(helixsr, 'portable_python', lambda: portable)
    monkeypatch.setattr(helixsr.shutil, 'which', lambda name: str(system) if name == 'python3' else None)
    usable = set()
    monkeypatch.setattr(helixsr, 'has_numpy', lambda python: str(python) in usable)
    return portable, system, usable


def test_the_linux_python_choice_mirrors_the_setup_script(pythons, monkeypatch):
    portable, system, usable = pythons
    usable.add(str(system))
    assert helixsr.find_python(windows=False) == system          # the system python3, when it has numpy
    usable.add(str(portable))
    assert helixsr.find_python(windows=False) == portable        # the portable one first, as in the script
    usable.clear()
    assert helixsr.find_python(windows=False) is None
    assert helixsr.helixsr_python(windows=False) == portable     # where the setup will put it
    monkeypatch.setattr(helixsr.shutil, 'which', lambda name: None)
    usable.add(str(portable))
    assert helixsr.find_python(windows=False) == portable        # no python3 on PATH at all


def test_a_python_that_does_not_exist_has_no_numpy():
    assert helixsr.has_numpy('amdlss-no-such-python') is False


def test_without_a_usable_python_the_setup_takes_the_portable_route(tmp_path, monkeypatch):
    calls = []

    def run(command, cwd, on_line=None, timeout=0, env=None):
        calls.append(([str(p) for p in command], env))
        (Path(cwd) / helixsr.BUILT).write_text('{}')
        (Path(cwd) / helixsr.FSRFG).mkdir(exist_ok=True)
        (Path(cwd) / helixsr.FSRFG / helixsr.DLL).write_bytes(b'x')
        return 0, []

    monkeypatch.setattr(helixsr, 'run_streaming', run)
    monkeypatch.setattr(helixsr, 'find_python', lambda windows=None: None)
    dlss = tmp_path / 'nvngx_dlss.dll'
    helixsr.build(tmp_path, windows=False, dlss=dlss)
    # a Python without numpy would otherwise offer a sudo package install nobody can answer from the window
    assert calls[-1][0] == ['bash', 'helixsr-setup.sh', '--no-fg', '--dlss', str(dlss)]
    assert calls[-1][1]['HELIXSR_FORCE_PORTABLE'] == '1'
    monkeypatch.setattr(helixsr, 'find_python', lambda windows=None: Path('python3'))
    helixsr.build(tmp_path, windows=False, dlss=dlss)
    assert 'HELIXSR_FORCE_PORTABLE' not in calls[-1][1]           # a usable Python: the script finds it itself
    helixsr.build(tmp_path, windows=True, dlss=dlss)
    assert calls[-1][0][0] == 'powershell' and 'HELIXSR_FORCE_PORTABLE' not in calls[-1][1]


def test_no_usable_python_is_one_clear_line(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'WINDOWS', False)
    monkeypatch.setattr(helixsr, 'helixsr_python', lambda windows=None: tmp_path / 'missing-python3')
    client = helixsr.HelixSR(tmp_path)
    with pytest.raises(helixsr.HelixSRError, match='no Python 3 with numpy'):
        client.list_games()


# --- Proton in a second Steam library -----------------------------------------------------------------------------

def fake_wine(folder):
    wine = folder / 'files' / 'bin' / 'wine'
    wine.parent.mkdir(parents=True)
    wine.write_text('#!/bin/sh\necho wine-fake "$@"\n')
    wine.chmod(0o755)
    return wine


def steam_home(tmp_path, libraries):
    """A home whose Steam lists `libraries` (folders) in libraryfolders.vdf, the way Steam writes it."""
    home = tmp_path / 'home'
    apps = home / '.steam' / 'steam' / 'steamapps'
    apps.mkdir(parents=True)
    entries = ''.join(f'\t"{n}"\n\t{{\n\t\t"path"\t\t"{str(lib).replace(chr(92), chr(92) * 2)}"\n\t}}\n'
                      for n, lib in enumerate(libraries))
    (apps / 'libraryfolders.vdf').write_text(f'"libraryfolders"\n{{\n{entries}}}\n')
    return home


def test_a_proton_in_a_second_steam_library_is_found_newest_first(tmp_path):
    drive = tmp_path / 'Data' / 'SteamLibrary'
    common = drive / 'steamapps' / 'common'
    old, experimental, new = (fake_wine(common / n) for n in ('Proton 9.0-4', 'Proton - Experimental', 'Proton 10.0'))
    home = steam_home(tmp_path, [tmp_path / 'main', drive])
    assert host.steam_libraries(home)[-1] == drive
    assert host.find_proton_wine(home) == experimental
    experimental.unlink()
    assert host.find_proton_wine(home) == new      # 10.0 beats 9.0-4, not the other way round as text
    new.unlink()
    assert host.find_proton_wine(home) == old
    old.unlink()
    custom = fake_wine(home / '.steam' / 'steam' / 'compatibilitytools.d' / 'GE-Proton9-5')
    assert host.find_proton_wine(home) == custom
    custom.unlink()
    assert host.find_proton_wine(home) is None


def test_the_setup_finds_wine_only_in_its_own_places(tmp_path):
    drive = tmp_path / 'Data'
    fake_wine(drive / 'steamapps' / 'common' / 'Proton - Experimental')
    home = steam_home(tmp_path, [drive])
    nowhere = {'PATH': str(tmp_path / 'empty')}
    assert host.setup_finds_wine(nowhere, home) is False              # the second library is not one of its globs
    fake_wine(home / '.local' / 'share' / 'Steam' / 'steamapps' / 'common' / 'Proton 9.0')
    assert host.setup_finds_wine(nowhere, home) is True
    if os.name != 'nt':   # a wine on PATH counts too
        bare = tmp_path / 'bare'
        (bare / 'bin').mkdir(parents=True)
        (bare / 'bin' / 'wine').symlink_to(fake_wine(bare / 'x'))
        assert host.setup_finds_wine({'PATH': str(bare / 'bin')}, bare) is True


@pytest.mark.skipif(os.name == 'nt', reason='the wrapper is a POSIX shell script')
def test_a_wine_wrapper_for_the_other_library_goes_first_on_path(tmp_path, monkeypatch):
    drive = tmp_path / 'Data'
    real = fake_wine(drive / 'steamapps' / 'common' / 'Proton - Experimental')
    home = steam_home(tmp_path, [drive])
    monkeypatch.setattr(Path, 'home', lambda: home)
    env, found = helixsr.with_wine({'PATH': str(tmp_path / 'empty')}, tmp_path / 'data')
    shim = tmp_path / 'data' / 'bin' / 'wine'
    assert found and env['PATH'].split(os.pathsep)[0] == str(shim.parent) and os.access(shim, os.X_OK)
    assert shim.read_text().startswith('#!/bin/sh\nexec ') and str(real) in shim.read_text()
    assert helixsr.subprocess.run([shim, '--version'], capture_output=True, text=True).stdout == 'wine-fake --version\n'
    other = helixsr.with_wine({'PATH': str(shim.parent)}, tmp_path / 'data2')   # a wine on PATH: nothing written
    assert other[1] and not (tmp_path / 'data2').exists()


def test_without_any_wine_the_failure_says_what_to_install(tmp_path, monkeypatch):
    home = steam_home(tmp_path, [])
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    line = '[helixsr-setup] error: no Proton or Wine found (needed to run the shader compiler); install Proton or Wine'
    monkeypatch.setattr(helixsr, 'run_streaming', lambda *a, **k: (1, [line]))
    monkeypatch.setattr(helixsr, 'find_python', lambda windows=None: Path('python3'))
    with pytest.raises(helixsr.HelixSRError, match=r'exit 1.*no Proton or Wine found.*any Steam library'):
        helixsr.build(tmp_path, windows=False, data_dir=tmp_path / 'data')
    # no wine shim was written; the data dir may hold the setup's own log (SETUP_LOG keeps the failure readable)
    assert not (tmp_path / 'data' / 'bin').exists()


# --- fonts and glyphs -------------------------------------------------------------------------------------------

def test_linux_picks_an_installed_family_whatever_its_case():
    core = ['fixed', 'helvetica', 'nimbus mono l', 'nimbus sans l', 'symbol']   # what the Tk of the binary lists
    assert ui.pick_fonts(core, 'tk-sans', 'tk-mono', windows=False) == ('nimbus sans l', 'nimbus mono l')
    assert ui.pick_fonts(['DejaVu Sans', 'DejaVu Sans Mono', 'Noto Sans'], 's', 'm', windows=False) == \
        ('Noto Sans', 'DejaVu Sans Mono')
    assert ui.pick_fonts(['symbol'], 'tk-sans', 'tk-mono', windows=False) == ('tk-sans', 'tk-mono')   # the Tk default
    assert ui.pick_fonts(['Segoe UI', 'Consolas', 'Arial'], 's', 'm', windows=True) == ('Segoe UI', 'Consolas')
    assert ui.pick_fonts([], 'Arial', 'Consolas', windows=True) == ('Arial', 'Consolas')


def test_linux_draws_the_icons_as_ascii_and_windows_keeps_them():
    assert ui.plain('✓ ✗ ⚠ • Add game folder…', windows=False) == '+ x ! - Add game folder...'
    assert ui.plain('✓ ✗ ⚠ • Add game folder…', windows=True) == '✓ ✗ ⚠ • Add game folder…'


# --- the update asset and the self-replace ---------------------------------------------------------------------

def release(tag, assets):
    return {'tag_name': tag, 'prerelease': False, 'draft': False, 'html_url': f'https://example.test/{tag}',
            'assets': [{'name': name, 'size': len(EXE), 'digest': f'sha256:{SHA}',
                        'browser_download_url': f'https://example.test/{tag}/{name}'} for name in assets]}


def test_each_system_downloads_its_own_asset():
    both = [release('v0.1.0', ('AMDLSS-linux-x86_64', 'AMDLSS.exe', 'notes.txt'))]
    assert update.newest_release(both, False, windows=False).url.endswith('/AMDLSS-linux-x86_64')
    assert update.newest_release(both, False, windows=True).url.endswith('/AMDLSS.exe')
    assert update.newest_release([release('v0.1.0', ('AMDLSS.exe',))], False, windows=False).url is None
    assert update.newest_release([release('v0.1.0', ('AMDLSS-linux-x86_64',))], False, windows=True).url is None
    assert update.asset_name(windows=False) == 'AMDLSS-linux-x86_64' and update.asset_name(True) == 'AMDLSS.exe'
    assert update.ASSET_NAME == update.asset_name()   # the module constant is the host's


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


def test_a_linux_update_replaces_the_file_and_keeps_it_executable(tmp_path, monkeypatch):
    exe = tmp_path / 'AMDLSS-linux-x86_64'
    exe.write_bytes(b'old amdlss linux')
    if os.name != 'nt':
        exe.chmod(0o755)
    monkeypatch.setattr(update.urllib.request, 'urlopen',
                        lambda request, timeout=None: Response(EXE))
    update.apply(update.newest_release([release('v0.1.0', ('AMDLSS-linux-x86_64',))], False, windows=False),
                 exe, windows=False)
    assert exe.read_bytes() == EXE
    assert sorted(p.name for p in tmp_path.iterdir()) == ['AMDLSS-linux-x86_64']   # no .old, no .download
    assert os.name == 'nt' or exe.stat().st_mode & 0o111 == 0o111

    def denied(a, b):
        raise PermissionError('read-only folder')

    monkeypatch.setattr(update.os, 'replace', denied)
    with pytest.raises(update.UpdateError, match='could not replace AMDLSS-linux-x86_64'):
        update.apply(update.newest_release([release('v0.1.0', ('AMDLSS-linux-x86_64',))], False, windows=False),
                     exe, windows=False)
    assert exe.read_bytes() == EXE and sorted(p.name for p in tmp_path.iterdir()) == ['AMDLSS-linux-x86_64']


def test_restart_detaches_the_way_each_system_does(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(update.subprocess, 'Popen',
                        lambda command, **options: seen.update(command=command, options=options))
    update.restart(tmp_path / 'AMDLSS-linux-x86_64', [], windows=False)
    assert seen['options'].get('start_new_session') is True and 'creationflags' not in seen['options']
    assert seen['options']['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    update.restart(tmp_path / 'AMDLSS.exe', [], windows=True)
    assert 'creationflags' in seen['options'] and 'start_new_session' not in seen['options']


# --- game folders and identification on POSIX paths --------------------------------------------------------------

@pytest.mark.skipif(os.name == 'nt', reason='identify() reads the host paths; Windows has its own manifest test')
def test_identify_finds_the_steam_app_in_a_posix_library(tmp_path, monkeypatch):
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path / 'nothing-here'))
    monkeypatch.setattr(identify, 'read_product_name', lambda path: None)
    steamapps = tmp_path / 'media' / 'x' / 'SteamLibrary' / 'steamapps'
    game_folder = steamapps / 'common' / 'Where Winds Meet'
    (game_folder / 'Engine' / 'Binaries' / 'Win64').mkdir(parents=True)
    (steamapps / 'appmanifest_3564740.acf').write_text(
        '"AppState"\n{\n\t"appid"\t\t"3564740"\n\t"name"\t\t"Where Winds Meet"\n'
        '\t"installdir"\t\t"Where Winds Meet"\n}\n')
    game = games.Game(name='Where Winds Meet', folder=str(game_folder),
                      exe_folder=str(game_folder / 'Engine' / 'Binaries' / 'Win64'))
    found = identify.identify(game)
    assert found.steam_appid == 3564740 and found.source == 'steam' and found.titles[0] == 'Where Winds Meet'


# --- logs: redaction, the Desktop and the file manager -----------------------------------------------------------

def test_home_paths_lose_the_user_name():
    redact = logs.redactor(['cydral'])
    assert redact('/home/cydral/Games and cydral') == '/home/<user>/Games and <user>'
    assert redact('/home/other/Games') == '/home/other/Games'        # somebody else's home is not the user's
    assert redact('the /home/cydral2/cydral') == 'the /home/cydral2/<user>'   # a longer name is not cut in half
    short = logs.redactor(['Al'])
    assert short('/home/Al/x, Albert, Al') == '/home/<user>/x, Albert, Al'   # short: only in the path, as on Windows


def test_the_linux_desktop_comes_from_the_xdg_user_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert logs.desktop_dir(windows=False) == tmp_path / 'Desktop'
    (tmp_path / 'Escritorio').mkdir()
    config = tmp_path / '.config'
    config.mkdir()
    (config / 'user-dirs.dirs').write_text('XDG_DESKTOP_DIR="$HOME/Escritorio"\nXDG_DOWNLOAD_DIR="$HOME/x"\n')
    assert logs.desktop_dir(windows=False) == tmp_path / 'Escritorio'


def test_reveal_and_open_folder_use_the_file_manager(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(logs.subprocess, 'Popen', lambda command, **k: seen.append(command))
    logs.reveal(tmp_path / 'AMDLSS-logs.zip', windows=False)
    assert seen[-1] == ['xdg-open', str(tmp_path)]
    logs.reveal(tmp_path / 'AMDLSS-logs.zip', windows=True)
    assert seen[-1] == f'explorer /select,"{tmp_path / "AMDLSS-logs.zip"}"'
    monkeypatch.setattr(host, 'WINDOWS', False)
    monkeypatch.setattr(host.subprocess, 'Popen', lambda command, **k: seen.append(command))
    host.open_folder(tmp_path / 'Game')
    assert seen[-1] == ['xdg-open', str(tmp_path / 'Game')]


def test_the_frozen_binary_hands_system_programs_the_users_library_path(monkeypatch):
    bundled = {'LD_LIBRARY_PATH': '/tmp/_MEI1:/usr/local/lib', 'LD_LIBRARY_PATH_ORIG': '/usr/local/lib', 'A': '1'}
    monkeypatch.setattr(host.sys, 'frozen', True, raising=False)
    assert host.clean_env(bundled) == {'LD_LIBRARY_PATH': '/usr/local/lib', 'A': '1'}
    assert host.clean_env({'LD_LIBRARY_PATH': '/tmp/_MEI1'}) == {}   # the user had none
    monkeypatch.setattr(host.sys, 'frozen', False, raising=False)
    assert host.clean_env(bundled) == bundled                         # from source the value is the user's own


def test_the_frozen_binary_restores_the_users_library_path_for_the_browser_too(monkeypatch):
    monkeypatch.setattr(host.sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/tmp/_MEI1:/usr/local/lib')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', '/usr/local/lib')
    host.restore_library_path()
    assert host.os.environ['LD_LIBRARY_PATH'] == '/usr/local/lib' and 'LD_LIBRARY_PATH_ORIG' not in host.os.environ
    monkeypatch.setenv('LD_LIBRARY_PATH', '/tmp/_MEI1')   # the user had none
    host.restore_library_path()
    assert 'LD_LIBRARY_PATH' not in host.os.environ


def test_linux_system_info_reads_no_powershell(monkeypatch):
    real_run = logs.subprocess.run

    def no_powershell(*args, **kwargs):   # the platform module may shell out for itself; PowerShell may not
        if args and 'powershell' in str(args[0]).lower():
            raise AssertionError('PowerShell ran for the Linux system info')
        return real_run(*args, **kwargs)

    monkeypatch.setattr(logs.subprocess, 'run', no_powershell)
    info = logs.system_info(windows=False)
    assert isinstance(info, dict) and ('cpu' not in info or isinstance(info['cpu'], str))
    assert 'Linux:' in logs.system_text(None, info, windows=False)


# --- display ----------------------------------------------------------------------------------------------------

def test_short_path_on_linux(monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: '/home/tester')   # a POSIX home spelled the same on either host
    assert ui.short_path('/home/tester/.local/share/AMDLSS', windows=False) == '~/.local/share/AMDLSS'
    long = '/home/tester/.local/share/AMDLSS/HelixSR-1.9.0/setup/helixsr_install.py'
    assert ui.short_path(long, windows=False) == '~/…/setup/helixsr_install.py'
    monkeypatch.setenv('LOCALAPPDATA', 'C:\\Users\\x\\AppData\\Local')
    assert ui.short_path('C:\\Users\\x\\AppData\\Local\\AMDLSS\\HelixSR-1.9.0', windows=True) == \
        '%LOCALAPPDATA%\\AMDLSS\\HelixSR-1.9.0'


@pytest.mark.skipif(os.name == 'nt', reason='Unix file modes')
def test_extract_keeps_the_executable_bit_the_linux_setup_needs(tmp_path):
    # HelixSR 1.9.0 ships setup/lib/model/launch_synth as 0o755; without the bit its setup fails with EACCES
    import zipfile
    part = tmp_path / 'h.zip'
    with zipfile.ZipFile(part, 'w') as archive:
        for name, mode in ((helixsr.SETUP, 0o644), (f'{helixsr.FSRFG}/{helixsr.DLL}', 0o644), ('setup/lib/model/launch_synth', 0o755)):
            info = zipfile.ZipInfo(f'{helixsr.TOP}/{name}')
            info.external_attr = mode << 16
            archive.writestr(info, b'x')
    helixsr.extract(part, tmp_path / 'out')
    top = tmp_path / 'out' / helixsr.TOP
    assert os.access(top / 'setup/lib/model/launch_synth', os.X_OK)
    assert not os.access(top / helixsr.FSRFG / helixsr.DLL, os.X_OK)
