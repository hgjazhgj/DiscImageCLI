# 来源和许可证

本工具是 Disc Image Studio DVD `dispersion` 算法的 Python 移植版，
于 2026-11-07 编写。原实现 Copyright (c) 2026 Disc Image Studio contributors。

上游项目：https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio

本次对照的上游提交：`b86404fef9111d418b234449a6d5c7839ae03542`。

主要参考和移植的源文件：

- `src/DiscImageStudio.Core/DvdTrackGeometry.cs`
- `src/DvdImageSolver/Solver/FastDispersionImageWriter.cs`
- `src/DvdImageSolver/Encoding/DvdEccBlockEncoder.cs`
- `src/DvdImageSolver/Encoding/DvdDataFrameBuilder.cs`
- `src/DvdImageSolver/Solver/DvdGrayscalePalette.cs`
- `src/DiscImageStudio.Iso/UdfImageWriter.cs`

修改包括严格浮点的融合 C 采样内核、PNG 灰度保留、轨道插值与码字级抖动、JSON 参数配置、
Python 命令行、UDF 2.01 文件系统与内外圈布局（复用原项目蓝光路径中的介质无关写入器结构）、
有界线程批次以及输出文件的临时写入与发布。
后续简化移除了输入参数、路径、容量及源文件变化的预检查，输入约定集中记录在 README。
本工具按 GNU GPL v3 提供，许可证全文见 `LICENSE`；不提供担保。
不包含上游字体、界面、CD 交织代码或 .NET 运行时。

彩色编码移植自 Disc Color Studio：https://github.com/Neo-626/DiscColorStudio

本次参考提交：`a34b7995e2734be6ba368291bd87d00a0e3ec2ff`。
主要参考 `FastRecipes.cs`、`ContinuousColorMap.cs`、`AngleColorEngine.cs` 和 `DvdImageEncoder.cs`。
随附的 `continuous-color-map.json`、`continuous-color-lut.bin.gz` 来自该项目。
彩色实现及其数值色表按原项目 GNU GPL v3 许可提供；本工具许可证全文见 `LICENSE`。
仅使用 CPU 编码和自动观看角度规划，不包含原项目的界面、GPU 预览或演示素材。

`dvd_kernel.dll` 由随附的 `dvd_kernel.c` 使用 MSVC x64、`/O2 /fp:strict /MT` 编译，
按 GNU GPL v3 提供；静态链接 Microsoft C 运行库，
运行时无需安装编译器。

NumPy 和 Pillow 是通过 requirements.txt 单独安装的依赖，
其许可证分别随各自的软件包提供。
