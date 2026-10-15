"""Print the VERSION baked into a PyInstaller AMDLSS build and whether the cover/Pillow modules are bundled."""
import re
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

source = (Path(__file__).resolve().parent.parent / 'amdlss' / '__init__.py').read_text(encoding='utf-8')
VERSION = re.search(r"^VERSION = '([^']+)'", source, re.M).group(1)

pyz = CArchiveReader(sys.argv[1]).open_embedded_archive('PYZ.pyz')
names = set(pyz.toc)
code = pyz.extract('amdlss')  # the package __init__
print('VERSION', VERSION if VERSION in code.co_consts else f'{VERSION} MISSING (baked: '
      f'{[c for c in code.co_consts if isinstance(c, str) and c[:1].isdigit()]})')
print('modules', all(m in names for m in ('amdlss.covers', 'amdlss.identify', 'amdlss.helixsr', 'amdlss.reports', 'PIL.ImageTk',
                                          'PIL._tkinter_finder')))
