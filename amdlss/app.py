# SPDX-License-Identifier: GPL-2.0-or-later
"""The AMDLSS window (Tkinter): install HelixSR once, see your games, click Install or Remove.

All the work is HelixSR's own: this file only asks amdlss.helixsr to run it and shows what comes back. Run it with
`python AMDLSS.pyw`; `python AMDLSS.pyw --selftest` checks the logic without opening a window.
"""
import ctypes
import os
import queue
import sys
import threading
import webbrowser
from pathlib import Path

from . import VERSION, framegen, games as gamelist, helixsr, logs, settings, update
from .ui import (BG, BLOOD, BLOOD_HI, CAUTION, CARD, DIM, FIELD, GLYPH_COLORS, LINE, LINE_HI, MUTED, TEXT, Bar, Card,
                 FlatButton, Gallery, Look, logo, magnifier, mix, segmented, short_path)

LINKS = (('HelixSR', 'https://github.com/lonewolf0622/HelixSR'),
         ('HelixSR license', 'https://github.com/lonewolf0622/HelixSR/blob/HEAD/LICENSE'),
         ('OptiScaler', 'https://github.com/optiscaler/OptiScaler'),
         ('NVIDIA DLSS license', helixsr.NVIDIA_LICENSE_URL),
         ('AMDLSS', 'https://github.com/0xCydral/AMDLSS'))
FOOTER = ('AMDLSS is unofficial and is not affiliated with HelixSR, NVIDIA or AMD. AMDLSS is free software '
          '(GPL-2.0-or-later); HelixSR is freeware under its own license.')
MAX_LOG_LINES = 3000


def card_of(game):
    """What a game's card shows: the name, what it supports, and the state (or why it is not offered)."""
    supports = game.supports.replace(', ', ' / ') if game.fsr31 or game.inputs else ''
    if game.reason:
        return Card(game.name, supports, game.reason, 'none', True)
    return Card(game.name, supports, game.state_text, 'ok' if game.installed else 'none', False)


