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
