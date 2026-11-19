# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare image planes, carrier schedules and metadata for DVD drawings.

By default grayscale pictures use one carrier plane and colour pictures keep
their three RGB planes. Optional channel merging selects one source channel per
carrier and groups neighbouring carriers into optical RGB trios. Carrier planes
share spiral turns; strings restart at each sync frame. Periods are expressed
in channel bits, so their physical lengths scale with the disc's channel_bit_nm.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
from PIL import Image


_RESOURCE = None


@dataclass(frozen=True)
class MultiviewProfile:
    field: np.ndarray
    source_paths: tuple[str, ...]
    periods: tuple[float, ...]
    gains: tuple[float, ...]
    carrier_bytes: np.ndarray
    trim: np.ndarray
    source_modes: tuple[str, ...]
    carrier_frames: tuple[int, ...]
    channels: tuple[str, ...]
    counts: np.ndarray
    cycle: np.ndarray
    rank: np.ndarray
    target_u: tuple[float, ...]
    levels: np.ndarray
    sampling_modes: np.ndarray
    single_byte_carriers: bool
    variant_offsets: np.ndarray
    variant_map: np.ndarray
    variant_periods: np.ndarray
    viewing_poses: tuple | None
    requested_periods: tuple[float, ...] | None = None
    channel_merge: bool = False


@dataclass(frozen=True)
class _Source:
    path: str
    mode: str
    pixels: np.ndarray | None = None


