# SPDX-License-Identifier: GPL-2.0-or-later
"""No test asks GitHub: the window's start-up update check is switched off for every test."""
import pytest


@pytest.fixture(autouse=True)
def no_update_check(monkeypatch):
    monkeypatch.setenv('AMDLSS_NO_UPDATE_CHECK', '1')
    monkeypatch.delenv('AMDLSS_FAKE_VERSION', raising=False)


@pytest.fixture(autouse=True)
def no_real_graphics_card(monkeypatch, request):
    """No test reads the real card from sysfs (test_gpu.py reads fake trees); a test that wants a card patches
    gpu.read_sysfs itself."""
    from amdlss import gpu
    if request.module.__name__ == 'test_gpu':
        return
    monkeypatch.setattr(gpu, 'read_sysfs', lambda *args, **kwargs: [])
