# SPDX-License-Identifier: GPL-2.0-or-later
"""The window with FSR 4.1.1: the line on every card, the Upscaler control, the DLL fallback, and a full install."""
import hashlib

import pytest

from amdlss import app as appmod, fsr4, gpu, helixsr
from amdlss.ui import plain
from fake_helixsr import age, make
from fake_scaler import make as make_package
from test_app_smoke import Answers, shown, wait, window, hidden_root  # noqa: F401 (fixtures)
from test_pe import build_dll

tk = pytest.importorskip('tkinter')
AMD = build_dll(version=(4, 1, 1, 2740))
CARDS = {'rdna4': gpu.Gpu('', '1002', '7550'), 'rdna3': gpu.Gpu('', '1002', '744c'),
         'rdna2': gpu.Gpu('', '1002', '73bf'), 'other': gpu.Gpu('NVIDIA GeForce RTX 4090', '10de', '2684')}


def build_app(window, monkeypatch, card='rdna3', answers=None, downloads_files=None):
    """A window over a fake HelixSR with One (FSR 3.1 + DLSS), Three (XeSS only) and Four (FSR 3.1 + Easy Anti-Cheat)."""
    from tkinter import filedialog, font, ttk
    top, _make, tmp = window
    monkeypatch.setattr(gpu, 'detect', lambda *args, **kwargs: [CARDS[card]])
    monkeypatch.setattr(fsr4, 'AMD_SIZE', len(AMD))
    monkeypatch.setattr(fsr4, 'AMD_SHA256', hashlib.sha256(AMD).hexdigest())
    downloads = tmp / 'Downloads'
    downloads.mkdir()
    for name, data in (downloads_files or {}).items():   # what the user already has in Downloads
        (downloads / name).parent.mkdir(parents=True, exist_ok=True)
        (downloads / name).write_bytes(data)
    monkeypatch.setattr(appmod, 'user_dirs', lambda: [downloads])
    folders = {}
    for name in ('One', 'Three', 'Four'):
        folders[name] = tmp / name
        folders[name].mkdir()
    for name in ('One', 'Four'):
        (folders[name] / fsr4.DLL).write_bytes(b'GAME')
    (folders['Four'] / 'EasyAntiCheat').mkdir()
    helix = make(tmp, [{'name': 'One', 'folder': str(folders['One']), 'fsr': True, 'inputs': ['DLSS']},
                       {'name': 'Three', 'folder': str(folders['Three']), 'fsr': False, 'inputs': ['XeSS']},
                       {'name': 'Four', 'folder': str(folders['Four']), 'fsr': True, 'inputs': []}])
    age(helix)
    (helix / helixsr.BUILT).write_text('{}')
    (helix / 'amd-fsr').mkdir()
    (helix / 'amd-fsr' / fsr4.DLL).write_bytes(AMD)
    (helix / 'amd-fsr' / fsr4.LICENSE).write_text('AMD license\n')
    make_package(tmp / 'data')   # HelixSR Scaler's package where the app looks for it
    monkeypatch.setenv('AMDLSS_HELIXSR', str(helix))
    answers = answers or Answers()
    app = appmod.App(top, tk, ttk, filedialog, answers, font)
    from test_app_smoke import pump
    pump(top, app)
    return top, app, folders, answers, downloads


def select(top, app, name):
    app.tree.selection_set(str([g.name for g in app.shown].index(name)))
    top.update()


def card_of(app, name):
    return next(c for c in app.gallery.cards if c.name == name)


def test_every_card_says_whether_the_game_can_use_fsr_4_and_why_not(window, monkeypatch):
    top, app, _f, _a, _d = build_app(window, monkeypatch)
    assert card_of(app, 'One').fsr4 == 'FSR 4.1.1: yes' and card_of(app, 'One').fsr4_ok
    assert card_of(app, 'Three').fsr4 == 'FSR 4.1.1: yes'
    four = card_of(app, 'Four')
    assert four.fsr4 == 'FSR 4.1.1: no, anti-cheat' and not four.fsr4_ok


