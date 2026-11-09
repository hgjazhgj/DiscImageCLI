#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# DVD dispersion algorithm adapted from Disc Image Studio.
# Copyright (c) 2026 Disc Image Studio contributors.
# Python port and command-line implementation: 2026-11-07. See NOTICE.md.
"""Generate a DVD drawing ISO or an 8192-square drawable-area PNG mask.

The ISO contains 2048-byte user sectors, optionally with UDF 2.01 files.
This program operates on local files only. A native sampler computes bounded batches of
codeword positions; a full-disc channel stream is never allocated.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from itertools import islice
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Callable

import numpy as np
from PIL import Image

from dvd_geometry import DvdGeometry
from dvd_kernel import generate_payload as _generate_payload, generate_color_payload as _generate_color_payload

SECTOR_BYTES = 2048
SECTORS_PER_BLOCK = 16
CHANNEL_BITS_PER_SECTOR = 38_688
CHANNEL_BITS_PER_BLOCK = CHANNEL_BITS_PER_SECTOR * SECTORS_PER_BLOCK
BLACK_BYTE = 0xA5
WHITE_BYTE = 0x92


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

    @property
    def track_length_mm(self) -> float:
        return self.total_sectors * float(CHANNEL_BITS_PER_SECTOR) * (self.channel_bit_nm * 1e-6)

def load_disc_config(path: str | Path) -> DvdOptions:
    """Load radius-based configuration and convert a hexadecimal PSN offset."""
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if isinstance(data.get("psn_offset"), str):
        data["psn_offset"] = int(data["psn_offset"], 0)
    return DvdOptions(**data)


@cache
def payload_channel_centers() -> np.ndarray:
    """Physical centres of the 32768 payload codewords in an ECC block.

    Accounts for the 12-byte sector header, 172+10 byte ECC rows, interleaved
    PO rows, and the 32 sync bits before each 91-byte EFM+ frame.
    """
    i = np.arange(SECTOR_BYTES * SECTORS_PER_BLOCK, dtype=np.int64)
    frame_byte = 12 + i % SECTOR_BYTES
    ecc_row = (i // SECTOR_BYTES) * 12 + frame_byte // 172
    recording_row = ecc_row + ecc_row // 12
    recording_byte = recording_row * 182 + frame_byte % 172
    centres = (recording_byte // 91) * 1488 + 32 + (recording_byte % 91) * 16 + 8
    centres.flags.writeable = False
    return centres


@cache
def scrambler_masks() -> np.ndarray:
    """DVD payload masks indexed by (PSN >> 4) & 15, matching the upstream LFSR."""
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


def load_png(path: str | Path) -> np.ndarray:
    """Route grayscale PNG modes to gray8 and color PNG modes to RGB8.

    Returns a read-only contiguous array. Palette colors are expanded and alpha
    is ignored. The image format, not its pixel values, selects the algorithm.
    """
    with Image.open(path) as image:
        image.info.pop("transparency", None)
        gray_mode = image.mode in ("1", "L", "LA", "I", "F") or image.mode.startswith("I;16")
        raster = image.convert("L" if gray_mode else "RGB")
    result = np.array(raster)
    raster.close()
    result.flags.writeable = False
    return result


def generate_payload(image: np.ndarray, options: DvdOptions, geometry: DvdGeometry,
                     start_block: int, block_count: int, *, dither_strength: float = 0.75,
                     binary_image: bool | None = None, color_rotation: float | None = None) -> bytes:
    """Generate complete ECC blocks of cooked user data; safe for concurrent calls."""
    if image.ndim == 3:
        from dvd_color import load_color_profile, plan_viewing_angle
        profile = load_color_profile()
        if color_rotation is None:
            color_rotation = plan_viewing_angle(image, options, profile=profile)
        return _generate_color_payload(image, options, geometry, start_block, block_count,
                                       payload_channel_centers(), scrambler_masks(), profile, color_rotation)
    if binary_image is None:
        binary_image = not np.any((image != 0) & (image != 255))
    return _generate_payload(image, options, geometry, start_block, block_count,
                             payload_channel_centers(), scrambler_masks(), binary_image, dither_strength)


def generate_mask(output_path: str | Path, options: DvdOptions, *,
                  data_dir: str | Path | None = None, file_position: str = "inner") -> None:
    """Write a binary annulus using the ISO layout's drawable LBA interval."""
    start, end = 0, options.total_sectors
    if data_dir is not None:
        from udf import plan_directory
        layout = plan_directory(Path(data_dir).absolute(), options.total_sectors, file_position)
        start, end = layout.drawing_start_lba, layout.drawing_end_lba
    geometry = DvdGeometry(options.inner_radius_mm, options.outer_radius_mm, options.track_length_mm,
                           options.pitch_linear, options.pitch_quadratic, options.pitch_cubic)
    arcs = np.array([start, end], dtype=np.float64) * CHANNEL_BITS_PER_SECTOR * (options.channel_bit_nm * 1e-6)
    inner, outer = geometry.positions(arcs)[0]
    # Match the ISO sampler's pixel centres and physical scale.
    axis = (np.arange(8192, dtype=np.float64) - 4095.5) * (2 * options.outer_radius_mm / 8191)
    x_squared = axis * axis
    pixels = np.empty((8192, 8192), dtype=np.bool_)
    for row, y in enumerate(axis):
        distance_squared = x_squared + y * y
        pixels[row] = (distance_squared >= inner * inner) & (distance_squared < outer * outer)
    with Image.fromarray(pixels) as mask:
        mask.save(output_path)


