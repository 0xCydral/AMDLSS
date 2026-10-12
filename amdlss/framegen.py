# SPDX-License-Identifier: GPL-2.0-or-later
"""The two small OptiScaler.ini switches AMDLSS makes for games installed through the OptiScaler route.

Frame generation (opt-in). Some games (Dragon's Dogma 2) offer only NVIDIA's DLSS Frame Generation, which cannot run on
AMD. OptiScaler 0.9.4 can take the game's DLSS FG calls as its input and run AMD's FSR frame generation as the output:

    [FrameGen]  FGInput=dlssg  FGOutput=fsrfg

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

# One switch: the [section] and the values that mean "on"; `tag` keeps its record apart in framegen.json.
Setting = namedtuple('Setting', 'section values tag')
ON = {'FGInput': 'dlssg', 'FGOutput': 'fsrfg'}
SECTION = b'FrameGen'
FRAMEGEN = Setting(SECTION, ON, '')
LOGGING = Setting(b'Log', {'LogToFile': 'true', 'LogLevel': '2'}, '#log')
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
    """{key: value} of the setting's keys in its section of the ini bytes."""
    span = _section(data, setting.section)
    values = {}
    for name in setting.values:
        match = _key(name).search(data, *span) if span else None
        if not match:
            raise HelixSRError(f'{INI} has no {name} line in [{setting.section.decode()}]')
        values[name] = match.group('val').decode('utf-8', errors='replace')
    return values


def set_values(data, values, setting=FRAMEGEN):
    """The ini bytes with the setting's keys set to `values`; every other byte is unchanged."""
    read_values(data, setting)  # raises when a line is missing
    for name, value in values.items():
        start, end = _section(data, setting.section)
        match = _key(name).search(data, start, end)
        data = data[:match.start('val')] + value.encode() + data[match.end('val'):]
    return data


def is_on(exe_folder, setting=FRAMEGEN):
    """True when the game's OptiScaler.ini already holds the setting's "on" values (False when unreadable)."""
    try:
        return read_values(ini_path(exe_folder).read_bytes(), setting) == setting.values
    except (OSError, HelixSRError):
        return False


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
    (unless it already holds ours) and sets ours. Off: puts the remembered values back, only while the file still holds
    ours. Returns True when the file changed."""
    path = ini_path(exe_folder)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise HelixSRError(f'cannot read {path}: {error}')
    current = read_values(data, setting)
    records, key = _load(data_dir), _record_key(exe_folder, setting)
    if on:
        if current == setting.values:
            return False
        records[key] = current
        _write(path, set_values(data, setting.values, setting))
        _save(data_dir, records)
        return True
    before = records.pop(key, None)
    if before is not None:
        _save(data_dir, records)
    if current != setting.values or before is None:
        return False
    _write(path, set_values(data, before, setting))
    return True


def forget(exe_folder, data_dir):
    """Drops the records of a game HelixSR was just removed from. When our logging switch was on there, the
    OptiScaler.log it made is deleted too (HelixSR deletes the log only for an OptiScaler.ini it created itself)."""
    records = _load(data_dir)
    changed = records.pop(_record_key(exe_folder), None) is not None
    logged = records.pop(_record_key(exe_folder, LOGGING), None) is not None
    if changed or logged:
        _save(data_dir, records)
    if logged:
        (Path(exe_folder) / LOG).unlink(missing_ok=True)
