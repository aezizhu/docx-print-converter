"""Resource-bounded OOXML inspection shared by the compatibility passes."""
import struct
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAX_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_ZIP_ENTRIES = 4096

class InspectionLimit(ValueError):
    pass

def _bounded_archive(src):
    with open(src, 'rb') as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 65577))
        tail = fh.read(65577)
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or len(tail) - offset < 22:
        raise BadZipFile('Missing ZIP directory')
    _, disk, start_disk, disk_entries, entries, directory_size, directory_offset, comment = struct.unpack_from('<4s4H2LH', tail, offset)
    if offset + 22 + comment != len(tail):
        raise BadZipFile('Invalid ZIP directory')
    if (disk or start_disk or disk_entries != entries or entries > MAX_ZIP_ENTRIES
            or directory_size > MAX_DIRECTORY_BYTES or directory_offset == 0xffffffff
            or (offset >= 20 and tail[offset - 20:offset - 16] == b'PK\x06\x07')):
        raise InspectionLimit('ZIP metadata inspection limit')
    if directory_offset + directory_size > size - len(tail) + offset:
        raise BadZipFile('Invalid ZIP directory range')
    return ZipFile(src)

def _xml(raw):
    declarations = raw.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise ValueError('XML entity declarations are not supported')
    return ET.fromstring(raw)
