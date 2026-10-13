# SPDX-License-Identifier: GPL-2.0-or-later
"""What AMDLSS knows about a game: the rows HelixSR's installer lists, the state of each, and the anti-cheat check.

HelixSR's own installer (setup/helixsr_install.py) finds the Steam games and decides the route per game. AMDLSS
reads that list (as JSON from a tiny read-only bridge, or by parsing the installer's printed `list` output when the
bridge cannot run) and never decides a route itself. Pure functions, no Tk, no subprocess: tests/ and --selftest
exercise them directly.
"""
import json
import ntpath  # game folders are Windows paths, whatever the host
import os
import re
from dataclasses import dataclass, field

SENTINEL = 'AMDLSS-GAMES '  # the bridge prints one line: this prefix and a JSON list

STATE_TEXT = {'none': 'not installed', 'direct': 'FSR drop-in', 'optiscaler': 'OptiScaler',
              'dlss': 'DLSS loader (experimental)', 'unknown': 'installed'}
# What to pick in the game's menu, as HelixSR's setup reports it (helixsr_install.in_game).
IN_GAME = {'direct': 'AMD FSR', 'optiscaler': 'DLSS', 'dlss': 'DLSS'}


@dataclass
class Game:
    name: str
    folder: str = ''            # the game folder HelixSR scanned; '' when only the printed list was parsed
    inputs: list = field(default_factory=list)   # upscalers the game ships: DLSS, XeSS, FSR 2
    fsr31: bool = False         # the game has an FSR 3.1 DLL (HelixSR's drop-in route)
    can_opti: bool = False      # FSR 3.1 DLL and DLSS/XeSS/FSR 2 inputs: both routes work (HelixSR's can_opti)
    route: str = 'optiscaler'   # the route HelixSR installs by default: direct or optiscaler
    state: str = 'none'         # none, direct, optiscaler, dlss or unknown
    fsr_targets: list = field(default_factory=list)
    exe_folder: str = ''
    index: int = 0              # 1-based position in HelixSR's list: the N of `install N`
    anticheat: list = field(default_factory=list)
    reason: str = ''            # why the list hides this game ('' = ready); set by classify()
    manual: bool = False        # the folder was added by hand with "Add game folder"
    helixsr: str = ''           # the HelixSR version installed in the game; '' when none or unknown

    @property
    def supports(self):
        return ', '.join((['FSR 3.1'] if self.fsr31 else []) + self.inputs) or 'unknown'

    @property
    def state_text(self):
        return STATE_TEXT.get(self.state, self.state)

    @property
    def installed(self):
        return self.state != 'none'

    @property
    def can_install(self):
        # The experimental DLSS loader is never offered: a game already on it can only be removed.
        return self.state != 'dlss' and not self.reason

    @property
    def route_choice(self):
        """True when the user can pick the route: both work and nothing is installed yet."""
        return self.can_opti and not self.installed

    @property
    def default_route(self):
        """The route offered first: OptiScaler (the game's DLSS option) when the game lists DLSS, else HelixSR's."""
        return 'optiscaler' if self.can_opti and 'DLSS' in self.inputs else self.route

    @property
    def active_route(self):
        """The route that is installed when the state says so, else the route HelixSR would install."""
        return self.state if self.state in IN_GAME else self.route

    def in_game(self, route=None):
        """What to choose in the game's graphics menu for `route` (default: the installed route, else HelixSR's)."""
        return IN_GAME.get(route or self.active_route, 'AMD FSR')


def from_json(entries):
    """Games from the bridge's JSON list (a list of dicts)."""
    games = []
    for entry in entries:
        games.append(Game(name=str(entry['name']), folder=str(entry.get('folder') or ''),
                          inputs=list(entry.get('inputs') or []), fsr31=bool(entry.get('fsr31')),
                          can_opti=bool(entry.get('can_opti')),
                          route='optiscaler' if entry.get('route') is None else str(entry['route']), state=str(entry.get('state') or 'none'),
                          fsr_targets=list(entry.get('fsr_targets') or []),
                          exe_folder=str(entry.get('exe_folder') or ''), index=int(entry.get('index') or 0),
                          helixsr=str(entry.get('helixsr') or '')))
    return games


