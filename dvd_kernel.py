# SPDX-License-Identifier: GPL-3.0-or-later
"""Fused native DVD sampling; the bundled library needs no build tools at runtime."""

from __future__ import annotations

import ctypes
from functools import cache
import math
from pathlib import Path

import numpy as np


class _Parameters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int64)
        for name in ("segment_count", "constant_pitch", "width", "height", "psn_offset", "binary_image")
    ] + [
        (name, ctypes.c_double)
        for name in ("inner_radius", "outer_radius", "track_length", "length_scale",
                     "total_angle", "growth", "inner_squared", "growth_squared",
                     "twice_growth", "inner_primitive", "start_angle", "direction",
                     "scale", "centre_x", "centre_y", "bit_length", "dither_strength")
    ] + [("segments", ctypes.c_void_p)]


class _ColorParameters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ("node_count", "dither_seed")] + [
        (name, ctypes.c_double) for name in ("model_start_deg", "model_end_deg", "rotation_deg")
    ] + [(name, ctypes.c_void_p) for name in ("lut_entries", "motif_bytes", "motif_offsets", "motif_lengths")]


@cache
def _library():
    library = ctypes.CDLL(str(Path(__file__).with_suffix(".dll")))
    library.dvd_generate.argtypes = [ctypes.POINTER(_Parameters), ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int64,
                                     ctypes.c_int64, ctypes.c_void_p]
    library.dvd_generate.restype = None
    library.dvd_generate_color.argtypes = [ctypes.POINTER(_Parameters), ctypes.POINTER(_ColorParameters),
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p]
    library.dvd_generate_color.restype = None
    return library


@cache
def _parameters(options, geometry, width, height, binary_image, dither_strength):
    names = ("radius_start", "width", "arc_start", "angle_start", "arc_delta",
             "angle_delta", "arc_derivative_start", "arc_derivative_end",
             "angle_derivative_start", "angle_derivative_end")
    table = (np.column_stack([geometry._table[name] for name in names])
             if not geometry.is_constant_pitch else np.empty((0, 10), dtype=np.float64))
    parameters = _Parameters(
        len(table), geometry.is_constant_pitch, width, height, options.psn_offset, binary_image,
        geometry.inner_radius_mm, geometry.outer_radius_mm, geometry.track_length_mm,
        getattr(geometry, "_length_scale", 1.0), geometry.total_angle_radians,
        geometry.radial_growth_per_radian_mm,
        getattr(geometry, "_constant_inner_squared", 0.0),
        getattr(geometry, "_constant_growth_squared", 0.0),
        getattr(geometry, "_constant_twice_growth", 0.0),
        getattr(geometry, "_constant_inner_primitive", 0.0),
        options.start_angle_deg * math.pi / 180.0,
        -1.0 if options.direction == "cw" else 1.0,
        (min(width, height) - 1) / (2.0 * options.outer_radius_mm),
        (width - 1) / 2.0, (height - 1) / 2.0, options.channel_bit_nm * 1e-6, dither_strength,
        table.ctypes.data,
    )
    return parameters, table


def generate_payload(image, options, geometry, start_block, block_count, centres, masks, binary_image, dither_strength):
    parameters, table = _parameters(options, geometry, image.shape[1], image.shape[0], binary_image, dither_strength)
    output = np.empty(block_count * 32768, dtype=np.uint8)
    _library().dvd_generate(ctypes.byref(parameters), image.ctypes.data, centres.ctypes.data,
                            masks.ctypes.data, start_block, block_count, output.ctypes.data)
    return output.tobytes()


def generate_color_payload(image, options, geometry, start_block, block_count, centres, masks, profile, rotation):
    parameters, table = _parameters(options, geometry, image.shape[1], image.shape[0], False, 0.0)
    color = _ColorParameters(profile.node_count, profile.dither_seed,
                             profile.model_start_deg, profile.model_end_deg, rotation,
                             profile.lut.ctypes.data, profile.motifs.ctypes.data,
                             profile.motif_offsets.ctypes.data, profile.motif_lengths.ctypes.data)
    output = np.empty(block_count * 32768, dtype=np.uint8)
    _library().dvd_generate_color(ctypes.byref(parameters), ctypes.byref(color), image.ctypes.data,
                                  centres.ctypes.data, masks.ctypes.data, start_block, block_count,
                                  output.ctypes.data)
    return output.tobytes()
