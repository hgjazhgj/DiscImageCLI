# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthesize deterministic DVD carrier strings and balance their optical signal."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

_TABLES = None
_CARRIERS = {}


def synthesize_carrier(period):
    """Return a deterministic 91-byte string, its trim table and spectral lines."""
    global _TABLES
    if _TABLES is None:
        with Path(__file__).with_name("efmplus-tables.json").open(encoding="utf-8") as stream:
            main = np.array(json.load(stream)["main"], dtype=np.int64)
        words, next_state = main[:, :, 0], main[:, :, 1] - 1
        bits = ((words[:, :, None] >> np.arange(15, -1, -1)) & 1).astype(np.uint8)
        leading = np.where(bits.any(axis=2), np.argmax(bits, axis=2), 16)
        trailing = np.where(bits.any(axis=2), np.argmax(bits[:, :, ::-1], axis=2), 16)
        parity = bits.sum(axis=2).astype(np.int64) & 1
        land_levels = 1.0 - 2.0 * (np.cumsum(bits, axis=2) & 1)
        levels = np.stack([-land_levels, land_levels])
        candidates = np.arange(88, 256)
        valid = np.empty((4, 17, len(candidates)), dtype=np.bool_)
        for state in range(4):
            for tz in range(17):
                combined = tz + leading[candidates, state]
                legal = (combined >= 2) & (combined <= 10)
                if state in (0, 3):
                    alternate = tz + leading[candidates, 3 if state == 0 else 0]
                    legal &= ~((alternate >= 2) & (alternate <= 10))
                valid[state, tz] = legal

        wavelengths = np.arange(380.0, 781.0, 2.0)

        def lobe(mu, first, last):
            t = (wavelengths - mu) / np.where(wavelengths < mu, first, last)
            return np.exp(-0.5 * t * t)

        cmf = np.stack([
            1.056 * lobe(599.8, 37.9, 31.0) + 0.362 * lobe(442.0, 16.0, 26.7)
            - 0.065 * lobe(501.1, 20.4, 26.2),
            0.821 * lobe(568.8, 46.9, 40.5) + 0.286 * lobe(530.9, 16.3, 31.1),
            1.217 * lobe(437.0, 11.8, 36.0) + 0.681 * lobe(459.0, 26.0, 13.8),
        ], axis=1)
        blue = np.exp(-0.5 * ((wavelengths - 452.0) / 9.5) ** 2)
        phosphor = np.exp(-0.5 * ((wavelengths - 560.0)
                                / np.where(wavelengths < 560.0, 42.0, 68.0)) ** 2)
        weight = (0.75 * blue + phosphor)[:, None] * cmf
        _TABLES = (next_state, leading, trailing, parity, levels, candidates, valid,
                   wavelengths, weight, np.linspace(0.0, 1.4, 2801))
    (next_state, leading, trailing, parity, levels, candidates, valid,
     wavelengths, weight, u_grid) = _TABLES

    period = float(period)
    if period not in _CARRIERS:
        best_score, best_bytes = -np.inf, None
        for phase in range(16):
            target = np.cos(2 * np.pi * np.arange(16 * 91) / period
                            + 2 * np.pi * phase / 16).reshape(91, 16)
            score = np.zeros(1)
            state, tz, land = np.array([0]), np.array([0]), np.array([1])
            sequence = np.zeros((1, 0), dtype=np.int64)
            for position in range(91):
                legal = valid[state, tz]
                waveforms = levels[land[:, None], candidates[None, :], state[:, None], :]
                delta = waveforms @ target[position]
                total = np.where(legal, score[:, None] + delta, -np.inf)
                flat = total.ravel()
                count = min(flat.size, 64 * 4)
                top = np.argpartition(-flat, count - 1)[:count]
                top = top[np.argsort(-flat[top], kind="stable")]
                keep, counts = [], {}
                for choice in top:
                    if not np.isfinite(flat[choice]):
                        break
                    previous, selected = divmod(int(choice), len(candidates))
                    value, old_state = candidates[selected], state[previous]
                    key = (int(next_state[value, old_state]), int(trailing[value, old_state]),
                           int(land[previous] ^ parity[value, old_state]))
                    used = counts.get(key, 0)
                    if used >= 6:
                        continue
                    counts[key] = used + 1
                    keep.append((previous, selected))
                    if len(keep) >= 64:
                        break
                if not keep:
                    raise RuntimeError(f"No deterministic carrier continuation at byte {position}.")
                previous = np.array([item[0] for item in keep])
                selected = np.array([item[1] for item in keep])
                values, old_states = candidates[selected], state[previous]
                score = flat[previous * len(candidates) + selected]
                land = land[previous] ^ parity[values, old_states]
                tz = trailing[values, old_states]
                state = next_state[values, old_states]
                sequence = np.concatenate([sequence[previous], selected[:, None]], axis=1)
            best = int(np.argmax(score))
            if score[best] > best_score:
                best_score = float(score[best])
                best_bytes = candidates[sequence[best]].astype(np.uint8)

        state, tz, land = 0, 0, 1
        waveform, bad = [], set()
        for length, value in enumerate(best_bytes, 1):
            waveform.append(levels[land, value, state])
            land ^= int(parity[value, state])
            tz = int(trailing[value, state])
            state = int(next_state[value, state])
            if length < 91:
                combined = tz + leading[0xA5, state]
                ambiguous = False
                if state in (0, 3):
                    alternate = tz + leading[0xA5, 3 if state == 0 else 0]
                    ambiguous = 2 <= alternate <= 10
                if not 2 <= combined <= 10 or ambiguous:
                    bad.add(length)
        trim = np.zeros(92, dtype=np.uint8)
        for length in range(92):
            drop = 0
            while length - drop in bad:
                drop += 1
            trim[length] = drop
        waveform = np.concatenate(waveform)
        frequencies = np.fft.rfftfreq(1 << 15)
        window = np.hanning(waveform.size)
        amplitude = np.abs(np.fft.rfft(waveform * window, n=1 << 15)) / window.sum()
        amplitude[frequencies < 1 / 40] = 0
        peaks = np.flatnonzero((amplitude[1:-1] > amplitude[:-2])
                              & (amplitude[1:-1] >= amplitude[2:])
                              & (amplitude[1:-1] >= 0.02)) + 1
        peaks = peaks[np.argsort(-amplitude[peaks])]
        kept = []
        for peak in peaks:
            if all(abs(frequencies[peak] - frequencies[other]) > 2.5 / waveform.size for other in kept):
                kept.append(peak)
        lines = tuple((float(1 / frequencies[peak]), float(amplitude[peak])) for peak in kept[:10])
        best_bytes.flags.writeable = False
        trim.flags.writeable = False
        _CARRIERS[period] = (best_bytes, trim, lines)

    return _CARRIERS[period]


