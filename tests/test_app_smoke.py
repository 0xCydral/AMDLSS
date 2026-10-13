# SPDX-License-Identifier: GPL-2.0-or-later
"""The window builds and fills its list. The root window is withdrawn before anything is drawn, so nothing shows."""
import json
import shutil
import sys
import time

import pytest

from amdlss import covers, helixsr, settings
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
    settings.save_covers(tmp_path / 'data', False)   # no test fetches covers from Steam unless it says so
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


def test_add_folder_and_rescan_work_before_helixsr_is_installed(window, monkeypatch):
    root, make_app, tmp = window
    app = make_app()
    pump(root, app, 0.5)
    assert app.helixsr is None and app.add_button.state == 'normal' and app.rescan_button.state == 'normal'
    assert app.game_install.state == 'disabled' and app.game_forget.state == 'disabled'   # nothing selected
    game = tmp / 'Game'
    game.mkdir()
    app.filedialog = type('Pick', (), {'askdirectory': staticmethod(lambda **options: str(game))})
    app.add_folder()
    assert app.extras == [str(game)] and settings.load_folders(tmp / 'data') == [str(game)]
    assert 'listed once HelixSR is installed' in app.empty.cget('text')
    app.rescan()   # still nothing: a line in the log says so
    assert 'still not installed' in app.log.get('1.0', 'end')
    other = tmp / 'Other'
    other.mkdir()
    folder = make(tmp, [{'name': 'Other', 'folder': str(other), 'fsr': True, 'inputs': ['DLSS']}])   # installed by hand
    (folder / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (folder / helixsr.BUILT).write_text('{}')
    monkeypatch.setenv('AMDLSS_HELIXSR', str(folder))
    app.rescan()
    pump(root, app)
    assert app.helixsr is not None and 'Other' in [r[0] for r in rows_of(app)]   # the manual install is picked up


def test_the_status_text_cannot_push_the_install_button_off_the_window(window):
    root, make_app, _tmp = window
    app = make_app()
    row = app.status.master
    order = [str(w) for w in row.pack_slaves()]
    assert order.index(str(app.install_button.canvas)) < order.index(str(app.status))   # the button gets its room first
    assert app.status.bind('<Configure>')   # and the text wraps in the width the button leaves it


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
    monkeypatch.setattr(logs, 'system_info', lambda *args, **kwargs: {})
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


def test_send_logs_button_sends_every_game_in_one_report_and_shows_the_code(window, monkeypatch):
    from amdlss import logs, reports
    answers, sent = Answers(), []
    top, app, one = two_game_app(window, monkeypatch, answers)
    monkeypatch.setattr(logs, 'system_info', lambda *args, **kwargs: {})
    monkeypatch.setattr(reports, 'REPORT_URL', 'https://logs.example.test')
    monkeypatch.setattr(reports.ReportClient, 'send', lambda self, report: sent.append(report) or 'ABCD2345')
    assert app.send_button.state == 'normal'   # not tied to a selection
    app.send_logs()   # nothing installed and no log anywhere: nothing to send and nothing asked
    assert not answers.asked and 'nothing to send' in answers.told[-1] and not sent
    app.tree.selection_set('0')
    top.update()
    app.install_game()
    wait(top, app, lambda: len(answers.told) == 2 and not app.busy)
    (window[2] / 'Three' / 'OptiScaler.log').write_text('[Info] three\n')
    app.send_logs()
    title, text, _options = answers.asked[-1]
    assert title == 'Send logs' and 'One: ' in text and 'Three: 2 files' in text and 'AMDLSS log server' in text
    wait(top, app, lambda: sent and not app.busy)
    assert sorted({n.split('/')[0] for n in sent[0]['files']}) == ['One', 'Three', 'games.txt', 'system.txt']
    assert sent[0]['files']['Three/exe-folder/OptiScaler.log'] == '[Info] three\n'
    assert 'AMDLSS report ABCD2345' in answers.told[-1] and top.clipboard_get() == 'AMDLSS report ABCD2345'
    assert 'logs sent: AMDLSS report ABCD2345' in app.log.get('1.0', 'end')
    assert app.send_button.state == 'normal' and not app.busy


def test_a_failed_or_impossible_send_offers_the_zip_of_the_selected_game(window, monkeypatch):
    import zipfile

    from amdlss import logs, reports
    answers = Answers()
    top, app, one = two_game_app(window, monkeypatch, answers)
    desktop = window[2] / 'desktop'
    monkeypatch.setattr(logs, 'desktop_dir', lambda: desktop)
    monkeypatch.setattr(logs, 'system_info', lambda *args, **kwargs: {})
    monkeypatch.setattr(logs, 'reveal', lambda path, say=print: None)
    (one / 'OptiScaler.log').write_text('[Info] hooked\n')

    def refused(self, report):
        raise reports.ReportError('could not reach the log server (offline)')

    monkeypatch.setattr(reports.ReportClient, 'send', refused)
    app.tree.selection_set('0')
    top.update()
    app.send_logs()   # the build has no server address: no confirmation of a send, the zip is offered at once
    offers = [(t, x) for t, x, _o in answers.asked if 'not set up in this build' in x]
    assert offers and offers[0][0] == 'Send logs' and 'Collect the logs of One into a zip' in offers[0][1]
    assert answers.asked[-1][0] == 'Collect logs for One'   # the tester said yes: the usual zip flow starts
    wait(top, app, lambda: list(desktop.glob('AMDLSS-logs-One-*.zip')) and not app.busy)
    assert len(list(desktop.glob('AMDLSS-logs-One-*.zip'))) == 1
    shutil.rmtree(desktop)
    monkeypatch.setattr(reports, 'REPORT_URL', 'https://logs.example.test')
    app.send_logs()
    assert [t for t, x, _o in answers.asked if 'AMDLSS log server' in x] == ['Send logs'] and not list(desktop.glob('*.zip'))
    wait(top, app, lambda: any('The logs were not sent' in text for _t, text, _o in answers.asked))
    failed = [text for _t, text, _o in answers.asked if 'The logs were not sent: could not reach' in text]
    assert failed and 'Collect the logs of One into a zip on your Desktop instead?' in failed[0]
    wait(top, app, lambda: list(desktop.glob('AMDLSS-logs-One-*.zip')) and not app.busy)
    with zipfile.ZipFile(next(desktop.glob('AMDLSS-logs-One-*.zip'))) as archive:
        assert 'exe-folder/OptiScaler.log' in archive.namelist()
    assert 'send logs failed: could not reach the log server (offline)' in app.log.get('1.0', 'end')


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


def next_version():
    """The beta after the running one, so the fixture stays newer at every version bump."""
    from amdlss import update
    base, number = update.VERSION.rsplit('.', 1)
    return f'{base}.{int(number) + 1}'


def new_release(version=None):
    from amdlss import update
    return update.Release(version or next_version(), 'https://example.test/AMDLSS.exe', 'a' * 64, 10, 'https://example.test/notes')


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
    assert shown(app.update_card) and f'AMDLSS {next_version()} is available.' in app.update_label.cget('text')
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
    assert answers.asked[-1][0] == f'Update to {next_version()}'
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


def test_the_cards_follow_the_hidden_tree_and_the_tree_follows_the_cards(window, monkeypatch):
    top, app, _one = two_game_app(window, monkeypatch)
    assert not shown(app.tree)  # the tree is the model, the cards are the view
    assert [c.name for c in app.gallery.cards] == ['One', 'Three'] == [r[0] for r in rows_of(app)]
    assert len(app.gallery.outlines) == 2 and app.gallery.selected is None
    app.gallery.clicked(None, 1)  # a click on the second card selects its tree row
    assert app.tree.selection() == ('1',) and app.gallery.selected == 1
    app.tree.selection_set('0')  # a tree selection lights its card
    top.update()
    assert app.gallery.selected == 0
    app.gallery.key('Right')
    assert app.tree.selection() == ('1',)
    app.gallery.key('Right')  # past the last card stays on it
    assert app.tree.selection() == ('1',)
    app.gallery.key('Home')
    assert app.tree.selection() == ('0',) and app.game_title.cget('text') == 'One'
    app.search.set('thr')
    top.update()
    assert [c.name for c in app.gallery.cards] == ['Three']


def test_the_segmented_toggle_drives_the_install_route(window, monkeypatch):
    from amdlss.ui import BLOOD
    top, app, _one = two_game_app(window, monkeypatch)
    app.tree.selection_set('0')  # One: both routes work
    top.update()
    optiscaler, direct = app.route_buttons
    assert app.route_var.get() == 'optiscaler' and shown(app.route_row)
    assert optiscaler.itemcget(optiscaler.rect, 'fill') == BLOOD != direct.itemcget(direct.rect, 'fill')
    direct.choose()  # a click, or Return/space on the focused half
    assert app.route_var.get() == 'direct' and not shown(app.fg_row)  # the drop-in has no frame generation
    assert direct.itemcget(direct.rect, 'fill') == BLOOD != optiscaler.itemcget(optiscaler.rect, 'fill')
    direct.step()  # an arrow key hands the choice to the other half
    assert app.route_var.get() == 'optiscaler' and shown(app.fg_row)
    direct.configure(state='disabled')
    direct.choose()
    assert app.route_var.get() == 'optiscaler'


def test_monogram_and_tint_come_from_the_name_alone():
    from amdlss.ui import monogram, tint
    names = {"Dragon's Dogma 2": 'DD2', 'Final Fantasy XVI': 'XVI', 'Stellar Blade': 'SB', 'Cyberpunk 2077': '2077',
             'The Witcher 3': 'W3', 'Far Cry 6': 'FC6', 'Like a Dragon Gaiden': 'LDG', 'Where Winds Meet': 'WWM'}
    assert {name: monogram(name) for name in names} == names
    assert monogram('???') == '?' and tint('Far Cry 6') == tint('Far Cry 6')


def image_items(app, number):
    return [i for i in app.gallery.canvas.find_withtag(f'c{number}') if app.gallery.canvas.type(i) == 'image']


def test_a_card_with_a_cached_cover_draws_it_and_one_without_keeps_the_tint(window, monkeypatch):
    image = pytest.importorskip('PIL.Image')
    tmp = window[2]
    cache = tmp / 'data' / 'covers'
    cache.mkdir(parents=True)
    image.new('RGB', (460, 215), '#c03040').save(cache / '123.jpg', 'JPEG')
    (cache / 'names.json').write_text(json.dumps({'names': {'one': 'title:one'},
                                                  'entries': {'title:one': {'source': 'steam:123', 'at': 0}}}))
    settings.save_covers(tmp / 'data', True)
    monkeypatch.setattr(covers, 'download', lambda url, limit: None)   # Steam knows nothing about Three
    top, app, _one = two_game_app(window, monkeypatch)
    assert [c.cover is not None for c in app.gallery.cards] == [True, False]
    assert len(image_items(app, 0)) == 1 and image_items(app, 1) == []
    assert any(app.gallery.canvas.type(i) == 'line' for i in app.gallery.canvas.find_withtag('c1'))   # the stripes
    app.covers_var.set(False)   # the footer's box: the tint comes back and nothing more is fetched
    app.covers_changed()
    assert settings.load_covers(tmp / 'data') is False and image_items(app, 0) == []


def test_a_cover_that_arrives_later_is_drawn_without_losing_the_selection(window, monkeypatch):
    image = pytest.importorskip('PIL.Image')
    import io
    stream = io.BytesIO()
    image.new('RGB', (460, 215), '#3040c0').save(stream, 'JPEG')

    def steam(url, limit):   # Steam knows Three as app 77 and has its header image
        if 'SearchApps' in url:
            return json.dumps([{'appid': '77', 'name': 'Three'}] if url.endswith('/Three') else []).encode()
        return stream.getvalue() if '/apps/77/' in url else None

    settings.save_covers(window[2] / 'data', True)
    monkeypatch.setattr(covers, 'download', steam)
    top, app, _one = two_game_app(window, monkeypatch)
    app.tree.selection_set('1')
    end = time.time() + 10
    while time.time() < end and not image_items(app, 1):
        top.update()
        time.sleep(0.02)
    assert image_items(app, 1) and app.gallery.selected == 1 and app.tree.selection() == ('1',)
    assert (window[2] / 'data' / 'covers' / '77.jpg').is_file()


def test_an_older_helixsr_is_listed_and_the_button_updates_it(window, monkeypatch):
    from tkinter import filedialog, font, ttk

    from amdlss.app import App
    top, _make, tmp = window
    one = tmp / 'One'
    one.mkdir()
    (one / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'GAME')
    old = tmp / 'data' / 'HelixSR-1.8.1'
    old.parent.mkdir(exist_ok=True)
    make(tmp, [{'name': 'One', 'folder': str(one), 'fsr': True, 'inputs': ['DLSS']}]).rename(old)
    (old / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (old / helixsr.BUILT).write_text('{}')
    monkeypatch.delenv('AMDLSS_HELIXSR', raising=False)
    answers, installs = Answers(), []

    def fake_install(data_dir, consented, progress, on_line):
        installs.append(consented)
        new = old.parent / helixsr.TOP
        shutil.copytree(old, new)
        (new / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.9.0\x00')
        return new

    monkeypatch.setattr(helixsr, 'install_helixsr', fake_install)
    app = App(top, tk, ttk, filedialog, answers, font)
    pump(top, app)
    assert app.status.cget('text') == 'HelixSR 1.8.1 is installed. HelixSR 1.9.0 is available.'
    assert app.install_button.text == 'Update HelixSR' and app.install_button.state == 'normal'
    assert [r[0] for r in rows_of(app)] == ['One']  # the older HelixSR still lists the games
    app.install_helixsr()
    assert answers.asked[0][0] == 'Install HelixSR' and 'HelixSR 1.9.0' in answers.asked[0][1]
    wait(top, app, lambda: installs and not app.busy)
    assert installs == [True]
    assert 'HelixSR 1.9.0 is installed and built' in app.status.cget('text')
    assert app.install_button.text == 'Install HelixSR' and app.install_button.state == 'disabled'
    assert (old / helixsr.DLL).is_file()  # the old folder stays on disk


def test_a_game_on_an_older_helixsr_shows_update(window, monkeypatch):
    from tkinter import filedialog, font, ttk

    from amdlss import games
    from amdlss.app import App
    top, _make, tmp = window
    rows = []
    for name, version in (('One', '1.7.0'), ('Two', '1.8.1'), ('Three', '')):
        game = tmp / name
        game.mkdir()
        (game / 'amd_fidelityfx_upscaler_dx12.dll').write_bytes(b'HELIX stand-in')   # installed: the drop-in
        (game / 'amd_fidelityfx_upscaler_dx12.original.dll').write_bytes(b'GAME')
        if version:
            (game / 'helixsr-install.json').write_text(json.dumps({'helixsr': version}))
        rows.append({'name': name, 'folder': str(game), 'fsr': True, 'inputs': ['DLSS']})
    folder = make(tmp, rows)
    (folder / helixsr.DLL).write_bytes(b'MZ\x00HelixSR 1.8.1\x00')
    (folder / helixsr.BUILT).write_text('{}')
    monkeypatch.setenv('AMDLSS_HELIXSR', str(folder))
    app = App(top, tk, ttk, filedialog, Answers(), font)
    pump(top, app)
    assert [g.helixsr for g in app.games] == ['1.7.0', '1.8.1', '']
    assert [(c.state, c.kind) for c in app.gallery.cards] == [('update', 'update'), ('FSR drop-in', 'ok'),
                                                              ('FSR drop-in', 'ok')]  # no version: no hint
    app.tree.selection_set('0')
    top.update()
    assert app.game_install.text == 'Update' and app.game_install.state == 'normal'
    assert not app.outdated(games.Game(name='x', state='none', helixsr='1.0.0'))  # nothing installed: nothing to update
