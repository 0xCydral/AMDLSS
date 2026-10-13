# SPDX-License-Identifier: GPL-2.0-or-later
"""The updater with canned GitHub JSON and a fake exe in a temp folder: no network, no window."""
import hashlib
import io
import json
import urllib.error

import pytest

from amdlss import settings, update

EXE = b'MZ new amdlss'
SHA = hashlib.sha256(EXE).hexdigest()


def release(tag, prerelease=False, draft=False, digest=f'sha256:{SHA}', assets=('AMDLSS.exe',)):
    return {'tag_name': tag, 'prerelease': prerelease, 'draft': draft, 'html_url': f'https://example.test/{tag}',
            'assets': [{'name': name, 'size': len(EXE), 'digest': digest,
                        'browser_download_url': f'https://example.test/{tag}/{name}'} for name in assets]}


LISTED = [release('v0.1.0-beta.3', True), release('v0.1.0-beta.2', True), release('v0.1.0-beta.1', True)]


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


def serve(monkeypatch, data):
    asked = []
    monkeypatch.setattr(update.urllib.request, 'urlopen', lambda request, timeout=None: asked.append(request) or Response(data))
    return asked


def test_versions_order_with_betas_below_their_final_release():
    order = ['0.1.0-beta.1', '0.1.0-beta.2', '0.1.0-beta.3', '0.1.0-beta.4', '0.1.0-beta.10', '0.1.0-rc.1', '0.1.0',
             '0.1.1', '0.2.0-beta.1', '0.2.0']
    keys = [update.version_tuple(v) for v in order]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert update.version_tuple('v0.1.0-beta.3') == update.version_tuple('0.1.0-beta.3')
    assert update.release_version('v0.1.0-beta.3') == '0.1.0-beta.3'
    assert update.is_prerelease('0.1.0-beta.4') and not update.is_prerelease('0.1.0')


def test_prereleases_are_offered_only_with_the_beta_switch():
    # LISTED carries the Windows asset, so these ask for Windows explicitly (they run on either host)
    assert update.newest_release(LISTED, beta=False, windows=True) is None
    assert update.newest_release(LISTED, beta=True, windows=True).version == '0.1.0-beta.3'
    mixed = [release('v0.1.0'), *LISTED, release('v0.0.9')]
    assert update.newest_release(mixed, beta=False, windows=True).version == '0.1.0'
    assert update.newest_release(mixed, beta=True, windows=True).version == '0.1.0'   # the final release wins
    assert update.newest_release([release('v0.1.0', prerelease=True)], beta=False, windows=True) is None
    assert update.newest_release(list(reversed(LISTED)), beta=True, windows=True).version == '0.1.0-beta.3'


def test_drafts_other_tags_and_junk_are_skipped():
    junk = [None, 'v9', 7, {}, {'tag_name': None}, release('windows-v9.9.9'), release('v9.9.9', draft=True)]
    assert update.newest_release(junk, beta=True) is None
    with pytest.raises(ValueError):
        update.newest_release({'message': 'API rate limit exceeded'}, beta=True)


def test_the_release_carries_url_digest_size_and_page():
    found = update.newest_release(LISTED, beta=True, windows=True)
    assert found == update.Release('0.1.0-beta.3', 'https://example.test/v0.1.0-beta.3/AMDLSS.exe', SHA, len(EXE),
                                   'https://example.test/v0.1.0-beta.3')
    assert update.newest_release([release('v0.1.0', assets=('notes.txt',))], False, windows=True).url is None


def test_a_missing_or_malformed_digest_is_none():
    for digest in (None, '', 'sha256:abc', 'md5:' + SHA, 'sha256:' + 'z' * 64, 5):
        assert update.newest_release([release('v0.1.0', digest=digest)], False, windows=True).sha256 is None
    assert update.digest_of({'digest': 'sha256:' + SHA.upper()}) == SHA


def test_only_a_newer_release_is_an_update():
    found = update.newest_release(LISTED, beta=True, windows=True)
    assert update.newer(found, '0.1.0-beta.2') is found
    assert update.newer(found, '0.1.0-beta.3') is None and update.newer(found, '0.1.0') is None
    assert update.newer(None, '0.1.0') is None
    final = update.newest_release([release('v0.1.0')], beta=False, windows=True)
    assert update.newer(final, '0.1.0-beta.4') is final   # a beta moves to its final release


def test_the_fake_version_is_a_test_override(monkeypatch):
    assert update.current_version() == update.VERSION
    monkeypatch.setenv('AMDLSS_FAKE_VERSION', '0.1.0-beta.2')
    assert update.current_version() == '0.1.0-beta.2'


