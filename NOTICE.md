
# 来源和许可证

本工具依据 DiscImageStudio 的 DVD `dispersion` 几何与扰码实现，
并参考 DiscDiffractStudio 的按圈复用与 RGB 载波。
当前实现、资源和随附 DLL 按源文件中的 GNU GPL v3 许可标识提供；许可证全文见 [LICENSE](LICENSE)。
来源、参考提交及移植记录集中保留在文档中；代码和数据资源不携带来源或配套版本元数据，
运行时始终假定二者相匹配。源文件保留必要的许可证与版权声明。

## DiscImageStudio

上游项目：https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio

几何、采样、DVD 数据位置及文件系统移植最初对照提交 `b86404fef9111d418b234449a6d5c7839ae03542`，
后续对照至 `619ec7505ac8955f35fe2f353ebda74b6ea90dda`。
原实现 Copyright (c) 2026 Disc Image Studio contributors。
Python/NumPy 几何与命令行移植始于 2026-11-07。

主要参考文件：

- `src/DiscImageStudio.Core/DvdTrackGeometry.cs`
- `src/DvdImageSolver/Solver/FastDispersionImageWriter.cs`
- `src/DvdImageSolver/Solver/RasterImage.cs`
- `src/DiscImageStudio.Iso/UdfImageWriter.cs`
- `DvdReadCompatibilityGuard.cs`、`DvdStreamingGenerator.cs`、`DiscParametersState.cs`

本项目移植为 Python/NumPy 几何求解和严格浮点 C 采样，保留最近邻、双线性插值及取整规则。
采样选择改为依据图片模式：1 位黑白使用最近邻，灰度与彩色各通道均使用双线性，不扫描像素内容；
双线性先插值原始 8 位通道，再按最近偶数规则取整；彩色随后才进行 sRGB 线性化。
默认载波、标准／加宽通道合并、自定义观看及周期补偿均采用这套规则，双线性路径的 Bayer 锚点使用向下取整坐标。
数组接口以二维 `bool` 表示黑白、二维 `uint8` 表示灰度，黑白数组展开为 0/255。
同时提供 JSON 配置、几何总容量与输出可用容量分离、UDF 2.01 文件系统、内外圈文件布局及有限批次并行输出。
UDF 写入器复用原项目蓝光路径中的介质无关结构。

完整通道反馈求解会在候选搜索中反复重建 ECC 并执行 EFMPlus／NRZI 编码，计算量大；当前不保留该实现，仅提供载波直接绘图。

`periodic-9b-16-v1` 读取保护沿用每 16 字节的规则，不包含旧版 bridge 算法，
并扩展为所有 ISO 绘图模式的统一可选处理，默认关闭。
偏移 0、16、…、2032 处的目标扰码纹理值 Q 非 `A5/9B` 时替换为 `9B`，再补偿扰码，保持输出长度。
字节替换统一在载波编码后处理。
文件和 UDF 元数据不经过此处理，PNG 蒙版和参数 JSON 不应用此字节处理；开启后会影响纹理、颜色与视角表现。

输入参数、路径、图片尺寸及原生数组布局由调用者保证，约定记录在 README；
命令行保留类型、可选值及必填声明，保留 UDF 编码、字段和布局完整性检查、数值收敛保护及 I/O 错误处理。
不包含原项目的字体、界面、CD 交织代码或 .NET 运行时。

## DiscDiffractStudio

2026-11-16 起参考本地 DiscDiffractStudio，提交 `439c876d71d977a86cc8430dd1e141e6bf388449`。
该项目使用 GPL-3.0-or-later；资源及独立移植文件按各自许可标识提供，合并 C 文件中的相关代码依 GPL v3 使用。

主要参考：

- `dvdgrating/multiview.py`：轨道分配、32 级位反转门限、4×4 有序抖动及字节串解析。
- `dvdgrating/presets.py`：灰度动画周期、colorflip 的 RGB 载波及白平衡，以及 `_color` 的通道合并、三元组白平衡与排程；`dvdgrating/server.py` 的 `WIDE` 提供宽九载波参数。
- `dvdgrating/viewing.py`：灯、眼、姿态的观看几何与光谱响应。
- `dvdgrating/strings.py`：同步帧重启、填充及尾部裁剪。
- `dvdgrating/presets.py` 的 `stretch_map`、`chirped`、`variant_period`：逐位置周期补偿、量化及共享周期网格。
- `dvdgrating/synth.py`、`dvdgrating/tables.py`：确定性 EFMPlus 字节串的束搜索合成、状态转换及 NRZI 波形。

`dvd_carriers.py` 移植上述动态合成和光谱计算；相同请求周期只在进程内缓存，不写磁盘载波缓存。
`dvd_chirp.py` 依据盘面位置及各视图的灯眼姿态计算周期拉伸，采用 1.005 步长和共享周期网格准备变体图。
周期补偿默认关闭，只有观看 JSON 明确设置 `chirp:true` 时启用；变体准备采用单行刷新进度并显示预计剩余时间。
名义周期保留精确值，其他变体采用源项目 `shared=True` 的吸附策略；仅处理记录环中实际使用的变体，
可写周期约为 6.4–22T。新合成的字节串、裁剪表和本次位置索引只保留在进程内存，不写磁盘缓存。
同一载波组的连续亮段在段首填充后的第一个实际字节处选择变体，并保持到组切换或同步帧边界。
`efmplus-tables.json` 从该提交 `data/efmplus_tables.json` 提取 EFMPlus 调制资源，
仅保留 ECMA-267 Annex G / Table 4 的 256×4 主编码表中的码字与后继状态，供动态载波合成使用。来源与参考提交仅记录于本文档。
自定义载波的光响应使用源项目的 LED 光谱、CIE XYZ 拟合和 0.01 角接受宽度，
灰度按亮度响应、RGB 按 D65 白点求所需轨道份额，不引入随机数据通道链模拟。

