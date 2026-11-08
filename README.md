# DVD 图片转 ISO

将 **8192 × 8192 PNG** 转为 DVD 盘面绘图 ISO，可同时加入目录中的文件。
也可根据盘片配置和文件布局生成 **8192 × 8192 黑白可绘区域蒙版**。
读取后转换为 8 位灰度并取最高位，接受彩色、连续灰度和带透明度的图片。
采用 Disc Image Studio 的 DVD dispersion 算法，容量及物理参数全部来自 JSON 配置。

程序完全脱机，只读取本地图片、配置和随盘文件，输出 ISO 及参数 JSON，或 PNG 蒙版。
不联网、不查询或打开光驱、不读取盘片容量、不执行刻录。
不需要安装 Disc Image Studio、.NET 或外部 ISO 制作工具。

程序假定输入始终正确，不做格式、类型、数值范围、路径、容量或源文件变化的预检查。
下面列出的输入约定由使用者保证；代码保留生成算法、数值求解的收敛保护及文件 I/O 错误处理。

## 配置文件

随附的 `disc.example.json` 已采用 [Issue #119](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio/issues/119)
列出的完整参数，用户已在 **Verbatim CMDR47G-CFMWM03-6865G** 上实盘确认准确。
可以直接使用这份配置，也可以复制为 `my-disc.json` 后使用：

```json
{
  "total_sectors": 2297888,
  "inner_radius_mm": 23.966240074096643,
  "outer_radius_mm": 57.95448936129535,
  "channel_bit_nm": 133.3,
  "start_angle_deg": 0.0,
  "direction": "cw",
  "pitch_linear": -5.116119772674893e-05,
  "pitch_quadratic": 7.288259315376144e-05,
  "pitch_cubic": -3.533283575483746e-05,
  "psn_offset": "0x30000"
}
```

`total_sectors` 必填，是完整盘片可容纳的 **2048 字节用户数据扇区数**，
须为 16 的倍数。上述容量会生成 4,706,074,624 字节的 ISO，程序不会从设备读取容量。

下表列出程序在省略可选字段时使用的默认值：

| 配置字段 | 默认值 | 含义 |
|---|---:|---|
| `total_sectors` | 必填 | 完整盘片容量，单位为 2048 字节扇区 |
| `inner_radius_mm` | 24 | 数据记录区内半径，mm |
| `outer_radius_mm` | 58 | 数据记录区外半径，mm |
| `channel_bit_nm` | 133.3 | 通道位长度，nm |
| `start_angle_deg` | 0 | 起始角度，0 向右，正角度逆时针 |
| `direction` | `cw` | 顺时针 `cw` 或逆时针 `ccw`，默认与原 GUI 一致 |
| `pitch_linear` | 0 | 相对轨距一次系数 c1 |
| `pitch_quadratic` | 0 | 相对轨距二次系数 c2 |
| `pitch_cubic` | 0 | 相对轨距三次系数 c3 |
| `psn_offset` | `0x30000` | 物理扇区号偏移，整数或十六进制字符串，须为 16 的倍数 |

内外圈尺寸只使用 `inner_radius_mm` 和 `outer_radius_mm`，单位为 mm。
数值描述数据记录区的半径，不是中心孔或盘片标称尺寸。
配置使用 UTF-8 JSON；字段名称、类型和几何范围遵循下方的输入约定。

三次轨距模型：`x=(r-ri)/(ro-ri)`，`p(x)=p0*(1+c1*x+c2*x²+c3*x³)`。
`p0` 由容量、位长和内外半径自动求解。已有完整四项 `a0+a1*x+a2*x²+a3*x³` 时，
填写 `c1=a1/a0`、`c2=a2/a0`、`c3=a3/a0`。三个系数全零即恒定轨距。

## 运行

需要 Windows x64、64 位 Python 3.10+ 以及 requirements.txt 中的 NumPy、Pillow。
随附的 `dvd_kernel.dll` 必须与 `dvd_kernel.py`、`dvd_image.py` 放在同一目录。
轨道求解和像素采样由该融合 C 内核执行；运行时不需要编译器，也不增加 Python 依赖。
依赖事先安装后，运行无需网络。若在隔离机器安装，可提前准备匹配目标 Python/系统的 wheel 目录：

```powershell
python -m pip install --no-index --find-links .\wheels -r requirements.txt
```

只生成绘图镜像：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json
```

加入目录，并选择文件内容位于内圈或外圈：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json --data-dir .\disc-files --file-position inner
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json --data-dir .\disc-files --file-position outer
```

生成可绘区域蒙版，不需要提供 `-i`：

```powershell
python dvd_image.py -o mask.png -c disc.example.json
python dvd_image.py -o mask.png -c disc.example.json --data-dir .\disc-files --file-position inner
python dvd_image.py -o mask.png -c disc.example.json --data-dir .\disc-files --file-position outer
```

输出由 `-o` 的扩展名决定（不区分大小写）：`.iso` 生成镜像，`.png` 生成蒙版。
蒙版为无透明度的 1 位黑白 PNG：可绘图区为白色，盘外、中心不可绘区、
文件系统元数据、文件内容及其对齐保留区为黑色。
目录仅用于计算占用空间；蒙版模式不读取输入图片，不生成 ISO 或参数 JSON。
蒙版沿用 ISO 的完整盘片几何及绘图起止扇区，将边界表示为同心圆环。
实际扇区的螺旋分界与圆环略有差异，当前 #119 配置下不足 0.06 像素。

| 命令行选项 | 含义 |
|---|---|
| `-i` / `--input` | 生成 ISO 时必填，输入 PNG 路径；生成蒙版不使用 |
| `-o` / `--output` | 必填，输出 `.iso` 镜像或 `.png` 蒙版路径 |
| `-c` / `--config` | 必填，盘片配置 JSON 路径 |
| `--data-dir` | 可选，递归加入 ISO 的本地目录 |
| `--file-position inner/outer` | 文件内容的内外圈位置，默认 inner |

物理参数不再作为命令行选项。外圈选项需要同时提供目录，卷标为 `DVD_IMAGE`。
固定黑色映射 `0xA5`、白色映射 `0x92`，不提供反色开关。
完整帮助：`python dvd_image.py --help`。
生成 ISO 时显示进度，完成后显示输出路径；已有同名输出会直接覆盖。

## 文件系统与布局

提供目录后，ISO 包含可挂载的 **ISO9660 + Joliet** 文件系统，保留子目录和中文名称，
支持空文件及空目录。源目录的内容直接位于 ISO 根目录。

```text
inner：文件系统元数据 → 文件内容 → 对齐填充 → 绘图
outer：文件系统元数据 → 对齐填充 → 绘图 → 对齐填充 → 文件内容
```

元数据始终位于内圈，位置选项控制文件内容。绘图区按 16 扇区对齐，至少保留一个完整块。
文件和元数据应能装入配置容量，并留下绘图区。文件占用区域不参与绘图；图像仍按完整盘片几何投影，
不会向剩余空间拉伸。未提供目录时，输出原来的纯绘图镜像，无可挂载文件系统；提供空目录也会生成文件系统。

ISO9660/Joliet 单文件最大 4 GiB − 1 字节；过长或不兼容名称会规范化并解决重名。
随盘目录应由普通文件和目录组成，不包含符号链接、junction、重解析点或特殊设备文件。
输出位于随盘目录之外，生成期间保持所有源文件及目录不变。

## 输入与输出

- PNG 中心对应盘心，方图边长覆盖数据区外径，四角及内半径以内不绘制。
- 输入约定为 8192 × 8192 单帧 PNG，使用 1 位黑白、8 位灰度/调色板或 RGB/RGBA/LA 模式。读取器按实际尺寸处理首帧，不缩放、不抖动。
- 按 Pillow `L` 模式转换为 8 位灰度，RGB 使用 `0.299R + 0.587G + 0.114B`；再取最高位 `(gray >> 7)`：0–127 为黑，128–255 为白。
- 透明通道直接忽略，不与背景合成；例如透明白色按白处理，透明黑色按黑处理。
- `drawing.iso.json` 保存图片转换规则、生成参数、绘图 LBA 范围、文件布局摘要及完整轨距多项式。
- 已有同名 ISO 及参数 JSON 会直接覆盖；输出不得与输入 PNG 或配置文件重合。
- 临时文件位于输出目录，成功后发布；失败或 Ctrl+C 清理本次临时输出。
- 使用单层 DVD 连续螺旋模型，不处理双层盘换层。
- 按逻辑 CPU 数并行采样（最多 64 线程）、分批写出，文件内容小缓冲复制，内存不随盘片容量增长。

## 输入约定（由使用者保证）

### 命令行与路径

- 每次提供 `--output`、`--config`，生成 ISO 时还需提供 `--input`；`--file-position` 只填写 `inner` 或 `outer`，选择 `outer` 时同时提供 `--data-dir`。
- 输入图片和配置为现存、可读的本地普通文件，随盘目录为现存、可读的本地普通目录。不使用网络路径、设备路径、保留设备名或备用数据流。
- 输出路径使用 `.iso` 或 `.png` 扩展名，父目录已存在且可写，空闲空间足够；生成 ISO 时需容纳完整新镜像及参数 JSON。
- 输出文件及 ISO 的同名 JSON 不得覆盖输入图片或配置，也不得放入随盘目录。
- 已有同名 ISO/JSON 会在新镜像生成成功后替换；生成新镜像期间，旧成品占用的磁盘空间仍会保留。
- 图片为 8192 × 8192 单帧 PNG，使用上面列出的 1/8 位模式；透明度按已说明的规则忽略。

### JSON 与物理参数

- 顶层为 JSON 对象，字段不重复；仅使用参数表中的字段。
- 提供整数 `total_sectors`；其他数值字段使用 JSON 数字，不使用布尔值或数字字符串。
- 内外半径满足 `0 < inner_radius_mm < outer_radius_mm <= 60`，单位 mm。
- 总扇区数为正数且能被 16 整除，单位为 2048 字节；`channel_bit_nm` 为正数，`direction` 为 `cw` 或 `ccw`。
- `psn_offset` 为非负整数且能被 16 整除，或者对应的 `0x` 十六进制字符串；`psn_offset + total_sectors - 1 <= 0xFFFFFF`。
- 几何参数均为有限数值；起始角度换算为弧度后仍为有限数值。
- 总轨长 `L = total_sectors * 38688 * channel_bit_nm * 1e-6`（mm）大于内外半径差。
- 相对轨距多项式 `1 + c1*x + c2*x² + c3*x³` 在整个 `0 <= x <= 1` 内严格为正，包括区间内部的极小值。

### 随盘文件与库调用

- 数据目录是普通文件和目录构成的有限树；包括根目录在内，目录总数不超过 65535，单文件不超过 4 GiB − 1 字节。
- 所有文件按 2048 字节扇区取整，加上 ISO9660/Joliet 元数据及对齐空间后，仍至少留出 16 个绘图扇区；生成过程中不更改、删除或新增源文件和目录。
- 直接调用 Python 接口时，`workers` 省略或为 `None` 时自动选择线程数；显式指定时使用 1–64 的整数，`batch_blocks` 使用 1–128 的整数。
- 直接调用 `generate_payload` 时，图片为 C 连续布局的二维 `uint8` 数组，像素值为 0/255；几何对象使用 `DvdGeometry`。
- `generate_payload` 的块范围位于配置容量之内；`DvdGeometry.positions` 的弧长数组为有限数，且在 `[0, L]` 内。
- 直接调用文件系统写入接口时，按规划顺序写入前缀、绘图区、后缀，边界使用布局计划中的 LBA。

许可证和来源见 [NOTICE.md](NOTICE.md) 与 [LICENSE](LICENSE)。
