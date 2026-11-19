# SPDX-License-Identifier: GPL-3.0-or-later
"""Build position-dependent carrier variants on a physical disc grid."""
from __future__ import annotations

import sys
import time

import numpy as np

from dvd_carriers import synthesize_carrier


def prepare_chirp(periods, frames, target_u, poses, radii_mm,
                  nominal_bytes, nominal_trim, cell_mm=0.05):
    """Return variant banks, plane offsets, local indexes and requested periods.

    Each plane's variant zero is its supplied nominal string. Other variants
    use a shared logarithmic period grid; geometry is evaluated once per frame.
    """
    inner, outer = radii_mm
    size = int(np.ceil(2 * outer / cell_mm))
    axis = -outer + (np.arange(size, dtype=np.float64) + 0.5) * (2 * outer / size)
    x, y = axis[None, :], -axis[:, None]
    radius = np.hypot(x, y)
    # Include every cell intersecting the recording ring, including edge cells.
    margin = np.sqrt(2.0) * outer / size
    ring = (radius >= inner - margin) & (radius <= outer + margin)
    maps = np.zeros((len(periods), size, size), dtype=np.uint8)
    local_periods = [None] * len(periods)
    requested = set()
    factor = 1.005
    step = np.log(factor)
    grid_size = int(np.floor(np.log(22.0 / 6.4) / step + 1e-9)) + 1

    for frame in dict.fromkeys(frames):
        gx = np.zeros((size, size), dtype=np.float64)
        gy = np.zeros_like(gx)
        for position in poses[frame]:
            dx, dy = position[0] - x, position[1] - y
            distance = np.sqrt(dx * dx + dy * dy + position[2] * position[2])
            gx += dx / distance
            gy += dy / distance
        with np.errstate(divide="ignore", invalid="ignore"):
            u = np.abs((gx * y - gy * x) / radius)
            stretch = target_u[frame] / u[ring]
            log_stretch = np.log(np.where(np.isnan(stretch), np.inf, stretch)) / step
        del gx, gy, dx, dy, distance, u, stretch

        for plane, owner in enumerate(frames):
            if owner != frame:
                continue
            nominal = float(periods[plane])
            lower = min(int(np.ceil(np.log(6.4 / nominal) / step - 1e-9)), 0)
            upper = max(int(np.floor(np.log(22.0 / nominal) / step + 1e-9)), 0)
            shifts = np.rint(np.clip(log_stretch, lower, upper)).astype(np.int64)
            used, inverse = np.unique(shifts, return_inverse=True)
            snapped = []
            for shift in used:
                if shift == 0:
                    period = nominal
                else:
                    period = nominal * factor ** int(shift)
                    index = int(np.rint(np.log(period / 6.4) / step))
                    period = 6.4 * factor ** min(max(index, 0), grid_size - 1)
                snapped.append(period)
            variants = [nominal] + sorted(set(snapped) - {nominal})
            lookup = {period: index for index, period in enumerate(variants)}
            ids = np.array([lookup[period] for period in snapped], dtype=np.uint8)
            maps[plane][ring] = ids[inverse]
            local_periods[plane] = variants
            requested.update(variants[1:])
            del shifts, inverse
        del log_stretch

    # All planes share the same in-process carrier cache. Prepare each distinct
    # grid period once before copying its small string into the plane banks.
    synthesized = {}
    total = len(requested)
    started = last_report = time.monotonic()
    line_width = 0

    def report(completed):
        nonlocal last_report, line_width
        now = time.monotonic()
        if 0 < completed < total and now - last_report < 1.0:
            return
        elapsed = max(now - started, 1e-9)
        speed = completed / elapsed
        eta = f"{(total - completed) / speed:.0f}s" if completed else "--s"
        line = (f"准备周期变体 {completed / total:6.2%}  {completed:,}/{total:,} 周期  "
                f"{speed:.2f} 周期/s  剩余约 {eta}")
        line_width = max(line_width, len(line))
        print("\r" + line.ljust(line_width), end="", file=sys.stderr, flush=True)
        last_report = now

    if total:
        report(0)
    try:
        for completed, period in enumerate(sorted(requested), 1):
            synthesized[period] = synthesize_carrier(period)
            report(completed)
    finally:
        if total:
            print(file=sys.stderr, flush=True)

    lengths = [len(values) for values in local_periods]
    offsets = np.zeros(len(periods), dtype=np.int64)
    offsets[1:] = np.cumsum(lengths[:-1])
    count = sum(lengths)
    byte_bank = np.empty((count, 91), dtype=np.uint8)
    trim_bank = np.empty((count, 92), dtype=np.uint8)
    variant_periods = np.empty(count, dtype=np.float64)
    for plane, variants in enumerate(local_periods):
        start = int(offsets[plane])
        byte_bank[start] = nominal_bytes[plane]
        trim_bank[start] = nominal_trim[plane]
        variant_periods[start:start + len(variants)] = variants
        for index, period in enumerate(variants[1:], 1):
            data, trim, _ = synthesized[period]
            byte_bank[start + index] = data
            trim_bank[start + index] = trim
    for array in (byte_bank, trim_bank, offsets, maps, variant_periods):
        array.flags.writeable = False
    return byte_bank, trim_bank, offsets, maps, variant_periods
