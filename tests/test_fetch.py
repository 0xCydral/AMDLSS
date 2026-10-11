# SPDX-License-Identifier: GPL-2.0-or-later
"""Download, verification, extraction and consent: nothing here touches the network or HelixSR."""
import hashlib
import io
import zipfile

import pytest

from amdlss import helixsr


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


GOOD = zip_bytes({f'{helixsr.TOP}/{helixsr.SETUP}': 'setup', f'{helixsr.TOP}/{helixsr.DLL}': 'dll'})


def serve(monkeypatch, data, size=None, sha=None):
    """Makes fetch() 'download' `data`, pinned as if it were the real release."""
    monkeypatch.setattr(helixsr, 'ZIP_SIZE', len(data) if size is None else size)
    monkeypatch.setattr(helixsr, 'ZIP_SHA256', hashlib.sha256(data).hexdigest() if sha is None else sha)
    monkeypatch.setattr(helixsr, '_download', lambda part, progress: part.write_bytes(data))


def test_good_zip_is_extracted(tmp_path, monkeypatch):
    serve(monkeypatch, GOOD)
    folder = helixsr.fetch(tmp_path)
    assert folder == tmp_path / helixsr.TOP and helixsr.extracted(tmp_path)
    assert not (tmp_path / '.download').exists()


def test_hash_mismatch_is_rejected(tmp_path, monkeypatch):
    serve(monkeypatch, GOOD, sha='0' * 64)
    with pytest.raises(helixsr.HelixSRError, match='SHA-256'):
        helixsr.fetch(tmp_path)
    assert not (tmp_path / helixsr.TOP).exists()
    assert not list((tmp_path / '.download').glob('*.part'))  # the bad download was deleted


def test_wrong_size_is_rejected(tmp_path, monkeypatch):
    serve(monkeypatch, GOOD, size=len(GOOD) + 1)
    with pytest.raises(helixsr.HelixSRError):
        helixsr.fetch(tmp_path)
    assert not helixsr.extracted(tmp_path)


def test_zip_slip_is_rejected_and_nothing_is_written(tmp_path, monkeypatch):
    evil = zip_bytes({f'{helixsr.TOP}/{helixsr.SETUP}': 'setup', '../evil.txt': 'x'})
    serve(monkeypatch, evil)
    target = tmp_path / 'target'
    with pytest.raises(helixsr.HelixSRError, match='outside the folder'):
        helixsr.fetch(target)
    assert not (tmp_path / 'evil.txt').exists()
    assert not (target / helixsr.TOP).exists()  # checked before any entry was written


def test_absolute_path_entry_is_rejected(tmp_path):
    part = tmp_path / 'a.zip'
    part.write_bytes(zip_bytes({'/tmp/amdlss-evil.txt': 'x'}))
    with pytest.raises(helixsr.HelixSRError):
        helixsr.extract(part, tmp_path / 'target')


def test_zip_without_the_setup_is_rejected(tmp_path):
    part = tmp_path / 'a.zip'
    part.write_bytes(zip_bytes({f'{helixsr.TOP}/README.md': 'x'}))
    with pytest.raises(helixsr.HelixSRError, match='does not hold'):
        helixsr.extract(part, tmp_path / 'target')


def test_consent_declined_runs_nothing(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError('something ran without consent')

    for name in ('fetch', 'build', 'run_streaming', '_download'):
        monkeypatch.setattr(helixsr, name, boom)
    monkeypatch.setattr(helixsr.subprocess, 'Popen', boom)
    assert helixsr.install_helixsr(tmp_path, consented=False) is None
    assert list(tmp_path.iterdir()) == []


def test_consent_text_names_nvidia_and_its_license():
    text = helixsr.consent_text()
    assert helixsr.NVIDIA_LICENSE_URL in text and 'NVIDIA' in text and '310.7.0' in text


def test_pinned_values_are_the_current_release():
    assert helixsr.ZIP_SIZE == 56650779
    assert helixsr.ZIP_SHA256 == '5b2d06db22d59aaa8c931fc06d3a8e354e65dde1bef9fce90086542f02d4a627'
    assert helixsr.ZIP_URL.endswith('/v1.8.1/HelixSR-1.8.1.zip')
