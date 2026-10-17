# SPDX-License-Identifier: GPL-2.0-or-later
"""A small reader for Windows DLLs: is it 64-bit, which functions does it export and what is its file version.

AMDLSS needs this to accept an FSR 4 DLL the user picked: it must be a 64-bit DLL that exports `ffxCreateContext` (AMD's
FidelityFX API) and whose file version starts with 4. logs.file_version() asks Windows for a version, which does not work
on Linux, and a DLL the user picked is untrusted input, so this reads the PE headers directly, only the parts it needs,
and refuses anything that points outside the file. Nothing is loaded or run.
"""
import os
import re
import struct
from collections import namedtuple

Info = namedtuple('Info', 'is_64bit exports version')
MAX_EXPORTS = 20000
MAX_NAME = 256
FIXED_FILE_INFO = b'\xbd\x04\xef\xfe'   # VS_FIXEDFILEINFO signature 0xFEEF04BD, little endian
RT_VERSION = 16


class PEError(Exception):
    pass


class _Image:
    """Reads fixed pieces of one PE file; every read that would pass the end of the file raises PEError."""

    def __init__(self, stream, size):
        self.stream, self.size = stream, size
        self.sections = []
        self.table = 0   # where the data directories start (set by _headers)

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.size:
            raise PEError('the file ends before its own headers say it does')
        self.stream.seek(offset)
        return self.stream.read(length)

    def unpack(self, form, offset):
        return struct.unpack(form, self.read(offset, struct.calcsize(form)))

    def to_offset(self, rva):
        for virtual, span, raw in self.sections:
            if virtual <= rva < virtual + span:
                return raw + rva - virtual
        raise PEError('an address in the file points to no section')

    def text(self, rva):
        data = self.read(self.to_offset(rva), min(MAX_NAME, self.size - self.to_offset(rva)))
        return data.split(b'\0', 1)[0].decode('ascii', errors='replace')


def _headers(image):
    if image.read(0, 2) != b'MZ':
        raise PEError('not a Windows program file (no MZ header)')
    (start,) = image.unpack('<I', 0x3c)
    if image.read(start, 4) != b'PE\0\0':
        raise PEError('not a Windows program file (no PE header)')
    machine, count, _stamp, _symbols, _count, optional_size, _flags = image.unpack('<HHIIIHH', start + 4)
    optional = start + 24
    (magic,) = image.unpack('<H', optional)
    if magic not in (0x10b, 0x20b):
        raise PEError('not a Windows program file (unknown optional header)')
    is_64 = magic == 0x20b
    table = optional + (112 if is_64 else 96)
    image.table = table
    directories = image.unpack('<' + 'II' * 3, table)   # export, import, resource
    if count > 96:
        raise PEError('too many sections')
    for index in range(count):
        _name, span, virtual, raw_size, raw = struct.unpack(
            '<8sIIII', image.read(optional + optional_size + 40 * index, 24))
        image.sections.append((virtual, max(span, raw_size), raw))
    return is_64 and machine == 0x8664, directories[0:2], directories[4:6]


def _exports(image, directory):
    rva, size = directory
    if not rva or not size:
        return frozenset()
    table = image.to_offset(rva)
    (count,), (names,) = image.unpack('<I', table + 24), image.unpack('<I', table + 32)
    if count > MAX_EXPORTS:
        raise PEError('the export table is absurdly large')
    base = image.to_offset(names)
    return frozenset(image.text(image.unpack('<I', base + 4 * index)[0]) for index in range(count))


def _version(image, directory):
    """The file version out of the RT_VERSION resource ('4.1.1.2740'), '' when there is none."""
    rva, size = directory
    if not rva or not size:
        return ''
    root = image.to_offset(rva)

    def entries(offset):
        named, ids = image.unpack('<HH', offset + 12)
        if named + ids > 512:
            raise PEError('too many resource entries')
        return [image.unpack('<II', offset + 16 + 8 * n) for n in range(named + ids)]

    def descend(offset, wanted=None):
        for name, target in entries(offset):
            if wanted is None or name == wanted:
                return root + (target & 0x7fffffff), bool(target & 0x80000000)
        return None, False

    here, folder = descend(root, RT_VERSION)
    for _ in range(2):   # name, then language
        if here is None or not folder:
            return ''
        here, folder = descend(here)
    if here is None or folder:
        return ''
    data_rva, length, _page, _reserved = image.unpack('<IIII', here)
    start = image.to_offset(data_rva)
    blob = image.read(start, min(length, 4096, image.size - start))
    at = blob.find(FIXED_FILE_INFO)
    if at < 0 or at + 16 > len(blob):
        return ''
    high, low = struct.unpack('<II', blob[at + 8:at + 16])
    return f'{high >> 16}.{high & 0xffff}.{low >> 16}.{low & 0xffff}'


def inspect(path):
    """Info(is_64bit, exports, version) of the DLL at `path`; PEError when it is not a readable PE file."""
    try:
        with open(path, 'rb') as stream:
            image = _Image(stream, os.fstat(stream.fileno()).st_size)
            is_64, export_dir, resource_dir = _headers(image)
            return Info(is_64, _exports(image, export_dir), _version(image, resource_dir))
    except (OSError, struct.error) as error:
        raise PEError(f'cannot read {path}: {error}')


MAX_IMPORTS = 2048
# (data directory index, descriptor size, offset of the DLL-name RVA in it): the import table and the delay-load one.
IMPORT_TABLES = ((1, 20, 12), (13, 32, 4))


def imports(path):
    """The DLL names (lower case) a program imports, from its import table and its delay-load table; PEError when it
    is not a readable PE file. Nothing is loaded or run. AMDLSS reads which graphics DLLs an Unreal .exe links."""
    try:
        with open(path, 'rb') as stream:
            image = _Image(stream, os.fstat(stream.fileno()).st_size)
            _headers(image)
            (count,) = image.unpack('<I', image.table - 4)
            names = set()
            for index, size, name_at in IMPORT_TABLES:
                if index >= count:
                    continue
                rva, _length = image.unpack('<II', image.table + 8 * index)
                if not rva:
                    continue
                start = image.to_offset(rva)
                for number in range(MAX_IMPORTS):
                    (name,) = image.unpack('<I', start + size * number + name_at)
                    if not name:
                        break
                    names.add(image.text(name).lower())
            return frozenset(names)
    except (OSError, struct.error) as error:
        raise PEError(f'cannot read {path}: {error}')


# The readable strings of a version resource: at least three printable ASCII characters, each followed by a NUL
# (UTF-16LE). Keys and values both come out; the values name the product.
STRING_RUN = re.compile(rb'(?:[\x20-\x7e]\x00){3,}')


def version_words(path):
    """The strings in the DLL's version resource (mostly its values: ProductName, FileDescription, InternalName,
    OriginalFilename), in file order; () when the file has none or is not a PE file. This is how an OptiScaler copy
    under another name is told from the system DLL it pretends to be; a copy that spoofs its version resource only
    shows itself in its bytes, which `amdlss.scaler` checks as well."""
    try:
        with open(path, 'rb') as stream:
            image = _Image(stream, os.fstat(stream.fileno()).st_size)
            _is_64, _exports, resource_dir = _headers(image)
            rva, size = resource_dir
            if not rva or not size:
                return ()
            start = image.to_offset(rva)
            blob = image.read(start, min(size, 1 << 16, image.size - start))
            return tuple(match.group().decode('utf-16-le', errors='replace') for match in STRING_RUN.finditer(blob))
    except (OSError, struct.error, PEError):
        return ()
