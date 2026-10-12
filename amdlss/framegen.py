# SPDX-License-Identifier: GPL-2.0-or-later
"""The opt-in frame generation switch for games installed through the OptiScaler route.

Some games (Dragon's Dogma 2) offer only NVIDIA's DLSS Frame Generation, which cannot run on AMD. OptiScaler 0.9.4 can
take the game's DLSS FG calls as its input and run AMD's FSR frame generation as the output:

    [FrameGen]  FGInput=dlssg  FGOutput=fsrfg

HelixSR's installer already puts the files that needs next to the game's .exe (OptiScaler.ini, fakenvapi,
amd_fidelityfx_framegeneration_dx12.dll, and its [Libraries] path to the last one). This module edits only those two
values in the OptiScaler.ini HelixSR placed there; every other byte of the file stays as it was. The values the file
held before are kept in AMDLSS's data folder (framegen.json), so unchecking the box puts them back. HelixSR's own
uninstall restores or deletes OptiScaler.ini, so Remove needs no help from here beyond forget() dropping the record.
"""
import json
import os
import re
from pathlib import Path

from .helixsr import HelixSRError

SECTION = b'FrameGen'
ON = {'FGInput': 'dlssg', 'FGOutput': 'fsrfg'}
RECORD = 'framegen.json'
INI = 'OptiScaler.ini'


def ini_path(exe_folder):
    return Path(exe_folder) / INI


def _section(data):
    """(start, end) of the [FrameGen] section body in `data`, or None."""
    head = re.search(rb'(?m)^\[' + SECTION + rb'\][ \t]*\r?$', data)
    if not head:
        return None
    nxt = re.search(rb'(?m)^\[', data[head.end():])
    return head.end(), head.end() + nxt.start() if nxt else len(data)


def _key(name):
    return re.compile(rb'(?m)^' + re.escape(name.encode()) + rb'[ \t]*=[ \t]*(?P<val>[^\r\n]*?)[ \t]*(?=\r?$)')


def read_values(data):
    """{key: value} of FGInput and FGOutput in the [FrameGen] section of the ini bytes."""
    span = _section(data)
    values = {}
    for name in ON:
        match = _key(name).search(data, *span) if span else None
        if not match:
            raise HelixSRError(f'{INI} has no {name} line in [FrameGen]')
        values[name] = match.group('val').decode('utf-8', errors='replace')
    return values


def set_values(data, values):
    """The ini bytes with FGInput and FGOutput set to `values`; every other byte is unchanged."""
    read_values(data)  # raises when a line is missing
    for name, value in values.items():
        start, end = _section(data)
        match = _key(name).search(data, start, end)
        data = data[:match.start('val')] + value.encode() + data[match.end('val'):]
    return data


def is_on(exe_folder):
    """True when the game's OptiScaler.ini already routes DLSS frame generation to FSR (False when unreadable)."""
    try:
        return read_values(ini_path(exe_folder).read_bytes()) == ON
    except (OSError, HelixSRError):
        return False


# --- the record of what the file held before ---------------------------------------------------------------------

def _record_key(exe_folder):
    return os.path.normcase(os.path.realpath(ini_path(exe_folder)))


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


def apply(exe_folder, on, data_dir):
    """Switches the game's DLSS-to-FSR frame generation on or off in the ini next to its .exe. On: remembers the
    values the file holds (unless it already holds ours) and sets ours. Off: puts the remembered values back, only
    while the file still holds ours. Returns True when the file changed."""
    path = ini_path(exe_folder)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise HelixSRError(f'cannot read {path}: {error}')
    current = read_values(data)
    records, key = _load(data_dir), _record_key(exe_folder)
    if on:
        if current == ON:
            return False
        records[key] = current
        _write(path, set_values(data, ON))
        _save(data_dir, records)
        return True
    before = records.pop(key, None)
    if before is not None:
        _save(data_dir, records)
    if current != ON or before is None:
        return False
    _write(path, set_values(data, before))
    return True


def forget(exe_folder, data_dir):
    """Drops the record of a game HelixSR was just removed from."""
    records = _load(data_dir)
    if records.pop(_record_key(exe_folder), None) is not None:
        _save(data_dir, records)
