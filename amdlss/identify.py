# SPDX-License-Identifier: GPL-2.0-or-later
"""Which game is this folder? Offline only: identify(game) reads the metadata the game's launcher left on disk
(Steam, GOG, Epic, Xbox/Game Pass, EA app) and the main exe's ProductName, and says what it found: a Steam app id, a
GOG id, and the game's titles, best first. Nothing here touches the network; amdlss.covers uses the answer to look the
game's cover up.

Folders are Windows paths whatever the host (ntpath), as in games.py.
"""
import json
import ntpath
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import games as gamelist

# ProductNames that name the engine or the OS, not the game
GENERIC_PRODUCTS = {'', 'ue4', 'ue5', 'unrealgame', 'unity', 'unity player', 'microsoft windows operating system'}
SKIPPED_EXES = re.compile(r'crash|launcher|setup|unins|redist|helper', re.I)


@dataclass
class Identity:
    steam_appid: int = None
    gog_id: int = None
    titles: list = field(default_factory=list)   # best first, one per normalised spelling
    source: str = 'name'                         # what named the game first: steam, gog, epic, xbox, ea, exe or name


def normalise(name):
    """A name for comparing: case, trademark signs, punctuation and spacing ignored."""
    return re.sub(r'\W+', ' ', re.sub('[™®©]', '', name).casefold()).strip()


def tidy_folder_name(name):
    """A folder name as a title: 'TheWitcher3_WildHunt' -> 'The Witcher 3 Wild Hunt'."""
    name = re.sub(r'[_.\-]+', ' ', name)
    name = re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()


def ancestors(folder):
    """`folder`, its parent and so on up to the drive root (Windows paths, the separators left as given)."""
    here = str(folder)
    while here:
        yield here
        stripped = here.rstrip('\\/') or here
        parent = ntpath.dirname(stripped)
        if parent in (here, stripped):
            return
        here = parent


def read_text(path):
    try:
        return Path(path).read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        return ''


def read_json(path):
    try:
        data = json.loads(read_text(path))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def read_xml(path):
    """The root element of an XML file, or None; namespaces are stripped from every tag."""
    try:
        root = ET.fromstring(read_text(path).encode('utf-8'))
    except ET.ParseError:
        return None
    for element in root.iter():
        element.tag = str(element.tag).rsplit('}', 1)[-1]
    return root


def steam_manifest(folder):
    """(app id, name) of the Steam game in `folder`, a path ...\\steamapps\\common\\<installdir>[\\...]: from the
    appmanifest in that steamapps folder whose installdir is <installdir>. None when there is none."""
    head = str(folder).rstrip('\\/')
    while True:
        parent, name = ntpath.split(head)
        if parent == head or not name:
            return None
        library, common = ntpath.split(parent)
        if common.casefold() == 'common' and ntpath.basename(library).casefold() == 'steamapps':
            break
        head = parent
    try:
        for manifest in sorted(Path(library).glob('appmanifest_*.acf')):
            text = manifest.read_text(encoding='utf-8', errors='replace')
            found = {key: re.search(rf'"{key}"\s+"([^"]*)"', text, re.I) for key in ('appid', 'installdir', 'name')}
            if found['appid'] and found['installdir'] and found['appid'].group(1).isdigit() and \
                    found['installdir'].group(1).casefold() == name.casefold():
                return int(found['appid'].group(1)), found['name'].group(1) if found['name'] else ''
    except OSError:
        pass
    return None


def gog_info(folders):
    """(game id, name) from the goggame-<id>.info in the first of `folders` that has one (the base game, not a DLC)."""
    for folder in folders:
        try:
            infos = [read_json(path) for path in sorted(Path(folder).glob('goggame-*.info'))]
        except OSError:
            continue
        infos = [i for i in infos if str(i.get('gameId', '')).isdigit()]
        if infos:
            base = next((i for i in infos if str(i.get('rootGameId', '')) == str(i['gameId'])), infos[0])
            return int(base['gameId']), str(base.get('name') or '')
    return None


def epic_title(folders):
    """DisplayName of the Epic launcher manifest whose InstallLocation is the game folder or one above it."""
    programdata = os.environ.get('PROGRAMDATA')
    if not programdata:
        return ''
    try:
        items = sorted((Path(programdata) / 'Epic' / 'EpicGamesLauncher' / 'Data' / 'Manifests').glob('*.item'))
    except OSError:
        return ''
    found = []
    for item in items:
        data = read_json(item)
        location, title = str(data.get('InstallLocation') or ''), str(data.get('DisplayName') or '')
        for folder in folders:
            if location and title and gamelist.overlaps(location, folder) and \
                    len(ntpath.normpath(location)) <= len(ntpath.normpath(folder)):
                found.append((data.get('AppName') != data.get('MainGameAppName'), -len(location), title))
    return min(found)[2] if found else ''   # the base game over its DLC, then the most specific folder


