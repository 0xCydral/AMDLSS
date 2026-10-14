# SPDX-License-Identifier: GPL-2.0-or-later
"""tools/report_html.py: a downloaded report read in plain words. The report text is untrusted, so every byte of it
is escaped on the way into the page."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location('report_html', ROOT / 'tools' / 'report_html.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


page = load()

EVIL = '<script>alert(1)</script>'
PERMISSION = r'''PermissionError: [WinError 5] Acceso denegado: "E:\SteamLibrary\steamapps\common\Assassin's Creed Shadows\dxgi.dll"'''
PERMISSION_PLAIN = ('The game was still running (or another program held dxgi.dll), so AMDLSS could not replace '
                    'it. Close the game and try again.')
FSRFG = '[14:15:34.971097] [E] FSRFG_Dx12::Dispatch allocator->Reset() error: 80004005'
FSRFG_PLAIN = ('Frame generation (OptiScaler) failed after the game rebuilt its window/swapchain (resolution, '
               'fullscreen or Alt+Tab change). Lead, not confirmed.')
DESTROY = '[13:24:21.704] destroy after 8972 frames (kept for 1000 ms in case the game creates the same upscaler again)'

REPORT = {
    'app': 'AMDLSS', 'version': '0.1.0-beta.9', 'game': EVIL,
    'files': {
        'system.txt': ('AMDLSS 0.1.0-beta.9\nHelixSR 1.9.0\nWindows: Windows 11 (10.0.26100)\n'
                       'GPU 1: AMD Radeon RX 7800 XT, driver 32.0.1, driver date 2025-09-01\n'
                       'CPU: AMD Ryzen 7 7800X3D\nRAM: 31.9 GB\nCollected: 2026-10-13 14:00:00\n'),
        'games.txt': f'Shadows/ | {EVIL} | E:\\Games\\Shadows | install: none | HelixSR: unknown\n',
        'Shadows/files.txt': ('Game: Shadows\n\n== exe-folder: E:\\Games\\Shadows\n'
                              'dxgi.dll                                             123456 2026-10-01 10:00:00  0.9.4            '
                              'OptiScaler\n'),
        'Shadows/amdlss-install.log': ('=== 2026-10-13 13:59:00 install (optiscaler) Shadows ===\ncopying files\n'
                                       'Traceback (most recent call last):\n  File "helixsr_install.py", line 1\n'
                                       + PERMISSION + '\n'),
        'Shadows/exe-folder/OptiScaler.log': ('[Info] hooked\n' + FSRFG + '\n' + FSRFG + '\n'
                                              f'[14:16:02.5] [E] {EVIL}\n'
                                              '[E] nvngx_update.exe failed\n'),
        'Shadows/exe-folder/OptiScaler.ini': '; Might cause crashes, especially with Async\n[E] error inside an ini\n',
        'Shadows/exe-folder/fakenvapi.log': 'GetLatency: Not enough data\nNVAPI_NO_IMPLEMENTATION error\n',
        'Shadows/exe-folder/HelixSR/helixsr.log': ('[13:24:20.100] create: render 1280x720 -> output 1920x1080\n'
                                                   + DESTROY + '\n'),
    },
}


def test_a_locked_file_is_said_in_plain_words_with_the_file_named():
    assert page.plain_cause(PERMISSION) == PERMISSION_PLAIN
    assert page.plain_cause('PermissionError: [Errno 13] Permission denied') is not None
    assert page.plain_cause('all good') is None


def test_the_frame_generation_swapchain_pattern_is_explained_as_a_lead():
    assert page.explain(FSRFG) == FSRFG_PLAIN
    assert page.explain('ffxCreateContextDescFrameGenerationSwapChainVersionDX12 failed') == FSRFG_PLAIN
    assert page.explain('some other error') is None


def test_noise_ini_files_comments_and_known_chatter_say_nothing():
    assert page.is_noise('G/exe-folder/OptiScaler.ini', '[E] error')
    assert page.is_noise('G/a.log', '; Might cause crashes, especially with Async')
    assert page.is_noise('G/a.log', '# error in a comment')
    assert page.is_noise('G/a.log', 'nvngx_update.exe failed to start')
    assert page.is_noise('G/a.log', 'readServerManifest error')
    assert page.is_noise('G/a.log', 'getDriverPath failed')
    assert page.is_noise('G/fakenvapi.log', 'GetLatency: Not enough data')
    assert page.is_noise('G/fakenvapi.log', 'NVAPI_NO_IMPLEMENTATION')
    assert not page.is_noise('G/exe-folder/OptiScaler.log', FSRFG)


def test_problems_are_the_real_errors_counted_once_each():
    found = page.problems_in({name: text for name, text in REPORT['files'].items() if name.startswith('Shadows/')})
    texts = [entry['text'] for entry in found]
    assert any('allocator->Reset()' in text for text in texts)
    [fsrfg] = [entry for entry in found if 'allocator->Reset()' in entry['text']]
    assert fsrfg['count'] == 2 and fsrfg['why'] == FSRFG_PLAIN
    assert any(text.startswith('PermissionError:') for text in texts)   # the traceback's last line
    assert any(EVIL in text for text in texts)   # an OptiScaler [E] line, kept as data
    joined = ' '.join(texts)
    assert 'Might cause crashes' not in joined and 'inside an ini' not in joined
    assert 'nvngx_update' not in joined and 'Not enough data' not in joined


def test_the_install_record_reads_as_what_happened():
    actions = page.parse_actions(REPORT['files']['Shadows/amdlss-install.log'])
    assert len(actions) == 1
    assert actions[0]['when'] == '2026-10-13 13:59:00' and actions[0]['action'] == 'install'
    assert actions[0]['route'] == 'optiscaler' and actions[0]['outcome'] == 'failed'
    assert actions[0]['cause'] == PERMISSION_PLAIN
    done = page.parse_actions('=== 2026-10-13 15:00:00 remove Shadows ===\noriginal files are back\n')
    assert done[0]['outcome'] == 'removed' and done[0]['route'] == ''
    ok = page.parse_actions('=== 2026-10-13 15:00:00 install (direct) Shadows ===\ndone\n')
    assert ok[0]['outcome'] == 'installed'


def test_system_and_games_text_become_rows():
    rows = dict(page.parse_system(REPORT['files']['system.txt']))
    assert rows['GPU 1'] == 'AMD Radeon RX 7800 XT' and rows['Driver'] == '32.0.1 (2025-09-01)'
    assert rows['CPU'] == 'AMD Ryzen 7 7800X3D' and rows['RAM'] == '31.9 GB'
    assert rows['OS'].startswith('Windows:')
    [game] = page.parse_games(REPORT['files']['games.txt'])
    assert game['folder'] == 'Shadows' and game['name'] == EVIL
    assert game['install'] == 'none' and game['helixsr'] == 'unknown' and game['framegen'] is None
    [fg] = page.parse_games('G/ | G | C:\\G | install: OptiScaler as dxgi.dll | HelixSR: 1.9.0 | frame generation: on\n')
    assert fg['install'] == 'OptiScaler as dxgi.dll' and fg['framegen'] is True


def test_helixsr_evidence_frames_clean_shutdown_and_a_cut_off_log():
    files = {'G/exe-folder/HelixSR/helixsr.log': REPORT['files']['Shadows/exe-folder/HelixSR/helixsr.log']}
    evidence = page.helix_evidence(files)
    assert evidence['frames'] == 8972 and evidence['clean'] is True and evidence['nul'] is False
    assert '1280x720 -> output 1920x1080' in evidence['create']
    cut = page.helix_evidence({'G/helixsr.log': 'create: x\nframes\x00\x00'})
    assert cut['clean'] is False and cut['nul'] is True and cut['frames'] is None
    assert page.helix_evidence({})['present'] is False


def test_another_installs_optiscaler_is_named_as_not_amdlss():
    files = {name: text for name, text in REPORT['files'].items() if name.startswith('Shadows/')}
    assert page.third_party_optiscaler(files, 'none') == \
        "OptiScaler 0.9.4 from another install is in this folder (not AMDLSS's)"
    assert page.third_party_optiscaler(files, 'OptiScaler as dxgi.dll') is None


def test_the_status_line_in_plain_words():
    game = {'install': 'OptiScaler as dxgi.dll'}
    clean = {'present': True, 'clean': True, 'nul': False}
    assert page.status_of(game, [], [], clean) == ('Working', 'green')
    assert page.status_of(game, [], [{'text': 'x'}], clean)[1] == 'amber'
    failed = [{'action': 'install', 'outcome': 'failed'}]
    assert page.status_of({'install': 'none'}, failed, [], clean) == ('Install failed', 'red')
    removed = [{'action': 'remove', 'outcome': 'removed'}]
    assert page.status_of({'install': 'none'}, removed, [], clean) == ('Removed', 'amber')
    assert page.status_of({'install': 'none'}, [], [], clean) == ('Not installed by AMDLSS', 'amber')


def test_the_page_shows_the_reading_and_escapes_every_byte_of_the_report():
    analysis = page.analyse(REPORT, 'ABCD2345', 1791792000)
    [game] = analysis['games']
    assert game['status'] == 'Install failed' and game['level'] == 'red'
    assert game['third_party'] and game['helix']['frames'] == 8972
    rendered = page.render_report(analysis)
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in rendered
    assert '<script>' not in rendered and EVIL not in rendered
    assert PERMISSION_PLAIN in rendered and FSRFG_PLAIN in rendered
    assert '8972' in rendered and 'AMD Radeon RX 7800 XT' in rendered and '0.9.4 from another install' in rendered
    assert '<details>' in rendered and 'Shadows/exe-folder/OptiScaler.log' in rendered   # the raw files stay
    assert 'http://' not in rendered and 'https://' not in rendered   # self-contained: nothing fetched
    assert '<a ' not in rendered   # no link is ever built from report text


# --- session timelines: the three questions, answered from the logs alone ----------------------------------------
# Line shapes copied from a real tester report (TFTKA6A3, 2026-10-13) with the counts scaled down.

OPTI_RUN = (
    '[14:14:17.315930] [W] OptiScaler v0.9.4-final (7534ad0) loaded\n'
    '[14:14:17.316025] [I] Upscalers.Dx12Upscaler: fsr31\n'
    '[14:14:17.316029] [I] FrameGen.Enabled: true\n'
    '[14:14:17.316032] [I] FrameGen.FGInput: dlssg\n'
    '[14:14:17.316036] [I] FrameGen.FGOutput: fsrfg\n'
    '[14:14:25.678837] [W] FSRFG_Dx12::Dispatch Depth or Velocity is not ready, skipping\n'
    '[14:14:35.497585] [W] FSRFG_Dx12::DispatchCallback frameID: 306\n'
    '[14:14:35.856912] [I] FSRFG_Dx12::Activate D3D12_Configure Enabled: true, result: FFX_API_RETURN_OK (0)\n'
    '[14:15:02.743613] [W] StreamlineHooks::streamlineLogCallback Exceeded VRAM budget, various performance issues\n'
    '[14:15:30.000000] [I] StreamlineHooks::streamlineLogCallback Total VRAM used: 7.34 GB\n'
    '[14:15:34.971097] [E] FSRFG_Dx12::Dispatch allocator->Reset() error: 80004005\n'
    '[14:16:02.186236] [I] FSRFG_Dx12::Deactivate D3D12_Configure Enabled: false, result: FFX_API_RETURN_OK (0)\n'
    '[14:16:39.811637] [W] MenuCommon::WndProc IsShuttingDown = true\n')
HELIX_RUN = (
    '[14:17:25.540] create: max render 1920x1080 -> upscale 1920x1080, flags 0x19 (as amd_fidelityfx_dx12.dll); '
    'sharpening: NVIDIA (NIS)\n'
    '[14:17:25.577] dispatch 0: render 1920x1080 -> 1920x1080, jitter -0.125,-0.278, reset 0, sharpness 0.10\n'
    '[14:19:36.367] destroy after 10512 frames (kept for 1000 ms in case the game creates the same upscaler again)\n')
CRASHES = ('Windows crashes for One (Application log, last 14 days, Event ID 1000 and 1001)\n'
           'Game executables checked: One.exe\n\n'
           '2026-10-13 14:20:11 | One.exe | faulting module: dxgi.dll | module version: 0.9.4.0 | '
           'exception: 0xc0000005 | offset: 0x0000000000123456 | Event ID: 1000 | module path: E:\\Games\\dxgi.dll\n\n'
           'Crash dumps listed only (not sent):\n  One.exe.1234.dmp  999 bytes  2026-10-13 14:20:12\n')


def test_the_timeline_answers_the_three_questions_for_an_optiscaler_run():
    helix = HELIX_RUN.replace('amd_fidelityfx_dx12.dll', 'helixsr_upscaler.dll') \
        .replace('14:17:25', '14:14:35').replace('14:19:36', '14:15:56')
    files = {'G/exe-folder/OptiScaler.log': OPTI_RUN, 'G/exe-folder/HelixSR/helixsr.log': helix}
    run = page.timeline_of(files, 'Latest run', '2026-10-13')
    assert run['started'] == '14:14:17' and run['last'] == '14:16:39' and run['route'] == 'OptiScaler route'
    assert run['helix_ran']['verdict'] == 'Yes' and 'create:' in run['helix_ran']['evidence']
    assert run['framegen'] == {'verdict': 'Yes', 'evidence': '1 frames delivered by FSR frame generation, 1 activation'}
    assert run['crashed'] == {'verdict': 'No', 'evidence': '[14:16:39.811637] [W] MenuCommon::WndProc IsShuttingDown = true'}
    assert run['opti']['fg_dispatch_lines'] == 3 and run['opti']['fg_delivered'] == 1 and run['opti']['fg_skipped'] == 1
    joined = '\n'.join(page.timeline_lines(run))
    for needle in ('Started 14:14:17 (OptiScaler v0.9.4-final loaded)', 'Dx12Upscaler fsr31',
                   'render 1920x1080 -> output 1920x1080', 'FrameGen.Enabled true, FGInput dlssg, FGOutput fsrfg',
                   '3 FSRFG dispatch lines (1 delivered, 1 skipping, 1 other)', '1 activates and 1 deactivates',
                   'first active 14:14:35', 'VRAM: 1 budget warnings, peak 7.34 GB',
                   'How it ended: clean shutdown at 14:16:39', 'Did HelixSR run? Yes',
                   'Did frame generation work? Yes', 'Did the game crash? No', 'allocator->Reset()'):
        assert needle in joined, needle


def test_the_timeline_reads_a_drop_in_run_and_ignores_a_stale_fakenvapi_log():
    files = {'G/exe-folder/helixsr.log': HELIX_RUN,
             'G/exe-folder/fakenvapi.log': '[13:20:06.100000] [C] fakenvapi version: v1.4.1\n'}
    run = page.timeline_of(files)
    assert run['started'] == '14:17:25' and run['route'] == 'FSR drop-in' and run['ended'] == 'clean'
    assert run['framegen']['verdict'] == 'No' and 'FSR drop-in route has no OptiScaler' in run['framegen']['evidence']
    joined = '\n'.join(page.timeline_lines(run))
    assert '10512 frames in 1 closed session(s)' in joined and 'clean shutdown at 14:19:36' in joined


def test_frame_generation_that_only_skips_did_not_work_and_a_stopped_log_is_unknown():
    skipped = {'G/exe-folder/OptiScaler.log': OPTI_RUN.replace('DispatchCallback frameID: 306', 'Dispatch skipping')}
    run = page.timeline_of(skipped)
    assert run['framegen']['verdict'] == 'No' and run['opti']['fg_delivered'] == 0
    stopped = page.timeline_of({'G/exe-folder/OptiScaler.log': OPTI_RUN.rsplit('[14:16:39', 1)[0]})
    assert stopped['ended'] == 'stops' and stopped['crashed']['verdict'] == 'Unknown'
    assert 'the log just stops at 14:16:02 (likely crash or kill)' in '\n'.join(page.timeline_lines(stopped))
    bare = page.timeline_of({'G/files.txt': 'Game: G\n'})
    assert [bare[key]['verdict'] for key in ('helix_ran', 'framegen', 'crashed')] == ['Unknown'] * 3


def test_a_recreated_upscaler_keeps_its_sessions_apart():
    text = ('[10:00:00.100] create: max render 1280x720 -> upscale 1920x1080, flags 0x28 (as helixsr_upscaler.dll)\n'
            '[10:00:01.100] destroy after 1 frames\n'
            '[10:00:02.100] create: max render 1280x720 -> upscale 1920x1080, flags 0x28 (as helixsr_upscaler.dll)\n'
            '[10:01:00.100] dispatch 3000: render 1102x620 -> 1920x1080, jitter 0,0, reset 0, sharpness 0.10\n')
    run = page.timeline_of({'G/exe-folder/HelixSR/helixsr.log': text})
    assert run['ended'] == 'stops'   # the early destroy does not close the last session
    joined = '\n'.join(page.timeline_lines(run))
    assert 'HelixSR sessions: 2; 1 frame in 1 closed session(s); last session reached dispatch 3000' in joined


def test_windows_crashes_name_the_faulting_module_and_date_the_verdict():
    crashes = page.parse_crashes(CRASHES)
    [entry] = crashes['entries']
    assert entry['module'] == 'dxgi.dll' and entry['exception'] == '0xc0000005' and crashes['dumps']
    assert page.crash_plain(entry).startswith('2026-10-13 14:20:11: the game crashed inside dxgi.dll (OptiScaler)')
    assert page.module_owner('dxgi.dll', r'C:\Windows\System32\dxgi.dll') == 'Windows'
    assert page.module_owner('amd_fidelityfx_dx12.dll') == 'HelixSR'
    stopped = {'G/exe-folder/OptiScaler.log': '[14:14:17.315930] [W] OptiScaler v0.9.4 loaded\n'}
    assert page.timeline_of(stopped, 'Latest run', '2026-10-13', crashes)['crashed']['verdict'] == 'Yes'
    clean = {'G/exe-folder/OptiScaler.log': OPTI_RUN}
    dated = page.timeline_of(clean, 'Latest run', '2026-10-14', crashes)   # the crash is the day before this run
    assert dated['crashed']['verdict'] == 'No' and 'also recorded 1 crash(es)' in dated['crash_note']


def test_history_runs_stay_apart_from_the_latest_run():
    files = {'G/exe-folder/helixsr.log': HELIX_RUN, 'G/crashes.txt': CRASHES, 'G/proton.log': 'proton\n',
             'G/history/20261012-090000/exe-folder/OptiScaler.log': OPTI_RUN}
    runs, crashes, proton = page.runs_of(files, 'G', '2026-10-13')
    assert proton and crashes['entries'] and [run['label'] for run in runs] == [
        'Latest run', 'Earlier run kept 2026-10-12 09:00:00']
    assert runs[0]['route'] == 'FSR drop-in' and runs[1]['route'] == 'OptiScaler route'
    assert runs[1]['framegen']['verdict'] == 'Yes'


def test_the_page_shows_answers_windows_crashes_and_the_proton_note_escaped():
    report = {'app': 'AMDLSS', 'version': '0.1.0-beta.10', 'game': 'One', 'files': {
        'system.txt': 'AMDLSS 0.1.0-beta.10\nLinux: Linux 6.10\nCollected: 2026-10-14 15:00:00\n',
        'games.txt': 'One/ | One | /games/One | install: OptiScaler as dxgi.dll | HelixSR: 1.9.0\n',
        'One/exe-folder/OptiScaler.log': OPTI_RUN.replace('loaded', EVIL),
        'One/crashes.txt': CRASHES}}
    [game] = page.analyse(report, 'ABCD2345')['games']
    assert game['timeline']['crashed']['verdict'] == 'No' and game['crashes']['entries']
    rendered = page.render_report(page.analyse(report, 'ABCD2345'))
    for needle in ('Answers (latest run)', 'Did frame generation work?', 'Session timeline', 'Windows crashes',
                   'the game crashed inside dxgi.dll (OptiScaler)', 'Listed only, not sent: One.exe.1234.dmp',
                   'PROTON_LOG=1', '&lt;script&gt;alert(1)&lt;/script&gt;'):
        assert needle in rendered, needle
    assert EVIL not in rendered and '<script>' not in rendered


def test_the_index_links_relative_pages_with_a_badge_per_game():
    analysis = page.analyse(REPORT, 'ABCD2345', 1791792000)
    index = page.render_index([analysis])
    assert 'href="ABCD2345/report.html"' in index
    assert 'badge red' in index and '&lt;script&gt;' in index and EVIL not in index
    assert '<a ' in index and 'http' not in index


# --- an older AMDLSS's unread WER line, and the route of a run whose logs do not name it -------------------------

OLD_1001 = ('Windows crashes for One (Application log, last 14 days, Event ID 1000 and 1001)\n'
            'Game executables checked: One.exe\n\n'
            '2026-10-13 14:19:36 | One.exe | faulting module: P5: | module version:  | exception:  | offset:  | '
            'Event ID: 1001 | module path: \n')
UNREAD = 'crash recorded by Windows Error Reporting; this AMDLSS version could not read the faulting module'


def test_an_unread_wer_label_is_never_shown_as_a_module_and_a_hang_says_so():
    [old] = page.parse_crashes(OLD_1001)['entries']
    assert page.crash_plain(old) == f'2026-10-13 14:19:36: {UNREAD}: One.exe (Event ID 1001)'
    hang = OLD_1001.replace('faulting module: P5: ', 'faulting module: unknown').replace(
        'module path: \n', 'module path:  | event name: AppHangB1\n')
    [stopped] = page.parse_crashes(hang)['entries']
    assert stopped['name'] == 'AppHangB1'
    assert page.crash_plain(stopped).startswith('2026-10-13 14:19:36: the game stopped responding (hang): One.exe')
    note = page.timeline_of({'G/exe-folder/OptiScaler.log': OPTI_RUN}, 'Latest run', '2026-10-14',
                            page.parse_crashes(OLD_1001))['crash_note']
    assert 'inside P5' not in note and UNREAD in note


# The last session of a drop-in run can have no `create:` line (the game recreated its upscaler and HelixSR only
# logged the dispatches and the destroy), as in a real Monster Hunter run of GZ633MDH.
DROP_IN_TAIL = (HELIX_RUN.split('[14:17:25.577]')[0] +
                '[14:18:00.100] destroy after 40 frames (kept for 1000 ms in case the game creates the same '
                'upscaler again)\n'
                '[14:19:00.100] dispatch 9: render 1920x1080 -> 1920x1080, jitter 0,0, reset 0, sharpness 0.10\n'
                '[14:19:36.367] destroy after 5322 frames (kept for 1000 ms in case the game creates the same '
                'upscaler again)\n')
NO_CREATE = '[14:19:00.100] dispatch 9: render 1920x1080 -> 1920x1080, jitter 0,0, reset 0, sharpness 0.10\n' \
            '[14:19:36.367] destroy after 5322 frames (kept for 1000 ms in case the game creates the same ' \
            'upscaler again)\n'


def test_a_drop_in_run_is_a_drop_in_run_whichever_session_the_log_ends_with():
    assert page.timeline_of({'G/exe-folder/helixsr.log': DROP_IN_TAIL})['route'] == 'FSR drop-in'
    bare = page.timeline_of({'G/exe-folder/helixsr.log': NO_CREATE})   # no session names its DLL at all
    assert bare['route'] == 'HelixSR' and bare['framegen']['verdict'] == 'Unknown'
    hinted = page.timeline_of({'G/exe-folder/helixsr.log': NO_CREATE}, drop_in=True)
    assert hinted['route'] == 'FSR drop-in' and hinted['framegen']['verdict'] == 'No'
    assert 'Upscaler: FSR drop-in' in '\n'.join(page.timeline_lines(hinted))


def test_the_install_record_names_the_route_for_the_latest_run_and_for_a_kept_one():
    install_log = ('=== 2026-10-13 13:00:00 install (optiscaler) One ===\nok\n'
                   '=== 2026-10-13 13:10:00 install (direct) One ===\nok\n'
                   '=== 2026-10-13 14:30:00 update (direct) One ===\nok\n')
    files = {'games.txt': 'G/ | One | E:\\One | install: drop-in DLL | HelixSR: 1.9.0\n',
             'G/amdlss-install.log': install_log,
             'G/exe-folder/helixsr.log': NO_CREATE,
             'G/history/20261013-143001/exe-folder/helixsr.log': NO_CREATE,
             'G/history/20261013-130001/exe-folder/helixsr.log': NO_CREATE}
    latest, kept_drop_in, kept_opti = page.runs_of(files, 'G', '2026-10-13')[0]
    assert latest['route'] == 'FSR drop-in' and latest['framegen']['verdict'] == 'No'
    assert kept_drop_in['route'] == 'FSR drop-in'   # the 13:10 install (direct) was in place before the 14:30 update
    assert kept_opti['route'] == 'HelixSR'          # before the 13:10 install the OptiScaler route was in place
    files['games.txt'] = files['games.txt'].replace('drop-in DLL', 'OptiScaler as dxgi.dll')
    assert page.runs_of(files, 'G', '2026-10-13')[0][0]['route'] == 'HelixSR'


def test_frame_generation_yes_cites_the_counts_not_an_arbitrary_log_line():
    run = page.timeline_of({'G/exe-folder/OptiScaler.log': OPTI_RUN + '[14:16:40.000000] [I] Upscaling paused!\n'})
    assert run['framegen']['evidence'] == '1 frames delivered by FSR frame generation, 1 activation'


# --- a game that ends without a destroy line and without an Event 1000: hang, Unreal crash folder, still running --

# A drop-in run whose HelixSR log just stops (AION2 in GZ633MDH: no destroy line, no Windows record).
STOPS = HELIX_RUN.rsplit('[14:19:36', 1)[0]
HEADER = ('Windows crashes for One (Application log, last 14 days, Event ID 1000, 1001 and 1002)\n'
          'Game executables checked: One.exe\nRunning when collected: {running}\n\n')
HANG_LINE = ('2026-10-13 14:19:50 | One.exe | faulting module: unknown | module version:  | exception:  | offset:  | '
             'Event ID: 1002 | module path:  | event name: AppHang\n')
UE_LINE = ('Unreal Engine crash folder | 2026-10-13 14:19:40 | UECC-Windows-ABC_0000 | 3 files, 1234 bytes | '
           'C:\\Users\\<user>\\AppData\\Local\\Proj\\Saved\\Crashes\n')


def stops_with(crash_text, date='2026-10-13'):
    return page.timeline_of({'G/exe-folder/helixsr.log': STOPS}, 'Latest run', date, page.parse_crashes(crash_text),
                            drop_in=True)


def test_a_hang_event_answers_yes_hang():
    run = stops_with(HEADER.format(running='no') + HANG_LINE)
    assert run['crashed']['verdict'] == 'Yes' and 'the game stopped responding (hang)' in run['crashed']['evidence']
    assert '(Event ID 1002)' in run['crashed']['evidence']


def test_an_unreal_crash_folder_in_the_window_of_the_run_answers_yes_and_one_outside_it_is_only_a_note():
    run = stops_with(HEADER.format(running='no') + UE_LINE)
    assert run['crashed']['verdict'] == 'Yes' and 'Unreal Engine recorded a crash' in run['crashed']['evidence']
    assert 'UECC-Windows-ABC_0000' in run['crashed']['evidence']
    older = stops_with(HEADER.format(running='no') + UE_LINE.replace('2026-10-13', '2026-10-09'))
    assert older['crashed']['verdict'] == 'Unknown'   # a log that stops, and a crash folder from days before
    assert 'Unreal Engine and Windows also recorded 1 crash(es)' in older['crash_note']


def test_a_game_still_running_when_the_report_was_made_did_not_crash():
    run = stops_with(HEADER.format(running='yes'))
    assert run['ended'] == 'running' and run['crashed'] == {'verdict': 'No',
                                                            'evidence': 'still running when the report was made'}
    assert 'How it ended: it had not ended, the game was still running when the report was made' in \
        '\n'.join(page.timeline_lines(run))
    assert page.status_of({'install': 'drop-in DLL'}, [], [], {'nul': False, 'present': True, 'clean': False},
                          running=True) == ('Working', 'green')
    # not running and no record stays Unknown, as before; a hang beats "running" if both are there
    assert stops_with(HEADER.format(running='no'))['crashed']['verdict'] == 'Unknown'
    assert stops_with(HEADER.format(running='yes') + HANG_LINE)['crashed']['verdict'] == 'Yes'
    assert page.parse_crashes(HEADER.format(running='unknown'))['running'] is None
    assert page.parse_crashes(CRASHES)['running'] is None   # a crashes.txt from before this line existed


def test_the_reader_reads_back_what_crashes_py_writes_for_an_unreal_game(tmp_path):
    from amdlss import crashes, games
    root = tmp_path / 'UE'
    exe = root / 'Proj' / 'Binaries' / 'Win64'
    crash = root / 'Proj' / 'Saved' / 'Crashes' / 'UECC-Windows-ABC_0000'
    exe.mkdir(parents=True)
    crash.mkdir(parents=True)
    (exe / 'Proj.exe').write_bytes(b'MZ')
    (crash / 'a.dmp').write_bytes(b'x')
    game = games.Game(name='UE', folder=str(root), exe_folder=str(exe))
    text = crashes.crashes_text(game, windows=True, run=lambda command: '', dumps_dir=tmp_path / 'none',
                                wer_folders=[], processes=['C:/Windows/explorer.exe'], local=tmp_path / 'nowhere')
    read = page.parse_crashes(text)
    [entry] = read['entries']
    assert entry['unreal'] and entry['exe'] == 'UECC-Windows-ABC_0000' and read['running'] is False
    assert read['dumps'] == [] and read['present']
    report = {'app': 'AMDLSS', 'version': '0.1.0-beta.13', 'game': 'UE', 'files': {
        'system.txt': 'AMDLSS 0.1.0-beta.13\nCollected: ' + entry['when'][:10] + ' 23:59:00\n',
        'games.txt': 'UE/ | UE | x | install: drop-in DLL | HelixSR: 1.9.0\n',
        'UE/exe-folder/helixsr.log': '[00:00:01.000] create: max render 1920x1080 -> upscale 1920x1080, flags 0x19\n'
                                     '[00:00:02.000] dispatch 0: render 1920x1080 -> 1920x1080, jitter 0,0\n',
        'UE/crashes.txt': text}}
    rendered = page.render_report(page.analyse(report, 'ABCD2345'))
    assert 'Unreal Engine recorded a crash' in rendered and 'UECC-Windows-ABC_0000' in rendered


# --- VRXKXVKN (beta.14): a crash while closing, and a crash of a helper program ----------------------------------

AC_RUN = ('[23:06:50.000000] [W] OptiScaler v0.9.4-final (7534ad0) loaded\n'
          '[23:07:05.000000] [W] FGHooks::hkFGRelease Preserving FG Swapchain from release\n'
          '[23:07:39.000000] [W] MenuCommon::WndProc IsShuttingDown = true\n')
AC_CRASH = ('Windows crashes for AC (Application log, last 14 days, Event ID 1000, 1001 and 1002)\n'
            'Game executables checked: ACShadows.exe, UbisoftConnectInstaller.exe\nRunning when collected: no\n\n'
            '2026-10-13 23:07:45 | ACShadows.exe | faulting module: ucrtbase.dll | module version: 10.0.26100.7019 | '
            'exception: c0000409 | offset: 00000000000a4ace | Event ID: 1000 | '
            'module path: C:\\WINDOWS\\System32\\ucrtbase.dll\n')
AC_FILES = ('Game: AC\n.exe folder: E:\\AC\n\n== exe-folder: E:\\AC\n'
            'ACShadows.exe                                       454074720 2026-10-12 10:46:11  -\n'
            'UbisoftConnectInstaller.exe                         264202144 2026-10-12 10:46:11  174.2.0.13389\n'
            '== exe-folder/HelixSR: E:\\AC\\HelixSR\n')


def test_a_crash_within_a_minute_after_the_clean_shutdown_marker_is_a_crash_while_closing():
    run = page.timeline_of({'AC/exe-folder/OptiScaler.log': AC_RUN}, 'Latest run', '2026-10-13',
                           page.parse_crashes(AC_CRASH))
    assert run['ended'] == 'closing'
    assert run['crashed']['verdict'] == 'Yes, while closing (ucrtbase.dll, c0000409)'
    lines = '\n'.join(page.timeline_lines(run))
    assert 'How it ended: crashed while closing' in lines and 'clean shutdown at' not in lines
    assert 'Did the game crash? Yes, while closing (ucrtbase.dll, c0000409).' in lines
    assert page.timeline_of({'AC/exe-folder/OptiScaler.log': AC_RUN}, 'Latest run', '2026-10-13')['ended'] == 'clean'


def test_a_crash_long_after_the_marker_or_a_hang_is_not_a_crash_while_closing():
    late = AC_CRASH.replace('23:07:45', '23:08:40')   # 61 s after the 23:07:39 marker
    run = page.timeline_of({'AC/exe-folder/OptiScaler.log': AC_RUN}, 'Latest run', '2026-10-13', page.parse_crashes(late))
    assert run['ended'] == 'clean' and run['crashed']['verdict'] == 'Yes'
    hang = AC_CRASH.replace('Event ID: 1000', 'Event ID: 1002').replace('module path:', 'event name: AppHang | x:')
    assert page.timeline_of({'AC/exe-folder/OptiScaler.log': AC_RUN}, 'Latest run', '2026-10-13',
                            page.parse_crashes(hang))['ended'] == 'clean'


DELTA_FILES = ('Game: Delta Force\n.exe folder: F:\\DF\\Game\\DeltaForce\\Binaries\\Win64\n\n'
               '== exe-folder: F:\\DF\\Game\\DeltaForce\\Binaries\\Win64\n'
               'DeltaForceClient-Win64-Shipping.exe                 517627888 2026-10-12 19:32:08  4.24.2.0\n'
               'icafe_iigw_handler.exe                                 378208 2026-10-12 19:32:08  1.0.0.1\n')
DELTA_CRASH = ('Windows crashes for Delta Force (Application log, last 14 days, Event ID 1000, 1001 and 1002)\n'
               'Game executables checked: DeltaForceClient-Win64-Shipping.exe, tbs_browser.exe\n'
               'Running when collected: no\n\n'
               '2026-10-13 14:20:00 | tbs_browser.exe | faulting module: qbcore.dll | module version: 94.0.80.81 | '
               'exception: 80000003 | offset: 0000000002f702f4 | Event ID: 1000 | '
               'module path: F:\\DF\\Launcher\\Service\\qbcore.dll\n')


def delta_run(crash_text, files_text=DELTA_FILES):
    files = {'D/files.txt': files_text, 'D/crashes.txt': crash_text, 'D/exe-folder/helixsr.log': STOPS}
    return page.runs_of(files, 'D', '2026-10-13')


def test_a_crash_of_a_helper_program_is_not_a_game_crash():
    runs, crashes, _proton = delta_run(DELTA_CRASH)
    run = runs[0]
    assert run['crashed']['verdict'] == 'Unknown'
    [entry] = crashes['entries']
    assert page.crash_plain(entry).startswith('2026-10-13 14:20:00: a helper program (tbs_browser.exe) crashed '
                                              'inside qbcore.dll')
    assert 'crash(es) of helper programs, not of the game' in run['crash_note']
    assert 'the game crashed' not in run['crash_note'] and not run['crashed']['verdict'].startswith('Yes')


def test_the_unreal_shipping_exe_and_the_largest_exe_are_the_game():
    own = DELTA_CRASH.replace('tbs_browser.exe |', 'DeltaForceClient-Win64-Shipping.exe |')
    assert delta_run(own)[0][0]['crashed']['verdict'] == 'Yes'
    at_two = AC_CRASH.replace('23:07:45', '14:20:00')   # inside the window of delta_run's log
    plain = delta_run(at_two.replace('ACShadows.exe |', 'UbisoftConnectInstaller.exe |'), AC_FILES)
    assert plain[0][0]['crashed']['verdict'] == 'Unknown' and plain[1]['entries'][0]['helper']
    assert delta_run(at_two, AC_FILES)[0][0]['crashed']['verdict'] == 'Yes'
    assert page.main_exes(AC_FILES) == {'acshadows.exe'}


def test_without_a_files_list_no_crash_is_called_a_helper():
    assert page.main_exes('') is None
    assert delta_run(DELTA_CRASH, '')[0][0]['crashed']['verdict'] == 'Yes'
    [entry] = delta_run(DELTA_CRASH, '')[1]['entries']
    assert 'helper' not in entry
