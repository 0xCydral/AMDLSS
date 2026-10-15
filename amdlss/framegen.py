# SPDX-License-Identifier: GPL-2.0-or-later
"""The two small OptiScaler.ini switches AMDLSS makes for games installed through the OptiScaler route.

Frame generation (opt-in). Some games (Dragon's Dogma 2) offer only NVIDIA's DLSS Frame Generation, which cannot run on
AMD. OptiScaler 0.9.4 can take the game's DLSS FG calls as its input and run AMD's FSR frame generation as the output:

    [FrameGen]  Enabled=true  FGInput=dlssg  FGOutput=fsrfg  PreserveSwapChain=false

Enabled is OptiScaler's own frame generation switch: false by default, and in 0.9.4 only its overlay menu turns it on
(menu_common.cpp); the game's frame generation setting never does. With it on, OptiScaler generates frames whenever
the game sends DLSS data, so the game's own FSR frame generation must stay off or two frame generators run at once.

PreserveSwapChain is OptiScaler's "keep the frame generation swapchain when the game releases it" (auto means true).
Tester logs of Assassin's Creed Shadows show that path, then a flood of repeated frame ids and a crash, and OptiScaler's
own game list turns it off for DLSSG games such as Control Resonant. The line is optional: an ini without it is left
as it is.

Logging (on after every OptiScaler install, so "it looks unchanged in game" can be answered from OptiScaler.log):

    [Log]  LogToFile=true  LogLevel=2

HelixSR's installer already puts the files that needs next to the game's .exe (OptiScaler.ini, fakenvapi,
amd_fidelityfx_framegeneration_dx12.dll, and its [Libraries] path to the last one). This module edits only those
values in the OptiScaler.ini HelixSR placed there; every other byte of the file stays as it was. The values the file
held before are kept in AMDLSS's data folder (framegen.json), and a game with frame generation off has the off values
written out (see OFF): Scaler reads its own auto as "run HelixSR's frame generation once the helixsr-fg files are
there", so off must be said, or every Scaler install turns DLSS 4 frame generation on by itself.
HelixSR's own uninstall restores or deletes OptiScaler.ini, so Remove needs no help from here beyond forget() dropping
the records (and the OptiScaler.log that our logging switch made).
"""
import json
import os
import re
from collections import namedtuple
from pathlib import Path

from .helixsr import HelixSRError

# One switch: the [section] and the values that mean "on"; `tag` keeps its record apart in framegen.json; `optional`
# names the keys an ini may lack (they are skipped, not an error).
Setting = namedtuple('Setting', 'section values tag optional', defaults=((),))
ON = {'FGInput': 'dlssg', 'FGOutput': 'fsrfg'}
SECTION = b'FrameGen'
FRAMEGEN = Setting(SECTION, {'Enabled': 'true', **ON, 'PreserveSwapChain': 'false'}, '',
                   ('Enabled', 'PreserveSwapChain'))
LOGGING = Setting(b'Log', {'LogToFile': 'true', 'LogLevel': '2'}, '#log')
# amdlss.luma's route: OptiScaler loads the ReShade build Luma UE is (renamed ReShade64.dll) from the game's .exe
# folder. Scaler's own ini ships "LoadReshade = auto", which OptiScaler reads as false, so the key must be written.
RESHADE = Setting(b'Plugins', {'LoadReshade': 'true'}, '#reshade')
# The frame generation generator: AMD FSR's (OptiScaler turns the game's DLSS Frame Generation into FSR frames) or
# HelixSR's own experimental DLSS 4 mode (the game's DLSS Frame Generation runs HelixSR's network on any GPU, from
# the helixsr-fg files; FGInput=nvngxfg makes OptiScaler leave FGOutput alone, dllmain.cpp).
AMD, DLSS4 = 'amd', 'dlss4'
HELIX_FG = Setting(b'HelixSR', {'FrameGeneration': 'on'}, '#helixsrfg')
DLSS4_FG = Setting(b'FrameGen', {'FGInput': 'nvngxfg', 'FGNvngxReplacement': 'HelixSR'}, '#dlss4')
NAMES = {AMD: 'AMD FSR', DLSS4: 'DLSS 4 (HelixSR)'}   # for reports, the window and the game's log
# What each switch says when frame generation is off. The ini Scaler ships says auto, and Scaler reads auto as "run
# HelixSR's frame generation" once the helixsr-fg files are there (its own ini: "[HelixSR] FrameGeneration=off"
# switches it off; FGInput's default is nofg, Enabled's is false). A game with no generator chosen must say so, or
# DLSS 4 frame generation turns itself on in every Scaler install.
OFF = ((FRAMEGEN, {'Enabled': 'false'}),
       (HELIX_FG, {'FrameGeneration': 'off'}),
       (DLSS4_FG, {'FGInput': 'nofg'}))


