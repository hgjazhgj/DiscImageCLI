"""Sequential ISO9660/Joliet filesystem planning for DVD picture images.

Ported from Disc Image Studio's Iso9660ImageWriter.cs.
Copyright (c) 2026 Disc Image Studio contributors.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import stat
import struct
from typing import BinaryIO
import unicodedata

SECTOR_BYTES = 2048
_COPY_BYTES = 128 * 1024


def _sectors(length: int) -> int:
    return (length + SECTOR_BYTES - 1) // SECTOR_BYTES


@dataclass
class _Node:
    path: Path
    name: str
    directory: bool
    timestamp: datetime
    parent: _Node | None = None
    children: list[_Node] = field(default_factory=list)
    primary: str = ""
    joliet: str = ""
    number: int = 0
    primary_lba: int = 0
    joliet_lba: int = 0
    primary_bytes: int = 0
    joliet_bytes: int = 0
    data_lba: int = 0
    data_bytes: int = 0

    def identifier(self, joliet: bool) -> bytes:
        return self.joliet.encode("utf-16-be") if joliet else self.primary.encode("ascii")


def _read_tree(source: Path) -> tuple[_Node, list[_Node], list[_Node]]:
    def node(path: Path, parent: _Node | None) -> _Node:
        info = path.stat()
        directory = stat.S_ISDIR(info.st_mode)
        return _Node(path, path.name if parent else "", directory,
                     datetime.fromtimestamp(info.st_mtime, timezone.utc), parent,
                     data_bytes=0 if directory else info.st_size)

    root = node(source, None)
    pending = deque([root])
    directories: list[_Node] = []
    while pending:
        directory = pending.popleft()
        directory.number = len(directories) + 1
        directories.append(directory)
        with os.scandir(directory.path) as entries:
            names = sorted((entry.name for entry in entries), key=lambda name: (name.casefold(), name))
        for name in names:
            path = directory.path / name
            child = node(path, directory)
            directory.children.append(child)
            if child.directory:
                pending.append(child)

    # Match the source writer's depth-first file placement independently of BFS
    # directory numbering. An explicit stack also supports deeply nested trees.
    files: list[_Node] = []
    stack = list(reversed(root.children))
    while stack:
        child = stack.pop()
        if child.directory:
            stack.extend(reversed(child.children))
        else:
            files.append(child)
    return root, directories, files


def _primary_name(value: str, maximum: int, fallback: str) -> str:
    result = "".join(char if char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_." else "_"
                     for char in unicodedata.normalize("NFKC", value).upper())[:maximum].strip(".")
    return result or fallback


def _joliet_name(value: str, maximum: int, fallback: str) -> str:
    # Joliet uses UCS-2, so supplementary characters become two replacements,
    # matching the upstream writer's iteration over UTF-16 surrogate code units.
    result = "".join("__" if ord(char) > 0xFFFF else
                     "_" if char in "\0/\\:;" or 0xD800 <= ord(char) <= 0xDFFF else char
                     for char in unicodedata.normalize("NFC", value))[:maximum]
    return result or fallback


def _unique(base: str, used: set[str], suffix: str, maximum: int) -> str:
    candidate = base[:maximum - len(suffix)] + suffix
    index = 2
    while candidate.casefold() in used:
        discriminator = f"~{index}"
        candidate = base[:maximum - len(suffix) - len(discriminator)] + discriminator + suffix
        index += 1
    used.add(candidate.casefold())
    return candidate


def _assign_identifiers(directories: list[_Node]) -> None:
    for directory in directories:
        primary_used: set[str] = set()
        joliet_used: set[str] = set()
        for child in directory.children:
            suffix = "" if child.directory else ";1"
            child.primary = _unique(_primary_name(child.name, 31 - len(suffix),
                                    "DIRECTORY" if child.directory else "FILE"), primary_used, suffix, 31)
            child.joliet = _unique(_joliet_name(child.name, 60 - len(suffix),
                                   "Directory" if child.directory else "File"), joliet_used, suffix, 60)


def _record_length(identifier_length: int) -> int:
    return 33 + identifier_length + (1 if identifier_length % 2 == 0 else 0)


def _directory_size(node: _Node, joliet: bool) -> int:
    offset = 0
    lengths = [34, 34, *(_record_length(len(child.identifier(joliet))) for child in node.children)]
    for length in lengths:
        if offset % SECTOR_BYTES + length > SECTOR_BYTES:
            offset = _sectors(offset) * SECTOR_BYTES
        offset += length
    return _sectors(offset) * SECTOR_BYTES


def _both16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _record(lba: int, length: int, directory: bool, identifier: bytes, timestamp: datetime) -> bytes:
    result = bytearray(_record_length(len(identifier)))
    result[0] = len(result)
    result[2:10] = _both32(lba)
    result[10:18] = _both32(length)
    result[18:25] = bytes((max(1900, min(2155, timestamp.year)) - 1900, timestamp.month,
                           timestamp.day, timestamp.hour, timestamp.minute, timestamp.second, 0))
    result[25] = 2 if directory else 0
    result[28:32] = _both16(1)
    result[32] = len(identifier)
    result[33:33 + len(identifier)] = identifier
    return bytes(result)


def _extent(node: _Node, joliet: bool) -> bytes:
    parent = node.parent or node
    def location(item: _Node) -> tuple[int, int]:
        if not item.directory:
            return item.data_lba, item.data_bytes
        return (item.joliet_lba, item.joliet_bytes) if joliet else (item.primary_lba, item.primary_bytes)

    result = bytearray(location(node)[1])
    records = [_record(*location(node), True, b"\0", node.timestamp),
               _record(*location(parent), True, b"\1", parent.timestamp)]
    records.extend(_record(*location(child), child.directory, child.identifier(joliet), child.timestamp)
                   for child in node.children)
    offset = 0
    for record in records:
        if offset % SECTOR_BYTES + len(record) > SECTOR_BYTES:
            offset = _sectors(offset) * SECTOR_BYTES
        result[offset:offset + len(record)] = record
        offset += len(record)
    return bytes(result)


class _SequentialWriter:
    def __init__(self, stream: BinaryIO, start_lba: int = 0):
        self.stream = stream
        self.position = start_lba * SECTOR_BYTES

    def write(self, data: bytes | memoryview) -> None:
        view = memoryview(data)
        while view:
            count = self.stream.write(view)
            if count is None:
                count = len(view)
            if count <= 0 or count > len(view):
                raise OSError("ISO output stream did not accept the requested bytes.")
            self.position += count
            view = view[count:]

    def pad(self, lba: int) -> None:
        remaining = lba * SECTOR_BYTES - self.position
        zeros = bytes(_COPY_BYTES)
        while remaining > 0:
            count = min(_COPY_BYTES, remaining)
            self.write(zeros[:count])
            remaining -= count

    def at(self, lba: int, data: bytes) -> None:
        self.pad(lba)
        self.write(data)


@dataclass
class IsoLayout:
    source_directory: Path
    total_sectors: int
    metadata_end_lba: int
    file_data_start_lba: int
    data_end_lba: int
    drawing_start_lba: int
    drawing_end_lba: int
    file_count: int
    directory_count: int
    file_bytes: int
    files_at_outer_ring: bool
    _root: _Node = field(repr=False)
    _directories: list[_Node] = field(repr=False)
    _files: list[_Node] = field(repr=False)
    _path_locations: list[int] = field(repr=False)
    _path_lengths: list[int] = field(repr=False)
    _primary_label: str = field(repr=False)
    _joliet_label: str = field(repr=False)
    _timestamp: datetime = field(repr=False)

    def _path_table(self, joliet: bool, little: bool) -> bytes:
        result = bytearray()
        order = "<" if little else ">"
        for node in self._directories:
            name = node.identifier(joliet) if node.parent else b"\0"
            lba = node.joliet_lba if joliet else node.primary_lba
            result.extend(bytes((len(name), 0)) + struct.pack(order + "IH", lba,
                          node.parent.number if node.parent else 1) + name)
            if len(name) % 2:
                result.append(0)
        return bytes(result)

    def _descriptor(self, joliet: bool) -> bytes:
        result = bytearray(SECTOR_BYTES)
        result[0] = 2 if joliet else 1
        result[1:7] = b"CD001\x01"
        encoding = "utf-16-be" if joliet else "ascii"
        label = self._joliet_label if joliet else self._primary_label
        result[8:40] = "DVDIMAGE".encode(encoding).ljust(32, b" ") if not joliet else ("DVDIMAGE".ljust(16)).encode(encoding)
        result[40:72] = label.ljust(16 if joliet else 32).encode(encoding)
        if joliet:
            result[88:91] = b"%/E"
        result[80:88] = _both32(self.total_sectors)
        result[120:124] = _both16(1)
        result[124:128] = _both16(1)
        result[128:132] = _both16(SECTOR_BYTES)
        index = 2 if joliet else 0
        result[132:140] = _both32(self._path_lengths[index])
        result[140:144] = struct.pack("<I", self._path_locations[index])
        result[148:152] = struct.pack(">I", self._path_locations[index + 1])
        root_lba = self._root.joliet_lba if joliet else self._root.primary_lba
        root_length = self._root.joliet_bytes if joliet else self._root.primary_bytes
        result[156:190] = _record(root_lba, root_length, True, b"\0", self._timestamp)
        date = self._timestamp.strftime("%Y%m%d%H%M%S").encode("ascii") + f"{self._timestamp.microsecond // 10000:02}".encode("ascii") + b"\0"
        result[813:830] = date
        result[830:847] = date
        result[847:864] = b"0" * 16 + b"\0"
        result[864:881] = b"0" * 16 + b"\0"
        result[881] = 1
        return bytes(result)

    def _write_metadata(self, output: _SequentialWriter) -> None:
        output.at(16, self._descriptor(False))
        output.at(17, self._descriptor(True))
        output.at(18, b"\xffCD001\x01" + bytes(SECTOR_BYTES - 7))
        for index, (joliet, little) in enumerate(((False, True), (False, False), (True, True), (True, False))):
            output.at(self._path_locations[index], self._path_table(joliet, little))
        for joliet in (False, True):
            for node in self._directories:
                output.at(node.joliet_lba if joliet else node.primary_lba, _extent(node, joliet))

    def _write_files(self, output: _SequentialWriter) -> None:
        for node in self._files:
            if not node.data_bytes:
                continue
            output.pad(node.data_lba)
            with node.path.open("rb") as source:
                shutil.copyfileobj(source, output.stream, _COPY_BYTES)
            output.position += node.data_bytes

    def write_prefix(self, stream: BinaryIO, end_lba: int | None = None) -> None:
        """Write front metadata, inner files if selected, and drawing-start padding."""
        boundary = self.drawing_start_lba if end_lba is None else end_lba
        output = _SequentialWriter(stream)
        self._write_metadata(output)
        if not self.files_at_outer_ring:
            self._write_files(output)
        output.pad(boundary)

    def write_suffix(self, stream: BinaryIO, start_lba: int | None = None) -> None:
        """Append outer files and tail padding; inner mode needs no suffix."""
        boundary = self.drawing_end_lba if start_lba is None else start_lba
        if self.files_at_outer_ring:
            output = _SequentialWriter(stream, boundary)
            self._write_files(output)
            output.pad(self.total_sectors)


def plan_directory(source_directory: Path, total_sectors: int, files_position: str = "inner",
                   volume_label: str = "DVD_IMAGE") -> IsoLayout:
    """Plan filesystem extents and the 16-sector-aligned drawing interval."""
    source = Path(os.path.abspath(source_directory))
    root, directories, files = _read_tree(source)
    _assign_identifiers(directories)
    cursor = 19
    path_locations: list[int] = []
    path_lengths: list[int] = []
    for joliet in (False, True):
        length = sum(8 + (len(node.identifier(joliet)) if node.parent else 1)
                     + ((len(node.identifier(joliet)) if node.parent else 1) % 2)
                     for node in directories)
        for _ in range(2):
            path_locations.append(cursor)
            path_lengths.append(length)
            cursor += _sectors(length)
    for joliet in (False, True):
        for node in directories:
            length = _directory_size(node, joliet)
            if joliet:
                node.joliet_lba, node.joliet_bytes = cursor, length
            else:
                node.primary_lba, node.primary_bytes = cursor, length
            cursor += _sectors(length)
    metadata_end = cursor
    file_sectors = sum(_sectors(node.data_bytes) for node in files)
    outer = files_position == "outer"
    if outer:
        cursor = total_sectors - file_sectors
    file_start = cursor
    for node in files:
        node.data_lba = 0 if outer and not node.data_bytes else cursor
        cursor += _sectors(node.data_bytes)
    drawing_start = ((metadata_end if outer else cursor) + 15) // 16 * 16
    drawing_end = file_start // 16 * 16 if outer else total_sectors
    return IsoLayout(source, total_sectors, metadata_end, file_start, cursor,
                       drawing_start, drawing_end, len(files), len(directories),
                       sum(node.data_bytes for node in files), outer,
                       root, directories, files, path_locations, path_lengths,
                       _primary_name(volume_label, 32, "DVD_IMAGE"),
                       _joliet_name(volume_label, 16, "DVD_IMAGE"), datetime.now(timezone.utc))