`multiview-carriers.json` 从该项目 `data/string_carriers.json` 提取所需字节串子集，
保留源字节串和裁剪规则，并包含当前单图、多图与混排方案的周期和白平衡数据。
两张彩色图沿用 colorflip 的名义周期和六载波白平衡；
单张彩色图采用其第一组 RGB 三周期，独立计算 32 圈排程与增益。
默认其他混排及多张完整 RGB 图为本项目扩展，每张彩色图保留三个通道。
可选 `--color-range`／`color_range` 同时启用通道合并并选择范围，沿用源彩色动画的 `floor(k*N/C)` 输入选取及 `RGB[k%3]` 通道分配，
每三载波组成一个光学视图。不指定命令行参数或 Python 使用默认 `None` 时不合并，接受 1–8 张输入。
命令行指定该选项时必须提供 `standard` 或 `wide`，不设置缺省范围。`standard`：1–6 张使用 16T 到 8T 的六载波及 32 圈；
`wide`：1–9 张使用 20.4T 到 8T 的宽九载波、525 nm 绿色目标及 48 圈。范围不随输入数量自动切换。
源图索引与光学视图分离，自定义观看、白平衡和周期补偿按标准的两个三元组或加宽的三个三元组计算。
源项目将灰度素材转 RGB 并线性化；本项目合并时仍按原始格式采样，灰度保持 `g/255`，彩色先对所选通道双线性插值并取整，再线性化。

所有灰度保留基础比例 `g/255`。未合并的默认全灰度载波增益为 `min(periods)/period_i`，单张自然为 1；
默认含彩色时，所有平面根据 `whiteNeed` 和实际圈份额求增益并归一化。
自定义观看参数时，依据参考点的实际 `u` 合成字符串载波，重新计算各平面的光谱响应及增益；
未合并单张灰度自然为 1，含 RGB 或启用通道合并时重新求解白平衡和轨道份额。
本项目另提供 `--no-gain`／`no_gain=True`，在建立亮度查表前将所有灰度及 RGB 载波的增益统一为 1，默认关闭。
该选项同样适用于通道合并、自定义观看和周期补偿；它保留原有载波、周期、白平衡圈份额和交错排程，只取消点亮比例的增益系数。
因此它不会恢复多图分配前的单图全圈亮度，彩色也不再应用原来的剩余增益补偿。
ISO 参数 JSON 的 `noGain` 记录此开关，逐载波 `gain` 记录实际采用的值；PNG 蒙版忽略该选项。
单图与多图统一使用按圈门限及有序抖动，不使用原字节级抖动强度参数。
原始 8 位通道配合亮度查表，不裁剪或缩放，也不识别共有区域或按局部内容重新分配轨道。

本项目将每帧映射到完整盘心画布，复用 JSON 几何和 UDF 布局。
字符串载波可选逐位置周期补偿，不指定观看参数或省略 `chirp` 时均保持固定名义周期；默认单张、两张全灰度的 `92/B9` 单字节载波不补偿。
实体几何使用实际灯眼位置；直接指定 `target_u` 时采用在参考点准确实现该 `u` 的对称规范姿态，
其构造和固定的 500 mm／1000 mm 参考距离见 README。支持参考点的灯、眼位置与观看姿态参数，
不包含参考项目的界面、视频导入、深度推断或刻录功能。

## 原生库与其他依赖

`dvd_kernel.c` 已合并轨道几何、采样与载波编码，不包含其他 `.c` 文件。
合并文件采用 GPL-3.0-only：原基础代码的 GPL v3 许可保持不变，DiscDiffractStudio 中按 GPL-3.0-or-later 提供的部分依 GPL v3 使用，来源归属保留。
`dvd_kernel.dll` 由该单一源码使用 MSVC x64、`/O2 /fp:strict /MT` 编译，静态链接 Microsoft C 运行库，运行时无需安装编译器。
Python 中的 `dvd_disc.py` 集中盘片配置类型、DVD 常量、扰码表和 `OutputPlan`，ISO 与蒙版复用同一几何及 UDF/可用容量计划。
`dvd_kernel.py` 统一封装 ctypes、DLL 及其两个导出：`dvd_generate`、`dvd_resolve_sector`。
`PreparedKernel` 每任务一次打包原生指针并保持相关数组存活，各批次复用该对象，不全局缓存每个几何对象。无需源项目或 .NET 运行时。
原生载波 token 使用 16 位组／变体编号，连续段按载波组划分；Python 扇区接口继续接收载波编号与可选变体数组。
`dvd_multiview.py` 的数组序列和文件序列入口共用一个构建流程，负责图片、观看几何、载波、增益、排程、可选通道合并及周期补偿准备；未合并的单张 `uint8` 灰度保留零拷贝视图。
业务模块不依赖 ctypes 或内核模块，也不保留原生包装兼容别名。`dvd_image.py` 负责命令行、配置读取、输出调度、读取保护和参数 JSON，经原生会话生成批次；默认名义载波与增益保持，可选统一增益为 1，字符串方案可选位置补偿。

历史版本曾参考 [DiscColorStudio](https://github.com/Neo-626/DiscColorStudio) 的实测彩色和周期梯实现；
这些模块及其色表、模型、独立 DLL 现已移除，当前不依赖该项目的运行资源。

NumPy 和 Pillow 通过 requirements.txt 单独安装，其许可证随各自软件包提供。
