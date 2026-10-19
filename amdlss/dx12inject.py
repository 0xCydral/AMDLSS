# SPDX-License-Identifier: GPL-2.0-or-later
"""Inject DLSS for DirectX 12: AMDLSS's own ReShade add-on (probe/dx12/dlss) as an install route.

Luma UE (amdlss.luma) is DirectX 11 only; an Unreal Engine 5 game that runs DirectX 12 and ships no upscaler of its own
keeps its temporal upscaler (TSR). The add-on replaces that TSR pass with a DLSS Super Resolution call: real NVIDIA
DLSS on a GeForce card, and on any other card HelixSR Scaler (OptiScaler) catches the call and runs HelixSR, the way
it does for a game's own DLSS.

Two layouts, chosen by the PC's graphics cards (amdlss.gpu), all next to the game's .exe:

  NVIDIA (any GeForce in the PC):   ReShade as dxgi.dll, amdlss_dx12_dlss.addon64, amdlss_dx12_dlss.ini and NVIDIA's
                                    nvngx_dlss.dll. No Scaler.
  anything else (AMD, Intel, ...):  HelixSR Scaler as dxgi.dll (amdlss.scaler, as in the Luma route), ReShade as
                                    ReShade64.dll, amdlss_dx12_dlss.addon64, amdlss_dx12_dlss.ini,
                                    HelixSR-fsrfg\\amd_fidelityfx_dx12.dll, and [Plugins] LoadReshade=true in
                                    OptiScaler.ini. Scaler 1.1.0 makes HelixSR its FidelityFX upscaler on its own, so
                                    the add-on's DLSS calls reach it (Upscalers auto).

ReShade 6.8.0.1 (with add-on support) and NVIDIA's nvngx_dlss.dll are not bundled: they come out of Luma's pinned
release zip, which amdlss.luma already fetches and checks. The add-on and its ini are AMDLSS's own files, bundled in
the release binaries (dx12dlss\\ inside the PyInstaller bundle) and, from source, built by
`bash probe/dx12/dlss/build.sh` into probe/dx12/dlss/out.

What this module placed is recorded in HelixSR\\amdlss-dx12dlss.json (moved and created files with SHA-256s), so
Remove puts every file back exactly as amdlss.luma's record does. The DLSS mode is the add-on's own `render_scale`
key: a new install writes `quality`, an Update keeps whatever the player set in the add-on's tab.
"""
import json
import os
import re
import sys
from pathlib import Path

from . import VERSION as APP_VERSION
from . import framegen, games as gamelist, gpu, helixsr, luma, scaler
from .helixsr import HelixSRError

NAME = 'DLSS add-on (DirectX 12)'
FULL_NAME = "AMDLSS's DirectX 12 DLSS add-on"
ADDON = 'amdlss_dx12_dlss.addon64'
INI = 'amdlss_dx12_dlss.ini'
BUNDLE = 'dx12dlss'                           # the folder of the PyInstaller bundle that holds both files
SOURCE = ('probe', 'dx12', 'dlss')            # ... and where they are in the source tree (the add-on in out/)
BUILD_COMMAND = 'bash probe/dx12/dlss/build.sh'
RECORD = 'amdlss-dx12dlss.json'               # AMDLSS's record, inside the game's HelixSR folder
NVIDIA = '10de'                               # PCI vendor ID
NVIDIA_LAYOUT, OTHER_LAYOUT = 'nvidia', 'amd'
RESHADE = luma.SCALER_NAME                    # ReShade's file name in Luma's zip (dxgi.dll)
DLSS = luma.DLSS                              # NVIDIA's DLSS runtime, from the same zip
DEFAULT_SCALE = b'quality'                    # the add-on's render_scale AMDLSS writes on a new install
RUNTIME = ('amdlss_dx12_dlss.log', 'amdlss_dx12_dlss.dump')   # what the add-on makes next to itself while it runs
TESTED = 'Echoes of Aincrad (Unreal Engine 5, TSR) on an NVIDIA card'