def _metadata(input_path: Path, output_path: Path, options: DvdOptions,
              geometry: DvdGeometry, elapsed: float, layout=None,
              config_path: Path | None = None, dither_strength: float = 0.75,
              color_rotation: float | None = None) -> dict:
    pitch_um = geometry.track_pitch_mm * 1000
    metadata = {
        "format": "dvd-nrzi-disc-fill-v2",
        "generator": "DiscImageBurn Python 1.7",
        "sourceImage": str(input_path), "isoOutput": str(output_path),
        "imagePreprocessing": {"grayscale": "Pillow L (0.299R+0.587G+0.114B)",
                               "alpha": "ignored", "operation": "preserve-gray8",
                               "sampling": "nearest-if-binary-otherwise-bilinear",
                               "quantization": "per-payload-byte-dither"},
        "configPath": str(config_path) if config_path else None,
        "startLba": layout.drawing_start_lba if layout else 0,
        "fillSectors": layout.drawing_end_lba - layout.drawing_start_lba if layout else options.total_sectors,
        "filesAtOuterRing": layout.files_at_outer_ring if layout else False,
        "totalSectors": options.total_sectors, "psnOffset": f"0x{options.psn_offset:X}",
        "algorithm": "dispersion", "fastOutput": True, "exactEccEfmReplay": False,
        "elapsedSeconds": elapsed,
        "imageMapping": {
            "totalSectors": options.total_sectors,
            "innerRadiusMm": options.inner_radius_mm, "outerRadiusMm": options.outer_radius_mm,
            "channelBitLengthNm": options.channel_bit_nm,
            "startAngleDegrees": options.start_angle_deg, "clockwise": options.direction == "cw",
            "luminanceThreshold": 128, "alphaThreshold": 1, "sampleEveryChannelBits": 1,
            "pitchLinear": options.pitch_linear, "pitchQuadratic": options.pitch_quadratic,
            "pitchCubic": options.pitch_cubic,
            "tone": {"ditherStrength": dither_strength, "levels": 2,
                     "brightness": 0, "srgbLinearization": False},
        },
        "pitchPolynomial": {
            "variable": "x=(radiusMm-innerRadiusMm)/(outerRadiusMm-innerRadiusMm)",
            "unit": "micrometres", "constant": pitch_um,
            "linear": pitch_um * options.pitch_linear,
            "quadratic": pitch_um * options.pitch_quadratic,
            "cubic": pitch_um * options.pitch_cubic,
        },
        "iso": {"mode": "udf-2.01" if layout else "raw-sector-image",
                "logicalSectorBytes": SECTOR_BYTES,
                "imageSectors": options.total_sectors, "mountableFilesystem": layout is not None,
                "volumeLabel": layout.volume_label if layout else None,
                "udfTimestamp": layout.timestamp.isoformat() if layout else None,
                "hybrid": {
                    "sourceDirectory": str(layout.source_directory),
                    "fileCount": layout.file_count, "directoryCount": layout.directory_count,
                    "fileBytes": layout.file_bytes, "metadataEndLbaExclusive": layout.metadata_end_lba,
                    "fileDataStartLba": layout.file_data_start_lba,
                    "dataEndLbaExclusive": layout.data_end_lba,
                    "drawingStartLba": layout.drawing_start_lba,
                    "drawingEndLbaExclusive": layout.drawing_end_lba,
                    "partitionStartLba": layout.partition_start_lba,
                    "partitionSectors": layout.partition_sectors,
                    "backupMetadataStartLba": layout.total_sectors - 257,
                } if layout else None},
    }
    if color_rotation is not None:
        from dvd_color import load_color_profile
        profile = load_color_profile()
        metadata["imagePreprocessing"] = {"color": "RGB8", "alpha": "ignored",
                                           "operation": "preserve-rgb", "sampling": "floor-nearest",
                                           "quantization": "angle-dependent-texture-mixture"}
        metadata["imageMapping"]["tone"] = None
        metadata["colorMapping"] = {"profile": "continuous-color-map.json",
                                     "viewingRotationDegrees": color_rotation,
                                     "modelStartDegrees": profile.model_start_deg,
                                     "modelEndDegrees": profile.model_end_deg,
                                     "bandingReduction": 1, "adaptiveMixing": 1, "preferDisDark": False}
    return metadata


