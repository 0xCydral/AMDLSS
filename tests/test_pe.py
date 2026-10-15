# SPDX-License-Identifier: GPL-2.0-or-later
"""amdlss.pe on small Windows DLLs built byte by byte, so it reads a file the way the Windows loader would."""
import os
import struct
from pathlib import Path

import pytest

from amdlss import pe


def _node(key, value=b'', children=b''):
    """One VS_VERSIONINFO node: its length and value length, the type, the UTF-16 key, the value, the children,
    every part padded to a four-byte boundary."""
    body = key.encode('utf-16-le') + b'\0\0'
    body += b'\0' * ((4 - (6 + len(body)) % 4) % 4)
    body += value
    body += b'\0' * ((4 - (6 + len(body)) % 4) % 4)
    body += children
    return struct.pack('<HHH', 6 + len(body), len(value), 1) + body


def version_blob(version=(4, 1, 1, 2740), strings=None):
    """The RT_VERSION data: VS_FIXEDFILEINFO and, with `strings`, a StringFileInfo of {name: value} pairs (each a
    UTF-16 string, as a real DLL's ProductName/FileDescription/OriginalFilename are)."""
    high, low = (version[0] << 16) | version[1], (version[2] << 16) | version[3]
    fixed = struct.pack('<IIIIIIIIIIIII', 0xFEEF04BD, 0x10000, high, low, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    children = b''
    if strings:
        table = b''.join(_node(name, value.encode('utf-16-le') + b'\0\0') for name, value in strings.items())
        children = _node('StringFileInfo', b'', _node('040904B0', b'', table))
    return _node('VS_VERSION_INFO', fixed, children)


def build_dll(exports=('ffxCreateContext', 'ffxDestroyContext'), version=(4, 1, 1, 2740), machine=0x8664,
              magic=0x20b, resources=True, strings=None):
    """A minimal PE with an export table and a version resource. Layout: headers at 0, .edata at file 0x400 (RVA
    0x1000), .rsrc at file 0x800 (RVA 0x2000)."""
    image = bytearray(0xD00)
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
        struct.pack_into('<II', image, directories + 16, 0x2000, 0x300)
    sections = base + optional
    for index, (name, virtual, raw, size) in enumerate(((b'.edata', 0x1000, 0x400, 0x200),
                                                        (b'.rsrc', 0x2000, 0x800, 0x300))):
        image[sections + 40 * index:sections + 40 * index + 8] = name.ljust(8, b'\0')
        struct.pack_into('<IIII', image, sections + 40 * index + 8, size, virtual, size, raw)
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
        blob = version_blob(version, strings)
        info = rsrc + 0x60
        assert info + len(blob) <= rsrc + 0x300, 'the version resource does not fit the section'
        struct.pack_into('<IIII', image, rsrc + 0x48, 0x2000 + 0x60, len(blob), 0, 0)   # the data entry
        image[info:info + len(blob)] = blob
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


def test_version_words_read_the_resource_texts(tmp_path):
    words = pe.version_words(written(tmp_path, build_dll(
        version=(0, 9, 4, 0), strings={'ProductName': 'OptiScaler', 'FileDescription': 'OptiScaler',
                                       'OriginalFilename': 'OptiScaler.dll'})))
    assert 'OptiScaler' in words and 'OptiScaler.dll' in words
    assert 'VS_VERSION_INFO' in words and 'StringFileInfo' in words   # keys come out too


def test_version_words_of_a_dll_without_strings_or_resources(tmp_path):
    assert 'OptiScaler' not in pe.version_words(written(tmp_path, build_dll()))
    assert pe.version_words(written(tmp_path, build_dll(resources=False))) == ()
    assert pe.version_words(written(tmp_path, b'not a dll at all' * 40)) == ()   # never raises


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
    if not dll.is_file():
        # HelixSR 1.9.0 does not build amd-fsr: AMD's DLL travels in the HelixSR Scaler package
        source = os.environ.get('AMDLSS_TEST_SCALER', '')
        if not source:
            pytest.skip("AMD's DLL is not in the HelixSR folder and AMDLSS_TEST_SCALER is not set")
        dll = Path(source) / 'OptiScaler' / 'amd_fidelityfx_upscaler_dx12.dll'
    info = pe.inspect(dll)
    assert info.is_64bit and info.version == '4.1.1.2740' and 'ffxCreateContext' in info.exports