# --- the bundled files and the layout -----------------------------------------------------------------------------

def bundled(name):
    """The shipped file `name` (ADDON or INI): inside the PyInstaller bundle, or in the source tree; None when it is
    not there (the add-on is built, not committed)."""
    frozen = getattr(sys, '_MEIPASS', None)
    if frozen:
        path = Path(frozen) / BUNDLE / name
    else:
        folder = Path(__file__).resolve().parent.parent.joinpath(*SOURCE)
        path = folder / 'out' / name if name == ADDON else folder / name
    return path if path.is_file() else None


def not_built_text():
    return (f'{FULL_NAME} is not built in this copy of AMDLSS ({ADDON} is missing). The release downloads include it; '
            f'from the source, build it with: {BUILD_COMMAND} (needs Docker).')


def layout(cards):
    """'nvidia' when any of the PC's cards is a GeForce (a laptop with an Intel or AMD iGPU next to one counts), else
    'amd': the add-on with HelixSR Scaler. A PC whose cards could not be read gets the Scaler layout, which runs
    real DLSS on a GeForce by itself."""
    return NVIDIA_LAYOUT if any(card.vendor == NVIDIA for card in cards) else OTHER_LAYOUT


def reshade_name(kind):
    """What ReShade is called in the game: dxgi.dll on its own, ReShade64.dll when OptiScaler takes dxgi.dll."""
    return RESHADE if kind == NVIDIA_LAYOUT else luma.RESHADE


# --- the ini ------------------------------------------------------------------------------------------------------

_SCALE = re.compile(rb'^[ \t]*render_scale[ \t]*=[ \t]*([^\r\n]*)', re.M)
_SCALE_VALUE = re.compile(rb'[A-Za-z0-9_.]{1,16}')


def kept_scale(exe_folder):
    """The render_scale the player has in the placed ini (set in the add-on's tab), else DEFAULT_SCALE."""
    try:
        found = _SCALE.search((Path(exe_folder) / INI).read_bytes())
    except OSError:
        return DEFAULT_SCALE
    value = found.group(1).strip() if found else b''
    return value if _SCALE_VALUE.fullmatch(value) else DEFAULT_SCALE


def ini_bytes(source, scale=DEFAULT_SCALE):
    """The bundled ini with its render_scale line set to `scale` (every other byte stays as shipped)."""
    return _SCALE.sub(lambda match: b'render_scale=' + scale, Path(source).read_bytes(), count=1)


# --- the record in the game ---------------------------------------------------------------------------------------

def record_path(exe_folder):
    return Path(exe_folder) / scaler.FOLDER / RECORD


def installed(exe_folder):
    return bool(exe_folder) and record_path(exe_folder).is_file()


def _load(exe_folder):
    try:
        record = json.loads(record_path(exe_folder).read_text(encoding='utf-8'))
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def addon_outdated(exe_folder):
    """True when the add-on AMDLSS placed in the game is not the one this AMDLSS carries, so the card asks for Update.
    No install, no record entry or no shipped add-on: False."""
    record, shipped = _load(exe_folder) if installed(exe_folder) else None, bundled(ADDON)
    placed = next((item for item in (record or {}).get('created', []) if item.get('name') == ADDON), None)
    return placed is not None and shipped is not None and placed.get('sha256') != helixsr.sha256_of(shipped)


def install_into(exe_folder, items, record):
    """Places `items` ([(name, source)] where source is a Path to copy or the bytes to write) in `exe_folder`, keeping
    every game file that had to give way in the HelixSR folder's backup, and fills record['moved']/record['created']."""
    exe_folder, backup = Path(exe_folder), Path(exe_folder) / scaler.FOLDER / scaler.BACKUP
    for rel, source in items:
        target = exe_folder / rel
        if target.is_file() or target.is_symlink():
            kept = backup / rel
            kept.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, kept)
            record['moved'].append({'name': rel, 'to': f'{scaler.FOLDER}/{scaler.BACKUP}/{rel}',
                                    'sha256': helixsr.sha256_of(kept)})
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, bytes):
            target.write_bytes(source)
        else:
            luma._put(source, target)
        record['created'].append({'name': rel, 'sha256': helixsr.sha256_of(target)})