def _prepare_sources(sources, viewing, channel_bit_nm, radii_mm, color_range):
    """Build every profile through one carrier plan and one streaming assembler."""
    global _RESOURCE
    if viewing is None and _RESOURCE is None:
        with Path(__file__).with_name("multiview-carriers.json").open(encoding="utf-8") as stream:
            _RESOURCE = json.load(stream)
    resource = _RESOURCE
    paths = tuple(source.path for source in sources)
    modes = tuple(source.mode for source in sources)
    merge_channels = color_range is not None
    any_color = merge_channels or "color" in modes
    if merge_channels:
        carrier_count = 6 if color_range == "standard" else 9
        carrier_frames = [plane * len(sources) // carrier_count for plane in range(carrier_count)]
        channels = ["RGB"[plane % 3] for plane in range(carrier_count)]
        carrier_views = [plane // 3 for plane in range(carrier_count)]
        view_count = carrier_count // 3
    else:
        carrier_frames, channels = [], []
        for frame, mode in enumerate(modes):
            for channel in (("R", "G", "B") if mode == "color" else ("L",)):
                carrier_frames.append(frame)
                channels.append(channel)
        carrier_count = len(channels)
        carrier_views = carrier_frames
        view_count = len(sources)

    requested_periods = None
    poses = [None] * view_count
    single_byte_carriers = viewing is None and not any_color and len(sources) <= 2
    if viewing is not None or (merge_channels and carrier_count == 9):
        from dvd_carriers import prepare_carriers

        if viewing is not None:
            views = viewing.get("views", ({},) * view_count)
            target_u = []
            for frame in range(view_count):
                view = views[frame]
                if "target_u" in view:
                    u = float(view["target_u"])
                else:
                    reference = np.asarray(viewing.get("reference_mm", (0.0, 43.0)), dtype=np.float64)
                    tangent = np.array((reference[1], -reference[0], 0.0)) / np.hypot(*reference)
                    point = np.array((reference[0], reference[1], 0.0))
                    turn, tilt, rotate = np.radians([
                        view.get("turn_deg", 0.0), view.get("tilt_deg", 0.0), view.get("rotate_deg", 0.0)])
                    cy, sy = np.cos(turn), np.sin(turn)
                    cx, sx = np.cos(tilt), np.sin(tilt)
                    cz, sz = np.cos(rotate), np.sin(rotate)
                    ry = np.array(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)))
                    rx = np.array(((1, 0, 0), (0, cx, -sx), (0, sx, cx)))
                    rz = np.array(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)))
                    rotation = rz @ rx @ ry
                    eye = rotation @ np.asarray(view.get("eye_mm", viewing.get("eye_mm", (0, 0, 500))))
                    lamp = rotation @ np.asarray(view.get("lamp_mm", viewing.get("lamp_mm", (350, 0, 1000))))
                    e, light = eye - point, lamp - point
                    u = abs(float((e / np.linalg.norm(e) + light / np.linalg.norm(light)) @ tangent))
                    poses[frame] = (eye, lamp)
                target_u.append(u)
            target_u = tuple(target_u)
            wavelengths = {"L": 550.0, **{
                channel: 16.0 / (2 ** (1 / 5)) ** k * 133.3 * 0.288
                for k, channel in enumerate(("R", "G", "B"))}}
            if merge_channels and carrier_count == 9:
                ratio = (20.4 / 8.0) ** (1 / 8)
                wavelengths.update(R=525.0 * ratio, G=525.0, B=525.0 / ratio)
            requested_periods = tuple(wavelengths[channel] / (channel_bit_nm * target_u[frame])
                                      for frame, channel in zip(carrier_views, channels))
        else:
            requested_periods = tuple(20.4 * (8.0 / 20.4) ** (plane / 8) for plane in range(9))
            target_u = tuple(525.0 / (requested_periods[3 * view + 1] * channel_bit_nm)
                             for view in range(3))
        carrier_bytes, trim, periods, need = prepare_carriers(
            requested_periods, target_u, carrier_views, channels, channel_bit_nm)
        if not any_color:
            counts = np.ones(len(sources), dtype=np.int64)
            gains = tuple(float(g) for g in need / need.max())
    elif any_color:
        mixed = resource["mixedPresets"][str(2 if merge_channels else len(sources))]
        entries = []
        if merge_channels:
            entries = [entry for frame in mixed["frames"] for entry in frame["color"]]
        else:
            for frame, mode in enumerate(modes):
                entry = mixed["frames"][frame]
                entries.extend(entry["color"] if mode == "color" else [entry["gray"]])
        preset = [entry["carrier"] for entry in entries]
        periods = tuple(float(p) for p in preset)
        need = np.array([entry["whiteNeed"] for entry in entries], dtype=np.float64)
        need /= need.sum()
        target_u = tuple(float(u) * 133.3 / channel_bit_nm for u in mixed["targetU"])
    else:
        preset = resource["presets"][str(len(sources))]
        periods = tuple(float(p) for p in preset)
        gains = tuple(min(periods) / period for period in periods)
        counts = np.ones(len(sources), dtype=np.int64)
        target_u = tuple(550.0 / (p * channel_bit_nm) for p in periods)
    if any_color:
        slots = max(32, 16 * view_count)
        counts = np.maximum(np.rint(need * slots).astype(np.int64), 1)
        while counts.sum() != slots:
            error = counts / slots - need
            if counts.sum() > slots:
                error = np.where(counts > 1, error, -np.inf)
                counts[int(np.argmax(error))] -= 1
            else:
                counts[int(np.argmin(error))] += 1
        gains_array = need / (counts / counts.sum())
        gains = tuple(float(g) for g in gains_array / gains_array.max())
    slots = sorted(((r + 0.5) / int(count), plane, r)
                   for plane, count in enumerate(counts) for r in range(int(count)))
    cycle = np.array([plane for _, plane, _ in slots], dtype=np.int64)
    rank = np.array([r for _, _, r in slots], dtype=np.int64)

    if requested_periods is None:
        carrier_bytes = np.empty((carrier_count, 91), dtype=np.uint8)
        trim = np.zeros((carrier_count, 92), dtype=np.uint8)
        if single_byte_carriers:
            for plane, byte in enumerate((0x92, 0xB9)[:len(sources)]):
                carrier_bytes[plane].fill(byte)
        else:
            for plane, key in enumerate(preset):
                entry = resource["carriers"][key]
                carrier_bytes[plane] = np.frombuffer(bytes.fromhex(entry["bytesHex"]), dtype=np.uint8)
                trim[plane] = entry["trim"]

    nominal_periods = requested_periods if requested_periods is not None else periods
    if viewing is not None and viewing.get("chirp", False):
        from dvd_chirp import prepare_chirp

        reference = np.asarray(viewing.get("reference_mm", (0.0, 43.0)), dtype=np.float64)
        tangent = np.array((reference[1], -reference[0], 0.0)) / np.hypot(*reference)
        point = np.array((reference[0], reference[1], 0.0))
        for frame, pose in enumerate(poses):
            if pose is None:
                half_u = target_u[frame] / 2.0
                direction = half_u * tangent + np.array((0.0, 0.0, np.sqrt(1.0 - half_u * half_u)))
                poses[frame] = (point + 500.0 * direction, point + 1000.0 * direction)
        cell_mm = viewing.get("chirp_cell_mm", 0.05)
        carrier_bytes, trim, variant_offsets, variant_map, variant_periods = prepare_chirp(
            nominal_periods, carrier_views, target_u, poses, radii_mm,
            carrier_bytes, trim, cell_mm)
        for eye, lamp in poses:
            eye.flags.writeable = False
            lamp.flags.writeable = False
        viewing_poses = tuple(poses)
    else:
        variant_offsets = np.arange(carrier_count, dtype=np.int64)
        variant_map = np.empty((carrier_count, 0, 0), dtype=np.uint8)
        variant_periods = np.asarray(nominal_periods, dtype=np.float64)
        viewing_poses = None
    gray = np.arange(256, dtype=np.uint8).astype(np.float32) / np.float32(255.0)
    linear = np.where(gray <= np.float32(0.04045), gray / np.float32(12.92),
                      ((gray + np.float32(0.055)) / np.float32(1.055)) ** np.float32(2.4))
    levels = np.empty((carrier_count, 256), dtype=np.float32)
    for plane, (frame, gain) in enumerate(zip(carrier_frames, gains)):
        levels[plane] = (linear if modes[frame] == "color" else gray) * np.float32(gain)

    share_plane = carrier_count == 1
    field = None if share_plane else np.empty((carrier_count, 8192, 8192), dtype=np.uint8)
    sampling_modes = np.zeros(carrier_count, dtype=np.int64)
    for frame, source in enumerate(sources):
        pixels = source.pixels
        if pixels is None:
            with Image.open(source.path) as image:
                image.info.pop("transparency", None)
                with image.convert("RGB" if source.mode == "color" else "L") as raster:
                    pixels = np.array(raster)
            pixels.flags.writeable = False
        elif source.mode == "monochrome":
            pixels = pixels.astype(np.uint8) * 255
        for plane, owner in enumerate(carrier_frames):
            if owner != frame:
                continue
            if source.mode == "color":
                field[plane] = pixels[:, :, "RGB".index(channels[plane])]
            else:
                if share_plane:
                    field = pixels[None]
                else:
                    field[plane] = pixels
                sampling_modes[plane] = 1 if source.mode == "monochrome" else 2
        del pixels
    for array in (field, sampling_modes, levels, carrier_bytes, trim, counts, cycle, rank,
                  variant_offsets, variant_map, variant_periods):
        array.flags.writeable = False
    return MultiviewProfile(
        field=field, source_paths=paths, source_modes=modes, periods=periods, gains=gains,
        carrier_frames=tuple(carrier_frames), channels=tuple(channels),
        carrier_bytes=carrier_bytes, trim=trim, counts=counts, cycle=cycle, rank=rank,
        target_u=target_u, levels=levels, sampling_modes=sampling_modes,
        single_byte_carriers=single_byte_carriers, requested_periods=requested_periods,
        variant_offsets=variant_offsets, variant_map=variant_map,
        variant_periods=variant_periods, viewing_poses=viewing_poses,
        channel_merge=merge_channels,
    )