def display_name(value):
    """A display name from a manifest, or '' for a resource reference (ms-resource:...) that is not a name."""
    value = (value or '').strip()
    return '' if value.lower().startswith('ms-resource:') else value


def xbox_title(folders):
    """The name in MicrosoftGame.config (ShellVisuals DefaultDisplayName, else Title), else appxmanifest.xml's."""
    for folder in folders:
        root = read_xml(Path(folder) / 'MicrosoftGame.config')
        if root is not None:
            names = [display_name(e.get('DefaultDisplayName')) for e in root.iter('ShellVisuals')]
            names += [display_name(e.text) for e in root.iter('Title')]
            if any(names):
                return next(n for n in names if n)
        root = read_xml(Path(folder) / 'appxmanifest.xml')
        if root is not None:
            names = [display_name(e.text) for e in root.iter('DisplayName')]
            if any(names):
                return next(n for n in names if n)
    return ''


def ea_title(folders):
    """The game title in __Installer\\installerdata.xml (the English one when there are several)."""
    for folder in folders:
        root = read_xml(Path(folder) / '__Installer' / 'installerdata.xml')
        titles = [e for e in root.iter('gameTitle') if (e.text or '').strip()] if root is not None else []
        if titles:
            best = next((e for e in titles if (e.get('locale') or '').lower().startswith('en')), titles[0])
            return best.text.strip()
    return ''


def main_exe(folder):
    """The largest .exe in `folder`, leaving out crash reporters, launchers, installers and redistributables."""
    try:
        exes = [p for p in Path(folder).iterdir() if p.suffix.lower() == '.exe' and not SKIPPED_EXES.search(p.name)]
        return max(exes, key=lambda p: p.stat().st_size) if exes else None
    except OSError:
        return None


def read_product_name(path):
    """The ProductName in the exe's version resource (Windows only), or None."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    try:
        dll = ctypes.WinDLL('version')
        dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        dll.GetFileVersionInfoW.restype = wintypes.BOOL
        dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
        dll.VerQueryValueW.restype = wintypes.BOOL
        size = dll.GetFileVersionInfoSizeW(str(path), None)
        data = ctypes.create_string_buffer(size)
        if not size or not dll.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        languages = []
        if dll.VerQueryValueW(data, '\\VarFileInfo\\Translation', ctypes.byref(pointer), ctypes.byref(length)):
            words = (wintypes.WORD * (length.value // 2)).from_address(pointer.value)
            languages = [f'{words[i]:04x}{words[i + 1]:04x}' for i in range(0, len(words) - 1, 2)]
        for language in languages + ['040904b0', '040904e4']:
            if dll.VerQueryValueW(data, f'\\StringFileInfo\\{language}\\ProductName', ctypes.byref(pointer),
                                  ctypes.byref(length)) and length.value:
                return ctypes.wstring_at(pointer.value, length.value).rstrip('\0').strip() or None
    except (OSError, ValueError, ctypes.ArgumentError):
        pass
    return None


def generic_product(name):
    """True for a ProductName that names an engine or the OS ('Unreal Engine', 'Microsoft Windows...'), not a game."""
    plain = normalise(name)
    return plain in GENERIC_PRODUCTS or plain.startswith('unreal engine')


def identify(game):
    """What the launchers' files and the exe say about `game` (its .folder and .exe_folder): an Identity."""
    folders = list(dict.fromkeys(f for root in (game.folder, game.exe_folder) if root for f in ancestors(root)))
    identity, found = Identity(), []   # found: (source, title), best first

    def add(source, title):
        if title and title.strip():
            found.append((source, title.strip()))

    steam = next(filter(None, (steam_manifest(f) for f in (game.folder, game.exe_folder) if f)), None)
    if steam:
        identity.steam_appid = steam[0]
        add('steam', steam[1])
    gog = gog_info(folders)
    if gog:
        identity.gog_id = gog[0]
        add('gog', gog[1])
    add('epic', epic_title([f for f in (game.folder, game.exe_folder) if f]))
    add('xbox', xbox_title(folders))
    add('ea', ea_title(folders))
    place = game.exe_folder or game.folder
    exe = main_exe(place) if place else None
    product = read_product_name(exe) if exe else None
    if product and not generic_product(product):
        add('exe', product)
    add('name', game.name)
    if game.folder:
        add('name', tidy_folder_name(ntpath.basename(game.folder.rstrip('\\/'))))
    seen = set()
    for source, title in found:
        if normalise(title) and normalise(title) not in seen:
            seen.add(normalise(title))
            identity.titles.append(title)
    identity.source = found[0][0] if found else 'name'
    return identity
