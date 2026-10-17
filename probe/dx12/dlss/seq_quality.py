"""Measure temporal flicker and stair-stepping of an upscaler from the frame sequences the DLSS add-on dumps.

The add-on writes dump_seq/<stamp>-<DLSS|TSR>-<render>/frame_NNN.dds (DX10-header DDS) plus index.txt when the file
amdlss_dx12_dlss.dump appears next to it. This script turns one such folder, or two to compare, into numbers and PNGs,
so image quality is read from files instead of screenshots of a desktop.

Needs numpy and Pillow. Run it isolated:

    python3 -I probe/dx12/dlss/seq_quality.py SEQ_DIR [SEQ_DIR_B] [--out DIR] [--edge-thresh 12]
    python3 -I probe/dx12/dlss/seq_quality.py --selftest
    uv run --no-project -q --with numpy --with pillow python -I probe/dx12/dlss/seq_quality.py ...   # without them installed

Decoded formats: r11g11b10_float, r16g16b16a16_float, r32g32b32a32_float (scene-linear, tone-mapped with Reinhard c / (1 + c)
then sRGB), r8g8b8a8_unorm(_srgb) and r10g10b10a2_unorm (taken as display-referred). The tone-mapped 8-bit image is what is
measured; luma is 0.2126 R + 0.7152 G + 0.0722 B of its values, 0..255.

Metrics, per sequence (each computed the same way for both):
  temporal instability  Between consecutive frames the global translation is estimated by phase correlation on a downscaled
                        luma (sub-pixel, parabolic peak) and removed from the previous frame by a bilinear shift; the metric is
                        the mean absolute luma difference that remains, median over frame pairs (two identical scenes
                        quantised to 8 bits independently already differ by about 0.25: that is the floor). Reported over edge pixels
                        (Sobel gradient above --edge-thresh levels per pixel in the later frame) and over all pixels. Pairs
                        whose later frame has camera_cut=1 in index.txt, or that are not consecutive, are skipped. Camera
                        parallax and animation also leave a residual: compare sequences of the same scene, not absolute values.
  stair-stepping        On edge pixels, the second derivative of luma along the edge direction (tangent to the Sobel gradient)
                        in levels per pixel squared, divided by the gradient magnitude in levels per pixel; mean over the
                        edge pixels, median over frames. A smooth edge has ~0 curvature along itself, a staircase does not.
PNGs: <label>_first/middle/last.png, <label>_crop3x.png (3x nearest-neighbour zoom of the 128x72 region with the highest
mean residual of the middle frame) and, for two folders, side_by_side.png (middle frames on top, the same region zoomed below).
The last stdout line per sequence is machine readable: RESULT label=... instab_edge=... instab_all=... stair=...
"""
import argparse
import glob
import math
import os
import re
import struct
import sys
import tempfile

import numpy as np
from PIL import Image

DDS_MAGIC = 0x20534444
DX10_FOURCC = 0x30315844
CROP_W, CROP_H, ZOOM = 128, 72, 3
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


# ---- DDS decoding ----------------------------------------------------------------------------------------------

def _small_float_table(mant_bits):
    idx = np.arange(1 << (5 + mant_bits), dtype=np.int64)
    exp, mant = idx >> mant_bits, idx & ((1 << mant_bits) - 1)
    val = np.where(exp == 0, mant / (1 << mant_bits) * 2.0 ** -14, (1 + mant / (1 << mant_bits)) * 2.0 ** (exp.astype(np.float64) - 15))
    return np.where(exp == 31, 65504.0, val).astype(np.float32)  # inf / NaN clamp to the largest half-float


T11, T10 = _small_float_table(6), _small_float_table(5)


def _r11g11b10(raw, w, h):
    p = np.frombuffer(raw, dtype="<u4", count=w * h)
    return np.stack([T11[p & 0x7FF], T11[(p >> 11) & 0x7FF], T10[p >> 22]], axis=-1).reshape(h, w, 3)


def _rgba16f(raw, w, h):
    return np.frombuffer(raw, dtype="<f2", count=w * h * 4).reshape(h, w, 4)[..., :3].astype(np.float32)


