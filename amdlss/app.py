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

from . import VERSION, games as gamelist, helixsr, settings
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
        self.busy = False
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
        self.rescan_button = FlatButton(tk, self.look, bar, 'Rescan', self.refresh_games)
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
        scroll = self.ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self.update_buttons())
        self.tree.bind('<Double-1>', lambda _e: self.open_folder())
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
        self.label(actions, 'Close the game first. No online or anti-cheat games: the account can be banned.',
                   'small', CAUTION, CARD).pack(side='right')

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
        self.game_folder.configure(state='normal' if game and game.folder else 'disabled')

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

    def refresh_games(self):
        if self.helixsr is None or self.busy:
            return
        self.set_busy(True, None)
        self.progress.configure(text='Looking for games…')
        found_with = list(self.extras)

        def scan():
            found = self.helixsr.list_games(found_with)
            for game in found:
                if game.folder:
                    game.anticheat = gamelist.scan_anticheat(game.folder)
            return found

        self.work(scan, self.games_done)

    def games_done(self, found):
        self.games = found
        self.set_busy(False)
        self.fill_tree()
        # A folder added by hand that HelixSR found no game in is dropped, with a message.
        missing = [f for f in self.extras if not any(g.folder and gamelist.overlaps(g.folder, f) for g in found)]
        if missing:
            self.extras = [f for f in self.extras if f not in missing]
            settings.save_folders(self.data_dir, self.extras)
            self.messagebox.showinfo('AMDLSS', 'HelixSR found no DLSS, XeSS or FSR files in:\n' + '\n'.join(missing))

    def fill_tree(self):
        wanted = self.search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        self.shown = [g for g in self.games if wanted in g.name.lower()]
        for number, game in enumerate(self.shown):
            notes = f'anti-cheat: {", ".join(game.anticheat)}' if game.anticheat else ''
            tags = ('installed',) if game.installed else ('warn',) if game.anticheat else ()
            self.tree.insert('', 'end', iid=str(number), tags=tags,
                             values=(game.name, game.supports, game.state_text, notes))
        if self.helixsr is None:
            self.empty.configure(text='Install HelixSR first, then your games show up here.')
        elif not self.games:
            self.empty.configure(text='No game with DLSS, XeSS or FSR found. Use "Add game folder…".')
        else:
            self.empty.configure(text=f'{len(self.shown)} of {len(self.games)} games')
        self.update_buttons()

    def add_folder(self):
        folder = self.filedialog.askdirectory(title='Pick the game folder')
        if not folder:
            return
        folder = os.path.normpath(folder)
        if folder not in self.extras:
            self.extras.append(folder)
            settings.save_folders(self.data_dir, self.extras)
        self.refresh_games()

    def open_folder(self):
        game = self.selected()
        if game and game.folder:
            os.startfile(game.folder)

    def install_game(self):
        game = self.selected()
        if not (self.helixsr and game and game.can_install and not self.busy):
            return
        if not self.messagebox.askyesno(f'Install into {game.name}', gamelist.plan_text(game), icon='warning',
                                        default='no'):
            return
        self.set_busy(True, None)
        self.progress.configure(text=f'Installing into {game.name}…')
        self.set_status(f'• Installing HelixSR into {game.name}…')
        extras = list(self.extras)
        self.work(lambda: self.helixsr.install(game, extras, self.on_line), self.installed)

    def installed(self, game):
        self.set_busy(False)
        self.refresh_helixsr_status()
        self.messagebox.showinfo('AMDLSS', gamelist.after_text(game))
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
        self.work(lambda: self.helixsr.remove(game, extras, self.on_line), self.removed)

    def removed(self, game):
        self.set_busy(False)
        self.refresh_helixsr_status()
        self.messagebox.showinfo('AMDLSS', f'HelixSR is removed from {game.name}. The original files are back.')
        self.refresh_games()

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