def prepare_carriers(periods, target_u, frames, channels, channel_bit_nm):
    """Prepare nominal strings and normalized optical shares for each view."""
    strings = [synthesize_carrier(period) for period in periods]
    wavelengths, weight, u_grid = _TABLES[-3:]
    output = np.empty((len(periods), 91), dtype=np.uint8)
    trims = np.empty((len(periods), 92), dtype=np.uint8)
    actual_periods = []
    response = np.empty((len(periods), 3), dtype=np.float64)
    for plane, (data, trim, lines) in enumerate(strings):
        output[plane], trims[plane] = data, trim
        actual_periods.append(lines[0][0])
        xyz = np.zeros((len(u_grid), 3))
        for peak_period, amplitude in lines:
            physical_period = peak_period * channel_bit_nm * 1e-3
            light = physical_period * u_grid * 1e3
            for component in range(3):
                xyz[:, component] += (amplitude * amplitude * physical_period
                                      * np.interp(light, wavelengths, weight[:, component], left=0.0, right=0.0))
        # The carrier signal excludes random-data haze; the common payload
        # coverage factor cancels when all carrier shares are normalized.
        pad = np.pad(xyz, ((20, 20), (0, 0)), mode="edge")
        smooth = np.stack([np.convolve(pad[:, c], np.ones(41) / 41, mode="valid")
                           for c in range(3)], axis=1)
        u = abs(target_u[frames[plane]])
        response[plane] = [np.interp(u, u_grid, smooth[:, c]) for c in range(3)]

    need = np.empty(len(periods), dtype=np.float64)
    for frame in dict.fromkeys(frames):
        indexes = [index for index, owner in enumerate(frames) if owner == frame]
        if channels[indexes[0]] == "L":
            need[indexes] = 1.0 / response[indexes, 1]
        else:
            need[indexes] = np.linalg.solve(response[indexes].T, np.array([0.9505, 1.0, 1.089]))
    need /= need.sum()
    return output, trims, tuple(actual_periods), need