def resource(name):
    """A data file shipped with the program: beside AMDLSS.pyw, or inside the PyInstaller bundle."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return base / name


class App:
    def __init__(self, root, tk, ttk, filedialog, messagebox, tkfont):
        self.root, self.tk, self.ttk, self.filedialog, self.messagebox = root, tk, ttk, filedialog, messagebox
        self.look = Look(root, tkfont)
        self.data_dir = settings.data_dir()
        self.extras = settings.load_folders(self.data_dir)
        self.helixsr_folder = None
        self.helixsr = None        # a HelixSR once its network is built
        self.games, self.shown = [], []
        self.failed = {}            # folder added by hand -> why it was not offered, until the next Rescan
        self.busy = False
        self.option_key = None      # (folder, state) of the game the route and frame generation boxes show
        self.route_var = tk.StringVar(value='optiscaler')
        self.fg_var = tk.BooleanVar(value=False)
        self.show_all = tk.BooleanVar(value=False)   # also list the games that are not offered, greyed out
        # a beta user keeps getting betas unless they say no
        self.beta_var = tk.BooleanVar(value=settings.load_beta(self.data_dir, update.is_prerelease(update.current_version())))
        self.update_card = None     # the "AMDLSS x is available" card, once an update was found
        self.log_hidden = False     # the log strip is dropped while the window is short
        self.calls = queue.Queue()  # work for the Tk thread from worker threads
        root.title(f'AMDLSS {VERSION}')
        root.configure(bg=BG)
        px = self.look.px
        root.geometry(f'{px(960)}x{px(760)}')
        root.minsize(px(860), px(640))
        try:
            root.iconbitmap(default=str(resource('amdlss.ico')))
        except tk.TclError:
            pass
        self.style()
        self.build()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Configure>', self.fit_height)
        root.after(50, self.pump)
        root.after(100, self.refresh_helixsr)
        root.after(300, self.start_update_check)

    # ---- look -----------------------------------------------------------------------------------
    def style(self):
        style = self.ttk.Style()
        style.theme_use('clam')
        style.configure('Vertical.TScrollbar', background=LINE, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED,
                        lightcolor=LINE, darkcolor=LINE, gripcount=0)
        style.map('Vertical.TScrollbar', background=[('active', LINE_HI)])

    def label(self, parent, text, font='body', fg=TEXT, bg=BG, **options):
        return self.tk.Label(parent, text=text, font=self.look.fonts[font], fg=fg, bg=bg, **options)

    def card(self, parent, pady=12, **pack):
        """A bordered panel one section tall, packed with a gap below it."""
        px = self.look.px
        frame = self.tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=px(18),
                              pady=px(pady))
        frame.pack(fill='x', pady=(0, px(10)), **pack)
        return frame

    def build(self):
        tk, px = self.tk, self.look.px
        gap = px(10)
        page = self.page = tk.Frame(self.root, bg=BG, padx=px(28), pady=px(16))
        page.pack(fill='both', expand=True)

        head = tk.Frame(page, bg=BG)
        head.pack(fill='x', pady=(0, gap))
        logo(tk, self.look, head).pack(side='left')
        self.label(head, 'AMDLSS', 'h1').pack(side='left', padx=(px(12), 0))
        self.label(head, VERSION, 'body', MUTED).pack(side='left', padx=px(10), pady=(px(10), 0))
        self.label(head, 'HelixSR for your AMD card', 'body', MUTED).pack(side='right', pady=(px(10), 0))

        # HelixSR itself
        status = self.status_card = tk.Frame(page, bg=BG)
        status.pack(fill='x', pady=(0, gap))
        row = tk.Frame(status, bg=BG)
        row.pack(fill='x')
        self.glyph = self.label(row, '•', 'body_bold', DIM)
        self.glyph.pack(side='left')
        self.status = self.label(row, 'Looking for HelixSR…', 'body', TEXT, anchor='w', justify='left')
        self.status.pack(side='left', padx=px(8), fill='x', expand=True)
        self.install_button = FlatButton(tk, self.look, row, 'Install HelixSR', self.install_helixsr, 'primary', bg=BG)
        self.install_button.pack(side='right')
        self.bar = Bar(tk, self.look, status, 400, bg=BG)
        self.progress = self.label(status, '', 'small', MUTED, anchor='w')

        # What stays visible when the window is short comes first: the bottom sections are packed from the bottom up
        # before the game cards, so it is the cards that give up room (and scroll).
        footer = self.label(page, FOOTER, 'small', DIM, anchor='w', justify='left', wraplength=px(780))
        footer.pack(side='bottom', fill='x', pady=(px(2), 0))
        footer.bind('<Configure>', lambda e: footer.configure(wraplength=e.width))
        links = tk.Frame(page, bg=BG)
        links.pack(side='bottom', fill='x', pady=(0, px(6)))
        for title, url in LINKS:
            link = self.label(links, title, 'small', BLOOD_HI, BG, cursor='hand2')
            link.pack(side='left', padx=(0, px(18)))
            link.bind('<Button-1>', lambda _e, url=url: webbrowser.open(url))
            link.bind('<Enter>', lambda _e, link=link: link.configure(fg=TEXT))
            link.bind('<Leave>', lambda _e, link=link: link.configure(fg=BLOOD_HI))
        FlatButton(tk, self.look, links, 'Check for updates', lambda: self.check_updates(True), bg=BG).pack(side='right')
        tk.Checkbutton(links, text='Beta versions', variable=self.beta_var, command=self.beta_changed, bg=BG, fg=TEXT,
                       selectcolor=FIELD, activebackground=BG, activeforeground=TEXT, font=self.look.fonts['small'],
                       bd=0, highlightthickness=0).pack(side='right', padx=(0, px(12)))

        # log
        logs = self.log_card = self.card(page, pady=8, side='bottom')
        self.label(logs, 'Log', 'body_bold', MUTED, CARD).pack(side='left', padx=(0, px(12)))
        FlatButton(tk, self.look, logs, 'Copy log', self.copy_log).pack(side='right', padx=(px(12), 0))
        self.log = tk.Text(logs, height=2, bg=FIELD, fg=MUTED, font=self.look.fonts['mono'], relief='flat', wrap='word',
                           state='disabled', highlightthickness=1, highlightbackground=LINE, padx=px(8), pady=px(4))
        self.log.pack(side='left', fill='x', expand=True)

        # the selected game: name and state, the route and frame generation choices, the actions
        detail = self.detail = self.card(page, side='bottom')
        title = tk.Frame(detail, bg=CARD)
        title.pack(fill='x')
        self.game_title = self.label(title, 'No game selected', 'h2', TEXT, CARD)
        self.game_title.pack(side='left')
        self.game_state = self.label(title, '', 'small', MUTED, CARD)
        self.game_state.pack(side='left', padx=px(12), pady=(px(4), 0))
        # per-game choices: which route (only when both work, on the title row) and frame generation (OptiScaler route)
        self.route_row = tk.Frame(title, bg=CARD)
        options = self.options = tk.Frame(detail, bg=CARD)
        options.pack(fill='x')
        self.label(self.route_row, 'Install as', 'small', MUTED, CARD).pack(side='left', padx=(0, px(12)))
        self.route_buttons = segmented(tk, self.look, self.route_row, (('DLSS (OptiScaler)', 'optiscaler'),
                                                              ('AMD FSR (drop-in)', 'direct')),
                                       self.route_var, self.update_buttons)
        self.fg_row = tk.Frame(options, bg=CARD)
        self.fg_box = tk.Checkbutton(self.fg_row, text=gamelist.FRAMEGEN_LABEL, variable=self.fg_var, bg=CARD, fg=TEXT,
                                     selectcolor=FIELD, activebackground=CARD, activeforeground=TEXT,
                                     disabledforeground=DIM, font=self.look.fonts['small'], anchor='w', bd=0,
                                     highlightthickness=0, command=self.update_buttons)
        self.fg_box.pack(side='left')
        actions = tk.Frame(detail, bg=CARD)
        actions.pack(fill='x', pady=(px(10), 0))
        self.game_install = FlatButton(tk, self.look, actions, 'Install', self.install_game, 'primary', height=40,
                                       wide=True)
        self.game_install.pack(side='left')
        self.game_remove = FlatButton(tk, self.look, actions, 'Remove', self.remove_game, height=40)
        self.game_remove.pack(side='left', padx=px(8))
        self.game_logs = FlatButton(tk, self.look, actions, 'Collect logs', self.collect_logs, height=40)
        self.game_logs.pack(side='left')
        self.game_folder = FlatButton(tk, self.look, actions, 'Open game folder', self.open_folder, height=40)
        self.game_folder.pack(side='left', padx=px(8))
        self.game_forget = FlatButton(tk, self.look, actions, 'Forget folder', self.forget_folder, height=40)
        self.game_forget.pack(side='left')
        hint = tk.Frame(detail, bg=CARD)
        hint.pack(fill='x', pady=(px(8), 0))
        self.game_notes = self.label(hint, '', 'small', CAUTION, CARD)
        self.game_notes.pack(side='right')
        self.hint_lead = self.label(hint, '', 'small', MUTED, CARD)
        self.hint_lead.pack(side='left')
        self.hint = self.label(hint, '', 'small', BLOOD_HI, CARD)
        self.hint.pack(side='left', padx=(px(4), 0))
        self.label(detail, 'Close the game first. No online or anti-cheat games: the account can be banned.',
                   'small', CAUTION, CARD, anchor='w').pack(fill='x', pady=(px(4), 0))

        # games: the cards are the view, the hidden tree is what the window selects in and reads from
        games = tk.Frame(page, bg=BG)
        games.pack(fill='both', expand=True, pady=(0, gap))
        bar = tk.Frame(games, bg=BG)
        bar.pack(fill='x', pady=(0, px(8)))
        self.label(bar, 'Your games', 'h2').pack(side='left')
        self.rescan_button = FlatButton(tk, self.look, bar, 'Rescan', self.rescan, bg=BG)
        self.rescan_button.pack(side='right')
        self.add_button = FlatButton(tk, self.look, bar, 'Add game folder…', self.add_folder, bg=BG)
        self.add_button.pack(side='right', padx=px(8))
        field = tk.Frame(bar, bg=FIELD, highlightthickness=1, highlightbackground=LINE, padx=px(10))
        field.pack(side='right', padx=px(8))
        magnifier(tk, self.look, field, FIELD).pack(side='left')
        self.search = tk.StringVar()
        entry = tk.Entry(field, textvariable=self.search, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief='flat',
                         font=self.look.fonts['body'], width=20, highlightthickness=0)
        entry.pack(side='left', padx=(px(8), 0), ipady=px(7))
        self.placeholder = self.label(field, 'Search games', 'body', DIM, FIELD)
        self.placeholder.place(x=px(36), rely=.5, anchor='w')
        self.placeholder.bind('<Button-1>', lambda _e: entry.focus_set())
        self.search.trace_add('write', lambda *_: self.search_changed())

        columns = ('name', 'supports', 'state', 'notes')
        self.tree = self.ttk.Treeview(games, columns=columns, show='headings', selectmode='browse')
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self.selection_changed())
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label='Collect logs', command=self.collect_logs)
        foot = tk.Frame(games, bg=BG)
        foot.pack(side='bottom', fill='x', pady=(px(6), 0))
        self.empty = self.label(foot, 'Install HelixSR first, then your games show up here.', 'small', MUTED,
                                anchor='w')
        self.empty.pack(side='left')
        tk.Checkbutton(foot, text='Show all games', variable=self.show_all, command=self.fill_tree, bg=BG, fg=TEXT,
                       selectcolor=FIELD, activebackground=BG, activeforeground=TEXT, font=self.look.fonts['small'],
                       bd=0, highlightthickness=0).pack(side='right')
        self.gallery = Gallery(tk, self.ttk, self.look, games, self.pick, lambda _n: self.open_folder(),
                               self.card_menu)
        self.gallery.frame.pack(fill='both', expand=True)

        self.update_buttons()

    # ---- helpers --------------------------------------------------------------------------------
    def post(self, function, *args):
        self.calls.put((function, args))

    def pump(self):
        while True:
            try:
                function, args = self.calls.get_nowait()
            except queue.Empty:
                break
            function(*args)
        self.root.after(50, self.pump)

    def set_status(self, text):
        glyph = text[0] if text[:1] in GLYPH_COLORS else '•'
        self.glyph.configure(text=glyph, fg=GLYPH_COLORS.get(glyph, DIM))
        self.status.configure(text=text[1:].strip() if text[:1] in GLYPH_COLORS else text)

    def log_line(self, text, final=True):
        if not final:  # a progress update (carriage return): shown under the status, not kept in the log
            self.progress.configure(text=text)
            return
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > MAX_LOG_LINES:
            self.log.delete('1.0', f'{lines - MAX_LOG_LINES}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get('1.0', 'end-1c'))

    def pick(self, number):
        """A card was chosen (click or key): the tree holds the selection, the cards show it."""
        self.tree.selection_set(str(number))
        self.selection_changed()

    def selection_changed(self):
        chosen = self.tree.selection()
        self.gallery.select(int(chosen[0]) if chosen else None)
        self.update_buttons()

    def card_menu(self, _number, x, y):
        """Right-click on a card: it is already selected, offer Collect logs."""
        self.menu.entryconfigure(0, state=self.game_logs.state)
        self.menu.tk_popup(x, y)

    def fit_height(self, event):
        """A short window drops the log strip (status line and message boxes still say what happened) so the cards
        keep room."""
        short = event.widget is self.root and event.height < self.look.px(720)
        if event.widget is not self.root or short == self.log_hidden:
            return
        self.log_hidden = short
        if short:
            self.log_card.pack_forget()
        else:
            self.log_card.pack(fill='x', pady=(0, self.look.px(10)), side='bottom', before=self.detail)

    def search_changed(self):
        if self.search.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=self.look.px(36), rely=.5, anchor='w')
        self.fill_tree()

    def selected(self):
        chosen = self.tree.selection()
        return self.shown[int(chosen[0])] if chosen else None

    def update_buttons(self):
        game, idle = self.selected(), not self.busy
        ready = idle and self.helixsr is not None
        self.install_button.configure(state='normal' if idle and self.helixsr is None else 'disabled')
        self.rescan_button.configure(state='normal' if ready else 'disabled')
        self.add_button.configure(state='normal' if ready else 'disabled')
        self.game_install.configure(state='normal' if ready and game and game.can_install else 'disabled',
                                    text='Update' if game and game.installed else 'Install')
        self.game_remove.configure(state='normal' if ready and game and game.installed else 'disabled')
        self.game_folder.configure(state='normal' if game and game.folder and os.path.isdir(game.folder) else 'disabled')
        self.game_logs.configure(state='normal' if idle and game and game.folder and os.path.isdir(game.folder)
                                 else 'disabled')
        self.game_forget.configure(state='normal' if idle and game and game.manual and not game.installed else 'disabled')
        self.update_detail(game)
        self.update_options(game, idle)

    def update_detail(self, game):
        """The panel's heading: the game's name and state, and why to be careful (anti-cheat) or why it is not offered."""
        note = game and (game.reason or (f'anti-cheat: {", ".join(game.anticheat)}' if game.anticheat else ''))
        self.game_title.configure(text=game.name if game else 'No game selected', fg=TEXT if game else MUTED)
        self.game_state.configure(text=game.state_text if game and not game.reason else '')
        self.game_notes.configure(text=note)
        hint = game and game.can_install
        self.hint_lead.configure(text='After install,' if hint else '')
        self.hint.configure(text=f'in the game choose {game.in_game(self.route_of(game))}.' if hint else '')

    def route_of(self, game):
        """The route Install will use for `game`: the installed one, the box's pick, or HelixSR's default."""
        if game.installed:
            return game.state
        return self.route_var.get() if game.route_choice else game.route

    def update_options(self, game, idle):
        """Shows the route choice (both routes work, not installed) and the frame generation box (OptiScaler route)."""
        key = (game.folder, game.state) if game else None
        if key != self.option_key:  # another game or another state: start from its own default
            self.option_key = key
            if game:
                self.route_var.set(game.default_route if game.route_choice else game.route)
                self.fg_var.set(game.installed and framegen.is_on(game.exe_folder) if game.exe_folder else False)
        self.route_row.pack_forget()
        self.fg_row.pack_forget()
        if game and game.route_choice:
            self.route_row.pack(side='right')
        if game and game.exe_folder and self.route_of(game) == 'optiscaler':
            self.fg_row.pack(fill='x', pady=(self.look.px(6), 0))
        for widget in (*self.route_buttons, self.fg_box):
            widget.configure(state='normal' if idle else 'disabled')

    def set_busy(self, busy, progress=None):
        self.busy = busy
        if busy:
            self.bar.pack(fill='x', pady=(self.look.px(10), 0))
            self.progress.pack(fill='x', pady=(self.look.px(4), 0))
            self.bar.set(progress)
        else:
            self.bar.set(0)
            self.bar.pack_forget()
            self.progress.pack_forget()
            self.progress.configure(text='')
        self.update_buttons()

    def fail(self, message):
        self.log_line(f'error: {message}')
        self.set_busy(False)
        self.set_status(f'✗ {message}')
        self.messagebox.showerror('AMDLSS', message)

    def work(self, function, done):
        """Runs function() off the window's thread; done(result) runs on it. A failure goes to fail()."""
        def run():
            try:
                result = function()
            except (helixsr.HelixSRError, OSError) as error:
                self.post(self.fail, str(error))
                return
            self.post(done, result)
        threading.Thread(target=run, daemon=True).start()

    # ---- HelixSR --------------------------------------------------------------------------------
    def refresh_helixsr(self):
        folder = helixsr.locate(self.data_dir, os.environ.get('AMDLSS_HELIXSR', ''))
        self.helixsr_folder, self.helixsr = folder, None
        if folder is None:
            self.set_status('✗ HelixSR is not installed. Install it once, then pick your games.')
            self.install_button.configure(text='Install HelixSR')
        elif not helixsr.built(folder):
            self.set_status(f'⚠ HelixSR {helixsr.version_of(folder) or helixsr.VERSION} is downloaded but its '
                            'network is not built yet.')
            self.install_button.configure(text='Build HelixSR')
        else:
            self.helixsr = helixsr.HelixSR(folder)
            self.set_status(f'✓ HelixSR {self.helixsr.version or "?"} is installed and built ({short_path(folder)})')
            self.install_button.configure(text='Install HelixSR')
            self.refresh_games()
        self.update_buttons()

    def install_helixsr(self):
        consented = self.messagebox.askyesno('Install HelixSR', helixsr.consent_text(), icon='warning', default='no')
        if not consented:
            return
        self.set_busy(True, 0.0)
        self.set_status('• Installing HelixSR…')

        def progress(done, total):
            self.post(self.download_progress, done, total)

        self.work(lambda: helixsr.install_helixsr(self.data_dir, consented, progress, self.on_line),
                  self.helixsr_done)

    def download_progress(self, done, total):
        self.bar.set(done / total)
        self.progress.configure(text=f'Downloading HelixSR: {done >> 20} of {total >> 20} MB')

    def helixsr_done(self, folder):
        self.set_busy(False)
        self.log_line(f'HelixSR installed in {folder}')
        self.refresh_helixsr()

    # ---- games ----------------------------------------------------------------------------------
    def on_line(self, text, final=True):
        self.post(self.log_line, text, final)

    def rescan(self):
        self.failed.clear()
        self.refresh_games()

    def refresh_games(self):
        if self.helixsr is None or self.busy:
            return
        self.set_busy(True, None)
        self.progress.configure(text='Looking for games…')
        extras = list(self.extras)
        # HelixSR climbs four parent folders to look for a game around a folder with nothing in it, which can take
        # minutes on a big drive: a folder that failed is not handed over again until Rescan.
        asked = [extra for extra in extras if extra not in self.failed]

        def scan():
            found = gamelist.classify(self.helixsr.list_games(asked), extras)
            for game in found:
                if game.folder and not game.reason:
                    game.anticheat = gamelist.scan_anticheat(game.folder)
            return found

        self.work(scan, self.games_done)

    def games_done(self, found):
        for extra in self.extras:
            row = next((g for g in found if g.manual and g.folder and gamelist.same_folder(g.folder, extra)), None)
            if row and extra in self.failed:
                row.reason = self.failed[extra]   # not scanned this time: it keeps the reason it had
            elif row and row.reason:
                self.failed[extra] = row.reason
        self.games = found
        self.set_busy(False)
        self.fill_tree()

    def fill_tree(self):
        wanted = self.search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        show_all = self.show_all.get()
        self.shown = [g for g in self.games if gamelist.visible(g, show_all) and wanted in g.name.lower()]
        for number, game in enumerate(self.shown):
            if game.reason:   # not offered: greyed out, with why
                supports = game.supports if game.fsr31 or game.inputs else ''
                values, tags = (game.name, supports, '', game.reason), ('hidden',)
            else:
                notes = f'anti-cheat: {", ".join(game.anticheat)}' if game.anticheat else ''
                tags = ('installed',) if game.installed else ('warn',) if game.anticheat else ()
                values = (game.name, game.supports, game.state_text, notes)
            self.tree.insert('', 'end', iid=str(number), tags=tags, values=values)
        self.gallery.set_items(card_of(game) for game in self.shown)
        if self.helixsr is None:
            self.empty.configure(text='Install HelixSR first, then your games show up here.')
        elif not self.games:
            self.empty.configure(text='No game with DLSS, XeSS or FSR found. Use "Add game folder…".')
        else:
            self.empty.configure(text=gamelist.count_text(self.games))
        self.update_buttons()

    def add_folder(self):
        folder = self.filedialog.askdirectory(title='Pick the game folder')
        if not folder:
            return
        folder = os.path.normpath(folder)
        if folder not in self.extras:
            self.extras.append(folder)
            settings.save_folders(self.data_dir, self.extras)
        self.failed.pop(folder, None)  # picked again: look at it again
        self.refresh_games()

    def forget_folder(self):
        """Drops a folder added by hand from the list and the saved folders (the game's files are not touched)."""
        game = self.selected()
        if not (game and game.manual and not self.busy):
            return
        self.extras = [f for f in self.extras if not gamelist.same_folder(f, game.folder)]
        settings.save_folders(self.data_dir, self.extras)
        self.failed = {f: why for f, why in self.failed.items() if f in self.extras}
        self.refresh_games()

    def open_folder(self):
        game = self.selected()
        if game and game.folder:
            os.startfile(game.folder)

    def install_game(self):
        game = self.selected()
        if not (self.helixsr and game and game.can_install and not self.busy):
            return
        plan = gamelist.plan_text(game, self.route_of(game))
        if not self.messagebox.askyesno(f'Install into {game.name}', plan, icon='warning', default='no'):
            return
        self.set_busy(True, None)
        self.progress.configure(text=f'Installing into {game.name}…')
        self.set_status(f'• Installing HelixSR into {game.name}…')
        extras = list(self.extras)
        route = self.route_var.get() if game.route_choice else None
        frame_gen = self.fg_var.get() if self.route_of(game) == 'optiscaler' and game.exe_folder else None
        record = self.game_log(game, f'{"update" if game.installed else "install"} ({self.route_of(game)})')

        def install():
            try:
                after = self.helixsr.install(game, extras, record, route)
            except helixsr.HelixSRError as error:
                record.fail(error)
                raise
            if after.state == 'optiscaler' and after.exe_folder:
                if frame_gen is not None and framegen.apply(after.exe_folder, frame_gen, self.data_dir):
                    record(f'frame generation {"on" if frame_gen else "off"}: {framegen.ini_path(after.exe_folder)}')
                try:   # so a game that "looks unchanged" leaves OptiScaler.log behind; never a reason to fail
                    if framegen.apply(after.exe_folder, True, self.data_dir, framegen.LOGGING):
                        record(f'OptiScaler logging on: {framegen.ini_path(after.exe_folder)}')
                except (helixsr.HelixSRError, OSError) as error:
                    record(f'OptiScaler logging was not turned on: {error}')
            return after

        self.work(install, lambda after: self.installed(after, bool(frame_gen)))

    def game_log(self, game, action):
        """on_line for one action in `game`: the window's log, and the game's own log file for Collect logs."""
        record = logs.GameLog(self.data_dir, game.folder, self.on_line)
        record.start(f'{action} {game.name}')
        return record

    def installed(self, game, frame_gen=False):
        self.set_busy(False)
        self.refresh_helixsr_status()
        self.messagebox.showinfo('AMDLSS', gamelist.after_text(game, frame_gen))
        self.refresh_games()

    def remove_game(self):
        game = self.selected()
        if not (self.helixsr and game and game.installed and not self.busy):
            return
        if not self.messagebox.askyesno(f'Remove from {game.name}',
                                        f'Close {game.name} first.\n\nHelixSR puts the game\'s original files back. '
                                        'Remove it?', icon='warning', default='no'):
            return
        self.set_busy(True, None)
        self.progress.configure(text=f'Removing from {game.name}…')
        extras = list(self.extras)
        record = self.game_log(game, 'remove')

        def remove():
            try:
                after = self.helixsr.remove(game, extras, record)
            except helixsr.HelixSRError as error:
                record.fail(error)
                raise
            if game.exe_folder:
                framegen.forget(game.exe_folder, self.data_dir)
            return after

        self.work(remove, self.removed)

    def removed(self, game):
        self.set_busy(False)
        self.refresh_helixsr_status()
        self.messagebox.showinfo('AMDLSS', f'HelixSR is removed from {game.name}. The original files are back.')
        self.refresh_games()

    def collect_logs(self):
        game = self.selected()
        if not (game and game.folder and os.path.isdir(game.folder) and not self.busy):
            return
        if not self.messagebox.askyesno(f'Collect logs for {game.name}', logs.confirm_text(game, self.data_dir)):
            return
        self.set_busy(True, None)
        self.progress.configure(text=f'Collecting logs for {game.name}…')
        self.work(lambda: logs.collect(game, self.helixsr_folder, self.data_dir), self.logs_done)

    def logs_done(self, path):
        self.set_busy(False)
        self.log_line(f'logs collected: {path}')
        logs.reveal(path, self.log_line)

    def refresh_helixsr_status(self):
        if self.helixsr:
            self.set_status(f'✓ HelixSR {self.helixsr.version or "?"} is installed and built '
                            f'({short_path(self.helixsr_folder)})')

    # ---- updates --------------------------------------------------------------------------------
    def start_update_check(self):
        if not os.environ.get('AMDLSS_NO_UPDATE_CHECK'):
            self.check_updates(False)

    def beta_changed(self):
        settings.save_beta(self.data_dir, self.beta_var.get())

    def check_updates(self, manual):
        """Asks GitHub off the window's thread. A failure is one log line (and a box when the user asked)."""
        beta = bool(self.beta_var.get())

        def run():
            try:
                release = update.fetch_release(beta)
            except update.UpdateError as error:
                self.post(self.update_check_failed, str(error), manual)
                return
            self.post(self.update_checked, release, manual)

        threading.Thread(target=run, daemon=True).start()

    def update_check_failed(self, message, manual):
        self.log_line(f'update check failed: {message}')
        if manual:
            self.messagebox.showerror('AMDLSS', f'Could not check for updates: {message}')

    def update_checked(self, release, manual):
        found = update.newer(release)
        if found:
            self.offer_update(found)
        elif manual:
            self.messagebox.showinfo('AMDLSS', f'You have the latest version ({update.current_version()}).')

    def offer_update(self, release):
        """A card above the HelixSR status: the new version, Update (an installed exe only) and the release notes."""
        if self.update_card:
            self.update_card.destroy()
        tk, px = self.tk, self.look.px
        fill = mix(CARD, BLOOD, .13)
        card = self.update_card = tk.Frame(self.page, bg=fill, highlightthickness=1,
                                           highlightbackground=mix(LINE, BLOOD, .45))
        card.pack(fill='x', pady=(0, px(10)), before=self.status_card)
        tk.Frame(card, bg=BLOOD, width=px(4)).pack(side='left', fill='y')
        body = tk.Frame(card, bg=fill, padx=px(16), pady=px(6))
        body.pack(side='left', fill='x', expand=True)
        self.update_label = self.label(body, f'AMDLSS {release.version} is available.', 'body_bold', TEXT, fill)
        self.update_label.pack(side='left')
        notes = FlatButton(tk, self.look, body, 'Release notes', lambda: webbrowser.open(release.page), bg=fill)
        notes.pack(side='right')
        if update.FROZEN:
            self.update_button = FlatButton(tk, self.look, body, 'Update', lambda: self.install_update(release),
                                            'primary', bg=fill)
            self.update_button.pack(side='right', padx=px(8))
            self.update_bar = Bar(tk, self.look, body, 120, bg=fill)

    def install_update(self, release):
        if self.busy:
            self.messagebox.showinfo('AMDLSS', 'Wait until HelixSR is done, then update.')
            return
        if not self.messagebox.askyesno(f'Update to {release.version}', f'Install AMDLSS {release.version} now? AMDLSS '
                                        'closes, installs it and opens again. Your games and settings are kept.'):
            return
        self.busy = True   # no HelixSR work meanwhile
        self.update_buttons()
        self.update_button.configure(state='disabled')
        self.update_bar.pack(side='right', padx=(0, self.look.px(12)))
        exe = update.exe_path()

        def progress(done, total):
            self.post(self.update_progress, release, done / total)

        def run():
            try:
                update.apply(release, exe, progress)
            except update.UpdateError as error:
                self.post(self.update_failed, release, str(error))
                return
            self.post(self.update_installed, exe)

        threading.Thread(target=run, daemon=True).start()

    def update_progress(self, release, fraction):
        self.update_label.configure(text=f'Downloading AMDLSS {release.version}… {int(fraction * 100)}%')
        self.update_bar.set(fraction)

    def update_failed(self, release, message):
        self.busy = False
        self.update_buttons()
        self.update_button.configure(state='normal')
        self.update_bar.pack_forget()
        self.update_label.configure(text=f'AMDLSS {release.version} is available.')
        self.log_line(f'update failed: {message}')
        self.messagebox.showerror('AMDLSS', f'Update failed: {message}')

    def update_installed(self, exe):
        try:
            update.restart(exe, sys.argv[1:])
        except OSError as error:
            self.busy = False
            self.messagebox.showinfo('AMDLSS', f'AMDLSS is updated but did not start again ({error}). Start it yourself.')
        self.root.destroy()

    def close(self):
        if self.busy and not self.messagebox.askyesno(
                'AMDLSS', 'HelixSR is still working. Closing now can leave a half-finished install. Close anyway?',
                icon='warning', default='no'):
            return
        self.root.destroy()


def main(argv):
    if '--selftest' in argv and '--update' not in argv:
        from .selftest import run
        return run()
    if '--update' in argv:   # the update without a window; the arguments after it go to the new exe
        beta = settings.load_beta(settings.data_dir(), update.is_prerelease(update.current_version()))
        return update.command(argv, beta)
    if update.FROZEN:   # what the last update left beside the exe; the old exe may still be closing
        threading.Thread(target=update.cleanup_old, args=(update.exe_path(),), daemon=True).start()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
    except (AttributeError, OSError):
        pass
    import tkinter as tk
    from tkinter import filedialog, font, messagebox, ttk
    root = tk.Tk()
    App(root, tk, ttk, filedialog, messagebox, font)
    root.mainloop()
    return 0