def prepare_images(images, *, source_paths=None, viewing=None, channel_bit_nm=133.3,
                   radii_mm=(24.0, 58.0), color_range=None):
    """Prepare bool black/white, uint8 grayscale or uint8 RGB arrays by format."""
    images = tuple(images)
    paths = ("<memory>",) * len(images) if source_paths is None else tuple(source_paths)
    sources = tuple(_Source(str(paths[index]),
                            "color" if image.ndim == 3 else
                            "monochrome" if image.dtype == np.bool_ else "grayscale", image)
                    for index, image in enumerate(images))
    return _prepare_sources(sources, viewing, channel_bit_nm, radii_mm, color_range)


def prepare_multiview(input_paths, *, viewing=None, channel_bit_nm=133.3,
                      radii_mm=(24.0, 58.0), color_range=None):
    """Read headers, then decode one PNG at a time into the common carrier planes.

    PNG mode 1 selects nearest sampling; grayscale and RGB planes use bilinear.
    Alpha is ignored, with no pixel-content classification.
    """
    sources = []
    for path in input_paths:
        path = Path(path).absolute()
        with Image.open(path) as image:
            mode = ("monochrome" if image.mode == "1" else
                    "grayscale" if image.mode in ("L", "LA", "I", "F")
                    or image.mode.startswith("I;16") else "color")
        sources.append(_Source(str(path), mode))
    return _prepare_sources(sources, viewing, channel_bit_nm, radii_mm, color_range)