def off_values(setting):
    """The setting's off values (OFF)."""
    for candidate, values in OFF:
        if candidate == setting:
            return values
    return {}
RECORD = 'framegen.json'
INI = 'OptiScaler.ini'
LOG = 'OptiScaler.log'


def ini_path(exe_folder):
    return Path(exe_folder) / INI


def _section(data, section):
    """(start, end) of the [section] body in `data`, or None."""
    head = re.search(rb'(?m)^\[' + section + rb'\][ \t]*\r?$', data)
    if not head:
        return None
    nxt = re.search(rb'(?m)^\[', data[head.end():])
    return head.end(), head.end() + nxt.start() if nxt else len(data)


def _key(name):
    return re.compile(rb'(?m)^' + re.escape(name.encode()) + rb'[ \t]*=[ \t]*(?P<val>[^\r\n]*?)[ \t]*(?=\r?$)')


def read_values(data, setting=FRAMEGEN):
    """{key: value} of the setting's keys in its section of the ini bytes (an optional key without a line is left out)."""
    span = _section(data, setting.section)
    values = {}
    for name in setting.values:
        match = _key(name).search(data, *span) if span else None
        if match:
            values[name] = match.group('val').decode('utf-8', errors='replace')
        elif name not in setting.optional:
            raise HelixSRError(f'{INI} has no {name} line in [{setting.section.decode()}]')
    return values


def set_values(data, values, setting=FRAMEGEN):
    """The ini bytes with the setting's keys set to `values`; every other byte is unchanged."""
    read_values(data, setting)  # raises when a required line is missing
    for name, value in values.items():
        start, end = _section(data, setting.section)
        match = _key(name).search(data, start, end)
        if match:
            data = data[:match.start('val')] + value.encode() + data[match.end('val'):]
    return data


def _holds(current, setting, names):
    """True when every one of `names` has the setting's "on" value in `current`."""
    return all(current.get(name) == setting.values[name] for name in names)


def is_on(exe_folder, setting=FRAMEGEN):
    """True when the game's OptiScaler.ini holds the setting's required "on" values (False when unreadable). An
    optional key still at its old value does not count, so a game switched on before it existed shows as on."""
    try:
        current = read_values(ini_path(exe_folder).read_bytes(), setting)
        return _holds(current, setting, [name for name in setting.values if name not in setting.optional])
    except (OSError, HelixSRError):
        return False


def name_of(generator):
    """The generator's name for reports, the window and the game's log; 'off' for none."""
    return NAMES.get(generator, 'off')


def generator_of(exe_folder):
    """Which frame generation the game's OptiScaler.ini runs: 'dlss4' (HelixSR's own, through the game's DLSS
    Frame Generation), 'amd' (AMD FSR's), or '' when neither is switched on."""
    if is_on(exe_folder, HELIX_FG) and is_on(exe_folder, DLSS4_FG):
        return DLSS4
    return AMD if is_on(exe_folder, FRAMEGEN) else ''


def write_off(exe_folder, setting):
    """Writes the setting's own "off" values (OFF), so it never stays at Scaler's auto, which Scaler resolves to
    HelixSR's frame generation once its files are there. A file without the setting's section cannot hold them and
    is left as it is. Returns True when the file changed."""
    path = ini_path(exe_folder)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise HelixSRError(f'cannot read {path}: {error}')
    try:
        current = read_values(data, setting)
    except HelixSRError:
        return False
    values = off_values(setting)
    if all(current.get(name) == value for name, value in values.items()):
        return False
    new = set_values(data, values, setting)
    if new == data:
        return False   # the keys have no line in this file: nothing to write
    _write(path, new)
    return True


def apply_generator(exe_folder, generator, data_dir):
    """Puts exactly one frame generation generator on — or all of them off for '' — in the game's OptiScaler.ini.
    Every switch that is not wanted goes back to what the file held first (the records) and then to its own off
    values, so none is left at Scaler's auto and switching back comes to exactly the same bytes; the wanted one
    goes on last, so a switch never leaves a mix. Returns True when the file changed."""
    chosen = {AMD: (FRAMEGEN,), DLSS4: (HELIX_FG, DLSS4_FG)}.get(generator, ())
    changed = False
    for setting in (FRAMEGEN, HELIX_FG, DLSS4_FG):
        if setting not in chosen:
            changed = apply(exe_folder, False, data_dir, setting) or changed   # the values the file held first
            changed = write_off(exe_folder, setting) or changed                # ...and never Scaler's auto
    for setting in chosen:
        changed = apply(exe_folder, True, data_dir, setting) or changed
    return changed


