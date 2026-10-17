"""Print the VERSION baked into a PyInstaller AMDLSS build, whether the cover/Pillow modules are bundled, and whether the
DirectX 12 DLSS add-on and its ini are inside it (the add-on byte for byte the one probe/dx12/dlss/build.sh made).
Exits 1 when the add-on or its ini is missing or the add-on is not a plausible size."""
import hashlib
import re
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
source = (ROOT / 'amdlss' / '__init__.py').read_text(encoding='utf-8')
VERSION = re.search(r"^VERSION = '([^']+)'", source, re.M).group(1)
ADDON, INI, FOLDER = 'amdlss_dx12_dlss.addon64', 'amdlss_dx12_dlss.ini', 'dx12dlss'
MIN_ADDON, MAX_ADDON = 100 << 10, 50 << 20   # the build is about 470 KB

archive = CArchiveReader(sys.argv[1])
pyz = archive.open_embedded_archive('PYZ.pyz')
names = set(pyz.toc)
code = pyz.extract('amdlss')  # the package __init__
print('VERSION', VERSION if VERSION in code.co_consts else f'{VERSION} MISSING (baked: '
      f'{[c for c in code.co_consts if isinstance(c, str) and c[:1].isdigit()]})')
print('modules', all(m in names for m in ('amdlss.covers', 'amdlss.identify', 'amdlss.helixsr', 'amdlss.reports',
                                          'amdlss.dx12inject', 'PIL.ImageTk', 'PIL._tkinter_finder')))

bundled = {name.replace(chr(92), '/'): name for name in archive.toc}   # a Windows build may key with backslashes
failed = False
for file in (ADDON, INI):
    key = bundled.get(f'{FOLDER}/{file}')
    if key is None:
        print(file, 'MISSING from the bundle')
        failed = True
        continue
    data = archive.extract(key)
    built = ROOT / 'probe' / 'dx12' / 'dlss' / ('out' if file == ADDON else '') / file
    shown = built.relative_to(ROOT).as_posix()
    same = ('identical to ' + shown if built.is_file() and built.read_bytes() == data
            else 'DIFFERENT from ' + shown if built.is_file() else 'not compared')
    plausible = file != ADDON or (MIN_ADDON <= len(data) <= MAX_ADDON and data[:2] == b'MZ')
    print(file, f'present, {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}, {same}',
          '' if plausible else 'IMPLAUSIBLE (not a PE file or outside the expected size)')
    failed = failed or not plausible or same.startswith('DIFFERENT')
sys.exit(1 if failed else 0)