def _rgba32f(raw, w, h):
    return np.frombuffer(raw, dtype="<f4", count=w * h * 4).reshape(h, w, 4)[..., :3].copy()


def _rgba8(raw, w, h):
    return np.frombuffer(raw, dtype=np.uint8, count=w * h * 4).reshape(h, w, 4)[..., :3].astype(np.float32) / 255.0


def _r10g10b10a2(raw, w, h):
    p = np.frombuffer(raw, dtype="<u4", count=w * h)
    return np.stack([p & 0x3FF, (p >> 10) & 0x3FF, (p >> 20) & 0x3FF], axis=-1).reshape(h, w, 3).astype(np.float32) / 1023.0


# dxgi -> (name, bytes per texel, scene-linear HDR?, decoder). Non-HDR formats are display-referred and only clipped.
FORMATS = {
    2: ("r32g32b32a32_float", 16, True, _rgba32f),
    10: ("r16g16b16a16_float", 8, True, _rgba16f),
    24: ("r10g10b10a2_unorm", 4, False, _r10g10b10a2),
    26: ("r11g11b10_float", 4, True, _r11g11b10),
    28: ("r8g8b8a8_unorm", 4, False, _rgba8),
    29: ("r8g8b8a8_unorm_srgb", 4, False, _rgba8),
}


