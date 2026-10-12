# SPDX-License-Identifier: GPL-2.0-or-later
"""The window builds and fills its list. The root window is withdrawn before anything is drawn, so nothing shows."""
import sys
import time

import pytest

from amdlss import helixsr
from fake_helixsr import make

tk = pytest.importorskip('tkinter')


def pump(root, app, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if not app.busy and app.games:
            return
        time.sleep(0.02)


@pytest.fixture(scope='module')
def hidden_root():
    """One Tk per run: starting a second one in the same process is flaky on Windows."""
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f'no display: {error}')
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(hidden_root, tmp_path, monkeypatch):
    from tkinter import filedialog, font, messagebox, ttk
    top = tk.Toplevel(hidden_root)
    top.withdraw()
    monkeypatch.setenv('AMDLSS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(helixsr, 'helixsr_python', lambda: sys.executable)
    from amdlss.app import App
    yield top, lambda: App(top, tk, ttk, filedialog, messagebox, font), tmp_path
    top.destroy()


def test_without_helixsr_the_window_asks_for_the_install(window):
    root, make_app, _tmp = window
    app = make_app()
    pump(root, app, 0.5)
    assert 'not installed' in app.status.cget('text')
    assert app.install_button.state == 'normal' and app.game_install.state == 'disabled'


def test_games_are_listed_and_buttons_follow_the_selection(window, monkeypatch):
    root, make_app, tmp = window
    one = tmp / 'One'
    one.mkdir()
    (one / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'GAME')
    (tmp / 'Three').mkdir()
    folder = make(tmp, [{'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']},
                        {'name': 'Three', 'folder': str(tmp / 'Three'), 'fsr': False, 'inputs': ['XeSS']}])
    (folder / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (folder / helixsr.BUILT).write_text('{}')
    monkeypatch.setenv('AMDLSS_HELIXSR', str(folder))
    app = make_app()
    pump(root, app)
    assert 'HelixSR 1.8.1 is installed and built' in app.status.cget('text')
    rows = [app.tree.item(i, 'values') for i in app.tree.get_children()]
    assert [r[0] for r in rows] == ['One', 'Three'] and rows[0][2] == 'not installed'
    assert app.game_install.state == 'disabled'  # nothing selected yet
    app.tree.selection_set('0')
    root.update()
    assert app.game_install.state == 'normal' and app.game_remove.state == 'disabled'
    app.search.set('thr')
    root.update()
    assert [app.tree.item(i, 'values')[0] for i in app.tree.get_children()] == ['Three']


class Answers:
    """A stand-in for tkinter.messagebox that agrees to everything and records what the window said."""

    def __init__(self):
        self.asked, self.told = [], []

    def askyesno(self, title, text, **options):
        self.asked.append((title, text, options))
        return True

    def showinfo(self, title, text):
        self.told.append(text)

    def showerror(self, title, text):
        self.told.append('ERROR ' + text)


def test_install_and_remove_buttons_drive_the_whole_path(window, monkeypatch):
    from tkinter import filedialog, font, ttk

    from amdlss.app import App
    top, _make, tmp = window
    one = tmp / 'One'
    one.mkdir()
    (one / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'GAME')
    folder = make(tmp, [{'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']}])
    (folder / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (folder / helixsr.BUILT).write_text('{}')
    monkeypatch.setenv('AMDLSS_HELIXSR', str(folder))
    answers = Answers()
    app = App(top, tk, ttk, filedialog, answers, font)
    pump(top, app)
    app.tree.selection_set('0')
    top.update()
    assert app.route_var.get() == 'optiscaler'  # the game lists DLSS: that is offered first
    app.route_var.set('direct')
    top.update()
    app.install_game()
    assert 'Close the game first' in answers.asked[0][1] and answers.asked[0][2]['default'] == 'no'
    end = time.time() + 10
    while time.time() < end and not (answers.told and not app.busy):
        top.update()
        time.sleep(0.02)
    assert 'choose AMD FSR' in answers.told[0]
    assert (one / 'amd_fidelityfx_upscaler_dx12.original.dll').read_bytes() == b'GAME'
    while time.time() < end and app.busy:
        top.update()
    top.update()
    assert app.tree.item('0', 'values')[2] == 'FSR drop-in'
    app.tree.selection_set('0')
    top.update()
    app.remove_game()
    end = time.time() + 10
    while time.time() < end and len(answers.told) < 2:
        top.update()
        time.sleep(0.02)
    assert (one / 'amd_fidelityfx_upscaler_dx12.dll').read_bytes() == b'GAME'
    assert not (one / 'amd_fidelityfx_upscaler_dx12.original.dll').exists()


def shown(widget):
    return widget.winfo_manager() == 'pack'


def two_game_app(window, monkeypatch, answers=None):
    """A window over a fake HelixSR with One (FSR 3.1 + DLSS: both routes) and Three (XeSS only: OptiScaler only)."""
    from tkinter import filedialog, font, ttk

    from amdlss.app import App
    top, _make, tmp = window
    one = tmp / 'One'
    one.mkdir()
    (one / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'GAME')
    (tmp / 'Three').mkdir()
    folder = make(tmp, [{'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']},
                        {'name': 'Three', 'folder': str(tmp / 'Three'), 'fsr': False, 'inputs': ['XeSS']}])
    (folder / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (folder / helixsr.BUILT).write_text('{}')
    monkeypatch.setenv('AMDLSS_HELIXSR', str(folder))
    app = App(top, tk, ttk, filedialog, answers or Answers(), font)
    pump(top, app)
    return top, app, one


def test_the_route_choice_and_frame_generation_boxes_follow_the_game(window, monkeypatch):
    top, app, _one = two_game_app(window, monkeypatch)
    assert not shown(app.route_row) and not shown(app.fg_row)  # nothing selected
    app.tree.selection_set('0')  # One: both routes work, DLSS listed -> OptiScaler is offered first
    top.update()
    assert shown(app.route_row) and shown(app.fg_row) and app.route_var.get() == 'optiscaler'
    assert not app.fg_var.get()  # default off
    app.route_var.set('direct')
    app.update_buttons()
    assert shown(app.route_row) and not shown(app.fg_row)  # the FSR drop-in has no frame generation box
    app.tree.selection_set('1')  # Three: OptiScaler is its only route, so no choice but the box
    top.update()
    assert not shown(app.route_row) and shown(app.fg_row)


def wait(top, app, until, seconds=10):
    end = time.time() + seconds
    while time.time() < end and not until():
        top.update()
        time.sleep(0.02)
    top.update()


def test_optiscaler_install_with_frame_generation_and_remove(window, monkeypatch):
    answers = Answers()
    top, app, one = two_game_app(window, monkeypatch, answers)
    app.tree.selection_set('0')
    top.update()
    app.fg_var.set(True)
    app.install_game()
    assert 'OptiScaler' in answers.asked[0][1]
    wait(top, app, lambda: answers.told and not app.busy)
    assert 'choose DLSS' in answers.told[0] and 'Turn on DLSS Frame Generation in the game' in answers.told[0]
    assert (one / 'amd_fidelityfx_upscaler_dx12.dll').read_bytes() == b'GAME'  # not the drop-in
    ini = (one / 'OptiScaler.ini').read_bytes()
    assert b'FGInput=dlssg\r\n' in ini and b'FGOutput=fsrfg\r\n' in ini and b'Key = 1\r\n' in ini
    assert b'LogToFile=true\r\n' in ini and b'LogLevel=2\r\n' in ini  # logging is on after an OptiScaler install
    wait(top, app, lambda: app.tree.item('0', 'values')[2] == 'OptiScaler')
    assert app.tree.item('0', 'values')[2] == 'OptiScaler'
    app.tree.selection_set('0')
    top.update()
    assert not shown(app.route_row) and shown(app.fg_row) and app.fg_var.get()  # installed: shows what the file says
    app.fg_var.set(False)  # Update with the box off puts the old values back
    app.install_game()
    wait(top, app, lambda: len(answers.told) == 2 and not app.busy)
    assert b'FGInput=auto\r\n' in (one / 'OptiScaler.ini').read_bytes() and 'Turn on DLSS' not in answers.told[1]
    app.tree.selection_set('0')
    top.update()
    app.fg_var.set(True)
    app.install_game()
    wait(top, app, lambda: len(answers.told) == 3 and not app.busy)
    app.tree.selection_set('0')
    top.update()
    app.remove_game()
    wait(top, app, lambda: len(answers.told) == 4)
    assert not (one / 'OptiScaler.ini').exists() and not (one / 'opti.marker').exists()
    assert not (window[2] / 'data' / 'framegen.json').exists()


def test_collect_logs_button_writes_the_zip_and_shows_it(window, monkeypatch):
    import zipfile

    from amdlss import logs
    answers = Answers()
    top, app, one = two_game_app(window, monkeypatch, answers)
    desktop, shown_in_explorer = window[2] / 'desktop', []
    monkeypatch.setattr(logs, 'desktop_dir', lambda: desktop)
    monkeypatch.setattr(logs, 'system_info', lambda: {})
    monkeypatch.setattr(logs, 'reveal', lambda path, say=print: shown_in_explorer.append(path))
    app.collect_logs()  # nothing selected: nothing asked
    assert not answers.asked and app.game_logs.state == 'disabled'
    app.tree.selection_set('0')
    top.update()
    assert app.game_logs.state == 'normal'
    app.install_game()
    wait(top, app, lambda: answers.told and not app.busy)
    assert 'press Collect logs and send the zip' in answers.told[0]
    (one / 'OptiScaler.log').write_text('[Info] hooked\n')
    app.tree.selection_set('0')
    top.update()
    app.collect_logs()
    assert answers.asked[-1][0] == 'Collect logs for One' and 'exe-folder/OptiScaler.log' in answers.asked[-1][1]
    wait(top, app, lambda: shown_in_explorer and not app.busy)
    [zipped] = desktop.glob('AMDLSS-logs-One-*.zip')
    assert shown_in_explorer == [zipped]
    with zipfile.ZipFile(zipped) as archive:
        assert {'exe-folder/OptiScaler.log', 'files.txt', 'system.txt', 'amdlss-install.log'} <= set(archive.namelist())
        assert 'install (optiscaler)' in archive.read('amdlss-install.log').decode()


def rows_of(app):
    return [app.tree.item(i, 'values') for i in app.tree.get_children()]


def test_leftovers_and_games_without_upscalers_are_hidden_until_show_all(window, monkeypatch):
    root, make_app, tmp = window
    ready, bare = tmp / 'Ready', tmp / 'Bare'
    for folder in (ready, bare):
        folder.mkdir()
    helix = make(tmp, [{'name': 'Ready', 'folder': str(ready), 'fsr': True, 'inputs': ['DLSS']},
                       {'name': 'Leftover', 'folder': str(tmp / 'Leftover'), 'fsr': True, 'inputs': ['DLSS']},
                       {'name': 'Bare', 'folder': str(bare), 'fsr': False, 'inputs': []}])
    (helix / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (helix / helixsr.BUILT).write_text('{}')
    monkeypatch.setenv('AMDLSS_HELIXSR', str(helix))
    app = make_app()
    app.extras = [str(tmp / 'Typo')]  # a folder added by hand that is not there
    pump(root, app)
    wait(root, app, lambda: not app.busy and len(rows_of(app)) == 2)
    assert [r[0] for r in rows_of(app)] == ['Ready', 'Typo']  # the leftover and Bare stay out
    assert rows_of(app)[1][3] == 'folder not found' and rows_of(app)[1][2] == ''
    assert app.empty.cget('text') == '1 games ready, 2 hidden'

    app.tree.selection_set('1')  # Typo: shown with its reason, nothing to install, only to forget
    root.update()
    assert app.game_install.state == 'disabled' and app.game_remove.state == 'disabled'
    assert app.game_forget.state == 'normal' and app.game_folder.state == 'disabled'

    app.show_all.set(True)
    app.fill_tree()
    rows = {r[0]: r for r in rows_of(app)}
    assert sorted(rows) == ['Bare', 'Leftover', 'Ready', 'Typo']
    assert rows['Leftover'][3] == 'not installed' and rows['Bare'][3] == 'no DLSS, FSR 3.1 or XeSS found'
    assert rows['Ready'][3] == ''
    names = [app.tree.item(i, 'values')[0] for i in app.tree.get_children()]
    assert [app.tree.item(i, 'tags') for i in app.tree.get_children()].count(('hidden',)) == 3  # greyed out
    app.tree.selection_set(str(names.index('Leftover')))
    root.update()
    assert app.game_install.state == 'disabled' and app.game_forget.state == 'disabled'
    assert app.empty.cget('text') == '1 games ready, 2 hidden'

    asked, real = [], app.helixsr.list_games  # a failed folder is not handed to HelixSR again until Rescan
    monkeypatch.setattr(app.helixsr, 'list_games', lambda extras=(): (asked.append(list(extras)), real(extras))[1])
    app.refresh_games()
    wait(root, app, lambda: not app.busy)
    assert asked == [[]] and 'folder not found' in [r[3] for r in rows_of(app)]
    app.rescan()
    wait(root, app, lambda: not app.busy)
    assert asked[-1] == [str(tmp / 'Typo')]

    app.tree.selection_set(str(names.index('Typo')))
    app.forget_folder()
    wait(root, app, lambda: not app.busy and app.extras == [])
    assert app.extras == [] and 'Typo' not in [r[0] for r in rows_of(app)]


def new_release(version='0.1.0-beta.5'):
    from amdlss import update
    return update.Release(version, 'https://example.test/AMDLSS.exe', 'a' * 64, 10, 'https://example.test/notes')


def test_an_update_is_a_card_with_a_link_and_from_source_no_update_button(window, monkeypatch):
    from amdlss import update
    top, make_app, _tmp = window
    app = make_app()
    pump(top, app, 0.3)
    monkeypatch.setattr(update, 'FROZEN', False)
    app.update_checked(None, manual=False)   # nothing newer: no card, no box
    assert app.update_card is None
    app.update_checked(new_release('0.1.0-beta.1'), manual=False)   # older than the running one
    assert app.update_card is None
    app.update_checked(new_release(), manual=False)
    top.update()
    assert shown(app.update_card) and 'AMDLSS 0.1.0-beta.5 is available.' in app.update_label.cget('text')
    assert not hasattr(app, 'update_button')   # from source nothing is replaced


def test_the_manual_check_answers_and_a_failure_is_one_log_line(window, monkeypatch):
    from amdlss import update
    top, make_app, _tmp = window
    answers = Answers()
    from tkinter import filedialog, font, ttk

    from amdlss.app import App
    app = App(top, tk, ttk, filedialog, answers, font)
    app.update_checked(None, manual=True)
    assert answers.told == [f'You have the latest version ({update.VERSION}).']
    app.update_check_failed('GitHub refused the request (rate limit, try later)', manual=False)
    assert 'update check failed: GitHub refused' in app.log.get('1.0', 'end') and len(answers.told) == 1
    app.update_check_failed('could not ask GitHub: offline', manual=True)
    assert answers.told[-1] == 'ERROR Could not check for updates: could not ask GitHub: offline'


def test_the_check_runs_off_the_window_thread_with_the_beta_switch(window, monkeypatch):
    from amdlss import update
    top, make_app, tmp = window
    app = make_app()
    asked = []
    monkeypatch.setattr(update, 'fetch_release', lambda beta: asked.append(beta) or new_release())
    assert app.beta_var.get() is True   # the running version is a beta: the switch starts on
    app.check_updates(False)
    wait(top, app, lambda: app.update_card is not None)
    assert asked == [True] and app.update_card is not None
    app.beta_var.set(False)
    app.beta_changed()
    from amdlss import settings
    assert settings.load_beta(tmp / 'data', True) is False
    app.check_updates(False)
    wait(top, app, lambda: len(asked) == 2)
    assert asked == [True, False]


def test_update_replaces_the_exe_and_restarts_it_then_the_window_closes(window, monkeypatch):
    from amdlss import update
    from tkinter import filedialog, font, ttk

    from amdlss.app import App
    top, _make, tmp = window
    answers = Answers()
    app = App(top, tk, ttk, filedialog, answers, font)
    monkeypatch.setattr(update, 'FROZEN', True)
    monkeypatch.setattr(update, 'exe_path', lambda: tmp / 'AMDLSS.exe')
    done = []
    monkeypatch.setattr(update, 'apply', lambda release, exe, progress: (progress(5, 10), done.append((release, exe))))
    monkeypatch.setattr(update, 'restart', lambda exe, args: done.append(('restart', exe)))
    destroyed = []
    monkeypatch.setattr(app.root, 'destroy', lambda: destroyed.append(1))
    app.offer_update(new_release())
    app.busy = True   # HelixSR is working: no update now
    app.install_update(new_release())
    assert answers.told == ['Wait until HelixSR is done, then update.'] and not done
    app.busy = False
    app.install_update(new_release())
    assert answers.asked[-1][0] == 'Update to 0.1.0-beta.5'
    wait(top, app, lambda: destroyed)
    assert done[-1] == ('restart', tmp / 'AMDLSS.exe') and destroyed == [1]


def test_a_failed_update_is_one_line_and_the_card_is_usable_again(window, monkeypatch):
    from amdlss import update
    from tkinter import filedialog, font, ttk

    from amdlss.app import App
    top, _make, tmp = window
    answers = Answers()
    app = App(top, tk, ttk, filedialog, answers, font)
    monkeypatch.setattr(update, 'FROZEN', True)
    monkeypatch.setattr(update, 'exe_path', lambda: tmp / 'AMDLSS.exe')

    def bad_hash(release, exe, progress):
        raise update.UpdateError('the download does not match the SHA-256 GitHub lists, nothing was changed')

    monkeypatch.setattr(update, 'apply', bad_hash)
    monkeypatch.setattr(update, 'restart', lambda *a: (_ for _ in ()).throw(AssertionError('restarted')))
    app.offer_update(new_release())
    app.install_update(new_release())
    wait(top, app, lambda: any(t.startswith('ERROR') for t in answers.told))
    assert answers.told == ['ERROR Update failed: the download does not match the SHA-256 GitHub lists, '
                            'nothing was changed']
    assert not app.busy and app.update_button.state == 'normal' and 'is available.' in app.update_label.cget('text')