def items_for(kind, package, scale=DEFAULT_SCALE):
    """[(name, source)] to place for a layout: ReShade and NVIDIA's DLSS runtime from Luma's unpacked zip (`package`),
    the add-on and its ini (render_scale set) from AMDLSS's own files."""
    addon, ini = bundled(ADDON), bundled(INI)
    if addon is None or ini is None:
        raise HelixSRError(not_built_text())
    items = [(reshade_name(kind), Path(package) / RESHADE), (ADDON, addon), (INI, ini_bytes(ini, scale))]
    if kind == NVIDIA_LAYOUT:
        items.append((DLSS, Path(package) / DLSS))
    return items


def _runtime_files(exe_folder):
    """The files the add-on makes while the game runs: its log and trigger file, the dumped TSR shaders, and NGX's
    logs (their folders go when they are left empty)."""
    exe = Path(exe_folder)
    for name in RUNTIME:
        (exe / name).unlink(missing_ok=True)
    for path in list(exe.glob('tsr_*.dxil')) + list((exe / 'ngx').glob('*.log')):
        path.unlink(missing_ok=True)
    luma._prune(exe, [exe / 'ngx', exe / 'dump_seq'])


def remove(exe_folder):
    """Puts back every file this install replaced in `exe_folder` and deletes what it created (a file the user changed
    after the install is kept and reported - except the ini, the add-on's own settings file, which the tab rewrites).
    Returns the notes. The HelixSR Scaler install of the other layout is a separate record (remove_game does both)."""
    exe_folder = Path(exe_folder)
    record = _load(exe_folder)
    if record is None:
        return []
    for item in record.get('created', []):
        if item['name'] == INI:
            (exe_folder / INI).unlink(missing_ok=True)
    notes = luma._unwind(exe_folder, record)
    _runtime_files(exe_folder)
    record_path(exe_folder).unlink(missing_ok=True)
    luma._prune(exe_folder, {exe_folder / scaler.FOLDER})
    return notes


def mark(game):
    """Sets `game.state` and `game.route` from AMDLSS's record, so an injected game keeps its state (and its Remove)."""
    if game.exe_folder and installed(game.exe_folder):
        game.state, game.route, game.rhi = gamelist.INJECT12, gamelist.INJECT12, gamelist.RHI_DX12
    return game


# --- what the window says ---------------------------------------------------------------------------------------

ROW_NOTE = (f'This game ships no upscaler of its own and runs DirectX 12: AMDLSS injects a DLSS call into it with '
            f'{FULL_NAME} (experimental, tested only in {TESTED}).')
CHOICE_NOTE = ("AMDLSS cannot tell if this game runs DirectX 11 or DirectX 12 ({why}). Luma UE needs DirectX 11, "
               "the DirectX 12 add-on needs DirectX 12: pick the one the game runs.")


def choice_note(game):
    return CHOICE_NOTE.format(why=game.rhi_why or 'nothing in its files says')


def download_text(done, total):
    """What the window says while this route's downloads run: Luma's zip (ReShade and NVIDIA's DLSS runtime), or
    Scaler's package (told apart by its pinned size)."""
    what = 'ReShade and the DLSS runtime' if total != scaler.ZIP_SIZE else f'HelixSR Scaler {scaler.VERSION}'
    return f'Downloading {what}: {done >> 20} of {total >> 20} MB'


