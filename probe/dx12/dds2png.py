"""Convert the .dds files written by amdlss_dx12_probe (F11 dump) into viewable PNGs.

Standard library only (numpy is not installed on the host). Run it isolated:

    python3 -I probe/dx12/dds2png.py dump/001_*.dds            # PNGs land next to the .dds files
    python3 -I probe/dx12/dds2png.py --out /tmp/png dump/001_*.dds
    python3 -I probe/dx12/dds2png.py --selftest

Per format family:
  r16g16b16a16_float, r32g32b32a32_float, r11g11b10_float  Reinhard tonemap (c / (1 + c)) then sRGB, alpha dropped.
  r16g16_unorm                                             R=ch0, G=ch1, B=0, values as stored. UE encodes velocity
                                                           centred at 0.5, so "no motion" shows as 128 in R and G.
  r16g16_float, r16_float/unorm, r8/r8g8 unorm/uint        stretched min..max to full range (R/G, B=0 for two channels).
  r32_float / r24 depth                                    stretched min..max, near = bright. UE uses reversed-Z (near
                                                           is the larger value); --z auto picks it from the median.
"""
import argparse
import os
import struct
import sys
import tempfile
import zlib
from array import array

DDS_MAGIC = 0x20534444
DX10_FOURCC = 0x30315844


def small_float(bits, mant_bits):
    exp, mant = bits >> mant_bits, bits & ((1 << mant_bits) - 1)
    if exp == 0:
        return mant / (1 << mant_bits) * 2.0 ** -14
    if exp == 31:
        return 65504.0
    return (1 + mant / (1 << mant_bits)) * 2.0 ** (exp - 15)


T11 = [small_float(i, 6) for i in range(2048)]
T10 = [small_float(i, 5) for i in range(1024)]