def parse_bridge_output(text):
    """The games from the bridge's stdout: the last SENTINEL line. Returns None when there is none."""
    found = None
    for line in text.splitlines():
        if line.startswith(SENTINEL):
            try:
                found = from_json(json.loads(line[len(SENTINEL):]))
            except (ValueError, KeyError, TypeError):
                return None
    return found


_INPUT = r'(?:DLSS|XeSS|FSR 2)'
# The status text of one printed row (helixsr_install.Game.label and show); the name is whatever precedes it.
_ROW = re.compile(r'^\s*(?P<index>\d+)\.\s+(?P<name>.+?)\s+(?P<status>HelixSR installed'
                  r'|FSR 3\.1 \(HelixSR replaces.*'
                  r'|DLSS \(HelixSR as the game.*'
                  r'|' + _INPUT + r'(?: / ' + _INPUT + r')* \(through OptiScaler.*)$')


def parse_list_output(text):
    """Games from the printed output of `helixsr_install.py HELIXSR_FOLDER list`. Defensive: lines that are not a
    numbered row are ignored, and a row whose status is not recognised is skipped rather than guessed."""
    games = []
    for line in text.splitlines():
        match = _ROW.match(line.rstrip())
        if not match:
            continue
        status = match.group('status')
        game = Game(name=match.group('name').strip(), index=int(match.group('index')))
        if status == 'HelixSR installed':
            game.state = 'unknown'
        elif status.startswith('FSR 3.1'):
            game.fsr31, game.route = True, 'direct'
        elif status.startswith('DLSS (HelixSR as'):
            game.state, game.route, game.inputs = 'dlss', 'dlss', ['DLSS']
        else:
            game.inputs = re.findall(_INPUT, status.split(' (')[0])
        games.append(game)
    return games


# --- which games the list shows ---------------------------------------------------------------------------------
NOT_INSTALLED = 'not installed'
NO_UPSCALER = 'no DLSS, FSR 3.1 or XeSS found'
NO_FOLDER = 'folder not found'


def hidden_reason(game):
    """Why `game` is not offered, or '' when it is. A game HelixSR already runs in always passes, so Remove stays
    reachable. Otherwise its folder and the folder of its .exe (HelixSR's `target`) must exist, and HelixSR must have
    something to work with: an FSR 3.1 DLL, or DLSS, XeSS or FSR 2. A game known only from the printed list has no
    folder to check and passes."""
    if game.installed or not game.folder:
        return ''
    if not (os.path.isdir(game.folder) and game.exe_folder and os.path.isdir(game.exe_folder)):
        return NOT_INSTALLED
    if not (game.fsr_targets or game.inputs):
        return NO_UPSCALER
    return ''


def classify(found, extras=()):
    """Sets `reason` on every game of HelixSR's list and returns the list plus a row for each folder added by hand
    that HelixSR did not list at all, so a bad folder is shown with its reason instead of vanishing."""
    rows = list(found)
    for game in rows:
        game.reason = hidden_reason(game)
    for extra in extras:
        mine = [g for g in rows if g.folder and same_folder(g.folder, extra)]
        for game in mine:
            game.manual = True
        if not any(g.folder and overlaps(g.folder, extra) for g in rows):
            name = os.path.basename(os.path.normpath(extra)) or str(extra)
            rows.append(Game(name=name, folder=str(extra), manual=True, route='',
                             reason=NO_UPSCALER if os.path.isdir(extra) else NO_FOLDER))
    return rows


def visible(game, show_all=False):
    """Ready games, folders added by hand (with their reason) and, with `show_all`, every hidden game."""
    return show_all or not game.reason or game.manual


def count_text(rows):
    ready = sum(1 for g in rows if not g.reason)
    return f'{ready} games ready, {sum(1 for g in rows if g.reason and not g.manual)} hidden'


def same_folder(a, b):
    return ntpath.normcase(ntpath.realpath(a)) == ntpath.normcase(ntpath.realpath(b))


def match_game(fresh, wanted):
    """The entry of a fresh list that is `wanted`: same folder when known, else the only one with the same name."""
    if wanted.folder:
        return next((game for game in fresh if game.folder and same_folder(game.folder, wanted.folder)), None)
    same = [g for g in fresh if g.name == wanted.name]
    return same[0] if len(same) == 1 else None


