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
                     binary_image: bool | None = None, color_rotation: float | None = None,
                     read_compatibility: bool = False, color_mode: str = "measured",
                     ladder_profile=None) -> bytes:
    """Generate complete ECC blocks of cooked user data; safe for concurrent calls."""
    if image.ndim == 3:
        if color_mode == "ladder":
            from dvd_ladder import prepare_ladder, generate_payload as generate_ladder_payload
            profile = ladder_profile if ladder_profile is not None else prepare_ladder(image, options)
            return generate_ladder_payload(image, options, geometry, start_block, block_count,
                                           payload_channel_centers(), scrambler_masks(), profile)
        from dvd_color import load_color_profile, plan_viewing_angle
        profile = load_color_profile()
        if color_rotation is None:
            color_rotation = plan_viewing_angle(image, options, profile=profile)
        return _generate_color_payload(image, options, geometry, start_block, block_count,
                                       payload_channel_centers(), scrambler_masks(), profile, color_rotation)
    if binary_image is None:
        binary_image = not np.any((image != 0) & (image != 255))
    payload = _generate_payload(image, options, geometry, start_block, block_count,
                                payload_channel_centers(), scrambler_masks(), binary_image, dither_strength)
    if read_compatibility:
        # Only this drawing payload is owned. Files and filesystem extents never
        # pass through this guard. Work in Q (after the drive's scrambling).
        guarded = np.frombuffer(payload, dtype=np.uint8).copy().reshape(block_count, 16, 2048)
        blocks = np.arange(start_block, start_block + block_count, dtype=np.int64)
        masks = scrambler_masks()[((blocks * 16 + options.psn_offset) >> 4) & 15, None, ::16]
        selected = guarded[:, :, ::16]
        q = selected ^ masks
        selected[:] = np.where((q == 0xA5) | (q == 0x9B), selected, 0x9B ^ masks)
        return guarded.tobytes()
    return payload


def _output_sectors(options, bleed=1.0):
    return int(options.total_sectors * bleed) // SECTORS_PER_BLOCK * SECTORS_PER_BLOCK


