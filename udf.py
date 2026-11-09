"""Sequential UDF 2.01 filesystem planning for DVD picture images.

Ported from Disc Image Studio's UdfImageWriter.cs.
Copyright (c) 2026 Disc Image Studio contributors.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

from binascii import crc_hqx
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import struct
from typing import BinaryIO

SECTOR_BYTES = 2048
_COPY_BYTES = 128 * 1024
_ALIGNMENT = 32
_FIRST_PARTITION_LBA = 288
_MAX_EXTENT_BYTES = 0x3FFFF800
_IMPLEMENTATION_ID = "*DiscImageBurn"


def _sectors(length: int) -> int:
    return (length + SECTOR_BYTES - 1) // SECTOR_BYTES


def _u16(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", data, offset, value)


def _u32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value)


def _u64(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<Q", data, offset, value)


def _identifier(text: str) -> bytes:
    if any(ord(char) > 0xFFFF or 0xD800 <= ord(char) <= 0xDFFF
           or char in "\0\ufeff\ufffe" for char in text):
        raise ValueError("UDF names must use Unicode BMP characters without byte-order marks.")
    narrow = all(ord(char) <= 255 for char in text)
    result = bytes((8 if narrow else 16,)) + text.encode("latin-1" if narrow else "utf-16-be")
    if len(result) > 255:
        raise ValueError("UDF names exceed 254 single-byte or 127 two-byte characters.")
    return result


def _fit_dstring(text: str, field_bytes: int) -> str:
    # Validate the whole label before fitting it to the fixed descriptor field.
    if any(ord(char) > 0xFFFF or 0xD800 <= ord(char) <= 0xDFFF
           or char in "\0\ufeff\ufffe" for char in text):
        raise ValueError("UDF volume labels must use Unicode BMP characters without byte-order marks.")
    limit = (field_bytes - 2) // (1 if all(ord(char) <= 255 for char in text) else 2)
    return text[:limit]


def _dstring(data: bytearray, offset: int, size: int, text: str) -> None:
    if text:
        encoded = _identifier(text)
        if len(encoded) >= size:
            raise ValueError("UDF volume label exceeds its descriptor field.")
        data[offset:offset + len(encoded)] = encoded
        data[offset + size - 1] = len(encoded)


def _charspec(data: bytearray, offset: int) -> None:
    data[offset + 1:offset + 24] = b"OSTA Compressed Unicode"


def _entity(data: bytearray, offset: int, identifier: str, *, domain: bool = False,
            revision: bool = False) -> None:
    encoded = identifier.encode("ascii")
    data[offset + 1:offset + 1 + len(encoded)] = encoded
    if domain or revision:
        _u16(data, offset + 24, 0x0201)
    if domain:
        data[offset + 26] = 1


def _timestamp(data: bytearray, offset: int, timestamp: datetime) -> None:
    utc = timestamp.astimezone(timezone.utc)
    struct.pack_into("<HH8B", data, offset, 0x1000, utc.year, utc.month, utc.day,
                     utc.hour, utc.minute, utc.second, utc.microsecond // 10000,
                     utc.microsecond // 100 % 100, utc.microsecond % 100)


def _extent(data: bytearray, offset: int, length: int, lba: int) -> None:
    struct.pack_into("<II", data, offset, length, lba)


def _tag(data: bytearray, identifier: int, location: int, length: int) -> bytearray:
    struct.pack_into("<HHBBHHHI", data, 0, identifier, 3, 0, 0, 1,
                     crc_hqx(data[16:length], 0), length - 16, location)
    data[4] = sum(data[:16]) & 255
    return data


def _descriptor(identifier: int, location: int, length: int = 512) -> bytearray:
    return _tag(bytearray(SECTOR_BYTES), identifier, location, length)


def _fid_length(identifier_bytes: int, offset: int) -> int:
    length = (38 + identifier_bytes + 3) // 4 * 4
    # A write-once UDF directory's next descriptor tag must not cross a block.
    return length + 32 if (offset + length) % SECTOR_BYTES > SECTOR_BYTES - 16 else length


@dataclass
class _Node:
    path: Path
    directory: bool
    timestamp: datetime
    parent: _Node | None = field(default=None, repr=False)
    children: list[_Node] = field(default_factory=list, repr=False)
    identifier: bytes = b""
    unique_id: int = 0
    entry_lba: int = 0
    data_lba: int = 0
    data_bytes: int = 0


def _read_tree(source: Path) -> list[_Node]:
    def node(path: Path, parent: _Node | None, unique_id: int) -> _Node:
        info = path.stat()
        directory = stat.S_ISDIR(info.st_mode)
        return _Node(path, directory, datetime.fromtimestamp(info.st_mtime, timezone.utc),
                     parent, identifier=_identifier(path.name) if parent else b"",
                     unique_id=unique_id, data_bytes=0 if directory else info.st_size)

    nodes = [node(source, None, 0)]
    # Appending during traversal preserves the upstream breadth-first allocation order.
    for directory in nodes:
        if directory.directory:
            for path in sorted(directory.path.iterdir(), key=lambda item: item.name):
                child = node(path, directory, len(nodes) + 15)
                directory.children.append(child)
                nodes.append(child)
    return nodes


class _SequentialWriter:
    def __init__(self, stream: BinaryIO, start_lba: int = 0):
        self.stream = stream
        self.position = start_lba * SECTOR_BYTES

    def write(self, data: bytes | bytearray | memoryview) -> None:
        view = memoryview(data)
        while view:
            count = self.stream.write(view)
            if count is None:
                count = len(view)
            if count <= 0 or count > len(view):
                raise OSError("UDF output stream did not accept the requested bytes.")
            self.position += count
            view = view[count:]

    def pad(self, lba: int) -> None:
        remaining = lba * SECTOR_BYTES - self.position
        if remaining < 0:
            raise ValueError("Sequential UDF layout moved backwards.")
        zeros = bytes(_COPY_BYTES)
        while remaining:
            count = min(_COPY_BYTES, remaining)
            self.write(zeros[:count])
            remaining -= count

    def at(self, lba: int, data: bytes | bytearray) -> None:
        self.pad(lba)
        self.write(data)


@dataclass
class UdfLayout:
    source_directory: Path
    total_sectors: int
    volume_label: str
    timestamp: datetime
    partition_start_lba: int
    metadata_end_lba: int
    file_data_start_lba: int
    data_end_lba: int
    drawing_start_lba: int
    drawing_end_lba: int
    files_at_outer_ring: bool
    _nodes: list[_Node] = field(repr=False)

    @property
    def partition_sectors(self) -> int:
        return self.data_end_lba - self.partition_start_lba

    @property
    def file_count(self) -> int:
        return sum(not node.directory for node in self._nodes)

    @property
    def directory_count(self) -> int:
        return len(self._nodes) - self.file_count

    @property
    def file_bytes(self) -> int:
        return sum(node.data_bytes for node in self._nodes if not node.directory)

    def _primary(self, location: int) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _u32(data, 16, 1)
        _dstring(data, 24, 32, _fit_dstring(self.volume_label, 32))
        for offset, value in ((56, 1), (58, 1), (60, 2), (62, 3), (488, 1)):
            _u16(data, offset, value)
        _u32(data, 64, 1)
        _u32(data, 68, 1)
        _dstring(data, 72, 128, f"{int(self.timestamp.timestamp()) & 0xFFFFFFFF:08X}00000000")
        _charspec(data, 200)
        _charspec(data, 264)
        _entity(data, 344, _IMPLEMENTATION_ID)
        _timestamp(data, 376, self.timestamp)
        _entity(data, 388, _IMPLEMENTATION_ID)
        return _tag(data, 1, location, 512)

    def _implementation(self, location: int) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _u32(data, 16, 2)
        _entity(data, 20, "*UDF LV Info", revision=True)
        _charspec(data, 52)
        _dstring(data, 116, 128, self.volume_label)
        _entity(data, 352, _IMPLEMENTATION_ID)
        return _tag(data, 4, location, 512)

    def _partition(self, location: int) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _u32(data, 16, 3)
        _u16(data, 20, 1)
        _entity(data, 24, "+NSR03")
        _u32(data, 184, 1)
        _u32(data, 188, self.partition_start_lba)
        _u32(data, 192, self.partition_sectors)
        _entity(data, 196, _IMPLEMENTATION_ID)
        return _tag(data, 5, location, 512)

    def _logical(self, location: int) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _u32(data, 16, 4)
        _charspec(data, 20)
        _dstring(data, 84, 128, self.volume_label)
        _u32(data, 212, SECTOR_BYTES)
        _entity(data, 216, "*OSTA UDF Compliant", domain=True)
        _extent(data, 248, SECTOR_BYTES * 2, 0)
        _u32(data, 264, 6)
        _u32(data, 268, 1)
        _entity(data, 272, _IMPLEMENTATION_ID)
        _extent(data, 432, SECTOR_BYTES * 4, 48)
        data[440:442] = b"\x01\x06"
        _u16(data, 442, 1)
        return _tag(data, 6, location, 446)

    def _integrity(self) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _timestamp(data, 16, self.timestamp)
        _u32(data, 28, 1)
        _u64(data, 40, len(self._nodes) + 15)
        _u32(data, 72, 1)
        _u32(data, 76, 46)
        _u32(data, 84, self.partition_sectors)
        _entity(data, 88, _IMPLEMENTATION_ID)
        _u32(data, 120, self.file_count)
        _u32(data, 124, self.directory_count)
        for offset in (128, 130, 132):
            _u16(data, offset, 0x0201)
        return _tag(data, 9, 48, 134)

    def _anchor(self, location: int) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _extent(data, 16, 16 * SECTOR_BYTES, 32)
        _extent(data, 24, 16 * SECTOR_BYTES, self.total_sectors - 256)
        return _tag(data, 2, location, 512)

    def _file_set(self) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _timestamp(data, 16, self.timestamp)
        _u16(data, 28, 3)
        _u16(data, 30, 3)
        _u32(data, 32, 1)
        _u32(data, 36, 1)
        _charspec(data, 48)
        _dstring(data, 112, 128, self.volume_label)
        _charspec(data, 240)
        _dstring(data, 304, 32, _fit_dstring(self.volume_label, 32))
        _extent(data, 400, SECTOR_BYTES, self._nodes[0].entry_lba - self.partition_start_lba)
        _entity(data, 416, "*OSTA UDF Compliant", domain=True)
        return _tag(data, 256, 0, 512)

    def _file_entry(self, node: _Node) -> bytearray:
        data = bytearray(SECTOR_BYTES)
        _u16(data, 20, 4)
        _u16(data, 24, 1)
        data[27] = 4 if node.directory else 5
        _u32(data, 28, (node.parent or node).entry_lba - self.partition_start_lba)
        _u32(data, 36, 0xFFFFFFFF)
        _u32(data, 40, 0xFFFFFFFF)
        _u32(data, 44, 0x14A5 if node.directory else 0x1084)
        _u16(data, 48, 1 + sum(child.directory for child in node.children) if node.directory else 1)
        _u64(data, 56, node.data_bytes)
        _u64(data, 64, _sectors(node.data_bytes))
        for offset in (72, 84, 96):
            _timestamp(data, offset, node.timestamp)
        _u32(data, 108, 1)
        _entity(data, 128, _IMPLEMENTATION_ID)
        _u64(data, 160, node.unique_id)
        offset, remaining = 176, node.data_bytes
        lbn = node.data_lba - self.partition_start_lba
        while remaining:
            length = min(remaining, _MAX_EXTENT_BYTES)
            _extent(data, offset, length, lbn)
            remaining -= length
            lbn += _sectors(length)
            offset += 8
        _u32(data, 172, offset - 176)
        return _tag(data, 261, node.entry_lba - self.partition_start_lba, offset)

    def _file_identifier(self, target: _Node, identifier: bytes, parent: bool,
                         location: int, offset: int) -> bytearray:
        data = bytearray(_fid_length(len(identifier), offset))
        implementation_bytes = len(data) - (38 + len(identifier) + 3) // 4 * 4
        _u16(data, 16, 1)
        data[18] = 10 if parent else 2 if target.directory else 0
        data[19] = len(identifier)
        _extent(data, 20, SECTOR_BYTES, target.entry_lba - self.partition_start_lba)
        _u32(data, 32, target.unique_id)
        _u16(data, 36, implementation_bytes)
        if implementation_bytes:
            _entity(data, 38, _IMPLEMENTATION_ID)
        data[38 + implementation_bytes:38 + implementation_bytes + len(identifier)] = identifier
        return _tag(data, 257, location, len(data))

    def _write_descriptors(self, output: _SequentialWriter, start: int) -> None:
        for offset, make in enumerate((self._primary, self._implementation, self._partition, self._logical)):
            output.at(start + offset, make(start + offset))
        data = bytearray(SECTOR_BYTES)
        _u32(data, 16, 5)
        output.at(start + 4, _tag(data, 7, start + 4, 24))
        output.at(start + 5, _descriptor(8, start + 5))
        output.pad(start + 16)

    def _write_partition(self, output: _SequentialWriter) -> None:
        output.at(self.partition_start_lba, self._file_set())
        output.at(self.partition_start_lba + 1, _descriptor(8, 1))
        for node in self._nodes:
            output.at(node.entry_lba, self._file_entry(node))
        for directory in (node for node in self._nodes if node.directory):
            output.pad(directory.data_lba)
            offset = 0
            targets = [(directory.parent or directory, b"", True)]
            targets.extend((child, child.identifier, False) for child in directory.children)
            for target, identifier, parent in targets:
                location = directory.data_lba - self.partition_start_lba + offset // SECTOR_BYTES
                record = self._file_identifier(target, identifier, parent, location, offset)
                output.write(record)
                offset += len(record)
        for node in (node for node in self._nodes if not node.directory):
            output.pad(node.data_lba)
            with node.path.open("rb") as source:
                remaining = node.data_bytes
                while remaining:
                    chunk = source.read(min(_COPY_BYTES, remaining))
                    if not chunk:
                        raise OSError(f"Unexpected end of source file: {node.path}")
                    output.write(chunk)
                    remaining -= len(chunk)

    def write_prefix(self, stream: BinaryIO) -> None:
        output = _SequentialWriter(stream)
        for location, identifier in enumerate((b"BEA01", b"NSR03", b"TEA01"), 16):
            data = bytearray(SECTOR_BYTES)
            data[1:6] = identifier
            data[6] = 1
            output.at(location, data)
        self._write_descriptors(output, 32)
        output.at(48, self._integrity())
        output.at(49, _descriptor(8, 49))
        output.at(256, self._anchor(256))
        if not self.files_at_outer_ring:
            self._write_partition(output)
        output.pad(self.drawing_start_lba)

    def write_suffix(self, stream: BinaryIO) -> None:
        output = _SequentialWriter(stream, self.drawing_end_lba)
        if self.files_at_outer_ring:
            self._write_partition(output)
        output.at(self.total_sectors - 257, self._anchor(self.total_sectors - 257))
        self._write_descriptors(output, self.total_sectors - 256)
        output.at(self.total_sectors - 1, self._anchor(self.total_sectors - 1))
        output.pad(self.total_sectors)


def plan_directory(source_directory: str | Path, total_sectors: int, files_position: str = "inner",
                   volume_label: str = "DVD_IMAGE", *, timestamp: datetime | None = None) -> UdfLayout:
    """Reserve UDF metadata, a physical file partition and an aligned DVD drawing range."""
    if total_sectors < 640 or total_sectors % 16:
        raise ValueError("UDF drawing volumes need at least 640 sectors and a multiple of 16 sectors.")
    source = Path(os.path.abspath(source_directory))
    nodes = _read_tree(source)
    cursor = _FIRST_PARTITION_LBA + 2
    for node in nodes:
        node.entry_lba = cursor
        cursor += 1
        if node.directory:
            node.data_bytes = _fid_length(0, 0)
            for child in node.children:
                node.data_bytes += _fid_length(len(child.identifier), node.data_bytes)
        if (node.data_bytes + _MAX_EXTENT_BYTES - 1) // _MAX_EXTENT_BYTES > (SECTOR_BYTES - 176) // 8:
            raise ValueError(f"Too many allocation extents in UDF file: {node.path}")
    for node in (node for node in nodes if node.directory):
        node.data_lba = cursor
        cursor += _sectors(node.data_bytes)
    file_start = cursor
    for node in (node for node in nodes if not node.directory):
        node.data_lba = cursor
        cursor += _sectors(node.data_bytes)
    drawing_start = (cursor + _ALIGNMENT - 1) // _ALIGNMENT * _ALIGNMENT
    drawing_end = (total_sectors - 257) // _ALIGNMENT * _ALIGNMENT
    if drawing_start >= drawing_end:
        raise ValueError("Files and UDF metadata leave no complete drawing region.")
    partition_start = _FIRST_PARTITION_LBA
    outer = files_position == "outer"
    if outer:
        partition_start = (drawing_end - (cursor - _FIRST_PARTITION_LBA)) // _ALIGNMENT * _ALIGNMENT
        shift = partition_start - _FIRST_PARTITION_LBA
        for node in nodes:
            node.entry_lba += shift
            node.data_lba += shift
        cursor += shift
        file_start += shift
        drawing_start, drawing_end = _FIRST_PARTITION_LBA, partition_start
    label = _fit_dstring(volume_label if volume_label.strip() else "DVD_IMAGE", 128)
    return UdfLayout(source, total_sectors, label, timestamp or datetime.now(timezone.utc),
                     partition_start, file_start, file_start, cursor, drawing_start, drawing_end, outer, nodes)
