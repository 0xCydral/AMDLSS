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
    assert run['framegen']['verdict'] == 'Yes' and 'DispatchCallback' in run['framegen']['evidence']
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