def generate_iso(input_path: str | Path, output_path: str | Path, options: DvdOptions, *,
                 dither_strength: float = 0.75, workers: int | None = None, batch_blocks: int = 8,
                 progress: Callable[[int, int, float], None] | None = None,
                 data_dir: str | Path | None = None, file_position: str = "inner",
                 volume_label: str = "DVD_IMAGE", config_path: str | Path | None = None) -> dict:
    workers = workers or min(64, os.cpu_count() or 1)
    source = Path(input_path).absolute()
    output = Path(output_path).absolute()
    sidecar = Path(str(output) + ".json")
    configuration = Path(config_path).absolute() if config_path is not None else None
    layout = None
    if data_dir is not None:
        from udf import plan_directory
        layout = plan_directory(Path(data_dir).absolute(), options.total_sectors, file_position, volume_label)
    image = load_png(source)
    color_rotation = None
    if image.ndim == 3:
        from dvd_color import plan_viewing_angle
        color_rotation = plan_viewing_angle(image, options, layout)
        print(f"观看角度: {color_rotation:.1f}°", file=sys.stderr, flush=True)
        binary_image = False
    else:
        binary_image = not np.any((image != 0) & (image != 255))
    expected_bytes = options.total_sectors * SECTOR_BYTES
    geometry = DvdGeometry(options.inner_radius_mm, options.outer_radius_mm, options.track_length_mm,
                           options.pitch_linear, options.pitch_quadratic, options.pitch_cubic)
    # Initialize shared lookup tables before launching workers.
    payload_channel_centers()
    scrambler_masks()
    started = time.monotonic()
    temp_paths: list[Path] = []
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".partial", dir=output.parent)
        temp_iso = Path(temp_name)
        temp_paths.append(temp_iso)
        drawing_start = layout.drawing_start_lba if layout else 0
        drawing_end = layout.drawing_end_lba if layout else options.total_sectors
        start_block, end_block = drawing_start // SECTORS_PER_BLOCK, drawing_end // SECTORS_PER_BLOCK
        jobs = iter((i, min(batch_blocks, end_block - i)) for i in range(start_block, end_block, batch_blocks))
        with os.fdopen(fd, "wb", buffering=1024 * 1024) as stream, ThreadPoolExecutor(workers) as executor:
            if progress:
                progress(0, options.total_sectors, 0.0)
            if layout:
                layout.write_prefix(stream)
            if progress and drawing_start:
                progress(drawing_start, options.total_sectors, time.monotonic() - started)
            while batch := list(islice(jobs, workers * 2)):
                futures = [executor.submit(generate_payload, image, options, geometry, start, count,
                                           dither_strength=dither_strength, binary_image=binary_image,
                                           color_rotation=color_rotation)
                           for start, count in batch]
                for future in futures:
                    stream.write(future.result())
                    if progress:
                        progress(stream.tell() // SECTOR_BYTES, options.total_sectors, time.monotonic() - started)
            if layout:
                layout.write_suffix(stream)
                if progress:
                    progress(options.total_sectors, options.total_sectors, time.monotonic() - started)
            if stream.tell() != expected_bytes:
                raise OSError("输出长度与扇区数不一致。")
            stream.flush()
            os.fsync(stream.fileno())
        metadata = _metadata(source, output, options, geometry, time.monotonic() - started,
                             layout, configuration, dither_strength, color_rotation)
        fd, temp_name = tempfile.mkstemp(prefix=f".{sidecar.name}.", suffix=".partial", dir=output.parent)
        temp_json = Path(temp_name)
        temp_paths.append(temp_json)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_iso, output)
        os.replace(temp_json, sidecar)
        return metadata
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="生成 DVD 盘面绘图 ISO，或 8192x8192 可绘区域 PNG 蒙版。",
        epilog="容量和校准参数全部来自 JSON 配置文件；只处理本地文件，不访问设备。",
    )
    input_argument = parser.add_argument("-i", "--input", type=Path, help="输入 PNG，用于生成 ISO")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出 .iso 镜像或 .png 蒙版")
    parser.add_argument("-c", "--config", type=Path, required=True, help="盘片容量及物理校准参数 JSON")
    parser.add_argument("-f", "--files", type=Path, help="递归加入 ISO 的本地目录")
    parser.add_argument("-p", "--position", default="inner",
                        help="随盘文件位置：inner 内圈，outer 外圈")
    parser.add_argument("-l", "--volume-label", default="DVD_IMAGE",
                        help="ISO 卷标，默认 DVD_IMAGE（配合 --files 使用）")
    parser.add_argument("-d", "--dither-strength", type=float, default=0.75,
                        help="灰度 ISO 抖动强度，0–1，默认 0.75；0 关闭抖动，RGB 彩色不使用")
    parser.add_argument("-j", "--jobs", type=int,
                        help="ISO 并行线程数，1–64；默认按逻辑 CPU 数选择，最多 64")
    args = parser.parse_args(argv)
    if args.output.suffix.lower() == ".iso":
        # Enforce the input requirement after determining the output mode.
        input_argument.required = True
        args = parser.parse_args(argv)
    last_report = -math.inf

    def report(done: int, total: int, elapsed: float) -> None:
        nonlocal last_report
        if done != total and elapsed - last_report < 1:
            return
        last_report = elapsed
        speed = done * SECTOR_BYTES / max(elapsed, 1e-9) / 1e6
        eta = (total - done) / (done / elapsed) if done and elapsed else 0
        print(f"\r生成 {done / total:6.2%}  {done:,}/{total:,} 扇区  "
              f"{speed:.1f} MB/s  剩余约 {eta:.0f}s", end="", file=sys.stderr, flush=True)

    try:
        options = load_disc_config(args.config)
        if args.output.suffix.lower() == ".png":
            generate_mask(args.output, options, data_dir=args.files, file_position=args.position)
            print(f"完成: {args.output.absolute()}（8192x8192 黑白蒙版）", file=sys.stderr)
            return 0
        metadata = generate_iso(args.input, args.output, options, progress=report,
                                workers=args.jobs,
                                data_dir=args.files, file_position=args.position,
                                volume_label=args.volume_label,
                                config_path=args.config, dither_strength=args.dither_strength)
    except KeyboardInterrupt:
        print("\n已取消，临时文件已清理。", file=sys.stderr)
        return 130
    except (ValueError, OSError, OverflowError, TypeError) as exc:
        print(f"\n错误: {exc}", file=sys.stderr)
        return 1
    print(f"\n完成: {metadata['isoOutput']}\n"
          f"参数: {metadata['isoOutput']}.json\n"
          f"大小: {options.total_sectors * SECTOR_BYTES:,} 字节；"
          f"轨距常数项: {metadata['pitchPolynomial']['constant']:.9f} μm", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
