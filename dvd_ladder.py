# SPDX-License-Identifier: GPL-3.0-only
# Adapted from Neo-626/DiscColorStudio v0.8.5, 2026-11-13.
"""Optional band-ladder color recipes with an explicit, bounded fitting grid.

The upstream model and carrier/frame seam rules are preserved. A single PNG is
treated as a hub-centred layer; its viewing scene uses LadderViewing(0, 180) by
default. Recipe fitting and per-codeword sampling run in the bundled C library.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from dataclasses import dataclass
from functools import cache
import json
import math
import os
from pathlib import Path

import numpy as np

from dvd_kernel import _Parameters, _parameters


class _Vec(ctypes.Structure):
    _fields_ = [(name, ctypes.c_double) for name in ("x", "y", "z")]


class _Model(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ("responses", "slots")] + [
        (name, ctypes.c_double) for name in ("channel_bit_nm", "step", "exposure", "half_log")
    ] + [("white", _Vec)] + [(name, ctypes.c_void_p) for name in (
        "periods", "gains", "counts", "cycle", "rank", "bytes", "trim", "response", "off", "scenes")]


@cache
def _library():
    lib = ctypes.CDLL(str(Path(__file__).with_suffix(".dll")))
    lib.ladder_fit_rows.argtypes = [ctypes.POINTER(_Model), ctypes.c_void_p,
        ctypes.c_int64, ctypes.c_int64, ctypes.c_double, ctypes.c_double,
        ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_double, ctypes.c_void_p]
    lib.ladder_fit_point.argtypes = [ctypes.POINTER(_Model), ctypes.c_void_p,
        ctypes.c_double, ctypes.c_void_p, ctypes.c_double, ctypes.c_void_p]
    lib.ladder_resolve_sector.argtypes = [ctypes.POINTER(_Model), ctypes.c_void_p, ctypes.c_void_p]
    lib.ladder_generate.argtypes = [ctypes.POINTER(_Parameters), ctypes.POINTER(_Model),
        ctypes.c_void_p, ctypes.c_int64, ctypes.c_double, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p]
    for name in ("ladder_fit_rows", "ladder_fit_point", "ladder_resolve_sector", "ladder_generate"):
        getattr(lib, name).restype = None
    return lib


def _transform(value, spin=0.0, tilt=0.0):
    x, y, z = value
    angle, t = math.radians(spin), math.radians(tilt)
    y, z = math.cos(t)*y-math.sin(t)*z, math.sin(t)*y+math.cos(t)*z
    return np.array([math.cos(angle)*x-math.sin(angle)*y,
                     math.sin(angle)*x+math.cos(angle)*y, z])


@dataclass
class LadderModel:
    data: dict
    arrays: tuple
    native: _Model


@cache
def load_ladder_model(disc_rotation_deg=0.0, observer_azimuth_deg=180.0):
    with Path(__file__).with_name("ladder-model.json").open(encoding="utf-8") as stream:
        data = json.load(stream)
    carriers = data["carriers"]
    if data["version"] != "ladder-full-response-1" or len(carriers) != 30 or data["channelBitNm"] != 133.3:
        raise ValueError("Invalid ladder model.")
    counts = np.array([c["count"] for c in carriers], np.int64)
    slots = sorted(((r+.5)/c["count"], v, r) for v, c in enumerate(carriers) for r in range(c["count"]))
    periods = np.array([c["period"] for c in carriers], np.float64)
    gains = np.array([c["gain"] for c in carriers], np.float64)
    cycle = np.array([s[1] for s in slots], np.int64)
    rank = np.array([s[2] for s in slots], np.int64)
    carrier_bytes = np.array([list(bytes.fromhex(c["bytesHex"])) for c in carriers], np.uint8)
    trim = np.array([c["trim"] for c in carriers], np.uint8)
    response = np.array([c["response"] for c in carriers], np.float64)
    off = np.array(data["offResponse"], np.float64)
    if carrier_bytes.shape != (30, 91) or trim.shape != (30, 92) or response.shape != (30, len(off), 3):
        raise ValueError("Invalid ladder carrier dimensions.")
    left, right = np.array(data["eyeLeft"]), np.array(data["eyeRight"])
    middle = (left+right)*.5
    beta = math.radians(observer_azimuth_deg)
    # Upstream uses a 41 mm reference radius when a layer is hub-centred.
    world_eye = np.array([41*math.cos(beta), -41*math.sin(beta), middle[2]])
    world_left, world_right = world_eye+left-middle, world_eye+right-middle
    lamp = np.array(data["lamp"])
    scenes = [(_transform(world_eye, disc_rotation_deg), _transform(lamp, disc_rotation_deg))]
    scenes += [(_transform(eye, disc_rotation_deg, tilt), _transform(lamp, disc_rotation_deg, tilt))
               for tilt in (-.5, 0, .5) for eye in (world_left, world_right)]
    scenes = np.array(scenes, np.float64)
    arrays = (periods, gains, counts, cycle, rank, carrier_bytes, trim, response, off, scenes)
    for array in arrays:
        array.flags.writeable = False
    native = _Model(len(off), len(slots), data["channelBitNm"], data["uStep"], data["exposure"],
        .5*math.log(periods[0]/periods[1]), _Vec(*data["white"]), *(a.ctypes.data for a in arrays))
    return LadderModel(data, arrays, native)


@dataclass
class LadderProfile:
    model: LadderModel
    field: np.ndarray
    radius_mm: float
    disc_rotation_deg: float
    observer_azimuth_deg: float
    view_balance: float

    @property
    def grid(self):
        return self.field.shape[0]


def prepare_ladder(image, options, *, grid=1024, disc_rotation_deg=0.0,
                   observer_azimuth_deg=180.0, view_balance=1.0, workers=None):
    """Fit one immutable RGB image to a square, physical recipe grid.

    ``grid`` controls recipe resolution independently of the input PNG. The
    full-resolution source is sampled with floor-nearest, like upstream export.
    """
    model = load_ladder_model(disc_rotation_deg, observer_azimuth_deg)
    field = np.empty((grid, grid, 4), np.float32)
    radius = getattr(options, "sampling_outer_radius_mm", options.outer_radius_mm)
    workers = workers or min(8, os.cpu_count() or 1)
    def fit_rows(first, count):
        _library().ladder_fit_rows(ctypes.byref(model.native), image.ctypes.data,
            image.shape[1], image.shape[0], radius, options.inner_radius_mm,
            grid, first, count, view_balance, field.ctypes.data)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda start: fit_rows(start, min(16, grid-start)), range(0, grid, 16)))
    field.flags.writeable = False
    return LadderProfile(model, field, radius, disc_rotation_deg, observer_azimuth_deg, view_balance)


def generate_payload(image, options, geometry, start_block, block_count, centres, masks, profile):
    """Sample ladder codes, repair frame seams, then undo DVD scrambling."""
    parameters, table = _parameters(options, geometry, image.shape[1], image.shape[0], False, 0.0)
    output = np.empty(block_count*32768, np.uint8)
    _library().ladder_generate(ctypes.byref(parameters), ctypes.byref(profile.model.native),
        profile.field.ctypes.data, profile.grid, profile.radius_mm, centres.ctypes.data, masks.ctypes.data,
        start_block, block_count, output.ctypes.data)
    return output.tobytes()