def unpack_row(code, scale):
    def decode(row, width):
        vals = struct.unpack("<%d%s" % (len(row) // struct.calcsize(code), code), row)
        return [v * scale for v in vals] if scale != 1 else list(vals)
    return decode


def row_r11g11b10(row, width):
    out = []
    for p in struct.unpack("<%dI" % width, row):
        out += (T11[p & 0x7FF], T11[(p >> 11) & 0x7FF], T10[p >> 22])
    return out


def row_r24(row, width):
    return [(p & 0xFFFFFF) / 16777215.0 for p in struct.unpack("<%dI" % width, row)]


# dxgi -> (name, channels, bytes per texel, kind, row decoder). Kinds: color, raw2, norm, depth, rgb8.
FORMATS = {
    2: ("r32g32b32a32_float", 4, 16, "color", unpack_row("f", 1)),
    10: ("r16g16b16a16_float", 4, 8, "color", unpack_row("e", 1)),
    26: ("r11g11b10_float", 3, 4, "color", row_r11g11b10),
    28: ("r8g8b8a8_unorm", 4, 4, "rgb8", unpack_row("B", 1 / 255.0)),
    34: ("r16g16_float", 2, 4, "norm", unpack_row("e", 1)),
    35: ("r16g16_unorm", 2, 4, "raw2", unpack_row("H", 1 / 65535.0)),
    41: ("r32_float", 1, 4, "depth", unpack_row("f", 1)),
    46: ("r24_unorm_x8", 1, 4, "depth", row_r24),
    49: ("r8g8_unorm", 2, 2, "norm", unpack_row("B", 1)),
    50: ("r8g8_uint", 2, 2, "norm", unpack_row("B", 1)),
    54: ("r16_float", 1, 2, "norm", unpack_row("e", 1)),
    56: ("r16_unorm", 1, 2, "norm", unpack_row("H", 1)),
    61: ("r8_unorm", 1, 1, "norm", unpack_row("B", 1)),
    62: ("r8_uint", 1, 1, "norm", unpack_row("B", 1)),
}


def read_dds(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, size = struct.unpack_from("<II", data, 0)
    if magic != DDS_MAGIC or size != 124:
        raise ValueError("not a DDS file")
    height, width = struct.unpack_from("<II", data, 12)
    if struct.unpack_from("<I", data, 84)[0] != DX10_FOURCC:
        raise ValueError("no DX10 header")
    dxgi = struct.unpack_from("<I", data, 128)[0]
    return width, height, dxgi, data[148:]


def decode(width, height, dxgi, payload):
    if dxgi not in FORMATS:
        raise ValueError("unsupported DXGI format %d" % dxgi)
    name, nch, texel, kind, row_fn = FORMATS[dxgi]
    if len(payload) < width * height * texel:
        raise ValueError("truncated pixel data")
    vals = array("f")
    for y in range(height):
        vals.extend(row_fn(payload[y * width * texel:(y + 1) * width * texel], width))
    return name, nch, kind, vals


def srgb8(c):
    c = 0.0 if c != c or c < 0 else min(c, 1.0)
    return int(round((12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055) * 255))


def to8(c):
    return int(round(0.0 if c != c or c < 0 else min(c, 1.0) * 255))


def stretch(vals):
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    return [(v - lo) / span for v in vals], lo, hi


def render(nch, kind, vals, z_mode="auto"):
    """Returns (bytes per pixel, flat list of 0..255 ints, note)."""
    note = ""
    if kind == "color":
        out = []
        for i in range(0, len(vals), nch):
            for c in vals[i:i + 3]:
                c = 0.0 if c != c or c < 0 else c
                out.append(srgb8(c / (1.0 + c)))
        return 3, out, "tonemap: Reinhard + sRGB"
    if kind == "rgb8":
        return 3, [to8(c) for i in range(0, len(vals), nch) for c in vals[i:i + 3]], ""
    if kind == "raw2":
        out = []
        for i in range(0, len(vals), 2):
            out += (to8(vals[i]), to8(vals[i + 1]), 0)
        return 3, out, "raw values; UE velocity is centred at 0.5, so 128 means no motion"
    norm, lo, hi = stretch(vals)
    note = "stretched %g..%g" % (lo, hi)
    if kind == "depth":
        sample = sorted(vals[::max(1, len(vals) // 4096)])
        median = sample[len(sample) // 2]
        reversed_z = z_mode == "reversed" or (z_mode == "auto" and median < (lo + hi) / 2)
        if not reversed_z:
            norm = [1.0 - v for v in norm]
        note += ", reversed-Z" if reversed_z else ", standard-Z inverted"
    if nch == 1:
        return 1, [to8(v) for v in norm], note
    out = []
    for i in range(0, len(norm), 2):
        out += (to8(norm[i]), to8(norm[i + 1]), 0)
    return 3, out, note


def chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


def write_png(path, width, height, bpp, pixels):
    rows = b"".join(b"\0" + bytes(pixels[y * width * bpp:(y + 1) * width * bpp]) for y in range(height))
    color_type = 0 if bpp == 1 else 2
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def convert(path, out_dir, z_mode="auto"):
    width, height, dxgi, payload = read_dds(path)
    name, nch, kind, vals = decode(width, height, dxgi, payload)
    bpp, pixels, note = render(nch, kind, vals, z_mode)
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".png")
    write_png(out, width, height, bpp, pixels)
    return out, "%s %dx%d %s" % (name, width, height, note)


# ---- self-check -------------------------------------------------------------------------------------------

def make_dds(width, height, dxgi, payload):
    header = struct.pack("<32I", DDS_MAGIC, 124, 0x100F, height, width, 0, 0, 1, *([0] * 11), 32, 4, DX10_FOURCC,
                         0, 0, 0, 0, 0, 0x1000, 0, 0, 0, 0)
    return header + struct.pack("<5I", dxgi, 3, 0, 1, 0) + payload


def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(tag + body) & 0xFFFFFFFF
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    width, height, _, color_type = ihdr[0], ihdr[1], ihdr[2], ihdr[3]
    bpp = 1 if color_type == 0 else 3
    raw = zlib.decompress(idat)
    stride = width * bpp + 1
    assert all(raw[y * stride] == 0 for y in range(height))
    return width, height, bpp, [raw[y * stride + 1 + x] for y in range(height) for x in range(width * bpp)]


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        def run(name, w, h, dxgi, payload, z="auto"):
            dds = os.path.join(tmp, name + ".dds")
            with open(dds, "wb") as f:
                f.write(make_dds(w, h, dxgi, payload))
            png, _ = convert(dds, tmp, z)
            return read_png(png)

        # float: 2x1 r16g16b16a16_float, black and (1,1,1,1) -> Reinhard 0.5 -> sRGB 188
        w, h, bpp, px = run("f16", 2, 1, 10, struct.pack("<8e", 0, 0, 0, 1, 1, 1, 1, 1))
        assert (w, h, bpp) == (2, 1, 3) and px[:3] == [0, 0, 0] and px[3:] == [188, 188, 188], px

        # unorm: r8_unorm 4x1 stretched to the full range
        w, h, bpp, px = run("u8", 4, 1, 61, bytes([0, 255, 128, 64]))
        assert (w, h, bpp) == (4, 1, 1) and px == [0, 255, 128, 64], px

        # r16g16_unorm velocity: 0.5 centre stays at 128, B stays 0
        w, h, bpp, px = run("vel", 1, 1, 35, struct.pack("<2H", 32768, 65535))
        assert px == [128, 255, 0], px

        # r11g11b10_float: 1.0 is 0x3C0 (11 bit), 0x1E0 (10 bit); Reinhard 0.5 -> 188
        packed = 0x3C0 | (0x3C0 << 11) | (0x1E0 << 22)
        w, h, bpp, px = run("r11", 1, 1, 26, struct.pack("<I", packed))
        assert px == [188, 188, 188], px

        # reversed-Z depth: far (0.0) dominates, near (0.9) must come out bright
        w, h, bpp, px = run("z", 4, 1, 41, struct.pack("<4f", 0.0, 0.0, 0.0, 0.9))
        assert px == [0, 0, 0, 255], px

        # standard-Z depth: far (1.0) dominates, near (0.1) must still come out bright
        w, h, bpp, px = run("zs", 4, 1, 41, struct.pack("<4f", 1.0, 1.0, 1.0, 0.1))
        assert px[3] > px[0] and px[3] == 255, px
    print("dds2png selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help=".dds files from the dump directory")
    ap.add_argument("--out", help="output directory (default: next to each .dds)")
    ap.add_argument("--z", choices=("auto", "reversed", "standard"), default="auto", help="depth convention")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.files:
        ap.error("no files given")
    status = 0
    for path in args.files:
        try:
            out_dir = args.out or os.path.dirname(os.path.abspath(path))
            os.makedirs(out_dir, exist_ok=True)
            out, info = convert(path, out_dir, args.z)
            print("%s -> %s (%s)" % (os.path.basename(path), os.path.basename(out), info))
        except (OSError, ValueError, struct.error) as e:
            print("%s: %s" % (path, e), file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
