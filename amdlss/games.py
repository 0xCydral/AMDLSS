# SPDX-License-Identifier: GPL-2.0-or-later
"""What AMDLSS knows about a game: the rows HelixSR's installer lists, the state of each, and the anti-cheat check.

HelixSR's own installer (setup/helixsr_install.py) finds the Steam games and decides the route per game. AMDLSS
reads that list (as JSON from a tiny read-only bridge, or by parsing the installer's printed `list` output when the
bridge cannot run) and never decides a route itself. Pure functions, no Tk, no subprocess: tests/ and --selftest
exercise them directly.
"""
import json
import os
import re
from dataclasses import dataclass, field

from . import host

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
    streamline: bool = True     # the game ships Streamline DLSS-G; set by scan_streamline(), True while unscanned

    @property
    def framegen_reason(self):
        """Why the frame generation option is not offered, or ''."""
        return '' if self.streamline else NO_DLSSG

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
UNSAFE_FOLDER = 'folder covers other games'
MS_CONFIG = 'MicrosoftGame.config'   # an Xbox (GDK) game keeps it in its Content folder
_INPUT_DLLS = {'nvngx_dlss.dll': 'DLSS', 'libxess.dll': 'XeSS', 'ffx_fsr2_api_x64.dll': 'FSR 2',
               'ffx_fsr2_api_dx12_x64.dll': 'FSR 2'}
_FSR_DLLS = ('amd_fidelityfx_upscaler_dx12.dll', 'amd_fidelityfx_dx12.dll')


def is_root_folder(folder, pathmod=None):
    """True for a drive or filesystem root ('E:\\\\', 'E:', '/', a UNC share root): never one game's folder.
    A Windows drive root counts in either system's spelling, so the check also holds for Windows paths
    read on Linux (tests, reports)."""
    if not folder:
        return False
    if re.fullmatch(r'[A-Za-z]:[\\/]*|\\\\[^\\/:]+\\[^\\/:]+[\\/]*', str(folder).strip()):
        return True
    path = pathmod or host.path_module()
    norm = path.normpath(str(folder))
    drive, rest = path.splitdrive(norm)
    return not rest.strip('\\/') and bool(drive or norm[:1] in '\\/')


def contains(outer, inner, pathmod=None):
    """True when folder `inner` is strictly inside folder `outer` (whole folders, as in overlaps())."""
    path = pathmod or host.path_module()
    outer, inner = (path.normcase(path.normpath(str(p))) for p in (outer, inner))
    return inner.startswith(outer if outer.endswith(path.sep) else outer + path.sep)


def unsafe_reason(game, others=(), pathmod=None, extras=()):
    """UNSAFE_FOLDER when HelixSR's folder for `game` is a drive root or holds another listed game: changing
    it by HelixSR's list number would rewrite that game's FSR DLLs too, so it is never offered. A folder
    added by hand inside it but away from this game's .exe counts as that other game: HelixSR's widened
    entry swallows its row, so the list alone may not show it. '' otherwise."""
    if not game.folder:
        return ''
    if is_root_folder(game.folder, pathmod):
        return UNSAFE_FOLDER
    if any(other is not game and other.folder and contains(game.folder, other.folder, pathmod)
           for other in others):
        return UNSAFE_FOLDER
    if game.exe_folder and any(contains(game.folder, str(extra), pathmod)
                               and not overlaps(str(extra), game.exe_folder, pathmod) for extra in extras):
        return UNSAFE_FOLDER
    return ''


def _folder_name(folder):
    """A folder's own name as a game name; a GDK Content folder takes its parent's name ('Valor Mortis')."""
    path = host.path_module()
    name = path.basename(path.normpath(str(folder or '')))
    if name.casefold() == 'content':
        name = path.basename(path.dirname(path.normpath(str(folder)))) or name
    return name


def _gdk_root(game):
    """The folder holding MicrosoftGame.config above the game's .exe (or folder), or '': the GDK game's root."""
    for start in (game.exe_folder, game.folder):
        here = str(start or '')
        while here:
            if os.path.isfile(os.path.join(here, MS_CONFIG)):
                return here
            parent = os.path.dirname(here.rstrip('\\/') or here)
            if parent in (here, ''):
                break
            here = parent
    return ''


def _rescan(folder):
    """(fsr targets, inputs) really inside `folder`, HelixSR's own rule (seven folders deep, its folder skipped)."""
    found, inputs, todo = [], set(), [(str(folder), 0)]
    while todo:
        here, level = todo.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for entry in entries:
            low = entry.name.lower()
            if entry.is_file(follow_symlinks=False) or entry.is_symlink():
                if low in _FSR_DLLS:
                    found.append(entry.path)
                if low in _INPUT_DLLS:
                    inputs.add(_INPUT_DLLS[low])
            elif level < 7 and entry.name != 'HelixSR' and entry.is_dir(follow_symlinks=False):
                todo.append((entry.path, level + 1))
    targets = [p for p in found if p.lower().endswith(_FSR_DLLS[0])] or found
    return targets, [name for name in ('DLSS', 'XeSS', 'FSR 2') if name in inputs]


