# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Disc Image Studio contributors.
"""Physical DVD spiral coordinates.

The local radial growth is b * (1 + c1*x + c2*x**2 + c3*x**3), where
x = (radius - inner_radius) / (outer_radius - inner_radius).  The scale b is
solved from the total physical track length.  Constant pitch uses a one-step
Newton inverse; varying pitch uses adaptive Gaussian
quadrature and a piecewise cubic Hermite inverse.  All distances are millimetres.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(slots=True)
class _Integral:
    length: float
    angle: float
    derivative: float

    def __add__(self, other: _Integral) -> _Integral:
        return _Integral(
            self.length + other.length,
            self.angle + other.angle,
            self.derivative + other.derivative,
        )


@dataclass(slots=True)
class _Segment:
    radius_start: float
    width: float
    arc_start: float
    angle_start: float
    arc_delta: float
    angle_delta: float
    arc_derivative_start: float
    arc_derivative_end: float
    angle_derivative_start: float
    angle_derivative_end: float


class DvdGeometry:
    """Map arc lengths on a spiral to radius and unwrapped angle arrays.

    ``positions`` preserves the input shape and returns two float64 ndarrays.
    Callers supply valid geometry and finite arc lengths in [0, track_length_mm].
    """

    _NODES = (0.1834346424956498, 0.5255324099163290,
              0.7966664774136267, 0.9602898564975363)
    _WEIGHTS = (0.3626837833783620, 0.3137066458778873,
                0.2223810344533745, 0.1012285362903763)

    def __init__(
        self,
        inner_radius_mm: float,
        outer_radius_mm: float,
        track_length_mm: float,
        pitch_linear: float = 0,
        pitch_quadratic: float = 0,
        pitch_cubic: float = 0,
    ) -> None:
        self.inner_radius_mm = float(inner_radius_mm)
        self.outer_radius_mm = float(outer_radius_mm)
        self.track_length_mm = float(track_length_mm)
        self._linear = float(pitch_linear)
        self._quadratic = float(pitch_quadratic)
        self._cubic = float(pitch_cubic)
        self._range = self.outer_radius_mm - self.inner_radius_mm
        self.is_constant_pitch = self._linear == self._quadratic == self._cubic == 0
        self._segments: list[_Segment] = []

        if self.is_constant_pitch:
            self._build_constant()
        else:
            self._build_varying()
        self.track_pitch_mm = 2 * math.pi * self.radial_growth_per_radian_mm

    def _build_constant(self) -> None:
        first, last = self.inner_radius_mm, self.outer_radius_mm
        low, high = 1e-15, max(last, 1.0)
        while self._constant_arc(first, last, high) > self.track_length_mm:
            high *= 2
            if not math.isfinite(high):
                raise ValueError("The track length cannot be resolved numerically.")
        for _ in range(120):
            middle = (low + high) / 2
            if self._constant_arc(first, last, middle) > self.track_length_mm:
                low = middle
            else:
                high = middle
        growth = (low + high) / 2
        self.radial_growth_per_radian_mm = growth
        self._constant_inner_squared = first * first
        self._constant_growth_squared = growth * growth
        self._constant_twice_growth = 2 * growth
        self._constant_inner_primitive = self._primitive(first, growth)
        self.total_angle_radians = self._range / growth

    def _build_varying(self) -> None:
        first, last = self.inner_radius_mm, self.outer_radius_mm
        thin_length = self._integrate(first, last, 1, thin=True).length
        lower, upper = 0.0, self.track_length_mm / thin_length
        inverse_growth = upper
        for _ in range(80):
            value = self._integrate(first, last, inverse_growth)
            error = value.length - self.track_length_mm
            if abs(error) <= self.track_length_mm * 2e-15:
                break
            if error > 0:
                upper = inverse_growth
            else:
                lower = inverse_growth
            next_value = inverse_growth - error / value.derivative
            if not math.isfinite(next_value) or next_value <= lower or next_value >= upper:
                next_value = lower + (upper - lower) / 2
            if next_value == inverse_growth:
                break
            inverse_growth = next_value
        growth = 1 / inverse_growth
        if not math.isfinite(growth) or growth <= 0:
            raise ValueError("The pitch coefficients cannot be resolved at this track length.")
        self.radial_growth_per_radian_mm = growth
        self._build_segments(first, last, inverse_growth, 0)
        arc = angle = arc_error = angle_error = 0.0
        for segment in self._segments:
            segment.arc_start = arc
            segment.angle_start = angle
            arc, arc_error = self._add_compensated(arc, arc_error, segment.arc_delta)
            angle, angle_error = self._add_compensated(angle, angle_error, segment.angle_delta)
        self._length_scale = self.track_length_mm / arc
        self.total_angle_radians = angle
        # Column arrays allow one vectorized binary search and interpolation per batch.
        columns = (
            "radius_start", "width", "arc_start", "angle_start", "arc_delta",
            "angle_delta", "arc_derivative_start", "arc_derivative_end",
            "angle_derivative_start", "angle_derivative_end",
        )
        self._table = {
            name: np.asarray([getattr(segment, name) for segment in self._segments], dtype=np.float64)
            for name in columns
        }

    def positions(self, arc_lengths: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return radius and unwrapped radians for finite, in-range arc lengths."""
        arcs = np.asarray(arc_lengths, dtype=np.float64)
        if self.is_constant_pitch:
            growth = self.radial_growth_per_radian_mm
            radius = np.clip(
                np.sqrt(self._constant_inner_squared + self._constant_twice_growth * arcs),
                self.inner_radius_mm, self.outer_radius_mm,
            )
            root = np.sqrt(radius * radius + self._constant_growth_squared)
            primitive = 0.5 * (
                radius * root
                + self._constant_growth_squared * np.arcsinh(radius / growth)
            )
            error = (primitive - self._constant_inner_primitive) / growth - arcs
            derivative = root / growth
            radius = np.clip(radius - error / derivative, self.inner_radius_mm, self.outer_radius_mm)
            return np.asarray(radius), np.asarray((radius - self.inner_radius_mm) / growth)

        unscaled = arcs / self._length_scale
        table = self._table
        indices = np.searchsorted(table["arc_start"], unscaled, side="right") - 1
        indices = np.clip(indices, 0, len(self._segments) - 1)
        width = table["width"][indices]
        t = self._invert_hermite(
            unscaled - table["arc_start"][indices], table["arc_delta"][indices],
            width * table["arc_derivative_start"][indices],
            width * table["arc_derivative_end"][indices],
        )
        radius = table["radius_start"][indices] + width * t
        angle = table["angle_start"][indices] + self._hermite(
            t, table["angle_delta"][indices],
            width * table["angle_derivative_start"][indices],
            width * table["angle_derivative_end"][indices],
        )
        # Match the explicit endpoints in C# AtArcLengthFast.
        radius = np.where(arcs <= 0, self.inner_radius_mm,
                          np.where(arcs >= self.track_length_mm, self.outer_radius_mm, radius))
        angle = np.where(arcs <= 0, 0.0,
                         np.where(arcs >= self.track_length_mm, self.total_angle_radians, angle))
        return radius, angle

    def _shape(self, radius: float) -> float:
        x = (radius - self.inner_radius_mm) / self._range
        return ((self._cubic * x + self._quadratic) * x + self._linear) * x + 1

    def _integrate(self, first: float, last: float, inverse_growth: float,
                   thin: bool = False) -> _Integral:
        evaluations = [0]

        def adaptive(start: float, end: float, depth: int) -> _Integral:
            evaluations[0] += 3
            if evaluations[0] > 32768:
                raise ValueError("The DVD pitch integral exceeded its numerical resolution budget.")
            whole = self._quadrature(start, end, inverse_growth, thin)
            if not math.isfinite(whole.length) or not math.isfinite(whole.angle):
                raise ValueError("The DVD pitch integral cannot be resolved numerically.")
            middle = start + (end - start) / 2
            left = self._quadrature(start, middle, inverse_growth, thin)
            right = self._quadrature(middle, end, inverse_growth, thin)
            split = left + right
            if (abs(split.length - whole.length) <= abs(split.length) * 8e-15
                    and abs(split.angle - whole.angle) <= abs(split.angle) * 8e-15):
                return split
            if depth >= 24 or middle == start or middle == end:
                raise ValueError("The DVD pitch integral did not converge at the requested accuracy.")
            return adaptive(start, middle, depth + 1) + adaptive(middle, end, depth + 1)

        return adaptive(first, last, 0)

    def _quadrature(self, first: float, last: float, inverse_growth: float,
                    thin: bool = False) -> _Integral:
        middle, half = first + (last - first) / 2, (last - first) / 2
        length = angle = derivative = 0.0
        for node, weight in zip(self._NODES, self._WEIGHTS):
            for sign in (-1, 1):
                radius = middle + sign * half * node
                inverse_shape = 1 / self._shape(radius)
                radial = radius * inverse_shape
                z = inverse_growth * radial
                ds = radial if thin else self._hypot(1, z)
                length += weight * ds
                angle += weight * inverse_shape * inverse_growth
                if not thin:
                    derivative += weight * radial * (z / ds)
        return _Integral(half * length, half * angle, half * derivative)

    def _build_segments(self, first: float, last: float, inverse_growth: float,
                        depth: int) -> None:
        if len(self._segments) >= 32768:
            raise ValueError("The pitch curve is too close to zero to resolve numerically.")
        width = last - first
        middle = first + width / 2
        whole = self._quadrature(first, last, inverse_growth)
        left = self._quadrature(first, middle, inverse_growth)
        right = self._quadrature(middle, last, inverse_growth)
        split = left + right
        a0, a1 = inverse_growth / self._shape(first), inverse_growth / self._shape(last)
        s0, s1 = self._hypot(1, first * a0), self._hypot(1, last * a1)
        angle_tolerance = 5e-10
        length_tolerance = angle_tolerance * self.inner_radius_mm
        accepted = (abs(split.angle - whole.angle) < angle_tolerance / 16
                    and abs(split.length - whole.length) < length_tolerance / 16)
        for i in range(1, 4):
            if not accepted:
                break
            t = i / 4.0
            actual = left if i == 2 else self._quadrature(first, first + width * t, inverse_growth)
            accepted = (
                abs(self._hermite(t, split.angle, width * a0, width * a1) - actual.angle)
                < angle_tolerance
                and abs(self._hermite(t, split.length, width * s0, width * s1) - actual.length)
                < length_tolerance
            )
        if not accepted and depth < 24 and middle != first and middle != last:
            self._build_segments(first, middle, inverse_growth, depth + 1)
            self._build_segments(middle, last, inverse_growth, depth + 1)
            return
        if (not accepted or not math.isfinite(split.length)
                or not math.isfinite(split.angle) or split.length <= 0):
            raise ValueError("The pitch curve cannot be resolved numerically.")
        self._segments.append(_Segment(
            first, width, 0.0, 0.0, split.length, split.angle, s0, s1, a0, a1,
        ))

    @staticmethod
    def _hermite(t, delta, derivative_start, derivative_end):
        return t * (derivative_start + t * (
            3 * delta - 2 * derivative_start - derivative_end
            + t * (derivative_start + derivative_end - 2 * delta)
        ))

    @staticmethod
    def _invert_hermite(value, delta, derivative_start, derivative_end):
        t = np.clip(value / delta, 0, 1)
        quadratic = 3 * delta - 2 * derivative_start - derivative_end
        cubic = derivative_start + derivative_end - 2 * delta
        for _ in range(4):
            error = t * (derivative_start + t * (quadratic + t * cubic)) - value
            derivative = derivative_start + t * (2 * quadratic + 3 * t * cubic)
            t = np.clip(t - error / derivative, 0, 1)
        return t

    @staticmethod
    def _hypot(first: float, second: float) -> float:
        # Keep the explicit operation order; math.hypot rounds differently.
        first, second = abs(first), abs(second)
        maximum = max(first, second)
        if maximum == 0:
            return 0.0
        ratio = min(first, second) / maximum
        return maximum * math.sqrt(1 + ratio * ratio)

    @staticmethod
    def _add_compensated(total: float, error: float, value: float) -> tuple[float, float]:
        adjusted = value - error
        next_value = total + adjusted
        error = (next_value - total) - adjusted
        return next_value, error

    @staticmethod
    def _primitive(radius: float, growth: float) -> float:
        return 0.5 * (
            radius * math.sqrt(radius * radius + growth * growth)
            + growth * growth * math.asinh(radius / growth)
        )

    @classmethod
    def _constant_arc(cls, first: float, second: float, growth: float) -> float:
        return (cls._primitive(second, growth) - cls._primitive(first, growth)) / growth