def overlaps(a, b):
    """True when folder a is folder b, inside it, or contains it (the way HelixSR decides a game is already listed)."""
    a, b = (ntpath.normcase(ntpath.normpath(p)) for p in (a, b))
    return a == b or a.startswith(b + ntpath.sep) or b.startswith(a + ntpath.sep)


# --- anti-cheat -------------------------------------------------------------------------------------------------
# Detection is by file or folder name, as a whole word, so a game nobody listed is still caught and an asset that
# merely contains the word is not (idea from DLSS5-Autopilot's core/anticheat.py; the list here is our own).
MARKERS = (
    ('easyanticheat', 'Easy Anti-Cheat'), ('eac_launcher', 'Easy Anti-Cheat'),
    ('battleye', 'BattlEye'), ('beservice', 'BattlEye'), ('beclient', 'BattlEye'),
    ('vgk.sys', 'Riot Vanguard'), ('vgc.exe', 'Riot Vanguard'),
    ('gameguard', 'GameGuard'), ('xigncode', 'XIGNCODE3'), ('punkbuster', 'PunkBuster'),
    ('faceit', 'FACEIT'), ('ricochet', 'Ricochet'), ('denuvoanticheat', 'Denuvo Anti-Cheat'),
    ('eaanticheat', 'EA anti-cheat'), ('mhyprot', 'HoYoverse anti-cheat'), ('anticheatexpert', 'ACE anti-cheat'),
)
_MARKER_RES = tuple((re.compile(r'(?<![a-z0-9])' + re.escape(word) + r'\d*(?![a-z0-9])'), product)
                    for word, product in MARKERS)


def anticheat_of(name):
    """The anti-cheat product a file or folder name belongs to, or None."""
    low = name.lower()
    for pattern, product in _MARKER_RES:
        if pattern.search(low):
            return product
    return None


def scan_anticheat(folder, depth=3):
    """Sorted product names found in and below `folder` (at most `depth` levels, links not followed)."""
    found, todo = set(), [(folder, 0)]
    while todo:
        path, level = todo.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            product = anticheat_of(entry.name)
            if product:
                found.add(product)
            if level < depth and entry.is_dir(follow_symlinks=False):
                todo.append((entry.path, level + 1))
    return sorted(found)


# --- what the window says ---------------------------------------------------------------------------------------

WARNINGS = ("Close the game first.\n"
            "Do not use HelixSR in online games or games with anti-cheat: the account can be banned.")


def plan_text(game, route=None):
    """What installing will do, before the user agrees (HelixSR's own description of its two routes)."""
    if (route or game.route) == 'direct':
        files = ''.join(f'\n  {path}' for path in game.fsr_targets[:3])
        what = ("HelixSR replaces the game's FSR 3.1 DLL and keeps the game's own file next to it as "
                f"*.original.dll:{files}")
    else:
        where = game.exe_folder or "the game's .exe folder"
        what = (f"HelixSR puts OptiScaler 0.9.4 next to the game's .exe ({where}) and points it at HelixSR. "
                "Files it replaces go to the HelixSR\\backup folder there.")
    text = f'{what}\n\n{WARNINGS}'
    if game.anticheat:
        text += (f"\n\nAMDLSS found anti-cheat files in this game ({', '.join(game.anticheat)}). Installing here "
                 "can stop the game from starting or get the account banned.")
    return text + f'\n\nInstall into {game.name}?'


FRAMEGEN_LABEL = ("Frame generation: use the game's DLSS Frame Generation option "
                  "(runs AMD FSR frame generation)")
FRAMEGEN_HINT = ("Turn on DLSS Frame Generation in the game; it runs AMD's frame generation. "
                 "Keep NVIDIA Reflex off.")


def after_text(game, frame_gen=False):
    """What to choose in the game's menu once HelixSR is installed, as HelixSR's setup reports it."""
    text = f"HelixSR is installed in {game.name}.\n\nIn the game's graphics settings choose {game.in_game()}."
    if game.active_route == 'optiscaler':
        text += " Press Insert in the game to open OptiScaler's menu."
        if frame_gen:
            text += '\n\n' + FRAMEGEN_HINT
    return text + ('\n\nIf it looks unchanged in game, press Collect logs and send the zip.'
                   '\n\nThe Remove button puts the original files back.')