def test_the_beta_default_follows_the_running_version_and_a_saved_choice_wins(tmp_path):
    assert settings.load_beta(tmp_path, update.is_prerelease('0.1.0-beta.4')) is True
    assert settings.load_beta(tmp_path, update.is_prerelease('0.1.0')) is False
    settings.save_folders(tmp_path, [])
    settings.save_beta(tmp_path, False)
    assert settings.load_beta(tmp_path, True) is False
    settings.save_folders(tmp_path, [str(tmp_path)])   # saving the folders keeps the switch, and the reverse
    assert settings.load_beta(tmp_path, True) is False and settings.load_folders(tmp_path) == [str(tmp_path)]
    settings.save_beta(tmp_path, True)
    assert settings.load_folders(tmp_path) == [str(tmp_path)]


def test_fetch_asks_the_releases_list_and_names_failures_in_one_line(monkeypatch):
    asked = serve(monkeypatch, json.dumps(LISTED).encode())
    assert update.fetch_release(True).version == '0.1.0-beta.3'
    assert asked[0].full_url.startswith('https://api.github.com/repos/0xCydral/AMDLSS/releases')

    def refuse(code):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, code, 'x', {}, None)
        return urlopen

    monkeypatch.setattr(update.urllib.request, 'urlopen', refuse(403))
    with pytest.raises(update.UpdateError, match='rate limit'):
        update.fetch_release(True)
    monkeypatch.setattr(update.urllib.request, 'urlopen', refuse(500))
    with pytest.raises(update.UpdateError, match='500'):
        update.fetch_release(True)

    def offline(request, timeout=None):
        raise urllib.error.URLError('no network')

    monkeypatch.setattr(update.urllib.request, 'urlopen', offline)
    with pytest.raises(update.UpdateError, match='no network'):
        update.fetch_release(True)
    serve(monkeypatch, b'<html>not json</html>')
    with pytest.raises(update.UpdateError):
        update.fetch_release(True)


def fake_exe(folder):
    exe = folder / 'AMDLSS.exe'
    exe.write_bytes(b'MZ old amdlss')
    return exe


def beside(folder):
    return sorted(p.name for p in folder.iterdir())


def test_a_good_download_replaces_the_exe_and_keeps_the_old_one_as_dot_old(tmp_path, monkeypatch):
    exe = fake_exe(tmp_path)
    serve(monkeypatch, EXE)
    seen = []
    update.apply(update.newest_release(LISTED, True, windows=True), exe,
                 lambda done, total: seen.append((done, total)), windows=True)
    assert exe.read_bytes() == EXE
    assert (tmp_path / 'AMDLSS.exe.old').read_bytes() == b'MZ old amdlss'
    assert beside(tmp_path) == ['AMDLSS.exe', 'AMDLSS.exe.old'] and seen[-1] == (len(EXE), len(EXE))


def test_a_digest_mismatch_is_rejected_and_nothing_changes(tmp_path, monkeypatch):
    exe = fake_exe(tmp_path)
    serve(monkeypatch, EXE + b'tampered')
    with pytest.raises(update.UpdateError, match='SHA-256'):
        update.apply(update.newest_release(LISTED, True, windows=True), exe, windows=True)
    assert exe.read_bytes() == b'MZ old amdlss' and beside(tmp_path) == ['AMDLSS.exe']