def layout_text(kind):
    if kind == NVIDIA_LAYOUT:
        return (f'It puts ReShade (as {RESHADE}), the add-on ({ADDON}, {INI}) and NVIDIA\'s {DLSS} next to the game\'s '
                f'.exe. Your GeForce card runs real DLSS; HelixSR is not used.')
    return (f'It puts HelixSR Scaler {scaler.VERSION} (about {scaler.ZIP_SIZE >> 20} MB, checked by SHA-256, as '
            f'{RESHADE}), ReShade (as {luma.RESHADE}), the add-on ({ADDON}, {INI}) and HelixSR\'s own DLL next to '
            f'the game\'s .exe, and turns on OptiScaler.ini\'s LoadReshade. Scaler sends the add-on\'s DLSS calls to '
            f'HelixSR, which runs them on your card.')


def plan_text(game, cards=None):
    """What installing the add-on will do, before the user agrees."""
    where = game.exe_folder or "the game's .exe folder"
    kind = layout(gpu.detect() if cards is None else cards)
    return (f"AMDLSS downloads ReShade 6.8 with add-on support and NVIDIA's DLSS runtime from Luma Framework's "
            f"release page (about {luma.ZIP_SIZE >> 20} MB, checked by SHA-256, custom MIT licence) and installs "
            f"{FULL_NAME} next to the game's .exe ({where}).\n\n"
            f"{layout_text(kind)}\n\n"
            f"The add-on replaces the game's TSR (Unreal's temporal upscaler) with DLSS, so the game must run "
            f"DirectX 12 with TSR as its anti-aliasing. It is experimental: it was tested only in {TESTED}.\n\n"
            f"Files it replaces are kept in the HelixSR folder's {scaler.BACKUP} and put back by Remove.\n\n"
            f"{gamelist.WARNINGS}\n\nInstall into {game.name}?")


def after_text(game, kind=OTHER_LAYOUT):
    """What to do in the game once the add-on is installed."""
    how = ('Your GeForce card runs real DLSS.' if kind == NVIDIA_LAYOUT
           else f'HelixSR Scaler {scaler.VERSION} runs the DLSS calls on your card (press Insert for its overlay).')
    return (f"{FULL_NAME} is installed in {game.name}. {how}\n\n"
            f"Start the game and press Home: ReShade's overlay opens with a DLSS tab. The add-on is on and the DLSS "
            f"mode is Quality ({INI}: render_scale=quality); change the mode in the tab, and it applies the next "
            f"time the game starts.\n\n"
            f"If the DLSS tab is missing, the game is not running DirectX 12. If the image is unchanged, press "
            f"Collect logs and send the zip (the add-on writes {RUNTIME[0]} next to the game's .exe).\n\n"
            f"It is experimental: it was tested only in {TESTED}. Do not use it in online games or games with "
            f"anti-cheat.\n\nThe Remove button puts the original files back.")


# --- install and remove ------------------------------------------------------------------------------------------

def _say(on_line, notes):
    for note in notes:
        if on_line:
            on_line(note, True)


def _package(data_dir, progress):
    """Luma's unpacked zip (the AMDLSS_LUMA override, the data folder's copy, or a fresh pinned download)."""
    if not data_dir:
        raise HelixSRError('no AMDLSS data folder is set, so ReShade and the DLSS runtime cannot be downloaded')
    return luma.locate(data_dir, os.environ.get(luma.EXTRA, '')) or luma.fetch(data_dir, progress)