def generate_mask(output_path: str | Path, options: DvdOptions, *,
                  data_dir: str | Path | None = None, file_position: str = "inner",
                  bleed: float = 1.0) -> None:
    """Write a binary annulus using the ISO layout's drawable LBA interval."""
    output_sectors = _output_sectors(options, bleed)
    start, end = 0, output_sectors
    if data_dir is not None:
        from udf import plan_directory
        layout = plan_directory(Path(data_dir).absolute(), output_sectors, file_position)
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
              color_rotation: float | None = None, output_sectors: int | None = None,
              read_compatibility: bool = False, file_position: str = "inner",
              ladder_profile=None, bleed: float = 1.0) -> dict:
    output_sectors = _output_sectors(options, bleed) if output_sectors is None else output_sectors
    pitch_um = geometry.track_pitch_mm * 1000
    metadata = {
        "format": "dvd-nrzi-disc-fill-v2",
        "generator": "DiscImageBurn Python 1.8",
        "sourceImage": str(input_path), "isoOutput": str(output_path),
        "imagePreprocessing": {"grayscale": "Pillow L (0.299R+0.587G+0.114B)",
                               "alpha": "ignored", "operation": "preserve-gray8",
                               "sampling": "nearest-if-binary-otherwise-bilinear",
                               "quantization": "per-payload-byte-dither"},
        "configPath": str(config_path) if config_path else None,
        "startLba": layout.drawing_start_lba if layout else 0,
        "fillSectors": layout.drawing_end_lba - layout.drawing_start_lba if layout else output_sectors,
        "filesAtOuterRing": layout.files_at_outer_ring if layout else False,
        "totalSectors": options.total_sectors, "psnOffset": f"0x{options.psn_offset:X}",
        "bleed": bleed, "outputSectors": output_sectors, "filePosition": file_position,
        "readCompatibility": {"enabled": read_compatibility,
                              "mode": "periodic-9b-16-v1" if read_compatibility else None},
        "upstreamReferences": {"DiscImageStudio": "619ec7505ac8955f35fe2f353ebda74b6ea90dda",
                               "DiscColorStudio": "ef44b13694e9873fc47ea9e0040ec5af502995ae"},
        "algorithm": "dispersion", "fastOutput": True, "exactEccEfmReplay": False,
        "elapsedSeconds": elapsed,
        "imageMapping": {
            "totalSectors": options.total_sectors,
            "outputSectors": output_sectors,
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
                "imageSectors": output_sectors, "mountableFilesystem": layout is not None,
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
        metadata["colorMapping"] = {"mode": "measured", "profile": "continuous-color-map.json",
                                     "viewingRotationDegrees": color_rotation,
                                     "modelStartDegrees": profile.model_start_deg,
                                     "modelEndDegrees": profile.model_end_deg,
                                     "bandingReduction": 1, "adaptiveMixing": 1, "preferDisDark": False}
    if ladder_profile is not None:
        metadata["imagePreprocessing"] = {"color": "RGB8", "alpha": "ignored",
            "operation": "preserve-rgb", "sampling": "floor-nearest-recipe-grid",
            "quantization": "band-ladder-carrier-mixture"}
        metadata["imageMapping"]["tone"] = None
        metadata["colorMapping"] = {"mode": "ladder", "profile": "ladder-model.json",
            "modelVersion": ladder_profile.model.data["version"],
            "recipeGrid": ladder_profile.grid, "discRotationDegrees": ladder_profile.disc_rotation_deg,
            "observerAzimuthDegrees": ladder_profile.observer_azimuth_deg,
            "viewBalance": ladder_profile.view_balance, "carrierCount": 30,
            "cycleTurns": ladder_profile.model.native.slots, "frameSeamRepair": True}
    return metadata


def generate_iso(input_path: str | Path, output_path: str | Path, options: DvdOptions, *,
                 dither_strength: float = 0.75, workers: int | None = None, batch_blocks: int = 8,
                 progress: Callable[[int, int, float], None] | None = None,
                 data_dir: str | Path | None = None, file_position: str = "inner",
                 volume_label: str = "DVD_IMAGE", config_path: str | Path | None = None,
                 read_compatibility: bool = False,
                 color_mode: str = "measured", ladder_grid: int = 1024,
                 disc_rotation_deg: float = 0.0, observer_azimuth_deg: float = 180.0,
                 view_balance: float = 1.0, bleed: float = 1.0) -> dict:
    workers = workers or min(64, os.cpu_count() or 1)
    source = Path(input_path).absolute()
    output = Path(output_path).absolute()
    sidecar = Path(str(output) + ".json")
    configuration = Path(config_path).absolute() if config_path is not None else None
    layout = None
    file_position = file_position if data_dir is not None else "inner"
    output_sectors = _output_sectors(options, bleed)
    if data_dir is not None:
        from udf import plan_directory
        layout = plan_directory(Path(data_dir).absolute(), output_sectors, file_position, volume_label)
    image = load_png(source)
    read_compatibility = read_compatibility and image.ndim == 2
    color_rotation = None
    ladder_profile = None
    if image.ndim == 3:
        if color_mode == "ladder":
            from dvd_ladder import prepare_ladder
            print(f"计算周期梯配方: {ladder_grid} × {ladder_grid}", file=sys.stderr, flush=True)
            ladder_profile = prepare_ladder(image, options, grid=ladder_grid,
                disc_rotation_deg=disc_rotation_deg, observer_azimuth_deg=observer_azimuth_deg,
                view_balance=view_balance, workers=min(8, workers))
        else:
            from dvd_color import plan_viewing_angle
            color_rotation = plan_viewing_angle(image, options, layout, output_sectors=output_sectors)
            print(f"观看角度: {color_rotation:.1f}°", file=sys.stderr, flush=True)
        binary_image = False
    else:
        binary_image = not np.any((image != 0) & (image != 255))
    expected_bytes = output_sectors * SECTOR_BYTES
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
        drawing_end = layout.drawing_end_lba if layout else output_sectors
        start_block, end_block = drawing_start // SECTORS_PER_BLOCK, drawing_end // SECTORS_PER_BLOCK
        jobs = iter((i, min(batch_blocks, end_block - i)) for i in range(start_block, end_block, batch_blocks))
        with os.fdopen(fd, "wb", buffering=1024 * 1024) as stream, ThreadPoolExecutor(workers) as executor:
            if progress:
                progress(0, output_sectors, 0.0)
            if layout:
                layout.write_prefix(stream)
            if progress and drawing_start:
                progress(drawing_start, output_sectors, time.monotonic() - started)
            while batch := list(islice(jobs, workers * 2)):
                futures = [executor.submit(generate_payload, image, options, geometry, start, count,
                                           dither_strength=dither_strength, binary_image=binary_image,
                                           color_rotation=color_rotation, read_compatibility=read_compatibility,
                                           color_mode=color_mode, ladder_profile=ladder_profile)
                           for start, count in batch]
                for future in futures:
                    stream.write(future.result())
                    if progress:
                        progress(stream.tell() // SECTOR_BYTES, output_sectors, time.monotonic() - started)
            if layout:
                layout.write_suffix(stream)
                if progress:
                    progress(output_sectors, output_sectors, time.monotonic() - started)
            if stream.tell() != expected_bytes:
                raise OSError("输出长度与扇区数不一致。")
            stream.flush()
            os.fsync(stream.fileno())
        metadata = _metadata(source, output, options, geometry, time.monotonic() - started,
                             layout, configuration, dither_strength, color_rotation,
                             output_sectors, read_compatibility, file_position, ladder_profile, bleed)
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
        epilog="盘片几何和校准参数来自 JSON，可用比例由 --bleed 指定；只处理本地文件，不访问设备。",
    )
    parser.add_argument("-i", "--input", type=Path, help="输入 PNG，生成 ISO 时提供；PNG 蒙版忽略")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出 .iso 镜像或 .png 蒙版")
    parser.add_argument("-c", "--config", type=Path, required=True, help="盘片容量及物理校准参数 JSON")
    parser.add_argument("-f", "--files", type=Path, help="递归加入 ISO 的本地目录")
    parser.add_argument("-p", "--position", choices=("inner", "outer"), default="inner",
                        help="随盘文件位置：inner 内圈（默认），outer 外圈；无 --files 时忽略")
    parser.add_argument("-b", "--bleed", type=float, default=1.0,
                        help="可用容量比例，0 < b <= 1，默认 1 全盘可用；按 16 扇区向下对齐，ISO 和蒙版均生效")
    parser.add_argument("-l", "--volume-label", default="DVD_IMAGE",
                        help="ISO 卷标，默认 DVD_IMAGE（配合 --files 使用）")
    parser.add_argument("-d", "--dither-strength", type=float, default=0.75,
                        help="灰度 ISO 抖动强度，0–1，默认 0.75；0 关闭抖动，RGB 彩色不使用")
    parser.add_argument("-j", "--jobs", type=int,
                        help="ISO 并行线程数，1–64；默认按逻辑 CPU 数选择，最多 64")
    parser.add_argument("--read-compatibility", action="store_true",
                        help="实验性灰度 DVD 读取兼容性：每 16 字节插入 9B，可能改变纹理及对比度；彩色和蒙版忽略")
    parser.add_argument("--color-mode", choices=("measured", "ladder"), default="measured",
                        help="彩色方案：measured 实测色表（默认）；ladder 周期梯，以亮度和色准换取观看范围；灰度和蒙版忽略")
    parser.add_argument("--ladder-grid", type=int, default=1024,
                        help="周期梯配方网格边长，16–8192，默认 1024；越大越耗时和内存，其他模式忽略")
    parser.add_argument("--disc-rotation", type=float, default=0.0,
                        help="周期梯观察时盘面顺时针旋转角度，0–<360°，默认 0；不修改校准起始角")
    parser.add_argument("--observer-azimuth", type=float, default=180.0,
                        help="周期梯从图片指向观察者的方向，右0°、下90°、左180°、上270°，默认180")
    parser.add_argument("--view-balance", type=float, default=1.0,
                        help="周期梯邻近视角权重，0–1，默认1；0更重视中心视角")
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
            generate_mask(args.output, options, data_dir=args.files, file_position=args.position,
                          bleed=args.bleed)
            print(f"完成: {args.output.absolute()}（8192x8192 黑白蒙版）", file=sys.stderr)
            return 0
        metadata = generate_iso(args.input, args.output, options, progress=report,
                                workers=args.jobs,
                                data_dir=args.files, file_position=args.position,
                                volume_label=args.volume_label,
                                config_path=args.config, dither_strength=args.dither_strength,
                                read_compatibility=args.read_compatibility,
                                color_mode=args.color_mode, ladder_grid=args.ladder_grid,
                                disc_rotation_deg=args.disc_rotation, observer_azimuth_deg=args.observer_azimuth,
                                view_balance=args.view_balance, bleed=args.bleed)
    except KeyboardInterrupt:
        print("\n已取消，临时文件已清理。", file=sys.stderr)
        return 130
    except (ValueError, OSError, OverflowError, TypeError) as exc:
        print(f"\n错误: {exc}", file=sys.stderr)
        return 1
    print(f"\n完成: {metadata['isoOutput']}\n"
          f"参数: {metadata['isoOutput']}.json\n"
          f"大小: {metadata['outputSectors'] * SECTOR_BYTES:,} 字节；"
          f"轨距常数项: {metadata['pitchPolynomial']['constant']:.9f} μm", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
