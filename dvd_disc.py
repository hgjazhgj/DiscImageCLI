# SPDX-License-Identifier: GPL-3.0-or-later
"""DVD format tables, physical configuration and shared output layout planning."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np

from dvd_geometry import DvdGeometry

SECTOR_BYTES = 2048
SECTORS_PER_BLOCK = 16
BLOCK_BYTES = SECTOR_BYTES * SECTORS_PER_BLOCK
CHANNEL_BITS_PER_SECTOR = 38_688


@dataclass(frozen=True)
class DvdOptions:
    total_sectors: int
    inner_radius_mm: float = 24.0
    outer_radius_mm: float = 58.0
    channel_bit_nm: float = 133.3
    start_angle_deg: float = 0.0
    direction: str = "cw"
    pitch_linear: float = 0.0
    pitch_quadratic: float = 0.0
    pitch_cubic: float = 0.0
    psn_offset: int = 0x30000


@cache
def scrambler_masks() -> np.ndarray:
    """Payload masks indexed by (PSN >> 4) & 15."""
    presets = (0x0001, 0x5500, 0x0002, 0x2A00, 0x0004, 0x5400, 0x0008, 0x2800,
               0x0010, 0x5000, 0x0020, 0x2001, 0x0040, 0x4002, 0x0080, 0x0005)
    masks = np.empty((16, SECTOR_BYTES), dtype=np.uint8)
    for preset_index, state in enumerate(presets):
        for byte_index in range(SECTOR_BYTES):
            masks[preset_index, byte_index] = state & 0xFF
            for _ in range(8):
                feedback = ((state >> 14) ^ (state >> 10)) & 1
                state = ((state << 1) | feedback) & 0x7FFF
    masks.flags.writeable = False
    return masks


@dataclass(frozen=True)
class OutputPlan:
    geometry: DvdGeometry
    output_sectors: int
    bleed: float
    file_position: str
    udf: object | None = None

    @property
    def drawing_start_lba(self):
        return self.udf.drawing_start_lba if self.udf else 0

    @property
    def drawing_end_lba(self):
        return self.udf.drawing_end_lba if self.udf else self.output_sectors

    def write_prefix(self, stream):
        if self.udf:
            self.udf.write_prefix(stream)

    def write_suffix(self, stream):
        if self.udf:
            self.udf.write_suffix(stream)


def plan_output(options: DvdOptions, *, data_dir=None, file_position="inner",
                volume_label="DVD_IMAGE", bleed=1.0) -> OutputPlan:
    """Plan the same physical drawing interval for ISO generation and PNG masks."""
    output_sectors = int(options.total_sectors * bleed) // SECTORS_PER_BLOCK * SECTORS_PER_BLOCK
    file_position = file_position if data_dir is not None else "inner"
    layout = None
    if data_dir is not None:
        from udf import plan_directory
        layout = plan_directory(Path(data_dir).absolute(), output_sectors, file_position, volume_label)
    track_length = options.total_sectors * float(CHANNEL_BITS_PER_SECTOR) * (options.channel_bit_nm * 1e-6)
    geometry = DvdGeometry(options.inner_radius_mm, options.outer_radius_mm, track_length,
                           options.pitch_linear, options.pitch_quadratic, options.pitch_cubic)
    return OutputPlan(geometry, output_sectors, bleed, file_position, layout)