def install_game(helix, game, extras=(), data_dir=None, on_line=None, progress=None, cards=None):
    """Installs (or updates) the add-on in `game` and returns the game with its state set. `cards` are the PC's
    graphics cards (amdlss.gpu; read when None) and pick the layout. The folder is safe and the game closed
    (helix.guard_placed). Luma's files first, then (other cards) HelixSR Scaler and OptiScaler.ini's LoadReshade.
    Nothing is left changed when it fails."""
    if game.anticheat:
        raise HelixSRError(f"AMDLSS does not inject into a game with anti-cheat files ({', '.join(game.anticheat)}): "
                           f'the account can be banned.')
    fresh = helix.guard_placed(game, extras)
    if not fresh.exe_folder:
        raise HelixSRError(f"HelixSR does not know {game.name}'s .exe folder, so the add-on has nowhere to go")
    exe = Path(fresh.exe_folder)
    if luma.installed(exe):
        raise HelixSRError(f'Luma UE is installed in {game.name}: press Remove first, then install the DirectX 12 '
                           f'add-on.')
    kind = layout(gpu.detect() if cards is None else cards)
    package = _package(data_dir, progress)
    if not (Path(package) / RESHADE).is_file() or (kind == NVIDIA_LAYOUT and not (Path(package) / DLSS).is_file()):
        raise HelixSRError(f"Luma's package does not hold {RESHADE}" + (f' and {DLSS}' if kind == NVIDIA_LAYOUT else ''))
    scale = kept_scale(exe) if installed(exe) else DEFAULT_SCALE
    items = items_for(kind, package, scale)       # before anything changes: a missing add-on stops here
    if _load(exe) is not None:                    # our own install: take it out first, so the record stays true
        _say(on_line, remove(exe))
        if kind == NVIDIA_LAYOUT and scaler.installed(exe):   # the PC changed layout: Scaler's dxgi.dll must go
            _say(on_line, scaler.remove(exe))
    record = {'amdlss': APP_VERSION, 'layout': kind, 'luma_zip_sha256': luma.ZIP_SHA256, 'moved': [], 'created': []}
    try:
        install_into(exe, items, record)
    except (OSError, HelixSRError) as error:
        luma._unwind(exe, record, force=True)
        raise HelixSRError(f"{NAME} was not installed ({error}); the game's files are as they were.")
    after = fresh
    if kind == OTHER_LAYOUT:
        try:
            after = scaler.install_game(helix, fresh, extras, data_dir, on_line, progress, name=RESHADE, placed=True)
        except HelixSRError as error:
            luma._unwind(exe, record, force=True)
            raise HelixSRError(f"{NAME} was not installed ({error}); the game's files are as they were.")
    record_path(exe).parent.mkdir(parents=True, exist_ok=True)
    record_path(exe).write_text(json.dumps(record, indent=1), encoding='utf-8')
    _say(on_line, [f'injected a DLSS call into {game.name}: {FULL_NAME} with ReShade as {reshade_name(kind)} in {exe}'
                   + (f' and HelixSR Scaler {scaler.VERSION} as {RESHADE}' if kind == OTHER_LAYOUT else '')
                   + f' ({"NVIDIA" if kind == NVIDIA_LAYOUT else "Scaler"} layout; render_scale={scale.decode()})'])
    if kind == OTHER_LAYOUT:
        try:
            if framegen.apply(exe, True, data_dir, framegen.RESHADE):
                _say(on_line, [f'{scaler.INI}: {framegen.ini_path(exe)} now loads {luma.RESHADE} (LoadReshade=true)'])
        except (HelixSRError, OSError) as error:
            raise HelixSRError(f'{NAME} and HelixSR Scaler are installed in {game.name}, but {scaler.INI} could not '
                               f'be set to load it ({error}). Press Remove, then Install again.')
    after.state, after.route, after.upscaler, after.rhi = gamelist.INJECT12, gamelist.INJECT12, '', gamelist.RHI_DX12
    return after


def remove_game(helix, game, extras=(), data_dir=None, on_line=None):
    """Removes the add-on (and the Scaler install, in the other layout) from `game`, putting every file back; returns
    the game as listed after. This record is undone before the Scaler one, since the Scaler folder holds both."""
    fresh = helix.guard_placed(game, extras)
    exe = fresh.exe_folder
    if exe:
        _say(on_line, remove(exe))
        if scaler.installed(exe):
            _say(on_line, scaler.remove(exe))
        if data_dir:
            framegen.forget(exe, data_dir)
    fresh.state, fresh.route, fresh.upscaler = 'none', '', ''
    return fresh
