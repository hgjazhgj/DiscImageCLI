# SPDX-License-Identifier: GPL-3.0-or-later
"""ctypes interface to the DVD kernel; image and carrier planning live separately."""

from __future__ import annotations

import ctypes
import math
from pathlib import Path

import numpy as np

from dvd_disc import BLOCK_BYTES, SECTOR_BYTES, scrambler_masks


class _Parameters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int64)
        for name in ("segment_count", "constant_pitch", "width", "height", "psn_offset")
    ] + [
        (name, ctypes.c_double)
        for name in ("inner_radius", "outer_radius", "track_length", "length_scale",
                     "total_angle", "growth", "inner_squared", "growth_squared",
                     "twice_growth", "inner_primitive", "start_angle", "direction",
                     "scale", "centre_x", "centre_y", "bit_length")
    ] + [("segments", ctypes.c_void_p)]


class _CarrierModel(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        "carriers", "slots", "single_byte_carriers", "variant_width", "variant_height")] + [
        (name, ctypes.c_void_p) for name in (
            "bytes", "trim", "counts", "cycle", "rank", "levels", "sampling_modes",
            "variant_offsets", "variant_map")]


_LIBRARY = None
_CENTRES = None


def _library():
    global _LIBRARY
    if _LIBRARY is None:
        library = ctypes.CDLL(str(Path(__file__).with_suffix(".dll")))
        library.dvd_generate.argtypes = [ctypes.POINTER(_Parameters), ctypes.POINTER(_CarrierModel),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p]
        library.dvd_resolve_sector.argtypes = [ctypes.POINTER(_CarrierModel), ctypes.c_void_p, ctypes.c_void_p]
        for name in ("dvd_generate", "dvd_resolve_sector"):
            getattr(library, name).restype = None
        _LIBRARY = library
    return _LIBRARY


class PreparedKernel:
    """Own native inputs once and generate independent batches of ECC blocks."""

    def __init__(self, options, geometry, profile):
        global _CENTRES
        library = _library()
        if _CENTRES is None:
            # Payload codeword centres, including sector headers, ECC and SYNC.
            i = np.arange(BLOCK_BYTES, dtype=np.int64)
            frame_byte = 12 + i % SECTOR_BYTES
            ecc_row = (i // SECTOR_BYTES) * 12 + frame_byte // 172
            recording_row = ecc_row + ecc_row // 12
            recording_byte = recording_row * 182 + frame_byte % 172
            _CENTRES = (recording_byte // 91) * 1488 + 32 + (recording_byte % 91) * 16 + 8
            _CENTRES.flags.writeable = False
        self._profile = profile
        # Keep the geometry table and all profile arrays alive behind C pointers.
        names = ("radius_start", "width", "arc_start", "angle_start", "arc_delta",
                 "angle_delta", "arc_derivative_start", "arc_derivative_end",
                 "angle_derivative_start", "angle_derivative_end")
        self._table = (np.column_stack([geometry._table[name] for name in names])
                       if not geometry.is_constant_pitch else np.empty((0, 10), dtype=np.float64))
        self._parameters = _Parameters(
            len(self._table), geometry.is_constant_pitch, 8192, 8192, options.psn_offset,
            geometry.inner_radius_mm, geometry.outer_radius_mm, geometry.track_length_mm,
            getattr(geometry, "_length_scale", 1.0), geometry.total_angle_radians,
            geometry.radial_growth_per_radian_mm,
            getattr(geometry, "_constant_inner_squared", 0.0),
            getattr(geometry, "_constant_growth_squared", 0.0),
            getattr(geometry, "_constant_twice_growth", 0.0),
            getattr(geometry, "_constant_inner_primitive", 0.0),
            options.start_angle_deg * math.pi / 180.0,
            -1.0 if options.direction == "cw" else 1.0,
            8191 / (2.0 * options.outer_radius_mm), 4095.5, 4095.5,
            options.channel_bit_nm * 1e-6, self._table.ctypes.data,
        )
        self._model = _CarrierModel(
            profile.field.shape[0], len(profile.cycle),
            profile.single_byte_carriers, profile.variant_map.shape[2], profile.variant_map.shape[1],
            *(array.ctypes.data for array in (
                profile.carrier_bytes, profile.trim, profile.counts, profile.cycle, profile.rank,
                profile.levels, profile.sampling_modes, profile.variant_offsets, profile.variant_map)),
        )
        self._model_pointer = ctypes.byref(self._model)
        self._centres = _CENTRES
        self._masks = scrambler_masks()
        self._generate = library.dvd_generate
        self._resolve = library.dvd_resolve_sector
        self._generate_arguments = (
            ctypes.byref(self._parameters), self._model_pointer, profile.field.ctypes.data,
            self._centres.ctypes.data, self._masks.ctypes.data,
        )

    def generate_payload(self, start_block, block_count):
        """Generate complete ECC blocks using this session's retained inputs."""
        output = np.empty(block_count * BLOCK_BYTES, dtype=np.uint8)
        self._generate(*self._generate_arguments, start_block, block_count, output.ctypes.data)
        return output.tobytes()

    def resolve_sector(self, codes, variants=None):
        """Resolve carrier indices and optional local variants; 0xA5 is filler."""
        planes = np.asarray(codes, dtype=np.uint16)
        values = planes << 8
        if variants is not None:
            values |= np.asarray(variants, dtype=np.uint16)
        values = np.ascontiguousarray(np.where(planes == 0xA5, 0xFFFF, values), dtype=np.uint16)
        output = np.empty(SECTOR_BYTES, dtype=np.uint8)
        self._resolve(self._model_pointer, values.ctypes.data, output.ctypes.data)
        return output