def test_a_card_that_cannot_run_it_says_so_on_every_game(window, monkeypatch):
    top, app, _f, _a, _d = build_app(window, monkeypatch, card='other')
    assert {c.fsr4 for c in app.gallery.cards if c.name != 'Four'} == {'FSR 4.1.1: no, your card'}
    assert card_of(app, 'Four').fsr4 == 'FSR 4.1.1: no, anti-cheat'   # the game is judged before the card
    select(top, app, 'One')
    assert str(app.upscaler_buttons[1].state) == 'disabled' and "can't run FSR 4" in app.fsr4_note.cget('text')
    assert not shown(app.dll_button)   # no DLL can help this card


def test_the_upscaler_row_follows_the_game(window, monkeypatch):
    top, app, _f, _a, _d = build_app(window, monkeypatch)
    assert not shown(app.upscaler_row)
    select(top, app, 'One')
    assert shown(app.upscaler_row) and app.upscaler_var.get() == 'helixsr'
    assert app.upscaler_buttons[1].state == 'normal' and 'AMD\'s DLL 4.1.1.2740' in app.fsr4_note.cget('text')
    select(top, app, 'Four')
    assert app.upscaler_buttons[1].state == 'disabled' and 'Anti-cheat found (Easy Anti-Cheat)' in app.fsr4_note.cget('text')
    assert app.game_install.state == 'normal'   # HelixSR is still the pick; the warning is shown, not enforced here


def test_picking_fsr_4_is_remembered_per_game_and_blocks_install_where_it_cannot_run(window, monkeypatch):
    top, app, _f, _a, _d = build_app(window, monkeypatch)
    select(top, app, 'One')
    app.upscaler_buttons[1].choose()
    top.update()
    assert app.upscaler_var.get() == 'fsr4'
    select(top, app, 'Three')
    assert app.upscaler_var.get() == 'helixsr'
    select(top, app, 'One')
    assert app.upscaler_var.get() == 'fsr4'   # remembered for One only


def test_install_fsr_4_through_the_drop_in_then_remove(window, monkeypatch):
    top, app, folders, answers, _d = build_app(window, monkeypatch)
    select(top, app, 'Four')   # anti-cheat: not offered at all
    app.upscaler_var.set('fsr4')
    app.update_buttons()
    assert app.game_install.state == 'disabled'
    select(top, app, 'One')
    app.upscaler_buttons[1].choose()
    app.route_var.set('direct')
    app.update_buttons()
    app.install_game()
    assert 'AMD\'s FSR 4.1.1' in answers.asked[0][1] and 'Install into One' == answers.asked[0][0]
    wait(top, app, lambda: answers.told and not app.busy)
    assert 'FSR 4.1.1 is installed in One' in answers.told[0] and 'choose AMD FSR' in answers.told[0]
    one = folders['One']
    assert (one / fsr4.DLL).read_bytes() == AMD and (one / 'amd_fidelityfx_upscaler_dx12.original.dll').read_bytes() == b'GAME'
    wait(top, app, lambda: app.shown and card_of(app, 'One').state == 'FSR 4.1.1 drop-in')
    assert card_of(app, 'One').kind == 'ok'
    select(top, app, 'One')
    assert app.game_state.cget('text') == 'FSR 4.1.1 drop-in'
    assert app.upscaler_var.get() == 'fsr4' and app.game_install.text == 'Update'
    app.remove_game()
    assert 'AMDLSS puts the game' in answers.asked[-1][1]
    wait(top, app, lambda: len(answers.told) == 2 and not app.busy)
    assert 'FSR 4.1.1 is removed from One' in answers.told[1]
    assert (one / fsr4.DLL).read_bytes() == b'GAME' and sorted(p.name for p in one.iterdir()) == [fsr4.DLL]


def test_install_fsr_4_through_optiscaler(window, monkeypatch):
    top, app, folders, answers, _d = build_app(window, monkeypatch)
    select(top, app, 'Three')
    app.upscaler_buttons[1].choose()
    top.update()
    app.install_game()
    wait(top, app, lambda: answers.told and not app.busy)
    assert 'choose DLSS' in answers.told[0] and 'FFX Upscaler' in answers.told[0]
    assert (folders['Three'] / 'dxgi.dll').is_file() and (folders['Three'] / 'HelixSR' / 'helixsr.dll').is_file()
    wait(top, app, lambda: card_of(app, 'Three').state == 'FSR 4.1.1 (OptiScaler)')
    assert fsr4.in_use(folders['Three'])
    select(top, app, 'Three')
    assert app.upscaler_var.get() == 'fsr4'
    app.upscaler_buttons[0].choose()   # back to HelixSR: Update puts the paths back
    app.install_game()
    wait(top, app, lambda: len(answers.told) == 2 and not app.busy)
    assert not fsr4.in_use(folders['Three']) and not (folders['Three'] / 'HelixSR' / 'fsr4').exists()
    wait(top, app, lambda: card_of(app, 'Three').state == 'OptiScaler')