def _correct(game, rows, extras, unsafe):
    """Puts a wrong folder or name right for showing and collecting. A GDK game's root is its MicrosoftGame.config
    folder, else a folder added by hand around its .exe; the name is the config's, else the folder's. The entry
    keeps its unsafe reason: HelixSR's own list still holds the wide folder, so its number stays unusable."""
    root = _gdk_root(game)
    if unsafe:
        inside = [str(extra) for extra in extras
                  if not is_root_folder(extra) and game.exe_folder
                  and (same_folder(extra, game.exe_folder) or contains(extra, game.exe_folder))
                  and not any(other is not game and other.folder and contains(extra, other.folder)
                              for other in rows)]
        candidate = root or (inside[0] if inside else '')
        if candidate and not same_folder(candidate, game.folder):
            game.folder = candidate
            game.fsr_targets, game.inputs = _rescan(candidate)
            game.fsr31 = bool(game.fsr_targets)
        elif game.exe_folder:   # no root to correct to: another game's DLLs are not this game's targets
            game.fsr_targets = [t for t in game.fsr_targets if contains(game.exe_folder, t)]
            game.fsr31 = bool(game.fsr_targets)
    # a widened entry carries the wide folder's name ('E:' has none): a GDK game takes its config's name
    if not (game.name or '').strip() or game.name.casefold() == 'content' or (unsafe and root):
        name = ''
        if root:
            from . import identify   # lazy: identify imports this module for overlaps()
            name = identify.xbox_title([root])
        game.name = name or _folder_name(game.folder) or _folder_name(game.exe_folder) or game.name


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
    that HelixSR did not list at all, so a bad folder is shown with its reason instead of vanishing. A folder
    HelixSR widened to a drive root (or over other games) is first put right for showing and collecting
    (_correct) and then refused with UNSAFE_FOLDER, installed or not."""
    rows = list(found)
    unsafe = {id(game): unsafe_reason(game, rows, extras=extras) for game in rows}   # on HelixSR's folders, before any fix
    for game in rows:
        if unsafe[id(game)] or not (game.name or '').strip() or game.name.casefold() == 'content':
            _correct(game, rows, extras, unsafe[id(game)])
        game.reason = unsafe[id(game)] or hidden_reason(game)
    for extra in extras:
        mine = [g for g in rows if g.folder and same_folder(g.folder, extra)]
        for game in mine:
            game.manual = True
        if not any(g.folder and overlaps(g.folder, extra) for g in rows):
            name = _folder_name(extra) or str(extra)
            rows.append(Game(name=name, folder=str(extra), manual=True, route='',
                             reason=NO_UPSCALER if os.path.isdir(extra) else NO_FOLDER))
    return rows


def visible(game, show_all=False):
    """Ready games, folders added by hand and unsafe folders (each with its reason) and, with `show_all`,
    every hidden game. An unsafe folder stays on show: the user must see why AMDLSS will not change it."""
    return show_all or not game.reason or game.manual or game.reason == UNSAFE_FOLDER


def count_text(rows):
    ready = sum(1 for g in rows if not g.reason)
    hidden = sum(1 for g in rows if g.reason and g.reason != UNSAFE_FOLDER and not g.manual)
    return f'{ready} games ready, {hidden} hidden'


def same_folder(a, b, pathmod=None):
    """`pathmod` is the paths' own module (the host's by default): ntpath folds case and separators, posixpath
    does neither, which is how the two systems' folders compare."""
    path = pathmod or host.path_module()
    return path.normcase(path.realpath(a)) == path.normcase(path.realpath(b))


def match_game(fresh, wanted, pathmod=None):
    """The entry of a fresh list that is `wanted`: same folder when known, else the only one with the same name."""
    if wanted.folder:
        return next((game for game in fresh if game.folder and same_folder(game.folder, wanted.folder, pathmod)), None)
    same = [g for g in fresh if g.name == wanted.name]
    return same[0] if len(same) == 1 else None


def overlaps(a, b, pathmod=None):
    """True when folder a is folder b, inside it, or contains it (the way HelixSR decides a game is already listed)."""
    path = pathmod or host.path_module()
    a, b = (path.normcase(path.normpath(p)) for p in (a, b))
    return a == b or a.startswith(b + path.sep) or b.startswith(a + path.sep)


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
    ('ace-idw', 'ACE anti-cheat'), ('ace-base', 'ACE anti-cheat'),   # Tencent ACE's DLLs beside the game (Delta Force)
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


# --- Streamline DLSS-G ------------------------------------------------------------------------------------------
# OptiScaler's FGInput=dlssg is "limited to games that use Streamline v2 and DLSSG" (its own ini). Such a game ships
# NVIDIA's interposer and the DLSS-G plugin (names as in NVIDIA's Streamline guide and in Monster Hunter Stories 3's
# files). They may sit in a subfolder (Assassin's Creed Shadows: NVStreamline\production) or, in Unreal Engine games,
# under <Project>\Plugins\...\Binaries\ThirdParty\Win64, six folders down.
NO_DLSSG = "This game has no DLSS Frame Generation for AMDLSS to replace."
STREAMLINE_DLLS = frozenset(('sl.interposer.dll', 'sl.dlss_g.dll'))


def scan_streamline(folder, depth=7):
    """True when `folder` holds both Streamline DLLs, in any two places up to `depth` levels down (links not followed)."""
    found, todo = set(), [(folder, 0)]
    while todo and found != STREAMLINE_DLLS:
        path, level = todo.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            if entry.name.lower() in STREAMLINE_DLLS:
                found.add(entry.name.lower())
            elif level < depth and entry.is_dir(follow_symlinks=False):
                todo.append((entry.path, level + 1))
    return found == STREAMLINE_DLLS


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
                f"Files it replaces go to the HelixSR{host.path_module().sep}backup folder there.")
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