# What AMDLSS's DLSS 4 keys are put to when the file holds them but no record of what it held is left (a user or
# another tool wrote them): each switch's own off values and the replacement back to the package's auto. They must
# never be left pointing at HelixSR's frame generation files that are not there, nor at Scaler's auto either.
DLSS4_OFF = ((HELIX_FG, off_values(HELIX_FG)),
             (DLSS4_FG, {'FGInput': 'nofg', 'FGNvngxReplacement': 'auto'}))


def _holds_any(exe_folder, setting):
    """True when the file holds any one of the setting's "on" values (the other may already be off)."""
    try:
        current = read_values(ini_path(exe_folder).read_bytes(), setting)
    except (OSError, HelixSRError):
        return False
    return any(current.get(name) == value for name, value in setting.values.items())


def clear_dlss4(exe_folder, data_dir=None):
    """Puts AMDLSS's DLSS 4 values to off wherever the file holds them without a record to restore (see DLSS4_OFF)
    and drops any record for them, so nothing points at HelixSR's frame generation files. Returns True when the file
    changed."""
    changed, dropped = False, False
    records = _load(data_dir) if data_dir else {}
    for setting, defaults in DLSS4_OFF:
        if not _holds_any(exe_folder, setting):
            continue
        path = ini_path(exe_folder)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise HelixSRError(f'cannot read {path}: {error}')
        current = read_values(data, setting)
        wanted = {name: defaults.get(name, value) for name, value in current.items()}
        if wanted != current:
            _write(path, set_values(data, wanted, setting))
            changed = True
        key = _record_key(exe_folder, setting)
        if key in records:
            del records[key]
            dropped = True
    if dropped:
        _save(data_dir, records)
    return changed


# --- the record of what the file held before ---------------------------------------------------------------------

def _record_key(exe_folder, setting=FRAMEGEN):
    return os.path.normcase(os.path.realpath(ini_path(exe_folder))) + setting.tag


def _load(data_dir):
    try:
        loaded = json.loads((Path(data_dir) / RECORD).read_text(encoding='utf-8'))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data_dir, records):
    path = Path(data_dir) / RECORD
    if not records:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=1), encoding='utf-8')


def _write(path, data):
    temporary = path.with_name(path.name + '.amdlss')
    temporary.write_bytes(data)
    os.replace(temporary, path)


def apply(exe_folder, on, data_dir, setting=FRAMEGEN):
    """Switches the setting on or off in the ini next to the game's .exe. On: remembers the values the file holds
    (unless it already holds ours) and sets ours; a key already ours keeps the value remembered earlier, so a game
    switched on by an older AMDLSS gains a new key without losing what it first held. Off: puts the remembered values
    back, only while the file still holds ours. Returns True when the file changed."""
    path = ini_path(exe_folder)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise HelixSRError(f'cannot read {path}: {error}')
    if not on:
        try:
            current = read_values(data, setting)
        except HelixSRError:
            return False   # off only puts our own values back: a file without the section cannot hold them
    else:
        current = read_values(data, setting)
    records, key = _load(data_dir), _record_key(exe_folder, setting)
    if on:
        if _holds(current, setting, current):
            return False
        old = records.get(key, {})
        records[key] = {n: old[n] if n in old and v == setting.values[n] else v for n, v in current.items()}
        _write(path, set_values(data, setting.values, setting))
        _save(data_dir, records)
        return True
    before = records.pop(key, None)
    if before is not None:
        _save(data_dir, records)
    if before is None or not _holds(current, setting, before):
        return False
    _write(path, set_values(data, before, setting))
    return True


def forget(exe_folder, data_dir):
    """Drops every record of a game HelixSR was just removed from (this module's and amdlss.fsr4's). When our logging
    switch was on there, the OptiScaler.log it made is deleted too (HelixSR deletes the log only for an OptiScaler.ini
    it created itself)."""
    records = _load(data_dir)
    base = _record_key(exe_folder)
    logged = _record_key(exe_folder, LOGGING) in records
    gone = [key for key in records if key.startswith(base)]
    for key in gone:
        del records[key]
    if gone:
        _save(data_dir, records)
    if logged:
        (Path(exe_folder) / LOG).unlink(missing_ok=True)