# --- RX 6000: the DLL is found, or picked as the fallback -------------------------------------------------------

def test_an_rx_6000_card_finds_the_dll_in_downloads_by_itself(window, monkeypatch):
    files = {'extracted/' + fsr4.DLL: build_dll(version=(4, 1, 1, 9))}
    top, app, _f, _a, _d = build_app(window, monkeypatch, card='rdna2', downloads_files=files)
    assert 'FSR 4 DLL 4.1.1.9 found and kept' in app.log.get('1.0', 'end')
    assert card_of(app, 'One').fsr4 == 'FSR 4.1.1: yes'
    select(top, app, 'One')
    assert 'your DLL 4.1.1.9, found in' in app.fsr4_note.cget('text')
    assert shown(app.dll_button) and app.dll_button.text == plain('Use another DLL…') and shown(app.dll_forget)


def test_an_rx_6000_card_with_no_dll_asks_for_one_and_hints_at_an_archive(window, monkeypatch):
    files = {'FSR4 int8 community build.7z': b'7z'}
    top, app, _f, _a, _d = build_app(window, monkeypatch, card='rdna2', downloads_files=files)
    assert card_of(app, 'One').fsr4 == 'FSR 4.1.1: no, needs your DLL'
    select(top, app, 'One')
    note = app.fsr4_note.cget('text')
    assert 'Choose FSR 4 DLL' in note and 'Unpack FSR4 int8 community build.7z and AMDLSS will find the DLL.' in note
    assert shown(app.dll_button) and app.dll_button.text == plain('Choose FSR 4 DLL…') and not shown(app.dll_forget)
    assert app.upscaler_buttons[1].state == 'disabled'


def test_choosing_a_dll_by_hand_unlocks_the_games_and_a_bad_file_is_refused(window, monkeypatch):
    top, app, _f, answers, downloads = build_app(window, monkeypatch, card='rdna2')
    select(top, app, 'One')
    mine, bad = downloads.parent / 'mine.dll', downloads.parent / 'bad.dll'
    mine.write_bytes(build_dll(version=(4, 0, 2, 3)))
    bad.write_bytes(b'not a dll')
    app.filedialog = type('Pick', (), {'askopenfilename': staticmethod(lambda **options: str(bad))})
    app.choose_fsr4_dll()
    assert answers.told[-1].startswith('ERROR ') and 'not a Windows DLL' in answers.told[-1]
    assert card_of(app, 'One').fsr4 == 'FSR 4.1.1: no, needs your DLL'
    app.filedialog = type('Pick', (), {'askopenfilename': staticmethod(lambda **options: str(mine))})
    app.choose_fsr4_dll()
    top.update()
    assert card_of(app, 'One').fsr4 == 'FSR 4.1.1: yes' and app.upscaler_buttons[1].state == 'normal'
    assert 'your DLL 4.0.2.3' in app.fsr4_note.cget('text') and app.dll_button.text == plain('Use another DLL…')
    app.forget_fsr4_dll()
    top.update()
    assert card_of(app, 'One').fsr4 == 'FSR 4.1.1: no, needs your DLL' and not shown(app.dll_forget)


def test_an_rx_7000_card_may_pick_its_own_dll_over_amds(window, monkeypatch):
    top, app, _f, _a, downloads = build_app(window, monkeypatch, card='rdna3')
    select(top, app, 'One')
    assert shown(app.dll_button) and 'AMD\'s DLL' in app.fsr4_note.cget('text')
    mine = downloads.parent / 'mine.dll'
    mine.write_bytes(build_dll(version=(4, 1, 1, 7)))
    app.filedialog = type('Pick', (), {'askopenfilename': staticmethod(lambda **options: str(mine))})
    app.choose_fsr4_dll()
    top.update()
    assert 'your DLL 4.1.1.7' in app.fsr4_note.cget('text')
