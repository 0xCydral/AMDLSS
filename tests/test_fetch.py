# SPDX-License-Identifier: GPL-2.0-or-later
"""Download, verification, extraction and consent: nothing here touches the network or HelixSR."""
import hashlib
import io
import json
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


def test_fetch_records_which_pinned_zip_the_folder_came_from(tmp_path, monkeypatch):
    """A folder without that record (an older AMDLSS built it, or another zip) must count as outdated: the app
    then offers Update instead of trusting the files."""
    assert not helixsr.package_current(tmp_path)
    serve(monkeypatch, GOOD)
    helixsr.fetch(tmp_path)
    assert helixsr.package_current(tmp_path)
    record = json.loads((tmp_path / helixsr.PACKAGE).read_text(encoding='utf-8'))
    assert record['zip_sha256'] == helixsr.ZIP_SHA256 and record['folder'] == helixsr.TOP
    assert record['zip_name'] == helixsr.ZIP_NAME and record['version'] == helixsr.VERSION


def test_a_record_of_another_zip_or_folder_is_not_current(tmp_path):
    (tmp_path / helixsr.PACKAGE).write_text('{}', encoding='utf-8')
    assert not helixsr.package_current(tmp_path)                       # junk
    (tmp_path / helixsr.PACKAGE).write_text(json.dumps({'zip_sha256': '0' * 64, 'folder': helixsr.TOP}))
    assert not helixsr.package_current(tmp_path)                       # another zip
    (tmp_path / helixsr.PACKAGE).write_text(json.dumps({'zip_sha256': helixsr.ZIP_SHA256,
                                                        'folder': 'HelixSR-1.8.1'}))
    assert not helixsr.package_current(tmp_path)                       # another folder


def test_fetch_drops_the_build_records_of_the_files_it_replaces(tmp_path, monkeypatch):
    """Update over an earlier build: the extracted package carries no build records, so the replaced build's
    records must go with the files they described. Else a failed build could pass for the new files."""
    folder = tmp_path / helixsr.TOP
    folder.mkdir(parents=True)
    (folder / helixsr.BUILT).write_text('{}', encoding='utf-8')
    (folder / helixsr.FG_SETUP).write_text('{}', encoding='utf-8')
    serve(monkeypatch, GOOD)
    helixsr.fetch(tmp_path)
    assert not (folder / helixsr.BUILT).exists() and not (folder / helixsr.FG_SETUP).exists()
    assert (folder / helixsr.DLL).is_file() and helixsr.package_current(tmp_path)


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
    assert helixsr.ZIP_SIZE == 3803249
    assert helixsr.ZIP_SHA256 == '795268f60a1262cd7c8cb01a38f553751cc380fa0cbec63c8f466b6807468be4'
    assert helixsr.ZIP_URL.endswith('/v1.9.0/HelixSR-1.9.0.zip')
    assert helixsr.TOP == 'HelixSR-1.9.0'  # the zip extracts to the version
    # HelixSR Scaler 1.0.3: the package the OptiScaler route installs now (amdlss.scaler)
    from amdlss import scaler
    assert scaler.ZIP_SIZE == 132504212
    assert scaler.ZIP_SHA256 == '974c9d62ea0e088a1e11fe8e7e3196b2d9939d39fb5d709b684c5e7c2c74c351'
    assert scaler.ZIP_URL.endswith('/v1.9.0/HelixSR-Scaler-1.0.3.zip')
    assert scaler.TOP == 'HelixSR-Scaler-1.0.3' and scaler.LIST == 'HelixSR-Scaler-package.txt'
