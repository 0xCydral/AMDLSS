"""Find the camera jitter in the constant buffers captured by amdlss_dx12_probe (cbcapture/ directory).

UE jitters the projection by a Halton(2,3) sub-pixel offset that cycles every few frames. For every 4-byte offset of
every captured constant buffer this reads float32 across the captured frames and ranks float pairs (offset, offset+4)
that (a) change on most frames, (b) stay within +-1.0 and (c) correlate with Halton(2,3)-0.5 for some phase and cycle
length. The scale is matched against pixels (+-0.5), UV (+-0.5/render width) and NDC (+-1/render width).

Standard library only. Run it isolated:

    python3 -I probe/dx12/find_jitter.py path/to/cbcapture
    python3 -I probe/dx12/find_jitter.py --selftest
"""
import argparse
import math
import os
import re
import struct
import sys
import tempfile

FILE_RE = re.compile(r"frame_(\d+)_p(\d+)_([A-Za-z0-9]+)_(\d+)\.bin$")
FRAME_RE = re.compile(r"^# frame (\d+) present (\d+) render=(\d+)x(\d+)")
CYCLES = (0, 4, 8, 16)  # 0 = the sequence never wraps; UE's default TSR/TAA sample count is 8
PHASES = range(16)
MIN_FRAMES = 4
MIN_CHANGE = 0.5


def halton(index, base):
    f, r = 1.0, 0.0
    while index > 0:
        f /= base
        r += f * (index % base)
        index //= base
    return r


def reference(base, cycle, phase, n):
    out = []
    for t in range(n):
        idx = phase + t
        if cycle:
            idx %= cycle
        out.append(halton(idx + 1, base) - 0.5)
    return out


def pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va < 1e-30 or vb < 1e-30:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def load(directory):
    groups, widths = {}, []
    for name in sorted(os.listdir(directory)):
        m = FILE_RE.match(name)
        if m:
            frame, param, slot, size = int(m[1]), int(m[2]), m[3], int(m[4])
            with open(os.path.join(directory, name), "rb") as f:
                groups.setdefault((param, slot, size), {})[frame] = f.read()
    index = os.path.join(directory, "index.txt")
    if os.path.exists(index):
        with open(index) as f:
            for line in f:
                m = FRAME_RE.match(line)
                if m:
                    widths.append(int(m[3]))
    widths.sort()
    return groups, (widths[len(widths) // 2] if widths else 0)


def float_series(frames, size):
    """offset -> list of float32 across the frames (frames sorted by number)."""
    data = [frames[k] for k in sorted(frames)]
    series = {}
    for off in range(0, size - 3, 4):
        series[off] = [struct.unpack_from("<f", d, off)[0] for d in data if len(d) >= off + 4]
    return series


def plausible(vals):
    if any(v != v or abs(v) == float("inf") for v in vals):
        return None
    if max(abs(v) for v in vals) > 1.0:
        return None
    change = sum(1 for a, b in zip(vals, vals[1:]) if a != b) / max(1, len(vals) - 1)
    return change if change >= MIN_CHANGE else None


def match_scale(amp, render_width):
    scales = [("pixels", 0.5)]
    if render_width:
        scales += [("uv", 0.5 / render_width), ("ndc", 1.0 / render_width)]
    best = ("?", None)
    for name, s in scales:
        ratio = amp / s
        if 0.35 <= ratio <= 1.3 and (best[1] is None or abs(math.log(ratio)) < abs(math.log(best[1]))):
            best = (name, ratio)
    return best[0]


def best_pair(a, b, refs):
    best = None
    for order in ((2, 3), (3, 2)):
        for (cycle, phase), by_base in refs.items():
            rx, ry = pearson(a, by_base[order[0]]), pearson(b, by_base[order[1]])
            score = min(abs(rx), abs(ry))
            if best is None or score > best["score"]:
                best = {"score": score, "rx": rx, "ry": ry, "cycle": cycle, "phase": phase, "order": order}
    return best


def rank(directory, render_width=0, top=10):
    groups, index_width = load(directory)
    render_width = render_width or index_width
    results = []
    for (param, slot, size), frames in groups.items():
        if len(frames) < MIN_FRAMES:
            continue
        series = float_series(frames, size)
        n = min(len(v) for v in series.values())
        refs = {(c, p): {2: reference(2, c, p, n), 3: reference(3, c, p, n)}
                for c in CYCLES for p in PHASES if not c or p < c}
        for off in sorted(series):
            nxt = off + 4
            if nxt not in series:
                continue
            a, b = series[off][:n], series[nxt][:n]
            ca, cb = plausible(a), plausible(b)
            if ca is None or cb is None:
                continue
            fit = best_pair(a, b, refs)
            amp = max(max(abs(v) for v in a), max(abs(v) for v in b))
            scale = match_scale(amp, render_width)
            key = fit["score"] * min(ca, cb) * (1.0 if scale != "?" else 0.9)
            results.append(dict(fit, key=key, param=param, slot=slot, size=size, offset=off, a=a, b=b, scale=scale,
                                amp=amp, change=(ca, cb), frames=n))
    results.sort(key=lambda r: -r["key"])
    return results[:top], render_width


def show(results, render_width):
    print("render width %s" % (render_width or "unknown"))
    if not results:
        print("no float pair changes on most frames and stays within +-1.0")
    for i, r in enumerate(results, 1):
        print("#%d p%d %s size=%d offset=%d  score=%.3f (x r=%+.3f, y r=%+.3f)  halton phase=%d cycle=%s x=h%d y=h%d  "
              "scale=%s (max %.5g)  change=%.2f/%.2f  frames=%d" % (
                  i, r["param"], r["slot"], r["size"], r["offset"], r["key"], r["rx"], r["ry"], r["phase"],
                  r["cycle"] or "none", r["order"][0], r["order"][1], r["scale"], r["amp"], r["change"][0],
                  r["change"][1], r["frames"]))
        print("   x: " + " ".join("%+.5g" % v for v in r["a"]))
        print("   y: " + " ".join("%+.5g" % v for v in r["b"]))


# ---- self-check -------------------------------------------------------------------------------------------

def make_capture(directory, scale, rw=1552):
    """16 frames of a 256-byte constant buffer with a Halton pair planted at offset 64 (phase 3, cycle 8)."""
    seed = 12345
    lines = []
    for t in range(16):
        buf = bytearray(256)
        struct.pack_into("<f", buf, 0, 1920.0)                 # constant
        struct.pack_into("<f", buf, 4, float(t))               # frame counter, leaves +-1.0
        for off in (8, 12):                                    # noise inside +-0.4
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            struct.pack_into("<f", buf, off, (seed / 0x7FFFFFFF - 0.5) * 0.8)
        idx = (3 + t) % 8 + 1
        struct.pack_into("<f", buf, 64, (halton(idx, 2) - 0.5) * scale)
        struct.pack_into("<f", buf, 68, -(halton(idx, 3) - 0.5) * scale)   # y is often flipped
        struct.pack_into("<f", buf, 128, t * 0.02)             # slow ramp
        with open(os.path.join(directory, "frame_%02d_p2_b1_256.bin" % t), "wb") as f:
            f.write(buf)
        lines.append("# frame %d present %d render=%dx876 output=2560x1440\n" % (t, 120 + t, rw))
    with open(os.path.join(directory, "index.txt"), "w") as f:
        f.writelines(lines)


def selftest():
    assert halton(1, 2) == 0.5 and halton(2, 2) == 0.25 and halton(3, 2) == 0.75
    assert abs(halton(1, 3) - 1 / 3) < 1e-12 and abs(halton(4, 3) - (1 / 3 + 1 / 9)) < 1e-12
    with tempfile.TemporaryDirectory() as tmp:
        make_capture(tmp, 1.0)
        results, rw = rank(tmp)
        top = results[0]
        assert rw == 1552, rw
        assert (top["param"], top["slot"], top["offset"]) == (2, "b1", 64), top["offset"]
        assert top["phase"] == 3 and top["cycle"] == 8 and top["order"] == (2, 3), top
        assert top["scale"] == "pixels" and top["score"] > 0.99, top
        assert all(r["offset"] not in (0, 4, 128) for r in results), "constant, counter and ramp must not qualify"
        assert len(results) < 2 or results[1]["key"] < 0.6 * top["key"], "noise pair must score well below the planted pair"

    with tempfile.TemporaryDirectory() as tmp:
        make_capture(tmp, 2.0 / 1552)
        results, _ = rank(tmp)
        assert results[0]["offset"] == 64 and results[0]["scale"] == "ndc", results[0]["scale"]
    print("find_jitter selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", nargs="?", help="cbcapture directory (frame_*.bin and index.txt)")
    ap.add_argument("--render-width", type=int, default=0, help="render width in pixels (default: from index.txt)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.directory:
        ap.error("no capture directory given")
    results, rw = rank(args.directory, args.render_width, args.top)
    show(results, rw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
