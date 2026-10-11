# SPDX-License-Identifier: GPL-2.0-or-later
"""AMDLSS's own files: the data folder (HelixSR is extracted into it) and the games added by hand."""
import json
import os
from pathlib import Path


def data_dir():
    """The AMDLSS folder in %LOCALAPPDATA%, or AMDLSS_DATA_DIR."""
    override = os.environ.get('AMDLSS_DATA_DIR')
    return Path(override) if override else Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AMDLSS'


def load_folders(folder):
    """The game folders added with "Add game folder", without the ones that no longer exist."""
    try:
        data = json.loads((Path(folder) / 'settings.json').read_text(encoding='utf-8'))
        return [f for f in data.get('folders', []) if isinstance(f, str) and Path(f).is_dir()]
    except (OSError, ValueError, AttributeError):
        return []


def save_folders(folder, folders):
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / 'settings.json').write_text(json.dumps({'folders': list(folders)}, indent=1), encoding='utf-8')
