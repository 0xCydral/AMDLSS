# SPDX-License-Identifier: GPL-2.0-or-later
"""Which Radeon family the PC has, because AMD's FSR 4 DLL runs FSR 4 only on some of them.

AMD's amd_fidelityfx_upscaler_dx12.dll 4.1.1 carries an FP8 FSR 4 for RDNA 4 (RX 9000) and an INT8 FSR 4 for RDNA 3
desktop cards (RX 7000); on any other card its API quietly uses FSR 3.1.5 instead. RDNA 2 (RX 6000) is not covered by AMD's
DLL at all. So AMDLSS classifies the cards and offers FSR 4.1.1 only where it is real.

The classes come from the PCI device ID (Windows: the VEN_ and DEV_ parts of the video controller's PNPDeviceID; Linux:
/sys/class/drm/card*/device), with the card's name as a fallback for an ID this list does not know. The ID lists are
read off libdrm's amdgpu.ids (the file Mesa uses, libdrm 2.4.134, /usr/share/libdrm/amdgpu.ids). RDNA 3 integrated
graphics (780M, 890M and the like) are 'other': AMD's INT8 path is for the RX 7000 desktop cards.

Pure functions plus one PowerShell call and one sysfs read, both injectable, so tests never touch the real system.
"""
import json
import re
import subprocess
from collections import namedtuple
from pathlib import Path

from . import host

RDNA4, RDNA3, RDNA2, OTHER = 'rdna4', 'rdna3', 'rdna2', 'other'
RANK = (OTHER, RDNA2, RDNA3, RDNA4)   # worst to best: with several cards the best one counts
TEXT = {RDNA4: 'RX 9000 (RDNA 4)', RDNA3: 'RX 7000 (RDNA 3)', RDNA2: 'RX 6000 (RDNA 2)',
        OTHER: 'not an RX 9000, RX 7000 or RX 6000 card'}
AMD = '1002'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Gpu(name, vendor, device): vendor and device are four lower-case hex digits, '' when unknown.
Gpu = namedtuple('Gpu', 'name vendor device')

_RDNA4_IDS = {0x7550, 0x7551, 0x7590}                                       # Navi 48 and Navi 44: RX 9070, 9060, AI PRO
_RDNA3_IDS = {0x744c, 0x747e, 0x7480, 0x7483, 0x7499,                       # Navi 31, 32, 33: RX 7900, 7800/7700, 7600, 7400
              0x7448, 0x7449, 0x744a, 0x744b, 0x745e, 0x7470, 0x7489}       # the Radeon PRO W7000 cards on the same dies
_RDNA2_IDS = set(range(0x73a0, 0x7400)) | set(range(0x7420, 0x7440))        # Navi 21, 22, 23, 24: RX 6950 down to RX 6300
_BY_NAME = ((RDNA4, re.compile(r'\bRX\s*9\d{3}|AI PRO R9\d{3}', re.I)),
            (RDNA3, re.compile(r'\bRX\s*7\d{3}|PRO W7\d{3}', re.I)),
            (RDNA2, re.compile(r'\bRX\s*6\d{3}', re.I)))
_PNP = re.compile(r'VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})', re.I)
_QUERY = ('Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID | ConvertTo-Json -Compress')


def classify_one(card):
    """The family of one card: its device ID when this list knows it, else its name; 'other' for anything not AMD."""
    if card.vendor not in (AMD, ''):
        return OTHER
    if card.vendor == AMD and card.device:
        number = int(card.device, 16)
        for family, known in ((RDNA4, _RDNA4_IDS), (RDNA3, _RDNA3_IDS), (RDNA2, _RDNA2_IDS)):
            if number in known:
                return family
    for family, pattern in _BY_NAME:
        if pattern.search(card.name):
            return family
    return OTHER


def classify(cards):
    """The best family among `cards` ('other' for none)."""
    return max((classify_one(card) for card in cards), key=RANK.index, default=OTHER)


def from_info(entries):
    """Cards from PowerShell's JSON: a list of {Name, PNPDeviceID}, or one bare object when there is one card."""
    entries = [entries] if isinstance(entries, dict) else entries or []
    cards = []
    for entry in entries:
        match = _PNP.search(str(entry.get('PNPDeviceID') or ''))
        vendor, device = (match.group(1).lower(), match.group(2).lower()) if match else ('', '')
        cards.append(Gpu(str(entry.get('Name') or ''), vendor, device))
    return cards


def _powershell(command):
    try:
        done = subprocess.run(command, capture_output=True, timeout=30, creationflags=NO_WINDOW, env=host.clean_env())
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return done.stdout.decode('utf-8', errors='replace')


def read_windows(run=None):
    """The video controllers PowerShell lists; [] when the query fails. `run(command)` returns its output text."""
    out = (run or _powershell)(['powershell', '-NoProfile', '-NonInteractive', '-Command', _QUERY])
    try:
        return from_info(json.loads(out))
    except (ValueError, AttributeError):
        return []


def read_sysfs(root='/sys/class/drm'):
    """The cards under /sys/class/drm: vendor and device IDs only (Linux has no card name in one standard place)."""
    cards = []
    try:
        folders = sorted(path for path in Path(root).iterdir() if re.fullmatch(r'card\d+', path.name))
    except OSError:
        return cards
    for folder in folders:
        try:
            vendor, device = ((folder / 'device' / part).read_text().strip().lower().removeprefix('0x')
                              for part in ('vendor', 'device'))
        except OSError:
            continue
        cards.append(Gpu('', vendor, device))
    return cards


def detect(windows=None, run=None, sysfs='/sys/class/drm'):
    """The PC's cards: PowerShell on Windows, sysfs on Linux."""
    return read_windows(run) if host.is_windows(windows) else read_sysfs(sysfs)
