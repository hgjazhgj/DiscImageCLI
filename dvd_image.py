#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Disc Image Studio contributors.
"""Coordinate image preparation, native batches and ISO or drawable-mask output."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from itertools import islice
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Callable, Sequence

import numpy as np
from PIL import Image

from dvd_disc import (
    CHANNEL_BITS_PER_SECTOR, SECTOR_BYTES, SECTORS_PER_BLOCK,
    DvdOptions, plan_output, scrambler_masks,
)
from dvd_kernel import PreparedKernel
from dvd_multiview import prepare_multiview


def generate_mask(output_path: str | Path, options: DvdOptions, *,
                  data_dir: str | Path | None = None, file_position: str = "inner",
                  bleed: float = 1.0) -> None:
    """Draw the same planned physical interval used by ISO generation."""
    plan = plan_output(options, data_dir=data_dir, file_position=file_position, bleed=bleed)
    arcs = (np.array([plan.drawing_start_lba, plan.drawing_end_lba], dtype=np.float64)
            * CHANNEL_BITS_PER_SECTOR * (options.channel_bit_nm * 1e-6))
    inner, outer = plan.geometry.positions(arcs)[0]
    axis = (np.arange(8192, dtype=np.float64) - 4095.5) * (2 * options.outer_radius_mm / 8191)
    x_squared = axis * axis
    pixels = np.empty((8192, 8192), dtype=np.bool_)
    for row, y in enumerate(axis):
        distance_squared = x_squared + y * y
        pixels[row] = (distance_squared >= inner * inner) & (distance_squared < outer * outer)
    with Image.fromarray(pixels) as mask:
        mask.save(output_path)


def generate_iso(input_path: str | Path | Sequence[str | Path], output_path: str | Path, options: DvdOptions, *,
                 workers: int | None = None, batch_blocks: int = 8,
                 progress: Callable[[int, int, float], None] | None = None,
                 data_dir: str | Path | None = None, file_position: str = "inner",
                 volume_label: str = "DVD_IMAGE",
                 read_compatibility: bool = False, bleed: float = 1.0, viewing: dict | None = None,
                 color_range: str | None = None, no_gain: bool = False) -> dict:
    """Generate carrier drawings and optional UDF files as an ISO image."""
    workers = workers or min(64, os.cpu_count() or 1)
    sources = [input_path] if isinstance(input_path, (str, Path)) else list(input_path)
    sources = [Path(path).absolute() for path in sources]
    output = Path(output_path).absolute()
    sidecar = Path(str(output) + ".json")
    plan = plan_output(options, data_dir=data_dir, file_position=file_position,
                       volume_label=volume_label, bleed=bleed)
    print(f"准备图像: {len(sources)} 张", file=sys.stderr, flush=True)
    profile = prepare_multiview(sources, viewing=viewing,
                                channel_bit_nm=options.channel_bit_nm,
                                radii_mm=(options.inner_radius_mm, options.outer_radius_mm),
                                color_range=color_range, no_gain=no_gain)
    print("输入模式: " + " / ".join({"color": "彩色", "grayscale": "灰度", "monochrome": "黑白"}[mode]
                                     for mode in profile.source_modes),
          file=sys.stderr, flush=True)
    kernel = PreparedKernel(options, plan.geometry, profile)
    started = time.monotonic()
    temp_paths: list[Path] = []
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".partial", dir=output.parent)
        temp_iso = Path(temp_name)
        temp_paths.append(temp_iso)
        start_block = plan.drawing_start_lba // SECTORS_PER_BLOCK
        end_block = plan.drawing_end_lba // SECTORS_PER_BLOCK
        jobs = iter((i, min(batch_blocks, end_block - i)) for i in range(start_block, end_block, batch_blocks))
        with os.fdopen(fd, "wb", buffering=1024 * 1024) as stream, ThreadPoolExecutor(workers) as executor:
            if progress:
                progress(0, plan.output_sectors, 0.0)
            plan.write_prefix(stream)
            if progress and plan.drawing_start_lba:
                progress(plan.drawing_start_lba, plan.output_sectors, time.monotonic() - started)
            while batch := list(islice(jobs, workers * 2)):
                futures = [executor.submit(kernel.generate_payload, start, count)
                           for start, count in batch]
                for (start, count), future in zip(batch, futures):
                    payload = future.result()
                    if read_compatibility:
                        guarded = np.frombuffer(payload, dtype=np.uint8).copy().reshape(
                            count, SECTORS_PER_BLOCK, SECTOR_BYTES)
                        blocks = np.arange(start, start + count, dtype=np.int64)
                        masks = scrambler_masks()[((blocks * SECTORS_PER_BLOCK + options.psn_offset) >> 4) & 15,
                                                  None, ::16]
                        selected = guarded[:, :, ::16]
                        q = selected ^ masks
                        selected[:] = np.where((q == 0xA5) | (q == 0x9B), selected, 0x9B ^ masks)
                        payload = guarded.tobytes()
                    stream.write(payload)
                    if progress:
                        progress(stream.tell() // SECTOR_BYTES, plan.output_sectors, time.monotonic() - started)
            plan.write_suffix(stream)
            if progress and plan.udf:
                progress(plan.output_sectors, plan.output_sectors, time.monotonic() - started)
            if stream.tell() != plan.output_sectors * SECTOR_BYTES:
                raise OSError("输出长度与扇区数不一致。")
            stream.flush()
            os.fsync(stream.fileno())
        layout = plan.udf
        metadata = {
            "isoOutput": str(output),
            "disc": asdict(options),
            "outputSectors": plan.output_sectors,
            "drawingStartLba": plan.drawing_start_lba,
            "drawingEndLbaExclusive": plan.drawing_end_lba,
            "trackPitchUm": plan.geometry.track_pitch_mm * 1000,
            "bleed": plan.bleed,
            "readCompatibility": read_compatibility,
            "noGain": no_gain,
            "images": [{"path": path, "mode": mode}
                       for path, mode in zip(profile.source_paths, profile.source_modes)],
            "carriers": [
                {"image": frame, "channel": channel, "periodT": period,
                 "gain": gain, "turnCount": int(count),
                 "sampling": ("color-bilinear", "gray-binary-nearest", "gray-bilinear")[int(mode)]}
                for frame, channel, period, gain, count, mode in zip(
                    profile.carrier_frames, profile.channels, profile.periods, profile.gains,
                    profile.counts, profile.sampling_modes)
            ],
            "targetU": list(profile.target_u),
            "files": {
                "directory": str(layout.source_directory), "position": plan.file_position,
                "volumeLabel": layout.volume_label, "timestamp": layout.timestamp.isoformat(),
                "fileCount": layout.file_count, "directoryCount": layout.directory_count,
                "fileBytes": layout.file_bytes,
                "partitionStartLba": layout.partition_start_lba,
                "partitionSectors": layout.partition_sectors,
                "fileDataStartLba": layout.file_data_start_lba,
                "dataEndLbaExclusive": layout.data_end_lba,
            } if layout else None,
            "elapsedSeconds": time.monotonic() - started,
        }
        metadata["channelMerge"] = profile.channel_merge
        if profile.channel_merge:
            metadata["colorRange"] = color_range
        if viewing is not None:
            metadata["viewing"] = viewing
        for plane, carrier in enumerate(metadata["carriers"]):
            if profile.channel_merge:
                carrier["view"] = plane // 3
            if profile.requested_periods is not None:
                carrier["requestedPeriodT"] = profile.requested_periods[plane]
        if profile.viewing_poses is not None:
            metadata["chirp"] = {
                "cellMm": 2 * options.outer_radius_mm / profile.variant_map.shape[2],
                "periodStep": 1.005, "periodLimitsT": [6.4, 22.0],
                "views": [{"eyeMm": eye.tolist(), "lampMm": lamp.tolist()}
                          for eye, lamp in profile.viewing_poses],
            }
            for plane, carrier in enumerate(metadata["carriers"]):
                first = profile.variant_offsets[plane]
                end = (profile.variant_offsets[plane + 1] if plane + 1 < len(profile.variant_offsets)
                       else len(profile.variant_periods))
                variants = profile.variant_periods[first:end]
                carrier["variantCount"] = len(variants)
                carrier["requestedVariantPeriodTRange"] = [float(variants.min()), float(variants.max())]
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
        description="将单图或多张角度切换图片生成 DVD ISO，或生成可绘区域 PNG 蒙版。",
        epilog="盘片几何和校准参数来自 JSON，可用比例由 --bleed 指定；只处理本地文件，不访问设备。",
    )
    parser.add_argument("-i", "--input", type=Path, nargs="+", action="extend",
                        help="输入 PNG；灰度统一按圈抖动，多图按顺序切换，可混排灰度和彩色；可重复 -i；蒙版忽略")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出 .iso 镜像或 .png 蒙版")
    parser.add_argument("-c", "--config", type=Path, required=True, help="盘片容量及物理校准参数 JSON")
    parser.add_argument("-f", "--files", type=Path, help="递归加入 ISO 的本地目录")
    parser.add_argument("-p", "--position", choices=("inner", "outer"), default="inner",
                        help="随盘文件位置：inner 内圈（默认），outer 外圈；无 --files 时忽略")
    parser.add_argument("-b", "--bleed", type=float, default=1.0,
                        help="可用容量比例，0 < b <= 1，默认 1 全盘可用；按 16 扇区向下对齐，ISO 和蒙版均生效")
    parser.add_argument("-l", "--volume-label", default="DVD_IMAGE",
                        help="ISO 卷标，默认 DVD_IMAGE（配合 --files 使用）")
    parser.add_argument("-j", "--jobs", type=int,
                        help="ISO 并行线程数，1–64；默认按 CPU 选择，最多 64；蒙版忽略")
    parser.add_argument("--read-compatibility", action="store_true",
                        help="ISO 绘图统一替换规则：每 16 字节按规则替换为 9B；蒙版忽略")
    parser.add_argument("--no-gain", action="store_true",
                        help="所有灰度和彩色载波增益固定为 1，保持原圈份额和排程；蒙版忽略")
    parser.add_argument("--viewing", type=Path,
                        help="观看 JSON：灯/眼位置、姿态或 target_u；周期补偿默认关闭，chirp:true 可开启；蒙版忽略")
    parser.add_argument("--color-range", choices=("standard", "wide"),
                        help="启用通道合并，必须指定 standard 标准六载波或 wide 加宽九载波；省略此选项不合并；蒙版忽略")
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
        with args.config.open(encoding="utf-8-sig") as stream:
            config = json.load(stream)
        if isinstance(config.get("psn_offset"), str):
            config["psn_offset"] = int(config["psn_offset"], 0)
        options = DvdOptions(**config)
        if args.output.suffix.lower() == ".png":
            generate_mask(args.output, options, data_dir=args.files, file_position=args.position,
                          bleed=args.bleed)
            print(f"完成: {args.output.absolute()}（8192x8192 黑白蒙版）", file=sys.stderr)
            return 0
        viewing = None
        if args.viewing is not None:
            with args.viewing.open(encoding="utf-8-sig") as stream:
                viewing = json.load(stream)
        metadata = generate_iso(args.input, args.output, options, progress=report,
                                workers=args.jobs,
                                data_dir=args.files, file_position=args.position,
                                volume_label=args.volume_label,
                                read_compatibility=args.read_compatibility, bleed=args.bleed, viewing=viewing,
                                color_range=args.color_range, no_gain=args.no_gain)
    except KeyboardInterrupt:
        print("\n已取消，临时文件已清理。", file=sys.stderr)
        return 130
    except (ValueError, OSError, OverflowError, TypeError) as exc:
        print(f"\n错误: {exc}", file=sys.stderr)
        return 1
    print(f"\n完成: {metadata['isoOutput']}\n"
          f"参数: {metadata['isoOutput']}.json\n"
          f"大小: {metadata['outputSectors'] * SECTOR_BYTES:,} 字节；"
          f"轨距常数项: {metadata['trackPitchUm']:.9f} μm", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
