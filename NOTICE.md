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
- `src/DiscImageStudio.Iso/Iso9660ImageWriter.cs`

修改包括严格浮点的融合 C 采样内核、PNG 灰度最高位转换、JSON 物理参数配置、
Python 命令行、ISO9660/Joliet 文件系统与内外圈布局、
有界线程批次以及输出文件的临时写入与发布。
后续简化移除了输入参数、路径、容量及源文件变化的预检查，输入约定集中记录在 README。
本工具按 GPL-3.0-or-later 提供，许可证全文见 `LICENSE`；不提供担保。
不包含上游字体、界面、CD 交织代码或 .NET 运行时。

`dvd_kernel.dll` 由随附的 `dvd_kernel.c` 使用 MSVC x64、`/O2 /fp:strict /MT` 编译，
与 Python 代码一样按 GPL-3.0-or-later 提供；静态链接 Microsoft C 运行库，
运行时无需安装编译器。

NumPy 和 Pillow 是通过 requirements.txt 单独安装的依赖，
其许可证分别随各自的软件包提供。
