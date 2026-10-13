"""Print the VERSION baked into a PyInstaller AMDLSS build and whether the cover/Pillow modules are bundled."""
import sys

from PyInstaller.archive.readers import CArchiveReader

pyz = CArchiveReader(sys.argv[1]).open_embedded_archive('PYZ.pyz')
names = set(pyz.toc)
code = pyz.extract('amdlss')  # the package __init__
print('VERSION', next(c for c in code.co_consts if isinstance(c, str) and c.startswith('0.1.0')))
print('modules', all(m in names for m in ('amdlss.covers', 'amdlss.identify', 'amdlss.helixsr', 'amdlss.reports', 'PIL.ImageTk',
                                          'PIL._tkinter_finder')))
