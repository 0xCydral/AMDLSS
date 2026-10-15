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
held before are kept in AMDLSS's data folder (framegen.json), so unchecking the frame generation box puts them back.
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
# The frame generation generator: AMD FSR's (OptiScaler turns the game's DLSS Frame Generation into FSR frames) or
# HelixSR's own experimental DLSS 4 mode (the game's DLSS Frame Generation runs HelixSR's network on any GPU, from
# the helixsr-fg files; FGInput=nvngxfg makes OptiScaler leave FGOutput alone, dllmain.cpp).
AMD, DLSS4 = 'amd', 'dlss4'
HELIX_FG = Setting(b'HelixSR', {'FrameGeneration': 'on'}, '#helixsrfg')
DLSS4_FG = Setting(b'FrameGen', {'FGInput': 'nvngxfg', 'FGNvngxReplacement': 'HelixSR'}, '#dlss4')
NAMES = {AMD: 'AMD FSR', DLSS4: 'DLSS 4 (HelixSR)'}   # for reports, the window and the game's log
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


def apply_generator(exe_folder, generator, data_dir):
    """Puts exactly one frame generation generator on — or both off for '' — in the game's OptiScaler.ini. The
    other generator's values go back first and the wanted one goes on last, so a switch never leaves a mix; every
    value the file held is kept in this module's records and comes back when it is switched again or turned off.
    Returns True when the file changed."""
    changed = False
    if generator != AMD:
        changed = apply(exe_folder, False, data_dir, FRAMEGEN) or changed
    if generator != DLSS4:
        changed = apply(exe_folder, False, data_dir, HELIX_FG) or changed
        changed = apply(exe_folder, False, data_dir, DLSS4_FG) or changed
    if generator == AMD:
        changed = apply(exe_folder, True, data_dir, FRAMEGEN) or changed
    elif generator == DLSS4:
        changed = apply(exe_folder, True, data_dir, HELIX_FG) or changed
        changed = apply(exe_folder, True, data_dir, DLSS4_FG) or changed
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
