#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""AMDLSS: install HelixSR once, then one click per DLSS game. Unofficial; see README.md.

    python AMDLSS.pyw              opens the window
    python AMDLSS.pyw --selftest   checks the logic, opens nothing, exits 0 when it holds
"""
import sys

from amdlss.app import main

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
