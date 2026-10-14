# SPDX-License-Identifier: GPL-2.0-or-later
"""The look of the window and the hand-drawn controls it needs: flat buttons, a segmented toggle, a progress bar
and the game gallery (cards drawn on a Canvas: the game's cover when it has one, else a tint and a stripe pattern
derived from the name).

mix() and short_path() are copied from launcher/bbport_launcher_win.py of the Bloodborne port
(https://github.com/0xCydral/bloodborne_pc, GPL-2.0-or-later); FlatButton and Bar are cut-down versions of its
widgets of the same names, without the animation layer.
"""
import colorsys
import os
import re
import sys
import zlib
from collections import namedtuple
from pathlib import Path

from . import host

try:   # without Pillow the cards keep their tint: covers are JPEG and Tk cannot read those
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None
COVER_ART = Image is not None

BG,PANEL, CARD, FIELD = '#101115', '#17181d', '#1c1d23', '#14151a'
LINE, LINE_HI = '#34353f', '#4b4d5a'
TEXT, MUTED, DIM = '#f4f4f5', '#aeb0bd', '#6f7180'
BLOOD, BLOOD_HI, BLOOD_DEEP = '#df3545', '#ee5562', '#6b1d27'
OK, CAUTION, BAD = '#65d6a0', '#efc577', '#e5736a'
GLYPH_COLORS = {'✓': OK, '⚠': CAUTION, '✗': BAD, '•': DIM}
COLUMNS = 4
TINT_HUES = (352, 18, 42, 185, 215, 265)   # crimson, rust, ochre, teal, steel, violet: nothing off the palette


def mix(a, b, t):
    """Colour a moved a fraction t towards colour b ('#rrggbb')."""
    t = max(0.0, min(1.0, t))
    return '#%02x%02x%02x' % tuple(round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * t)
                                   for i in (1, 3, 5))


def short_path(path, limit=44, windows=None):
    """A path for display: %LOCALAPPDATA% & co. (or ~) for the usual roots, the middle cut out when still long."""
    text = str(path)
    if host.is_windows(windows):
        for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE'):
            base = os.environ.get(name)
            if base and text.lower().startswith(base.lower()):
                text = f'%{name}%' + text[len(base):]
                break
        sep = '\\'
    else:
        home = str(Path.home())
        if text == home or text.startswith(home + '/'):
            text = '~' + text[len(home):]
        sep = '/'
    parts = text.split(sep)
    if len(text) > limit and len(parts) > 3:
        text = sep.join([parts[0], '…', *parts[-2:]])
    return text


def monogram(name):
    """The short mark on a card: 'Dragon's Dogma 2' -> DD2, 'Final Fantasy XVI' -> XVI, 'Cyberpunk 2077' -> 2077."""
    words = re.findall(r'[A-Za-z0-9]+', name.replace("'", '').replace('’', ''))
    year = next((w for w in words if w.isdigit() and len(w) >= 4), None)
    if year:
        return year
    if len(words) > 1 and re.fullmatch(r'[IVXLC]{2,}', words[-1]):
        return words[-1]
    initials = ''.join(w if w.isdigit() else w[0].upper() for w in words if w.lower() not in ('the', 'a', 'an', 'of'))
    return initials[:4] or '?'


def tint(name):
    """The colour a game's card art starts from: a muted hue picked by the name, the same every run."""
    hue = TINT_HUES[zlib.crc32(name.encode('utf-8')) % len(TINT_HUES)] / 360
    return '#%02x%02x%02x' % tuple(round(v * 255) for v in colorsys.hsv_to_rgb(hue, .4, .46))


def cover_art(path, w, h, r):
    """A cover JPEG as a w x h card background (a PIL image): centre-cropped to the card's aspect, a dark scrim rising
    from the bottom so the title stays readable on bright art, the corners rounded to `r` over the window colour."""
    with Image.open(path) as source:
        art = source.convert('RGB')
    scale = max(w / art.width, h / art.height)
    art = art.resize((max(w, round(art.width * scale)), max(h, round(art.height * scale))), Image.LANCZOS)
    left, top = (art.width - w) // 2, (art.height - h) // 2
    art = art.crop((left, top, left + w, top + h))
    scrim = Image.new('L', (w, h))
    ramp = ImageDraw.Draw(scrim)
    for y in range(h):
        level = .15 + .79 * min(1.0, max(0.0, (y / h - .1) / .7)) ** .7
        ramp.line([(0, y), (w, y)], fill=round(255 * level))
    art = Image.composite(Image.new('RGB', (w, h), '#0b0b0e'), art, scrim)
    corners = Image.new('L', (w, h), 0)
    ImageDraw.Draw(corners).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
    return Image.composite(art, Image.new('RGB', (w, h), BG), corners)