def test_a_missing_digest_is_rejected_before_anything_is_downloaded(tmp_path, monkeypatch):
    exe = fake_exe(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError('downloaded without a digest')

    monkeypatch.setattr(update.urllib.request, 'urlopen', boom)
    with pytest.raises(update.UpdateError, match='no SHA-256'):
        update.apply(update.newest_release([release('v0.1.0', digest=None)], False, windows=True), exe, windows=True)
    with pytest.raises(update.UpdateError, match='no AMDLSS.exe'):
        update.apply(update.newest_release([release('v0.1.0', assets=('x.txt',))], False, windows=True), exe,
                     windows=True)
    assert exe.read_bytes() == b'MZ old amdlss' and beside(tmp_path) == ['AMDLSS.exe']


def test_a_failed_download_leaves_no_partial_file(tmp_path, monkeypatch):
    exe = fake_exe(tmp_path)

    def broken(request, timeout=None):
        raise urllib.error.URLError('connection reset')

    monkeypatch.setattr(update.urllib.request, 'urlopen', broken)
    with pytest.raises(update.UpdateError, match='connection reset'):
        update.apply(update.newest_release(LISTED, True, windows=True), exe, windows=True)
    assert beside(tmp_path) == ['AMDLSS.exe']


def test_the_swap_renames_first_then_moves_and_puts_the_old_exe_back_when_the_move_fails(tmp_path, monkeypatch):
    exe = fake_exe(tmp_path)
    new = tmp_path / 'AMDLSS.exe.download'
    new.write_bytes(EXE)
    calls = []
    real = update.os.replace
    monkeypatch.setattr(update.os, 'replace', lambda a, b: (calls.append((a.name, b.name)), real(a, b))[1])
    update.swap(exe, new, windows=True)
    assert calls == [('AMDLSS.exe', 'AMDLSS.exe.old'), ('AMDLSS.exe.download', 'AMDLSS.exe')]
    assert exe.read_bytes() == EXE and (tmp_path / 'AMDLSS.exe.old').read_bytes() == b'MZ old amdlss'

    again = tmp_path / 'again'
    again.mkdir()
    exe2, new2 = fake_exe(again), again / 'AMDLSS.exe.download'
    new2.write_bytes(EXE)

    def second_move_fails(a, b):
        if a.name.endswith('.download'):
            raise PermissionError('locked')
        real(a, b)

    monkeypatch.setattr(update.os, 'replace', second_move_fails)
    with pytest.raises(PermissionError):
        update.swap(exe2, new2, windows=True)
    assert exe2.read_bytes() == b'MZ old amdlss' and not (again / 'AMDLSS.exe.old').exists()   # rolled back


def test_a_folder_that_cannot_be_written_is_one_line_and_changes_nothing(tmp_path, monkeypatch):
    exe = fake_exe(tmp_path)
    serve(monkeypatch, EXE)

    def denied(a, b):
        raise PermissionError('access denied')

    monkeypatch.setattr(update.os, 'replace', denied)
    with pytest.raises(update.UpdateError, match='could not replace AMDLSS.exe'):
        update.apply(update.newest_release(LISTED, True, windows=True), exe, windows=True)
    assert exe.read_bytes() == b'MZ old amdlss' and beside(tmp_path) == ['AMDLSS.exe']


def test_the_next_start_deletes_what_the_update_left(tmp_path):
    exe = fake_exe(tmp_path)
    (tmp_path / 'AMDLSS.exe.old').write_bytes(b'old')
    (tmp_path / 'AMDLSS.exe.download').write_bytes(b'half')
    assert update.cleanup_old(exe, tries=1) is True
    assert beside(tmp_path) == ['AMDLSS.exe']
    assert update.cleanup_old(exe, tries=1) is True   # nothing to do is fine


def test_a_locked_old_exe_is_tried_again_and_then_given_up_on(tmp_path, monkeypatch):
    exe = fake_exe(tmp_path)
    old = tmp_path / 'AMDLSS.exe.old'
    old.write_bytes(b'old')
    real = type(old).unlink
    attempts = []

    def locked_twice(self, missing_ok=False):
        if self.name.endswith('.old') and len(attempts) < 2:
            attempts.append(1)
            raise PermissionError('in use')
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(old), 'unlink', locked_twice)
    assert update.cleanup_old(exe, tries=5, delay=0) is True and len(attempts) == 2 and not old.exists()
    old.write_bytes(b'old')
    attempts.clear()
    monkeypatch.setattr(type(old), 'unlink', lambda self, missing_ok=False: (_ for _ in ()).throw(PermissionError('in use')))
    assert update.cleanup_old(exe, tries=2, delay=0) is False and old.exists()


def test_restart_passes_the_arguments_and_resets_pyinstaller(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(update.subprocess, 'Popen', lambda command, **options: started.update(command=command, **options))
    update.restart(tmp_path / 'AMDLSS.exe', ['--selftest'])
    assert started['command'] == [str(tmp_path / 'AMDLSS.exe'), '--selftest']
    assert started['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1' and started['cwd'] == str(tmp_path)


def test_the_command_line_update_from_source_only_names_the_release(monkeypatch):
    said = []
    monkeypatch.setenv('AMDLSS_FAKE_VERSION', '0.1.0-beta.2')
    monkeypatch.setattr(update, 'fetch_release', lambda beta: update.newest_release(LISTED, beta, windows=True))
    monkeypatch.setattr(update, 'FROZEN', False)
    monkeypatch.setattr(update, 'apply', lambda *a: (_ for _ in ()).throw(AssertionError('replaced from source')))
    assert update.command(['--update'], True, said.append) == 0
    assert '0.1.0-beta.3 is available' in said[0] and 'example.test' in said[0]
    monkeypatch.setenv('AMDLSS_FAKE_VERSION', '0.1.0-beta.3')
    assert update.command(['--update'], True, said.append) == 0 and 'up to date' in said[1]


def test_the_command_line_update_of_an_exe_swaps_and_restarts_without_the_flag(monkeypatch, tmp_path):
    exe, restarted = fake_exe(tmp_path), []
    monkeypatch.setenv('AMDLSS_FAKE_VERSION', '0.1.0-beta.2')
    monkeypatch.setattr(update, 'fetch_release', lambda beta: update.newest_release(LISTED, beta, windows=True))
    monkeypatch.setattr(update, 'FROZEN', True)
    monkeypatch.setattr(update, 'exe_path', lambda: exe)
    monkeypatch.setattr(update, 'restart', lambda path, args: restarted.append((path, args)))
    serve(monkeypatch, EXE)
    assert update.command(['--update', '--selftest'], True, lambda text: None) == 0
    assert exe.read_bytes() == EXE and restarted == [(exe, ['--selftest'])]


def test_the_command_line_update_reports_a_failure_and_exits_1(monkeypatch):
    said = []

    def fail(beta):
        raise update.UpdateError('GitHub refused the request (rate limit, try later)')

    monkeypatch.setattr(update, 'fetch_release', fail)
    assert update.command(['--update'], True, said.append) == 1 and 'rate limit' in said[0]
