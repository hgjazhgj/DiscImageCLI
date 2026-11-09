# SPDX-License-Identifier: GPL-3.0-only
# Continuous color mapping adapted from Neo-626/DiscColorStudio, 2026-11-09.
"""Bundled color recipes and automatic viewing rotation for one RGB disc image.

Only Disc Color Studio's default bank is used: full banding reduction and
adaptive mixing, with DIS dark substitution off. Physical DVD geometry stays
in the existing disc configuration; this module adds no user configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
import gzip
import json
import math
from pathlib import Path
import struct

import numpy as np

from dvd_geometry import DvdGeometry


_ENTRY = np.dtype([("ids", "<u2", (16,)), ("weights", "<f4", (16,))])
_F = np.float32


def _oklab(rgb):
    r, g, b = np.moveaxis(np.asarray(rgb, dtype=np.float32), -1, 0)
    l = np.cbrt(_F(.4122214708) * r + _F(.5363325363) * g + _F(.0514459929) * b)
    m = np.cbrt(_F(.2119034982) * r + _F(.6806995451) * g + _F(.1073969566) * b)
    s = np.cbrt(_F(.0883024619) * r + _F(.2817188376) * g + _F(.6299787005) * b)
    return np.stack((
        _F(.2104542553) * l + _F(.793617785) * m - _F(.0040720468) * s,
        _F(1.9779984951) * l - _F(2.428592205) * m + _F(.4505937099) * s,
        _F(.0259040371) * l + _F(.7827717662) * m - _F(.808675766) * s,
    ), axis=-1)


def _from_oklab(lab):
    x, y, z = np.moveaxis(lab, -1, 0)
    l = x + _F(.3963377774) * y + _F(.2158037573) * z
    m = x - _F(.1055613458) * y - _F(.0638541728) * z
    s = x - _F(.0894841775) * y - _F(1.291485548) * z
    l, m, s = l * l * l, m * m * m, s * s * s
    return np.stack((
        _F(4.0767416621) * l - _F(3.3077115913) * m + _F(.2309699292) * s,
        -_F(1.2684380046) * l + _F(2.6097574011) * m - _F(.3413193965) * s,
        -_F(.0041960863) * l - _F(.7034186147) * m + _F(1.707614701) * s,
    ), axis=-1)


def _tetra(rgb):
    coords = np.asarray(rgb, dtype=np.float32) * _F(16) / _F(255)
    base = np.minimum(coords.astype(np.int32), 15)
    fractions = coords - base.astype(np.float32)
    order = np.argsort(-fractions, axis=1, kind="stable")
    fractions = np.take_along_axis(fractions, order, axis=1)
    strides = np.array([289, 17, 1])[order]
    first = base[:, 0] * 289 + base[:, 1] * 17 + base[:, 2]
    indices = np.column_stack((first, first + strides[:, 0],
                               first + strides[:, 0] + strides[:, 1],
                               first + strides.sum(axis=1)))
    p, q, t = fractions.T
    return indices, np.column_stack((_F(1) - p, p - q, q - t, t))


@dataclass
class ColorProfile:
    lut: np.ndarray
    motifs: np.ndarray
    motif_offsets: np.ndarray
    motif_lengths: np.ndarray
    model_start_deg: float
    model_end_deg: float
    node_count: int
    dither_seed: int
    _data: dict = field(repr=False)
    _frames: dict = field(default_factory=dict, repr=False)

    def _responses_at(self, angle):
        values = np.full((len(self.motif_lengths), 3), np.nan, dtype=np.float32)
        for i, texture in enumerate(self._data["textures"]):
            samples = texture["samples"]
            if len(samples) < 2 or angle < samples[0]["angle"] or angle > samples[-1]["angle"]:
                continue
            lo, hi = 0, len(samples) - 1
            while hi - lo > 1:
                middle = (lo + hi) // 2
                if samples[middle]["angle"] <= angle:
                    lo = middle
                else:
                    hi = middle
            left, right = samples[lo], samples[hi]
            if abs(angle - left["angle"]) < 1e-9:
                values[i] = left["linear"]
            elif abs(angle - right["angle"]) < 1e-9:
                values[i] = right["linear"]
            elif right["angle"] - left["angle"] <= self._data["maxResponseGapDeg"]:
                f = _F((angle - left["angle"]) / (right["angle"] - left["angle"]))
                values[i] = (np.asarray(left["linear"], np.float32) * (_F(1) - f)
                             + np.asarray(right["linear"], np.float32) * f)
        return values

    def _frame(self, index):
        """Create just one default-bank quarter-degree RGB prediction frame."""
        if index not in self._frames:
            angle = self.model_start_deg + index * .25
            x = angle - self.model_start_deg
            lo = min(int(x), self.node_count - 2)
            f = x - lo
            responses = self._responses_at(angle)
            result = np.zeros((4913, 3), dtype=np.float32)
            total = np.zeros(4913, dtype=np.float64)
            for node, factor in ((lo, 1 - f), (lo + 1, f)):
                if factor <= 0:
                    continue
                entries = self.lut[node]
                for k in range(16):
                    weights = entries["weights"][:, k].astype(np.float64) * factor
                    active = weights > 0
                    result[active] += (responses[entries["ids"][active, k]]
                                       * weights[active].astype(np.float32)[:, None])
                    total[active] += weights[active]
            result /= total.astype(np.float32)[:, None]
            result.flags.writeable = False
            self._frames[index] = result
        return self._frames[index]

    def predict(self, rgb, beta):
        """Default-bank Atlas.Map: RGB tetrahedra, then quarter-angle interpolation."""
        rgb = np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)
        angles = np.broadcast_to(np.asarray(beta, dtype=np.float64), (len(rgb),))
        indices, weights = _tetra(rgb)
        a = (angles - self.model_start_deg) * 4
        lo = np.minimum(a.astype(np.int32), (self.node_count - 1) * 4 - 1)
        f = (a - lo).astype(np.float32)
        result = np.empty((len(rgb), 3), dtype=np.float32)
        for frame_index in np.unique(lo):
            selected = lo == frame_index
            ix, w = indices[selected], weights[selected]
            frames = (self._frame(int(frame_index)), self._frame(int(frame_index) + 1))
            mapped = []
            for frame in frames:
                value = frame[ix[:, 0]] * w[:, 0, None]
                for k in range(1, 4):
                    value += frame[ix[:, k]] * w[:, k, None]
                mapped.append(value)
            fraction = f[selected, None]
            result[selected] = mapped[0] * (_F(1) - fraction) + mapped[1] * fraction
        return result

    def target(self, rgb):
        rgb = np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)
        srgb = rgb.astype(np.float64) / 255.0
        linear = np.where(srgb <= .04045, srgb / 12.92,
                          ((srgb + .055) / 1.055) ** 2.4).astype(np.float32)
        lab = _oklab(linear)
        black = _F(np.cbrt(self._data["sharedBlackY"]))
        white = _F(np.cbrt(self._data["sharedWhiteY"]))
        hue = np.arctan2(lab[:, 2], lab[:, 1]) * _F(180) / _F(math.pi)
        chroma = np.sqrt(lab[:, 1] * lab[:, 1] + lab[:, 2] * lab[:, 2])

        def smooth(value):
            value = np.clip(value, _F(0), _F(1))
            return value * value * (_F(3) - _F(2) * value)

        boost = (_F(1) + _F(self._data["purpleChromaBoost"])
                 * smooth((hue + _F(90)) / _F(20))
                 * smooth((_F(10) - hue) / _F(20)) * smooth(chroma / _F(.025)))
        return _from_oklab(np.column_stack((black + (white - black) * lab[:, 0],
                                             white * lab[:, 1] * boost,
                                             white * lab[:, 2] * boost)))

    def error(self, actual, target):
        difference = _oklab(actual) - _oklab(target)
        x, y, z = difference.T
        return np.sqrt((x * x).astype(np.float64)
                       + 3.2 * y.astype(np.float64) * y
                       + 3.2 * z.astype(np.float64) * z) / np.cbrt(self._data["referenceLinearWhite"])


@cache
def load_color_profile() -> ColorProfile:
    directory = Path(__file__).resolve().parent
    with (directory / "continuous-color-map.json").open(encoding="utf-8") as stream:
        data = json.load(stream)
    with gzip.open(directory / data["lutFile"], "rb") as stream:
        _, _, nodes, count, _, _ = struct.unpack("<8s5I", stream.read(28))
        bank_bytes = nodes * count * _ENTRY.itemsize
        stream.seek(28 + 3 * bank_bytes)
        lut = np.frombuffer(stream.read(bank_bytes), dtype=_ENTRY).reshape(nodes, count)
    texture_bytes = [bytes.fromhex(texture["motif"]) for texture in data["textures"]]
    lengths = np.asarray([len(motif) for motif in texture_bytes], dtype=np.int64)
    offsets = np.concatenate((np.array([0], dtype=np.int64), np.cumsum(lengths[:-1])))
    offsets.flags.writeable = lengths.flags.writeable = False
    return ColorProfile(lut, np.frombuffer(b"".join(texture_bytes), dtype=np.uint8), offsets, lengths,
                        data["modelStartDeg"], data["modelEndDeg"], nodes, data["ditherSeed"], data)


def plan_viewing_angle(rgb, options, layout=None, profile=None) -> float:
    """Choose one image's rotation using the source coverage/weighted-error score.

    The input is already a disc-centred raster, so CenterAlpha is zero and its
    angular span is [-180, 180]. Planning uses the source's small 768/20 grid,
    sampling the existing RGB raster rather than a separate GDI preview render.
    """
    profile = profile or load_color_profile()
    geometry = DvdGeometry(options.inner_radius_mm, options.outer_radius_mm, options.track_length_mm,
                           options.pitch_linear, options.pitch_quadratic, options.pitch_cubic)
    start, end = ((layout.drawing_start_lba, layout.drawing_end_lba)
                  if layout else (0, options.total_sectors))
    radii = geometry.positions(np.array([start, end]) * 38688.0 * (options.channel_bit_nm * 1e-6))[0]
    step = math.ceil(768 / 20)
    axis = (np.arange(step // 2, 768, step) + .5) * (2 * options.outer_radius_mm / 768) - options.outer_radius_mm
    x, y = np.meshgrid(axis, axis)
    inside = (x * x + y * y >= radii[0] ** 2) & (x * x + y * y < radii[1] ** 2)
    x, y = x[inside], y[inside]
    height, width = rgb.shape[:2]
    scale = (min(width, height) - 1) / (2 * options.outer_radius_mm)
    ix = np.clip(np.floor((width - 1) / 2 + x * scale + .5).astype(np.intp), 0, width - 1)
    iy = np.clip(np.floor((height - 1) / 2 + y * scale + .5).astype(np.intp), 0, height - 1)
    colors = rgb[iy, ix]
    delta = (np.arctan2(y, x) * 180 / math.pi + 540) % 360 - 180
    targets = profile.target(colors)
    low, high = profile._data["windowStartDeg"], profile._data["windowEndDeg"]
    best, best_score = (low + high) / 2, math.inf

    def consider(candidate):
        nonlocal best, best_score
        beta = candidate + delta
        supported = (beta >= profile.model_start_deg) & (beta <= profile.model_end_deg)
        fraction = np.count_nonzero(supported) / len(colors) if len(colors) else 0
        mean = float(np.mean(profile.error(profile.predict(colors[supported], beta[supported]),
                                           targets[supported]))) if np.any(supported) else 1
        score = (1 - fraction) * 100 + mean
        if score < best_score - 1e-12:
            best, best_score = candidate, score

    candidate = low
    while candidate <= high:
        consider(candidate)
        candidate += 1
    consider(high)
    around = best
    candidate = max(low, around - 1)
    while candidate <= min(high, around + 1):
        consider(candidate)
        candidate += .1
    return float(best)