def round_rect(canvas, x0, y0, x1, y1, r, **options):
    """A rounded rectangle (a smoothed polygon); `options` go to create_polygon."""
    points = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
              x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(points, smooth=True, **options)


# Font families in order of preference, per system: (sans, mono). The Tk of the Linux binary draws with the X server's
# core fonts (no Xft), so the Linux lists end in the Type 1 families that ship with the X fonts; a family that is not
# installed makes Tk pick a stand-in whose missing glyphs show up as stray symbols (a "®" for a "✗").
FONT_CHOICES = {
    True: (('Segoe UI', 'Arial', 'DejaVu Sans', 'Liberation Sans'), ('Consolas', 'DejaVu Sans Mono', 'Liberation Mono')),
    False: (('Inter', 'Noto Sans', 'Cantarell', 'Ubuntu', 'DejaVu Sans', 'Liberation Sans', 'Nimbus Sans L',
             'Helvetica'),
            ('DejaVu Sans Mono', 'Noto Sans Mono', 'Liberation Mono', 'Nimbus Mono L', 'Courier'))}
# The core fonts hold ✓ ✗ ⚠ • … at some sizes and weights only, and Tk fills the rest from whatever font it loaded
# last (a "®" for a "✗", "®ƒ" for a "…"): on Linux the UI text uses ASCII for them.
ASCII = str.maketrans({'✓': '+', '⚠': '!', '✗': 'x', '•': '-', '…': '...'})


def plain(text, windows=None):
    """`text` as the window draws it: unchanged on Windows, ✓ ✗ ⚠ • … as ASCII on Linux."""
    return text if host.is_windows(windows) else text.translate(ASCII)


def pick_fonts(families, default_sans, default_mono, windows=None):
    """(sans, mono): the first preferred family that is installed (compared without case: the core fonts are
    lower case), else the system's own default families (on Windows the names the window always used)."""
    installed = {f.lower(): f for f in families}
    sans_choices, mono_choices = FONT_CHOICES[host.is_windows(windows)]
    sans = next((installed[f.lower()] for f in sans_choices if f.lower() in installed), None)
    mono = next((installed[f.lower()] for f in mono_choices if f.lower() in installed), None)
    return sans or default_sans, mono or default_mono


class Look:
    """Scale and fonts shared by every control: px() scales by the display's DPI."""

    def __init__(self, root, tkfont):
        self.dpi = root.winfo_fpixels('1i') / 96.0
        if host.WINDOWS:
            default_sans, default_mono = 'Arial', 'Consolas'
        else:
            default_sans = tkfont.nametofont('TkDefaultFont', root=root).actual('family')
            default_mono = tkfont.nametofont('TkFixedFont', root=root).actual('family')
        sans, mono = pick_fonts(tkfont.families(root), default_sans, default_mono)
        self.fonts = {'body': (sans, 10), 'body_bold': (sans, 10, 'bold'), 'small': (sans, 9),
                      'chip': (sans, 8, 'bold'), 'h1': (sans, 24, 'bold'), 'h2': (sans, 14, 'bold'),
                      'mono': (mono, 9)}
        self.tkfont = tkfont

    def px(self, size):
        return int(size * self.dpi)

    def measure(self, spec, text):
        return self.tkfont.Font(font=spec).measure(text)

    def fit(self, spec, text, width):
        """`text` cut with an ellipsis so it is at most `width` pixels wide in font `spec`."""
        dots = plain('…')
        while len(text) > 1 and self.measure(spec, text) > width:
            text = text[:-len(dots) - 1].rstrip() + dots if text.endswith(dots) else text[:-1].rstrip() + dots
        return text


