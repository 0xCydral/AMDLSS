# SPDX-License-Identifier: GPL-2.0-or-later
"""HTTPS trust (amdlss/net.py): a PC whose certificate store lacks GitHub's root CAs still reaches GitHub."""
import urllib.request

import pytest

from amdlss import net

pytest.importorskip('certifi')

# api.github.com chains to USERTrust ECC, the release downloads to ISRG Root X1 (both measured 2026-10-24).
GITHUB_ROOTS = {'USERTrust ECC Certification Authority', 'ISRG Root X1'}


def common_names(context):
    return {dict(pair[0] for pair in cert['subject']).get('commonName') for cert in context.get_ca_certs()}


@pytest.fixture
def empty_store(tmp_path, monkeypatch):
    """The system store with no roots at all: the worst case of a Windows PC that never cached GitHub's."""
    (tmp_path / 'empty.pem').write_text('')
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'empty.pem'))
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path))


def test_context_trusts_github_roots_with_an_empty_system_store(empty_store):
    assert GITHUB_ROOTS <= common_names(net.context())


def test_install_makes_urlopen_use_that_context(empty_store, monkeypatch):
    installed = []
    monkeypatch.setattr(urllib.request, 'install_opener', installed.append)
    net.install()
    (handler,) = [h for h in installed[0].handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert GITHUB_ROOTS <= common_names(handler._context)


def test_selftest_check_net_holds_in_a_frozen_build(monkeypatch):
    from amdlss import selftest, update
    monkeypatch.setattr(update, 'FROZEN', True)
    selftest.check_net()
