# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for an unpacked HelixSR Scaler package, so tests run AMDLSS's Scaler install path with no network
and no 132 MB download. make() writes <root>/<scaler.TOP>, exactly where scaler.locate() looks; the
package list names the files its real HelixSR-Scaler-package.txt names, and a few files it does not, so a test can
see that only listed files are copied."""
from pathlib import Path

from amdlss import scaler

TOP = scaler.TOP
LIST = 'HelixSR-Scaler-package.txt'
# The sections AMDLSS touches in the real file, with its own comment lines and CRLFs, so tests read like the real
# ini (shipped by the package, OptiScaler 0.9.4 with HelixSR built in).
INI = (b'; header\r\n'
       b'[HelixSR]\r\n; frame generation\r\nFrameGeneration=auto\r\nInt8=auto\r\n\r\n'
       b'[FrameGen]\r\nEnabled=auto\r\nFGInput=auto\r\nFGOutput=auto\r\nFGNvngxReplacement=auto\r\n'
       b'PreserveSwapChain=auto\r\nFTInput=auto\r\n\r\n'
       b'[Other]\r\nKey = 1\r\nFGInput=decoy\r\n\r\n'
       b'[Log]\r\nLogToFile=auto\r\nLogLevel=auto\r\n\r\n'
       b'[Libraries]\r\nFfxDx12Path=auto\r\nFfxDx12SRPath=auto\r\n\r\n'
       b'[Plugins]\r\n; Loads Reshade64.dll from game\'s exe folder\r\nLoadAsiPlugins=auto\r\nLoadReshade=auto\r\n')
LISTED = ('OptiScaler.dll', 'OptiScaler.ini', 'OptiScaler/amd_fidelityfx_upscaler_dx12.dll',
          'OptiScaler/streamline/sl.common.dll', 'OptiScaler/streamline/sl.dlss_g.dll',
          'OptiScaler/streamline/sl.interposer.dll', 'OptiScaler/D3D12_OptiScaler/D3D12Core.dll',
          'Licenses/OptiScaler_LICENSE.txt', 'Licenses/FidelityFX_v2_LICENSE.md')
UNLISTED = ('README.txt', 'setup_windows.bat', 'setup_linux.sh')


def make(root, listed=LISTED, unlisted=UNLISTED, ini=INI):
    """The unpacked package under `root` (root/HelixSR-Scaler-1.0.3). Its list names the not-installed files too,
    like the real HelixSR-Scaler-package.txt; scaler.files() leaves those out. Returns its folder."""
    folder = Path(root) / TOP
    for rel in listed + unlisted:
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ini if rel == 'OptiScaler.ini' else f'STAND-IN {rel}'.encode())
    (folder / LIST).write_text('\n'.join(listed + unlisted) + '\n', encoding='utf-8')
    return folder
