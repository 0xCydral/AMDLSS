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


def test_the_index_links_relative_pages_with_a_badge_per_game():
    analysis = page.analyse(REPORT, 'ABCD2345', 1791792000)
    index = page.render_index([analysis])
    assert 'href="ABCD2345/report.html"' in index
    assert 'badge red' in index and '&lt;script&gt;' in index and EVIL not in index
    assert '<a ' in index and 'http' not in index
