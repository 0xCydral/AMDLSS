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

from . import VERSION, framegen, games as gamelist, helixsr, logs, settings
from .ui import (BAD, BG, CAUTION, CARD, DIM, FIELD, GLYPH_COLORS, GOLD, LINE, MUTED, OK, TEXT, Bar, FlatButton,
                 Look, mix, short_path)

LINKS = (('HelixSR', 'https://github.com/lonewolf0622/HelixSR'),
         ('HelixSR license', 'https://github.com/lonewolf0622/HelixSR/blob/HEAD/LICENSE'),
         ('OptiScaler', 'https://github.com/optiscaler/OptiScaler'),
         ('NVIDIA DLSS license', helixsr.NVIDIA_LICENSE_URL),
         ('AMDLSS', 'https://github.com/0xCydral/AMDLSS'))
FOOTER = ('AMDLSS is unofficial and is not affiliated with HelixSR, NVIDIA or AMD. AMDLSS is free software '
          '(GPL-2.0-or-later); HelixSR is freeware under its own license.')
MAX_LOG_LINES = 3000


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
        root.after(50, self.pump)
        root.after(100, self.refresh_helixsr)

    # ---- look -----------------------------------------------------------------------------------
    def style(self):
        style, px, fonts = self.ttk.Style(), self.look.px, self.look.fonts
        style.theme_use('clam')
        style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=TEXT, borderwidth=0,
                        rowheight=px(28), font=fonts['body'])
        style.configure('Treeview.Heading', background=CARD, foreground=MUTED, borderwidth=0, relief='flat',
                        font=fonts['small'])
        style.map('Treeview', background=[('selected', mix(FIELD, GOLD, .22))], foreground=[('selected', TEXT)])
        style.map('Treeview.Heading', background=[('active', CARD)])
        style.configure('Vertical.TScrollbar', background=CARD, troughcolor=FIELD, bordercolor=FIELD,
                        arrowcolor=MUTED, lightcolor=CARD, darkcolor=CARD)

    def label(self, parent, text, font='body', fg=TEXT, bg=BG, **options):
        return self.tk.Label(parent, text=text, font=self.look.fonts[font], fg=fg, bg=bg, **options)

    def choice(self, parent, text, value):
        button = self.tk.Radiobutton(parent, text=text, value=value, variable=self.route_var, bg=CARD, fg=TEXT,
                                     selectcolor=FIELD, activebackground=CARD, activeforeground=TEXT,
                                     disabledforeground=DIM, font=self.look.fonts['small'], bd=0, highlightthickness=0,
                                     command=self.update_buttons)
        button.pack(side='left', padx=(self.look.px(10), 0))
        return button

    def card(self, parent, **pack):
        frame = self.tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE,
                              padx=self.look.px(14), pady=self.look.px(12))
        frame.pack(fill='x', pady=(0, self.look.px(10)), **pack)
        return frame

    def build(self):
        tk, px = self.tk, self.look.px
        page = tk.Frame(self.root, bg=BG, padx=px(22), pady=px(16))
        page.pack(fill='both', expand=True)

        head = tk.Frame(page, bg=BG)
        head.pack(fill='x', pady=(0, px(10)))
        self.label(head, 'AMDLSS', 'h1').pack(side='left')
        self.label(head, 'HelixSR for DLSS games on AMD cards, one click per game', 'body', MUTED).pack(
            side='left', padx=px(14), pady=(px(8), 0))

        # HelixSR itself
        status = self.card(page)
        row = tk.Frame(status, bg=CARD)
        row.pack(fill='x')
        self.glyph = self.label(row, '•', 'body_bold', DIM, CARD)
        self.glyph.pack(side='left')
        self.status = self.label(row, 'Looking for HelixSR…', 'body', TEXT, CARD, anchor='w', justify='left')
        self.status.pack(side='left', padx=px(8), fill='x', expand=True)
        self.install_button = FlatButton(tk, self.look, row, 'Install HelixSR', self.install_helixsr, 'primary')
        self.install_button.pack(side='right')
        self.bar = Bar(tk, self.look, status, 400)
        self.progress = self.label(status, '', 'small', MUTED, CARD, anchor='w')

        # games
        games = self.card(page, expand=True)
        games.pack_configure(fill='both')
        bar = tk.Frame(games, bg=CARD)
        bar.pack(fill='x', pady=(0, px(8)))
        self.label(bar, 'Your games', 'h2', TEXT, CARD).pack(side='left')
        tk.Checkbutton(bar, text='Show all games', variable=self.show_all, command=self.fill_tree, bg=CARD, fg=TEXT,
                       selectcolor=FIELD, activebackground=CARD, activeforeground=TEXT, font=self.look.fonts['small'],
                       bd=0, highlightthickness=0).pack(side='left', padx=(px(14), 0), pady=(px(4), 0))
        self.rescan_button = FlatButton(tk, self.look, bar, 'Rescan', self.rescan)
        self.rescan_button.pack(side='right')
        self.add_button = FlatButton(tk, self.look, bar, 'Add game folder…', self.add_folder)
        self.add_button.pack(side='right', padx=px(8))
        self.search = tk.StringVar()
        self.search.trace_add('write', lambda *_: self.fill_tree())
        tk.Entry(bar, textvariable=self.search, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief='flat',
                 font=self.look.fonts['body'], width=22, highlightthickness=1, highlightbackground=LINE,
                 highlightcolor=MUTED).pack(side='right', padx=px(8), ipady=px(5))
        self.label(bar, 'Search', 'small', MUTED, CARD).pack(side='right')

        table = tk.Frame(games, bg=CARD)
        table.pack(fill='both', expand=True)
        columns = ('name', 'supports', 'state', 'notes')
        self.tree = self.ttk.Treeview(table, columns=columns, show='headings', selectmode='browse', height=9)
        for key, title, width in (('name', 'Game', 300), ('supports', 'Supports', 160), ('state', 'State', 170),
                                  ('notes', 'Notes', 200)):
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=px(width), anchor='w', stretch=key in ('name', 'notes'))
        self.tree.tag_configure('installed', foreground=OK)
        self.tree.tag_configure('warn', foreground=CAUTION)
        self.tree.tag_configure('hidden', foreground=DIM)
        scroll = self.ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self.update_buttons())
        self.tree.bind('<Double-1>', lambda _e: self.open_folder())
        self.menu = tk.Menu(self.tree, tearoff=0)
        self.menu.add_command(label='Collect logs', command=self.collect_logs)
        self.tree.bind('<Button-3>', self.row_menu)
        self.empty = self.label(games, 'Install HelixSR first, then your games show up here.', 'small', MUTED, CARD,
                                anchor='w')
        self.empty.pack(fill='x', pady=(px(6), 0))

        actions = tk.Frame(games, bg=CARD)
        actions.pack(fill='x', pady=(px(10), 0))
        self.game_install = FlatButton(tk, self.look, actions, 'Install', self.install_game, 'primary')
        self.game_install.pack(side='left')
        self.game_remove = FlatButton(tk, self.look, actions, 'Remove', self.remove_game)
        self.game_remove.pack(side='left', padx=px(8))
        self.game_folder = FlatButton(tk, self.look, actions, 'Open game folder', self.open_folder)
        self.game_folder.pack(side='left')
        self.game_forget = FlatButton(tk, self.look, actions, 'Forget folder', self.forget_folder)
        self.game_forget.pack(side='left', padx=px(8))
        self.game_logs = FlatButton(tk, self.look, actions, 'Collect logs', self.collect_logs)
        self.game_logs.pack(side='left')
        self.label(games, 'Close the game first. No online or anti-cheat games: the account can be banned.',
                   'small', CAUTION, CARD, anchor='w').pack(fill='x', pady=(px(6), 0))

        # per-game choices: which route (only when both work) and frame generation (OptiScaler route only)
        options = tk.Frame(games, bg=CARD)
        options.pack(fill='x')
        self.route_row = tk.Frame(options, bg=CARD)
        self.label(self.route_row, 'Install as', 'small', MUTED, CARD).pack(side='left')
        self.route_buttons = [self.choice(self.route_row, text, value) for text, value in
                              (('DLSS (OptiScaler)', 'optiscaler'), ('AMD FSR (drop-in)', 'direct'))]
        self.fg_row = tk.Frame(options, bg=CARD)
        self.fg_box = tk.Checkbutton(self.fg_row, text=gamelist.FRAMEGEN_LABEL, variable=self.fg_var, bg=CARD, fg=TEXT,
                                     selectcolor=FIELD, activebackground=CARD, activeforeground=TEXT,
                                     disabledforeground=DIM, font=self.look.fonts['small'], anchor='w', bd=0,
                                     highlightthickness=0, command=self.update_buttons)
        self.fg_box.pack(side='left')
        self.options = options

        # log
        logs = self.card(page)
        head = tk.Frame(logs, bg=CARD)
        head.pack(fill='x', pady=(0, px(6)))
        self.label(head, 'Log', 'body_bold', MUTED, CARD).pack(side='left')
        FlatButton(tk, self.look, head, 'Copy log', self.copy_log).pack(side='right')
        self.log = tk.Text(logs, height=6, bg=FIELD, fg=MUTED, font=self.look.fonts['mono'], relief='flat', wrap='word',
                           state='disabled', highlightthickness=1, highlightbackground=LINE, padx=px(8), pady=px(6))
        self.log.pack(fill='x')

        # links and footer
        links = tk.Frame(page, bg=BG)
        links.pack(fill='x')
        for title, url in LINKS:
            link = self.label(links, title, 'small', GOLD, BG, cursor='hand2')
            link.pack(side='left', padx=(0, px(16)))
            link.bind('<Button-1>', lambda _e, url=url: webbrowser.open(url))
            link.bind('<Enter>', lambda _e, link=link: link.configure(fg=TEXT))
            link.bind('<Leave>', lambda _e, link=link: link.configure(fg=GOLD))
        self.label(page, FOOTER, 'small', DIM, BG, anchor='w', justify='left', wraplength=px(900)).pack(
            fill='x', pady=(px(8), 0))
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

    def row_menu(self, event):
        """Right-click on a game row: select it and offer Collect logs."""
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.update_buttons()
            self.menu.entryconfigure(0, state=self.game_logs.state)
            self.menu.tk_popup(event.x_root, event.y_root)

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
        self.update_options(game, idle)

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
            self.route_row.pack(fill='x', pady=(self.look.px(8), 0))
        if game and game.exe_folder and self.route_of(game) == 'optiscaler':
            self.fg_row.pack(fill='x', pady=(self.look.px(4), 0))
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

    def close(self):
        if self.busy and not self.messagebox.askyesno(
                'AMDLSS', 'HelixSR is still working. Closing now can leave a half-finished install. Close anyway?',
                icon='warning', default='no'):
            return
        self.root.destroy()


def main(argv):
    if '--selftest' in argv:
        from .selftest import run
        return run()
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
