# SPDX-License-Identifier: GPL-2.0-or-later
"""amdlss.pe on small Windows DLLs built byte by byte, so it reads a file the way the Windows loader would."""
import os
import struct
from pathlib import Path

import pytest

from amdlss import pe


def build_dll(exports=('ffxCreateContext', 'ffxDestroyContext'), version=(4, 1, 1, 2740), machine=0x8664,
              magic=0x20b, resources=True):
    """A minimal PE with an export table and a version resource. Layout: headers at 0, .edata at file 0x400 (RVA
    0x1000), .rsrc at file 0x800 (RVA 0x2000)."""
    image = bytearray(0xC00)
    image[0:2] = b'MZ'
    struct.pack_into('<I', image, 0x3c, 0x80)
    image[0x80:0x84] = b'PE\0\0'
    optional = 240 if magic == 0x20b else 224
    struct.pack_into('<HHIIIHH', image, 0x84, machine, 2, 0, 0, 0, optional, 0x2022)
    base = 0x98
    struct.pack_into('<H', image, base, magic)
    directories = base + (112 if magic == 0x20b else 96)
    struct.pack_into('<I', image, directories - 4, 16)
    if exports is not None:
        struct.pack_into('<II', image, directories, 0x1000, 0x200)
    if resources:
        struct.pack_into('<II', image, directories + 16, 0x2000, 0x200)
    sections = base + optional
    for index, (name, virtual, raw) in enumerate(((b'.edata', 0x1000, 0x400), (b'.rsrc', 0x2000, 0x800))):
        image[sections + 40 * index:sections + 40 * index + 8] = name.ljust(8, b'\0')
        struct.pack_into('<IIII', image, sections + 40 * index + 8, 0x200, virtual, 0x200, raw)
    if exports is not None:
        edata = 0x400
        names_at, strings_at = 0x1000 + 0x80, 0x1000 + 0x100
        struct.pack_into('<IIIII', image, edata + 20, len(exports), len(exports), 0x1000 + 0x40, names_at, 0x1000 + 0xC0)
        cursor = strings_at
        for index, name in enumerate(exports):
            struct.pack_into('<I', image, edata + 0x80 + 4 * index, cursor)
            image[edata + (cursor - 0x1000):edata + (cursor - 0x1000) + len(name) + 1] = name.encode() + b'\0'
            cursor += len(name) + 1
    if resources:
        rsrc = 0x800
        struct.pack_into('<H', image, rsrc + 14, 1)                          # one ID entry: type 16 (RT_VERSION)
        struct.pack_into('<II', image, rsrc + 16, 16, 0x80000000 | 0x18)
        struct.pack_into('<H', image, rsrc + 0x18 + 14, 1)                # name 1
        struct.pack_into('<II', image, rsrc + 0x18 + 16, 1, 0x80000000 | 0x30)
        struct.pack_into('<H', image, rsrc + 0x30 + 14, 1)                # language 0x409
        struct.pack_into('<II', image, rsrc + 0x30 + 16, 0x409, 0x48)
        struct.pack_into('<IIII', image, rsrc + 0x48, 0x2000 + 0x60, 0x40, 0, 0)   # the data entry
        info = rsrc + 0x60
        struct.pack_into('<HHH', image, info, 0x40, 0x34, 0)
        image[info + 6:info + 38] = 'VS_VERSION_INFO'.encode('utf-16-le') + b'\0\0'
        high, low = (version[0] << 16) | version[1], (version[2] << 16) | version[3]
        struct.pack_into('<IIIII', image, info + 40, 0xFEEF04BD, 0x10000, high, low, 0)
    return bytes(image)


def written(tmp_path, data, name='a.dll'):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_a_good_dll_gives_its_exports_and_version(tmp_path):
    info = pe.inspect(written(tmp_path, build_dll()))
    assert info.is_64bit and info.version == '4.1.1.2740'
    assert info.exports == {'ffxCreateContext', 'ffxDestroyContext'}


def test_a_32_bit_dll_is_not_64_bit(tmp_path):
    info = pe.inspect(written(tmp_path, build_dll(machine=0x14c, magic=0x10b)))
    assert not info.is_64bit and 'ffxCreateContext' in info.exports


def test_a_dll_without_exports_or_version_still_reads(tmp_path):
    info = pe.inspect(written(tmp_path, build_dll(exports=None, resources=False)))
    assert info.exports == frozenset() and info.version == ''


def test_files_that_are_not_dlls_raise(tmp_path):
    for data in (b'', b'MZ' + b'\0' * 10, b'hello world' * 100, b'MZ' + b'\0' * 0x3a + b'\xff\xff\xff\x7f' + b'\0' * 100):
        with pytest.raises(pe.PEError):
            pe.inspect(written(tmp_path, data))
    with pytest.raises(pe.PEError):
        pe.inspect(tmp_path / 'missing.dll')


def test_a_truncated_dll_raises_instead_of_reading_past_the_end(tmp_path):
    with pytest.raises(pe.PEError):
        pe.inspect(written(tmp_path, build_dll()[:0x500]))


def test_an_absurd_export_count_is_refused(tmp_path):
    data = bytearray(build_dll())
    struct.pack_into('<I', data, 0x400 + 24, 0x7fffffff)
    with pytest.raises(pe.PEError):
        pe.inspect(written(tmp_path, bytes(data)))


@pytest.mark.skipif(not os.environ.get('AMDLSS_TEST_HELIXSR'), reason='needs a real HelixSR folder')
def test_amds_real_dll_reads():
    dll = Path(os.environ['AMDLSS_TEST_HELIXSR']).expanduser() / 'amd-fsr' / 'amd_fidelityfx_upscaler_dx12.dll'
    info = pe.inspect(dll)
    assert info.is_64bit and info.version == '4.1.1.2740' and 'ffxCreateContext' in info.exports
