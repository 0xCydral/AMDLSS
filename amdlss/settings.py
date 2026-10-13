# SPDX-License-Identifier: GPL-2.0-or-later
"""AMDLSS's own files: the data folder (HelixSR is extracted into it), the games added by hand and the update switch."""
import json
import os
from pathlib import Path

from . import host


def data_dir(windows=None):
    """The AMDLSS data folder (%LOCALAPPDATA%\\AMDLSS, or $XDG_DATA_HOME/AMDLSS), or AMDLSS_DATA_DIR."""
    override = os.environ.get('AMDLSS_DATA_DIR')
    return Path(override) if override else host.data_home(windows) / 'AMDLSS'


def _read(folder):
    try:
        data = json.loads((Path(folder) / 'settings.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(folder, key, value):
    """Writes one key of settings.json and keeps the others."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    data = {**_read(folder), key: value}
    (Path(folder) / 'settings.json').write_text(json.dumps(data, indent=1), encoding='utf-8')


def load_folders(folder):
    """The game folders added with "Add game folder", without the ones that no longer exist."""
    listed = _read(folder).get('folders')
    return [f for f in listed if isinstance(f, str) and Path(f).is_dir()] if isinstance(listed, list) else []


def save_folders(folder, folders):
    _save(folder, 'folders', list(folders))


def load_beta(folder, default):
    """The "Beta versions" switch: what the user saved, else `default`."""
    value = _read(folder).get('beta_versions')
    return value if isinstance(value, bool) else default


def save_beta(folder, value):
    _save(folder, 'beta_versions', bool(value))


def load_covers(folder):
    """The "Show game covers" switch: on unless the user turned it off."""
    value = _read(folder).get('show_covers')
    return value if isinstance(value, bool) else True


def save_covers(folder, value):
    _save(folder, 'show_covers', bool(value))
