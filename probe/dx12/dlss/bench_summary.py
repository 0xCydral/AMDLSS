"""Summarises bench.sh output: median fps and 1% low per render scale and upscaler, pure TSR or DLSS windows only.
Run: python3 -I probe/dx12/dlss/bench_summary.py [/tmp/aincrad-bench]"""
import glob
import re
import statistics
import sys

LINE = re.compile(r'rq=(\d+) cycle=\d+ mode=(\w+) perf: .*?fps=([\d.]+) low1_fps=([\d.]+) p99_ms=[\d.]+ '
                  r'upscaler=(\w+) render=(\S+)')


def summarise(folder):
    rows = {}
    for path in sorted(glob.glob(f'{folder}/rq*.perf')):
        for line in open(path, encoding='utf-8'):
            m = LINE.search(line)
            if not m or m.group(5) not in ('DLSS', 'TSR'):
                continue  # the window that spans a switch is mixed
            key = (int(m.group(1)), m.group(5), m.group(6))
            rows.setdefault(key, []).append((float(m.group(3)), float(m.group(4))))
    out = []
    for (scale, mode, render), values in sorted(rows.items(), key=lambda kv: (-kv[0][0], kv[0][1])):
        fps = statistics.median(v[0] for v in values)
        low = statistics.median(v[1] for v in values)
        out.append(f'{scale:>3}% {mode:4} {render:>10}  windows {len(values):2}  median fps {fps:6.1f}  median 1% low {low:5.1f}')
    return out


if __name__ == '__main__':
    print('\n'.join(summarise(sys.argv[1] if len(sys.argv) > 1 else '/tmp/aincrad-bench')))
