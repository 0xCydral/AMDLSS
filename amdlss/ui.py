# SPDX-License-Identifier: GPL-2.0-or-later
"""The look of the window and the two hand-drawn controls it needs.

Palette, mix() and short_path() are copied from launcher/bbport_launcher_win.py of the Bloodborne port
(https://github.com/0xCydral/bloodborne_pc, GPL-2.0-or-later) so the two programs look alike; FlatButton and Bar
are cut-down versions of its widgets of the same names, without the animation layer.
"""
import os

BG, PANEL, CARD, FIELD = '#0b0a0c', '#0f0d0f', '#171315', '#0d0b0c'
LINE, LINE_HI = '#2a2224', '#4a3a36'
TEXT, MUTED, DIM, GOLD = '#e8dfcf', '#a09282', '#6b6054', '#c8a96a'
BLOOD, BLOOD_HI, BLOOD_DEEP = '#8e1a1f', '#b8282e', '#5c1014'
OK, CAUTION, BAD = '#8fae7e', '#d9a441', '#d9695c'
GLYPH_COLORS = {'✓': OK, '⚠': CAUTION, '✗': BAD, '•': DIM}


def mix(a, b, t):
    """Colour a moved a fraction t towards colour b ('#rrggbb')."""
    t = max(0.0, min(1.0, t))
    return '#%02x%02x%02x' % tuple(round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * t)
                                   for i in (1, 3, 5))


def short_path(path, limit=44):
    """A path for display: %LOCALAPPDATA% & co. for the usual roots, the middle cut out when still long."""
    text = str(path)
    for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE'):
        base = os.environ.get(name)
        if base and text.lower().startswith(base.lower()):
            text = f'%{name}%' + text[len(base):]
            break
    parts = text.split('\\')
    if len(text) > limit and len(parts) > 3:
        text = '\\'.join([parts[0], '…', *parts[-2:]])
    return text


class Look:
    """Scale and fonts shared by every control: px() scales by the display's DPI."""

    def __init__(self, root, tkfont):
        self.dpi = root.winfo_fpixels('1i') / 96.0
        families = set(tkfont.families(root))
        sans = 'Segoe UI' if 'Segoe UI' in families else 'Arial'
        serif = next((f for f in ('Cinzel', 'Palatino Linotype', 'Georgia') if f in families), 'Times New Roman')
        self.fonts = {'body': (sans, 10), 'body_bold': (sans, 10, 'bold'), 'small': (sans, 9),
                      'h1': (serif, 20), 'h2': (serif, 13), 'mono': ('Consolas', 9)}
        self.tkfont = tkfont

    def px(self, size):
        return int(size * self.dpi)

    def measure(self, spec, text):
        return self.tkfont.Font(font=spec).measure(text)


class FlatButton:
    """A flat button: kind 'ghost' (quiet) or 'primary' (accent). Geometry methods go to the canvas."""

    def __init__(self, tk, look, parent, text, command, kind='ghost', bg=CARD):
        self.look, self.text, self.command, self.kind, self.bg = look, text, command, kind, bg
        self.state, self.hover, self.pressed = 'normal', 0.0, False
        self.font = look.fonts['body']
        self.width, self.height = look.measure(self.font, text) + look.px(32), look.px(34)
        self.canvas = tk.Canvas(parent, width=self.width, height=self.height, bg=bg, highlightthickness=0, bd=0,
                                cursor='hand2', takefocus=True)
        self.rect = self.canvas.create_rectangle(0, 0, self.width - 1, self.height - 1, width=1)
        self.label = self.canvas.create_text(self.width // 2, self.height // 2, text=text, font=self.font)
        c = self.canvas
        c.bind('<Enter>', lambda _e: self.set_hover(1.0))
        c.bind('<Leave>', lambda _e: self.leave())
        c.bind('<ButtonPress-1>', self.press)
        c.bind('<ButtonRelease-1>', self.release)
        c.bind('<Return>', lambda _e: self.invoke())
        c.bind('<space>', lambda _e: self.invoke())
        self.paint()

    def __getattr__(self, name):
        if name == 'canvas':
            raise AttributeError(name)
        return getattr(self.canvas, name)

    def set_hover(self, value):
        self.hover = value if self.state == 'normal' else 0.0
        self.paint()

    def leave(self):
        self.pressed = False
        self.set_hover(0.0)

    def press(self, _event):
        if self.state == 'normal':
            self.pressed = True
            self.canvas.focus_set()
            self.paint()

    def release(self, event):
        was, self.pressed = self.pressed, False
        self.paint()
        if was and 0 <= event.x < self.canvas.winfo_width() and 0 <= event.y < self.canvas.winfo_height():
            self.invoke()

    def invoke(self):
        if self.state == 'normal':
            self.command()

    def configure(self, **options):
        if 'state' in options:
            self.state = options['state']
            self.canvas.configure(cursor='hand2' if self.state == 'normal' else 'arrow')
            self.hover = 0.0
        if 'text' in options:
            self.text = options['text']
            self.canvas.itemconfigure(self.label, text=self.text)
        self.paint()

    def paint(self):
        t, bg = self.hover, self.bg
        if self.state != 'normal':
            fill, line, ink = bg, mix(bg, TEXT, .08), DIM
        elif self.kind == 'primary':
            fill, line, ink = mix(BLOOD_DEEP, BLOOD, t), mix(BLOOD, BLOOD_HI, t), TEXT
        else:
            fill = mix(mix(bg, TEXT, .05), mix(bg, TEXT, .13), t)
            line, ink = mix(mix(bg, TEXT, .14), mix(bg, TEXT, .34), t), TEXT
        if self.pressed:
            fill = mix(fill, BG, .35)
        self.canvas.itemconfigure(self.rect, fill=fill, outline=line)
        self.canvas.itemconfigure(self.label, fill=ink)


class Bar:
    """A progress bar: set(fraction) fills it; set(None) shows a light band running across (unknown length)."""

    def __init__(self, tk, look, parent, width=280, bg=CARD):
        self.look, self.w, self.h = look, look.px(width), look.px(8)
        self.canvas = tk.Canvas(parent, width=self.w, height=self.h, bg=bg, highlightthickness=0, bd=0)
        self.canvas.create_rectangle(0, 0, self.w - 1, self.h - 1, fill=FIELD, outline=LINE)
        self.fill = self.canvas.create_rectangle(1, 1, 1, self.h - 2, fill=BLOOD_HI, outline='')
        self.job, self.phase = None, 0

    def __getattr__(self, name):
        if name == 'canvas':
            raise AttributeError(name)
        return getattr(self.canvas, name)

    def set(self, fraction):
        if self.job:
            self.canvas.after_cancel(self.job)
            self.job = None
        if fraction is None:
            self.sweep()
        else:
            fraction = max(0.0, min(1.0, fraction))
            self.canvas.coords(self.fill, 1, 1, 1 + fraction * (self.w - 3), self.h - 2)

    def sweep(self):
        band = self.w // 4
        x = self.phase % (self.w + band) - band
        self.canvas.coords(self.fill, max(1, x), 1, min(self.w - 2, x + band), self.h - 2)
        self.phase += self.look.px(12)
        self.job = self.canvas.after(40, self.sweep)