class FlatButton:
    """A flat button: kind 'ghost' (quiet) or 'primary' (accent). Geometry methods go to the canvas."""

    def __init__(self, tk, look, parent, text, command, kind='ghost', bg=CARD, height=34, wide=False):
        text = plain(text)
        self.look, self.text, self.command, self.kind, self.bg = look, text, command, kind, bg
        self.state, self.hover, self.pressed = 'normal', 0.0, False
        self.font = look.fonts['body_bold' if wide else 'body']
        self.width, self.height = look.measure(self.font, text) + look.px(56 if wide else 32), look.px(height)
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
            self.text = plain(options['text'])
            self.canvas.itemconfigure(self.label, text=self.text)
        self.paint()

    def paint(self):
        t, bg = self.hover, self.bg
        if self.state != 'normal':
            fill, line, ink = mix(bg, TEXT, .035), mix(bg, TEXT, .07), DIM
        elif self.kind == 'primary':
            fill, line, ink = mix(BLOOD, BLOOD_HI, t), mix(BLOOD, BLOOD_HI, t), '#ffffff'
        else:
            fill = mix(mix(bg, TEXT, .05), mix(bg, TEXT, .13), t)
            line, ink = mix(mix(bg, TEXT, .14), mix(bg, TEXT, .34), t), TEXT
        if self.pressed:
            fill = mix(fill, BG, .35)
        self.canvas.itemconfigure(self.rect, fill=fill, outline=line)
        self.canvas.itemconfigure(self.label, fill=ink)


class Segment:
    """One half of a segmented toggle: a bordered button with a ring that shows the choice. Setting `variable` to
    `value` makes it the active one (crimson, filled dot); Return, space and a click do that, and so do the arrow
    keys when it has the focus. configure(state=) works like FlatButton's."""

    def __init__(self, tk, look, parent, text, value, variable, command, others=(), first=True, bg=CARD):
        self.look, self.value, self.variable, self.command, self.bg = look, value, variable, command, bg
        self.others, self.first = others, first
        self.state, self.focused = 'normal', False
        font, px = look.fonts['small'], look.px
        self.width, self.height = look.measure(font, text) + px(58), px(36)
        self.canvas = tk.Canvas(parent, width=self.width, height=self.height, bg=bg, highlightthickness=0, bd=0,
                                cursor='hand2', takefocus=True)
        c, edge = self.canvas, 0 if first else -1   # the seam between the two halves is one border, not two
        self.rect = c.create_rectangle(edge, 0, self.width - 1, self.height - 1)
        r, cx, cy = px(6), px(20), self.height // 2
        self.ring = c.create_oval(cx - r, cy - r, cx + r, cy + r, width=2)
        d = px(2.5)
        self.dot = c.create_oval(cx - d, cy - d, cx + d, cy + d, width=0)
        self.label = c.create_text(px(36), cy, text=text, font=font, anchor='w')
        c.bind('<ButtonPress-1>', lambda _e: self.choose(focus=True))
        c.bind('<Return>', lambda _e: self.choose())
        c.bind('<space>', lambda _e: self.choose())
        c.bind('<FocusIn>', lambda _e: self.focus_changed(True))
        c.bind('<FocusOut>', lambda _e: self.focus_changed(False))
        for key in ('<Left>', '<Right>'):
            c.bind(key, lambda _e: self.step())
        variable.trace_add('write', lambda *_: self.paint())
        self.paint()

    def __getattr__(self, name):
        if name == 'canvas':
            raise AttributeError(name)
        return getattr(self.canvas, name)

    def choose(self, focus=False):
        if self.state != 'normal':
            return
        if focus:
            self.canvas.focus_set()
        self.variable.set(self.value)
        self.command()

    def step(self):
        """An arrow key: the other half takes the choice and the focus."""
        if self.state == 'normal' and self.others:
            self.others[0].choose(focus=True)

    def focus_changed(self, focused):
        self.focused = focused
        self.paint()

    def configure(self, **options):
        if 'state' in options:
            self.state = options['state']
            self.canvas.configure(cursor='hand2' if self.state == 'normal' else 'arrow')
        self.paint()

    def paint(self):
        c, active = self.canvas, self.variable.get() == self.value
        if self.state != 'normal':
            fill, line, ink, ring = mix(self.bg, TEXT, .035), mix(self.bg, TEXT, .07), DIM, DIM
        elif active:
            fill, line, ink, ring = BLOOD, BLOOD_HI, '#ffffff', '#ffffff'
        else:
            fill, line, ink, ring = FIELD, mix(self.bg, TEXT, .2), MUTED, MUTED
        if self.focused:
            line = TEXT
        c.itemconfigure(self.rect, fill=fill, outline=line)
        c.itemconfigure(self.ring, outline=ring)
        c.itemconfigure(self.dot, fill=ring if active else '', state='normal' if active else 'hidden')
        c.itemconfigure(self.label, fill=ink)


