# SPDX-License-Identifier: GPL-2.0-or-later
"""No test asks GitHub: the window's start-up update check is switched off for every test."""
import pytest


@pytest.fixture(autouse=True)
def no_update_check(monkeypatch):
    monkeypatch.setenv('AMDLSS_NO_UPDATE_CHECK', '1')
    monkeypatch.delenv('AMDLSS_FAKE_VERSION', raising=False)