def read_dds(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, size = struct.unpack_from("<II", data, 0)
    if magic != DDS_MAGIC or size != 124:
        raise ValueError("%s: not a DDS file" % path)
    height, width = struct.unpack_from("<II", data, 12)
    if struct.unpack_from("<I", data, 84)[0] != DX10_FOURCC:
        raise ValueError("%s: no DX10 header" % path)
    dxgi = struct.unpack_from("<I", data, 128)[0]
    if dxgi not in FORMATS:
        raise ValueError("%s: unsupported DXGI format %d" % (path, dxgi))
    name, texel, hdr, decode = FORMATS[dxgi]
    if len(data) - 148 < width * height * texel:
        raise ValueError("%s: truncated pixel data" % path)
    return width, height, name, hdr, decode(data[148:], width, height)


def tonemap8(rgb, hdr):
    """Scene-linear float RGB (or display-referred 0..1) to the 8-bit sRGB image that is measured."""
    c = np.nan_to_num(rgb, nan=0.0, posinf=65504.0, neginf=0.0)
    c = np.maximum(c, 0.0)
    if hdr:
        c = c / (1.0 + c)
        c = np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return np.rint(np.clip(c, 0.0, 1.0) * 255.0).astype(np.uint8)


def luma_of(rgb8):
    return rgb8.astype(np.float32) @ LUMA


# ---- measurements ----------------------------------------------------------------------------------------------

def downscale(a, d):
    if d <= 1:
        return a
    h, w = (a.shape[0] // d) * d, (a.shape[1] // d) * d
    return a[:h, :w].reshape(h // d, d, w // d, d).mean(axis=(1, 3))


def _parabola(m, c, p):
    den = m - 2 * c + p
    return 0.0 if den == 0 else 0.5 * (m - p) / den


def refine_shift(prev, cur, sx, sy, iters=4):
    """Gauss-Newton on the whole-image translation, starting at (sx, sy): the phase-correlation peak of sub-pixel motion is
    biased towards whole pixels, and a bias of a few tenths of a pixel would show up as flicker. Stops and keeps the best
    estimate when the system is singular or a step exceeds 2 px."""
    for _ in range(iters):
        m = int(math.ceil(max(abs(sx), abs(sy)))) + 3
        warped = shift_image(prev, sx, sy)
        gx, gy = gradients(warped)  # Sobel / 8 is the per-pixel derivative of the warped image
        r = (cur - warped)[m:-m, m:-m]
        gx, gy = gx[m:-m, m:-m], gy[m:-m, m:-m]
        a11, a12, a22 = float((gx * gx).sum()), float((gx * gy).sum()), float((gy * gy).sum())
        det = a11 * a22 - a12 * a12
        if det <= 1e-6 * max(a11 * a22, 1e-12):
            break
        b1, b2 = float((gx * r).sum()), float((gy * r).sum())
        dx, dy = -(a22 * b1 - a12 * b2) / det, -(a11 * b2 - a12 * b1) / det
        if math.hypot(dx, dy) > 2.0:
            break
        sx, sy = sx + dx, sy + dy
        if math.hypot(dx, dy) < 0.005:
            break
    return sx, sy


def estimate_shift(prev, cur):
    """(sx, sy) in pixels with cur(x, y) ~ prev(x - sx, y - sy): phase correlation on a downscaled luma for the whole-pixel
    part, then refine_shift at half resolution; (0, 0) when there is no clear peak (blank or unrelated frames)."""
    d = max(1, int(round(prev.shape[1] / 640.0)))
    a, b = downscale(prev, d), downscale(cur, d)
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1])).astype(np.float32)
    fa, fb = np.fft.rfft2((a - a.mean()) * win), np.fft.rfft2((b - b.mean()) * win)
    cross = np.conj(fa) * fb
    cross /= np.maximum(np.abs(cross), 1e-9)
    corr = np.fft.irfft2(cross, s=a.shape)
    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    if corr[py, px] < 0.03:
        return 0.0, 0.0
    h, w = corr.shape
    fx = _parabola(corr[py, (px - 1) % w], corr[py, px], corr[py, (px + 1) % w])
    fy = _parabola(corr[(py - 1) % h, px], corr[py, px], corr[(py + 1) % h, px])
    sx = ((px if px <= w // 2 else px - w) + fx) * d
    sy = ((py if py <= h // 2 else py - h) + fy) * d
    r = 2 if d > 1 else 1  # refine on a 2x downscaled copy (full size when the frames are small)
    if r == 1:
        return refine_shift(prev, cur, sx, sy)
    sx, sy = refine_shift(downscale(prev, r), downscale(cur, r), sx / r, sy / r)
    return sx * r, sy * r


def shift_image(a, sx, sy):
    """a moved by (sx, sy): out(x, y) = a(x - sx, y - sy), Catmull-Rom (bilinear would blur and show up as residual).
    Edges wrap; callers crop a margin."""
    def axis(img, s, ax):
        i = math.floor(s)
        f = s - i
        if f == 0:
            return np.roll(img, i, axis=ax)
        w = ((-0.5 * f**3 + f**2 - 0.5 * f), (1.5 * f**3 - 2.5 * f**2 + 1.0), (-1.5 * f**3 + 2.0 * f**2 + 0.5 * f), (0.5 * f**3 - 0.5 * f**2))
        # out(x) = sum_k w[k] * img(x - i - (k - 1)) for k = 0..3 (samples at offsets +1, 0, -1, -2 from the integer shift)
        return sum(wk * np.roll(img, i + k - 1, axis=ax) for k, wk in enumerate(w))
    return axis(axis(a, sx, 1), sy, 0)


def gradients(l):
    """Sobel gradient in levels per pixel (so a one-level-per-pixel ramp reads 1.0), with edge replication."""
    p = np.pad(l, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:] - p[:-2, :-2] - 2 * p[1:-1, :-2] - p[2:, :-2]) / 8.0
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:] - p[:-2, :-2] - 2 * p[:-2, 1:-1] - p[:-2, 2:]) / 8.0
    return gx, gy


def edge_mask(l, thresh):
    gx, gy = gradients(l)
    return np.hypot(gx, gy) > thresh


def stair_score(l, thresh):
    """Mean |d2 luma along the edge tangent| over edge pixels, divided by their mean gradient magnitude; nan with no edge pixels."""
    gx, gy = gradients(l)
    mag = np.hypot(gx, gy)
    p = np.pad(l, 1, mode="edge")
    c = p[1:-1, 1:-1]
    ixx = p[1:-1, 2:] + p[1:-1, :-2] - 2 * c
    iyy = p[2:, 1:-1] + p[:-2, 1:-1] - 2 * c
    ixy = (p[2:, 2:] - p[2:, :-2] - p[:-2, 2:] + p[:-2, :-2]) / 4.0
    m = mag > thresh
    m[:2], m[-2:], m[:, :2], m[:, -2:] = False, False, False, False
    if not m.any():
        return float("nan")
    tx, ty = -gy[m] / mag[m], gx[m] / mag[m]
    d2 = tx * tx * ixx[m] + 2 * tx * ty * ixy[m] + ty * ty * iyy[m]
    return float(np.abs(d2).mean() / mag[m].mean())


def load_index(folder):
    """frame number -> camera_cut flag (1 / 0 / None) from index.txt; empty when the file is missing."""
    cuts = {}
    path = os.path.join(folder, "index.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                cols = [c.strip() for c in line.split("|")]
                if len(cols) >= 8 and cols[0].isdigit():
                    cuts[int(cols[0])] = {"1": 1, "0": 0}.get(cols[6])
    return cuts


def list_frames(folder):
    found = []
    for path in glob.glob(os.path.join(folder, "frame_*.dds")):
        m = re.search(r"frame_(\d+)\.dds$", path)
        if m:
            found.append((int(m.group(1)), path))
    return sorted(found)


def analyse(folder, edge_thresh=12.0):
    frames = list_frames(folder)
    if len(frames) < 2:
        raise ValueError("%s: need at least 2 frame_NNN.dds files, found %d" % (folder, len(frames)))
    cuts = load_index(folder)
    mid = len(frames) // 2
    res = {"folder": folder, "frames": len(frames), "pairs": 0, "skipped": 0}
    edge_res, all_res, stairs, edge_share, shifts = [], [], [], [], []
    resid_sum, resid_n = None, 0
    prev, prev_no = None, None
    for i, (no, path) in enumerate(frames):
        w, h, fmt, hdr, rgb = read_dds(path)
        if i == 0:
            res.update(width=w, height=h, format=fmt)
        elif (w, h) != (res["width"], res["height"]):
            raise ValueError("%s: size %dx%d differs from the first frame %dx%d" % (path, w, h, res["width"], res["height"]))
        rgb8 = tonemap8(rgb, hdr)
        luma = luma_of(rgb8)
        if i in (0, mid, len(frames) - 1):
            res["rgb_first" if i == 0 else "rgb_last" if i == len(frames) - 1 else "rgb_mid"] = rgb8
            if i == mid:
                res["rgb_mid"] = rgb8
        s = stair_score(luma, edge_thresh)
        if not math.isnan(s):
            stairs.append(s)
        if prev is not None:
            if no != prev_no + 1 or cuts.get(no) == 1:
                res["skipped"] += 1
            else:
                sx, sy = estimate_shift(prev, luma)
                if math.hypot(sx, sy) < 0.02:
                    sx = sy = 0.0
                shifts.append(math.hypot(sx, sy))
                m = int(math.ceil(max(abs(sx), abs(sy)))) + 2
                comp = shift_image(prev, sx, sy) if (sx or sy) else prev
                diff = np.zeros_like(luma)
                diff[m:-m, m:-m] = np.abs(luma[m:-m, m:-m] - comp[m:-m, m:-m])
                mask = edge_mask(luma, edge_thresh)
                mask[:m], mask[-m:], mask[:, :m], mask[:, -m:] = False, False, False, False
                inner = np.zeros(luma.shape, dtype=bool)
                inner[m:-m, m:-m] = True
                all_res.append(float(diff[inner].mean()))
                if mask.any():
                    edge_res.append(float(diff[mask].mean()))
                edge_share.append(float(mask.mean()))
                resid_sum = diff if resid_sum is None else resid_sum + diff
                resid_n += 1
                res["pairs"] += 1
        prev, prev_no = luma, no
    if res["pairs"] == 0:
        raise ValueError("%s: no usable consecutive pair (cuts or gaps everywhere)" % folder)
    res.update(instab_edge=float(np.median(edge_res)) if edge_res else float("nan"), instab_all=float(np.median(all_res)),
               stair=float(np.median(stairs)) if stairs else float("nan"), edge_share=float(np.median(edge_share)),
               shift_median=float(np.median(shifts)), shift_max=float(np.max(shifts)), resid=resid_sum / resid_n)
    return res


def worst_region(resid, w=CROP_W, h=CROP_H):
    """Top-left corner of the w x h window with the highest mean residual (integral image)."""
    h, w = min(h, resid.shape[0]), min(w, resid.shape[1])
    ii = np.pad(resid.astype(np.float64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    box = ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]
    y, x = np.unravel_index(int(np.argmax(box)), box.shape)
    return int(x), int(y), w, h


def zoom_crop(rgb8, x, y, w, h):
    return np.repeat(np.repeat(rgb8[y:y + h, x:x + w], ZOOM, axis=0), ZOOM, axis=1)


def save_png(path, arr):
    Image.fromarray(np.ascontiguousarray(arr)).save(path)


def write_outputs(results, labels, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    regions = [worst_region(r["resid"]) for r in results]
    if len(results) == 2 and results[0]["resid"].shape == results[1]["resid"].shape:
        regions = [worst_region(results[0]["resid"] + results[1]["resid"])] * 2  # one shared region, so the crops compare
    for r, label, region in zip(results, labels, regions):
        for part in ("first", "mid", "last"):
            name = "middle" if part == "mid" else part
            paths.append(os.path.join(out_dir, "%s_%s.png" % (label, name)))
            save_png(paths[-1], r["rgb_" + part])
        paths.append(os.path.join(out_dir, "%s_crop3x.png" % label))
        save_png(paths[-1], zoom_crop(r["rgb_mid"], *region))
        r["region"] = region
    if len(results) == 2:
        tiles = [Image.fromarray(np.ascontiguousarray(r["rgb_mid"])) for r in results]
        scale = min(1.0, 960.0 / tiles[0].width)
        tiles = [t.resize((max(1, int(t.width * scale)), max(1, int(t.height * scale))), Image.LANCZOS) for t in tiles]
        crops = [Image.fromarray(np.ascontiguousarray(zoom_crop(r["rgb_mid"], *reg))) for r, reg in zip(results, regions)]
        width = max(tiles[0].width + tiles[1].width, crops[0].width + crops[1].width)
        sheet = Image.new("RGB", (width, max(t.height for t in tiles) + max(c.height for c in crops)), (24, 24, 24))
        sheet.paste(tiles[0], (0, 0))
        sheet.paste(tiles[1], (tiles[0].width, 0))
        top = max(t.height for t in tiles)
        sheet.paste(crops[0], (0, top))
        sheet.paste(crops[1], (crops[0].width, top))
        paths.append(os.path.join(out_dir, "side_by_side.png"))
        sheet.save(paths[-1])
    return paths


def report(r, label):
    x, y, w, h = r["region"]
    lines = [
        "sequence %s: %s" % (label, r["folder"]),
        "  %d frames %dx%d %s, %d frame pairs measured (%d skipped: gaps or camera cuts)" % (r["frames"], r["width"], r["height"], r["format"], r["pairs"], r["skipped"]),
        "  temporal instability, edge pixels : %.3f  (median of mean |luma diff|, 0..255)" % r["instab_edge"],
        "  temporal instability, all pixels  : %.3f" % r["instab_all"],
        "  stair-stepping score on edges     : %.3f  (|d2 along edge| / gradient, median over frames)" % r["stair"],
        "  edge pixels                       : %.1f%% of the frame (median)" % (100 * r["edge_share"]),
        "  global motion removed per pair    : median %.2f px, max %.2f px" % (r["shift_median"], r["shift_max"]),
        "  worst flicker region              : x=%d y=%d %dx%d" % (x, y, w, h),
        "RESULT label=%s instab_edge=%.4f instab_all=%.4f stair=%.4f frames=%d pairs=%d" % (label, r["instab_edge"], r["instab_all"], r["stair"], r["frames"], r["pairs"]),
    ]
    return "\n".join(lines)


def compare(a, b, la, lb):
    def ratio(x, y):
        return "%.2fx" % (y / x) if x else "n/a"
    return "\n".join([
        "%s relative to %s (below 1.00x means %s is steadier / smoother):" % (lb, la, lb),
        "  temporal instability, edge pixels : %s" % ratio(a["instab_edge"], b["instab_edge"]),
        "  temporal instability, all pixels  : %s" % ratio(a["instab_all"], b["instab_all"]),
        "  stair-stepping score              : %s" % ratio(a["stair"], b["stair"]),
    ])


# ---- self-check ------------------------------------------------------------------------------------------------

def _write_dds(path, rgb):
    """r16g16b16a16_float DDS with the DX10 header the add-on writes."""
    h, w = rgb.shape[:2]
    rgba = np.concatenate([rgb, np.ones((h, w, 1), dtype=rgb.dtype)], axis=-1).astype("<f2")
    header = struct.pack("<32I", DDS_MAGIC, 124, 0x100F, h, w, w * 8, 0, 1, *([0] * 11), 32, 4, DX10_FOURCC, 0, 0, 0, 0, 0, 0x1000, 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(header + struct.pack("<5I", 10, 3, 0, 1, 0) + rgba.tobytes())


def _pattern(w=256, h=144, seed=1):
    """Smooth random blobs plus hard-edged rectangles: texture for phase correlation and edges for the metrics."""
    rng = np.random.default_rng(seed)
    base = rng.random((h // 8 + 1, w // 8 + 1)).astype(np.float32)
    img = np.array(Image.fromarray((base * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), dtype=np.float32) / 255.0
    for _ in range(12):
        x, y = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 30))
        img[y:y + int(rng.integers(10, 30)), x:x + int(rng.integers(15, 40))] = rng.random()
    return np.repeat(img[..., None], 3, axis=-1) * 2.0  # scene-linear, up to 2.0


def _blobs(dx, dy, w=256, h=144):
    """Analytic Gaussian blobs moved by a sub-pixel (dx, dy): frames that differ by exactly that translation."""
    rng = np.random.default_rng(4)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.full((h, w), 0.2, dtype=np.float32)
    for _ in range(40):
        bx, by, s, a = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(2, 6), rng.uniform(0.3, 1.5)
        img += a * np.exp(-((xs - bx - dx) ** 2 + (ys - by - dy) ** 2) / (2 * s * s))
    return np.repeat(img[..., None], 3, axis=-1)


def _sequence(folder, frames, cuts=()):
    os.makedirs(folder)
    for i, f in enumerate(frames):
        _write_dds(os.path.join(folder, "frame_%03d.dds" % i), f)
    rows = ["frame | present | upscaler | render | output | jitter | camera_cut | file"]
    rows += ["%03d | %d | TSR | 1x1 | 1x1 | n/a | %d | frame_%03d.dds" % (i, i, 1 if i in cuts else 0, i) for i in range(len(frames))]
    with open(os.path.join(folder, "index.txt"), "w") as f:
        f.write("\n".join(rows) + "\n")


def selftest():
    # decoders: r11g11b10 1.0 is 0x3C0 (11 bit) and 0x1E0 (10 bit); r10g10b10a2 and r8g8b8a8 scale to 0..1
    packed = struct.pack("<I", 0x3C0 | (0x3C0 << 11) | (0x1E0 << 22))
    assert np.allclose(_r11g11b10(packed, 1, 1), 1.0), _r11g11b10(packed, 1, 1)
    assert np.allclose(_r10g10b10a2(struct.pack("<I", 1023 | (512 << 10)), 1, 1)[0, 0], [1.0, 512 / 1023.0, 0.0])
    assert np.allclose(_rgba8(bytes([255, 0, 51, 255]), 1, 1)[0, 0], [1.0, 0.0, 0.2])
    assert tonemap8(np.ones((1, 1, 3), dtype=np.float32), True)[0, 0, 0] == 188  # Reinhard 0.5 -> sRGB 188

    pat = _pattern()
    rng = np.random.default_rng(7)
    with tempfile.TemporaryDirectory() as tmp:
        static = os.path.join(tmp, "static")
        noisy = os.path.join(tmp, "noisy")
        moving = os.path.join(tmp, "moving")
        cut = os.path.join(tmp, "cut")
        sub = os.path.join(tmp, "sub")
        n = 8
        _sequence(sub, [_blobs(0.4 * i, -0.25 * i) for i in range(n)])  # sub-pixel motion: estimated and removed too
        e = analyse(sub)
        assert abs(e["shift_median"] - math.hypot(0.4, 0.25)) < 0.05 and e["instab_all"] < 0.6, (e["shift_median"], e["instab_all"])
        _sequence(static, [pat] * n)
        _sequence(noisy, [np.clip(pat + rng.normal(0, 0.08, pat.shape).astype(np.float32), 0, None) for _ in range(n)])
        _sequence(moving, [np.roll(pat, (2 * i, 3 * i), axis=(0, 1)) for i in range(n)])
        _sequence(cut, [pat] * 3 + [_pattern(seed=9)] * 3, cuts=(3,))
        a, b, c, d = (analyse(p) for p in (static, noisy, moving, cut))
        assert a["instab_all"] < 0.01 and a["instab_edge"] < 0.01, a["instab_all"]
        assert b["instab_all"] > 2.0 and b["instab_edge"] > 2.0, b["instab_all"]  # random per-frame noise scores higher
        assert c["instab_all"] < 0.05 * b["instab_all"] and c["instab_edge"] < 0.5, (c["instab_all"], c["instab_edge"])  # a translation is compensated
        assert abs(c["shift_median"] - math.hypot(2, 3)) < 0.2, c["shift_median"]
        assert d["skipped"] == 1 and d["pairs"] == 4 and d["instab_all"] < 0.01, (d["skipped"], d["pairs"], d["instab_all"])  # the cut pair is not measured
        out = os.path.join(tmp, "png")
        paths = write_outputs([a, b], ["static", "noisy"], out)
        assert all(os.path.getsize(p) > 0 for p in paths) and any(p.endswith("side_by_side.png") for p in paths), paths
        assert Image.open(os.path.join(out, "static_crop3x.png")).size == (CROP_W * ZOOM, CROP_H * ZOOM)
        assert "RESULT label=static" in report(a, "static") and "relative to" in compare(a, b, "static", "noisy")

    # stair-stepping: the same shallow edge, aliased (binary) against 8x8 supersampled
    h, w = 128, 256
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    def edge_image(ss):
        acc = np.zeros((h, w), dtype=np.float32)
        for sy in range(ss):
            for sx in range(ss):
                acc += (ys + (sy + 0.5) / ss > 0.25 * (xs + (sx + 0.5) / ss) + 40).astype(np.float32)
        return acc / (ss * ss) * 200.0
    aliased, smooth = stair_score(edge_image(1), 12.0), stair_score(edge_image(8), 12.0)
    assert aliased > 1.5 * smooth > 0, (aliased, smooth)
    print("seq_quality selftest ok (static %.4f, noisy %.3f, translated %.4f flicker; stair aliased %.3f vs smooth %.3f)" %
          (a["instab_all"], b["instab_all"], c["instab_all"], aliased, smooth))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="*", help="one or two dump_seq/<stamp>-... folders")
    ap.add_argument("--out", default="seq_quality_out", help="directory for the PNGs (default ./seq_quality_out)")
    ap.add_argument("--edge-thresh", type=float, default=12.0, help="Sobel gradient (levels per pixel) above which a pixel is an edge")
    ap.add_argument("--labels", default="", help="two comma-separated labels (default A,B or the folder's upscaler)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not 1 <= len(args.folders) <= 2:
        ap.error("give one or two sequence folders")
    labels = [s for s in args.labels.split(",") if s] or ["A", "B"]
    try:
        results = [analyse(f, args.edge_thresh) for f in args.folders]
    except (OSError, ValueError, struct.error) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    paths = write_outputs(results, labels[:len(results)], args.out)
    for r, label in zip(results, labels):
        print(report(r, label))
    if len(results) == 2:
        print(compare(results[0], results[1], labels[0], labels[1]))
    print("PNGs: " + ", ".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