def segmented(tk, look, parent, choices, variable, command, bg=CARD):
    """Two or more adjacent Segments over one StringVar; returns them (each is packed, left to right)."""
    segments = []
    for number, (text, value) in enumerate(choices):
        segment = Segment(tk, look, parent, text, value, variable, command, first=number == 0, bg=bg)
        segment.pack(side='left')
        segments.append(segment)
    for segment in segments:
        segment.others = [s for s in segments if s is not segment]
    return segments


def logo(tk, look, parent):
    """The red rounded square with a white A that opens the header."""
    size = look.px(38)
    canvas = tk.Canvas(parent, width=size, height=size, bg=BG, highlightthickness=0, bd=0)
    round_rect(canvas, 0, 0, size - 1, size - 1, look.px(9), fill=BLOOD, outline='')
    canvas.create_text(size // 2, size // 2, text='A', font=look.fonts['h2'], fill='#ffffff')
    return canvas


def magnifier(tk, look, parent, bg):
    """A small magnifying glass for the search field."""
    size, px = look.px(18), look.px
    canvas = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
    canvas.create_oval(px(2), px(2), px(11), px(11), outline=MUTED, width=2)
    canvas.create_line(px(10), px(10), px(15), px(15), fill=MUTED, width=2)
    return canvas


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


# what a card shows: kind 'ok' is a green pill (installed), 'update' an amber one (an older HelixSR), 'none' a grey
# one; dim greys a game that is not offered;
# cover is the path of its cover JPEG when it has one (the tint and stripes otherwise); fsr4 is the line "FSR 4.1.1: yes"
# or "FSR 4.1.1: no, <why>" (fsr4_ok picks its colour), empty when the game was not judged
Card = namedtuple('Card', 'name supports state kind dim cover fsr4 fsr4_ok', defaults=(None, '', False))


class Gallery:
    """The games as a grid of cards on a Canvas, COLUMNS across, scrolling when there are more rows than room.

    set_items(cards) draws them; select(i) outlines one in crimson (None clears it). The gallery only reports what
    the user did: choose(i) for a click or an arrow/Home/End/Return key, double(i) and context(i, x, y) for a
    double click and a right click. It never decides what is selected, the window does and calls select()."""

    def __init__(self, tk, ttk, look, parent, choose, double, context):
        self.look, self.choose, self.double, self.context = look, choose, double, context
        px = look.px
        self.card_h, self.gap = px(136), px(10)
        self.frame = tk.Frame(parent, bg=BG)
        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0, bd=0, takefocus=True, width=px(800),
                                height=2 * self.card_h + self.gap, yscrollincrement=px(24))
        self.scroll = ttk.Scrollbar(self.frame, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.cards, self.selected, self.outlines = [], None, {}
        self.photos, self.last_photos = {}, {}   # PhotoImages must stay referenced or Tk blanks them
        self.cover_error = ''   # the first reason a cover could not be drawn
        self.size, self.total, self.scrolls = None, 0, False
        c = self.canvas
        c.bind('<Configure>', self.resized)
        for key in ('Left', 'Right', 'Up', 'Down', 'Home', 'End', 'Return', 'space'):
            c.bind(f'<{key}>', lambda _e, key=key: self.key(key))
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            c.bind_all(sequence, self.wheel, add='+')

    def set_items(self, cards):
        self.cards, self.selected = list(cards), None
        self.draw()

    def update(self, cards):
        """New cards for the same games (a cover arrived, or covers were switched): redrawn, selection kept."""
        self.cards = list(cards)
        self.draw()

    def resized(self, event):
        if (event.width, event.height) != self.size:
            self.size = (event.width, event.height)
            self.draw()

    def draw(self):
        c, px = self.canvas, self.look.px
        c.delete('all')
        self.outlines = {}
        self.last_photos, self.photos = self.photos, {}
        width = c.winfo_width() if c.winfo_width() > 1 else c.winfo_reqwidth()
        card_w = max(px(120), (width - self.gap * (COLUMNS - 1)) // COLUMNS)
        rows = -(-len(self.cards) // COLUMNS)
        self.total = max(1, rows * (self.card_h + self.gap) - self.gap)
        for number, card in enumerate(self.cards):
            row, col = divmod(number, COLUMNS)
            self.draw_card(number, card, col * (card_w + self.gap), row * (self.card_h + self.gap), card_w)
        c.configure(scrollregion=(0, 0, width, self.total))
        self.show_scrollbar(self.total > max(c.winfo_height(), 1) > 1)
        if self.selected is not None:
            self.select(self.selected)

    def show_scrollbar(self, needed):
        if needed and not self.scrolls:
            self.scroll.pack(side='right', fill='y')
        elif not needed and self.scrolls:
            self.scroll.pack_forget()
            self.canvas.yview_moveto(0)
        self.scrolls = needed

    def draw_card(self, number, card, x, y, w):
        c, px, h, tag = self.canvas, self.look.px, self.card_h, f'c{number}'
        fonts, r = self.look.fonts, px(8)
        top = tint(card.name)
        round_rect(c, x, y, x + w, y + h, r, fill=CARD, outline='', tags=tag)
        cover = self.photo(card.cover, w, h, r) if card.cover else None
        if cover:
            c.create_image(x, y, image=cover, anchor='nw', tags=tag)
        else:
            # art: bands from the tint down to the card colour by 78% of the height, the top band rounded
            fade, bands = int(h * .78), 14
            for k in range(bands):
                y0, y1 = y + fade * k // bands, y + fade * (k + 1) // bands
                color = mix(top, CARD, ((k + .5) / bands) ** 1.4)
                if k == 0:
                    round_rect(c, x + 1, y0 + 1, x + w - 1, y1 + r, r, fill=color, outline='', tags=tag)
                else:
                    c.create_rectangle(x + 1, y0, x + w - 1, y1, fill=color, outline='', tags=tag)
            self.stripes(x, y + r, w, int(h * .46) - r, mix(top, TEXT, .09), tag)
        # monogram chip, top left
        chip_font = fonts['chip']
        chip_w = max(px(30), self.look.measure(chip_font, monogram(card.name)) + px(16))
        round_rect(c, x + px(10), y + px(10), x + px(10) + chip_w, y + px(30), px(6), tags=tag,
                   fill=mix('#0b0b0e', top, .22), outline=mix('#0b0b0e', top, .4))
        c.create_text(x + px(10) + chip_w // 2, y + px(20), text=monogram(card.name), font=chip_font,
                      fill=MUTED if card.dim else TEXT, tags=tag)
        # title, supports, state pill
        inner = w - px(24)
        c.create_text(x + px(12), y + h - px(82), anchor='w', font=fonts['body_bold'], tags=tag,
                      text=self.look.fit(fonts['body_bold'], card.name, inner), fill=MUTED if card.dim else TEXT)
        c.create_text(x + px(12), y + h - px(62), anchor='w', font=fonts['small'], tags=tag,
                      text=self.look.fit(fonts['small'], card.supports, inner), fill=MUTED)
        if card.fsr4:   # can the game use FSR 4.1.1: green when yes, amber with the reason when no
            c.create_text(x + px(12), y + h - px(43), anchor='w', font=fonts['small'], tags=tag,
                          text=self.look.fit(fonts['small'], card.fsr4, inner), fill=OK if card.fsr4_ok else CAUTION)
        text = self.look.fit(fonts['small'], card.state, inner - px(20))
        pill_w = self.look.measure(fonts['small'], text) + px(20)
        ink = {'ok': OK, 'update': CAUTION}.get(card.kind, MUTED)
        round_rect(c, x + px(12), y + h - px(30), x + px(12) + pill_w, y + h - px(10), px(10), tags=tag,
                   fill=mix(CARD, ink, .16) if ink != MUTED else mix(CARD, TEXT, .1), outline='')
        c.create_text(x + px(12) + pill_w // 2, y + h - px(20), text=text, font=fonts['small'], fill=ink, tags=tag)
        self.outlines[number] = round_rect(c, x + 1, y + 1, x + w - 1, y + h - 1, r, fill='', outline=LINE, tags=tag)
        for sequence, handler in (('<Button-1>', self.clicked), ('<Double-Button-1>', self.doubled),
                                  ('<Button-3>', self.right_clicked)):
            c.tag_bind(tag, sequence, lambda e, n=number, handler=handler: handler(e, n))

    def photo(self, path, w, h, r):
        """The cover as a PhotoImage sized for a card, or None (no Pillow, unreadable file): the card keeps its tint."""
        key = (str(path), w, h, r)
        if key not in self.photos:
            try:
                self.photos[key] = self.last_photos.get(key) or ImageTk.PhotoImage(cover_art(path, w, h, r))
            except Exception as error:   # Pillow raises many kinds for a bad file; a card must never fail to draw
                if not self.cover_error:   # but say why once, or a broken build looks like "no covers"
                    self.cover_error = f'{type(error).__name__}: {error}'
                    print(f'AMDLSS: game cover not drawn ({path}): {self.cover_error}', file=sys.stderr)
                return None
        return self.photos[key]

    def stripes(self, x, y, w, height, color, tag):
        """Diagonal lines across the art area, clipped to the card's width."""
        left, right, step = x + 1, x + w - 1, self.look.px(15)
        for start in range(x - height, x + w, step):
            (x0, y0), (x1, y1) = (start, y + height), (start + height, y)
            if x0 < left:
                x0, y0 = left, y0 - (left - x0)
            if x1 > right:
                x1, y1 = right, y1 + (x1 - right)
            if x0 < x1:
                self.canvas.create_line(x0, y0, x1, y1, fill=color, tags=tag)

    def select(self, number):
        """Outline card `number` in crimson (2 px), the others in a hairline; scroll it into view."""
        self.selected = number
        for n, outline in self.outlines.items():
            chosen = n == number
            self.canvas.itemconfigure(outline, outline=BLOOD if chosen else LINE, width=2 if chosen else 1)
            if chosen:
                self.canvas.tag_raise(outline)
        if number is not None:
            self.reveal(number)

    def reveal(self, number):
        c, step = self.canvas, self.card_h + self.gap
        top, bottom, view = number // COLUMNS * step, number // COLUMNS * step + self.card_h, c.winfo_height()
        if view <= 1 or self.total <= view:
            return
        if top < c.canvasy(0):
            c.yview_moveto(top / self.total)
        elif bottom > c.canvasy(0) + view:
            c.yview_moveto((bottom - view) / self.total)

    def key(self, name):
        count = len(self.cards)
        if not count:
            return
        here = self.selected
        if name in ('Return', 'space'):
            target = 0 if here is None else here
        elif here is None:
            target = count - 1 if name == 'End' else 0
        else:
            moves = {'Left': -1, 'Right': 1, 'Up': -COLUMNS, 'Down': COLUMNS, 'Home': -count, 'End': count}
            target = max(0, min(count - 1, here + moves[name]))
        self.choose(target)

    def clicked(self, _event, number):
        self.canvas.focus_set()
        self.choose(number)

    def doubled(self, _event, number):
        self.choose(number)
        self.double(number)

    def right_clicked(self, event, number):
        self.choose(number)
        self.context(number, event.x_root, event.y_root)

    def wheel(self, event):
        """The mouse wheel scrolls the cards while the pointer is over them (a Windows wheel goes to the focus)."""
        if not self.canvas.winfo_exists() or self.total <= self.canvas.winfo_height() or \
                self.canvas.winfo_containing(event.x_root, event.y_root) is not self.canvas:
            return
        down = event.num == 5 or event.delta < 0
        self.canvas.yview_scroll(3 if down else -3, 'units')
