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


GOOD = zip_bytes({f'{helixsr.TOP}/{helixsr.SETUP}': 'setup', f'{helixsr.TOP}/{helixsr.FSRFG}/{helixsr.DLL}': 'dll'})


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


def test_fetch_drops_the_build_record_of_the_files_it_replaces(tmp_path, monkeypatch):
    """Update over an earlier build: the extracted package carries no build record, so the replaced build's
    record must go with the files it described. Else a failed build could pass for the new files."""
    folder = tmp_path / helixsr.TOP
    folder.mkdir(parents=True)
    (folder / helixsr.BUILT).write_text('{}', encoding='utf-8')
    serve(monkeypatch, GOOD)
    helixsr.fetch(tmp_path)
    assert not (folder / helixsr.BUILT).exists()
    assert (folder / helixsr.FSRFG / helixsr.DLL).is_file() and helixsr.package_current(tmp_path)


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
    assert helixsr.ZIP_SIZE == 136997025
    assert helixsr.ZIP_SHA256 == '0d60686eddbb0abd680b27d12a088344b212464ebf0a2dc18d10387e1c817f52'
    assert helixsr.ZIP_URL.endswith('/v2.0.0/HelixSR-2.0.0.zip')
    assert helixsr.TOP == 'HelixSR-2.0.0'  # the zip extracts to the version
    assert helixsr.FSRFG == 'HelixSR-fsrfg' and helixsr.BUILT == 'helixsr_setup.json'   # the layout of 2.0
    # NVIDIA's DLSS 310.7.0 DLL, which the setup builds from (the values in HelixSR 2.0.0's own helixsr_setup.py)
    assert helixsr.DLSS_SIZE == 58977904
    assert helixsr.DLSS_SHA256 == 'be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e'
    assert helixsr.DLSS_URL.endswith('/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll')
    # HelixSR Scaler 1.1.0: the package the OptiScaler route installs now (amdlss.scaler), a release asset of v2.0.0
    from amdlss import scaler
    assert scaler.ZIP_SIZE == 130590483
    assert scaler.ZIP_SHA256 == '827547c368a8468f9d888419f8f11cda687d3df7959725234a4dce4d8b3fc5fd'
    assert scaler.ZIP_URL.endswith('/v2.0.0/HelixSR-Scaler-1.1.0.zip')
    assert scaler.TOP == 'HelixSR-Scaler-1.1.0' and scaler.LIST == 'HelixSR-Scaler-package.txt'
