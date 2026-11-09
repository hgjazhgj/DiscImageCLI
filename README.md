# DVD 图片转 ISO

将 **8192 × 8192 PNG** 转为 DVD 盘面绘图 ISO，可同时加入目录中的文件。
也可根据盘片配置和文件布局生成 **8192 × 8192 黑白可绘区域蒙版**。
灰度、黑白格式的图片保留 8 位灰度，沿光盘轨道采样后再按输出字节抖动为黑白。
RGB、RGBA、调色板等彩色格式的 PNG 使用 [DiscColorStudio](https://github.com/Neo-626/DiscColorStudio) 的角度相关色表生成彩色纹理，并自动选择观看角度。
可直接使用未抖动的原图，无需预先转换为黑白点阵。
采用 [DiscImageStudio](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio) 的 DVD dispersion 几何与扰码算法，容量及物理参数全部来自 JSON 配置。

程序完全脱机，只读取本地图片、配置和随盘文件，输出 ISO 及参数 JSON，或 PNG 蒙版。
不联网、不查询或打开光驱、不读取盘片容量、不执行刻录。
不需要安装 Disc Image Studio、DiscColorStudio、.NET 或外部 ISO 制作工具。

程序按下述输入约定运行。UDF 写入器检查名称编码、描述符字段长度及可绘区容量，避免写出不完整的文件系统；
其余输入约定由使用者保证。代码保留生成算法、数值求解的收敛保护及文件 I/O 错误处理。

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
彩色模式还需要同目录下的 `dvd_color.py` 和随附的两份固定色表资源。
轨道求解和像素采样由该融合 C 内核执行；运行时不需要编译器，也不增加 Python 依赖。
依赖事先安装后，运行无需网络。若在隔离机器安装，可提前准备匹配目标 Python/系统的 wheel 目录：

```powershell
python -m pip install --no-index --find-links .\wheels -r requirements.txt
```

只生成绘图镜像：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json
```

使用 `-j` / `--jobs` 指定 ISO 生成的并行线程数，例如 `-j 8`；默认按逻辑 CPU 数自动选择，最多 64 个线程。

用 `-d` / `--dither-strength` 调整灰度模式的输出字节级抖动强度，默认 `0.75`，设为 `0` 可关闭抖动：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json --dither-strength 0.75
```

抖动强度只通过命令行指定，碟片 JSON 仅保存容量及物理校准参数。
每个输出字节都按其绝对位置生成确定的抖动相位，不会在批次、线程或绘图区起点重置。
RGB 彩色编码和 PNG 蒙版不受此参数影响。

图片格式自动决定编码方式，无需新增命令行参数：

| 图片的颜色格式 | 编码方式 |
|---|---|
| RGB、RGBA、调色板等彩色格式 | 保留或展开为 RGB，按角度色表输出彩色纹理 |
| 黑白、灰度、带透明度的灰度格式 | 保留原灰度采样与字节级黑白抖动 |

分流依据图片格式，不检查像素颜色；RGB、RGBA、调色板图片即使只含黑白或灰色像素，也使用彩色算法。
调色板先展开为 RGB，透明度仍按原规则忽略。需要灰度编码时，将图片保存为黑白或灰度格式。

彩色模式随附 `continuous-color-map.json` 和 `continuous-color-lut.bin.gz`，
合计约 54.6 MB，不修改碟片物理 JSON。程序沿用原项目默认的条带平滑、自适应混色设置，
在可绘区域自动寻找观看方向，在生成进度开始前输出观看角度，并将选择结果写入 ISO 的参数 JSON。
图片仍按当前外径内切圆映射，不改变图案缩放或现有校准参数。

内置色表基于 Verbatim 95058 DVD-R、4×刻录条件，实测角度范围为 64°–124°。
整盘图案覆盖的方位超过该范围时，无法在同一姿态下同时准确呈现全部颜色，需要旋转盘片观察不同区域。
该色表尚未在 CMDR47G-CFMWM03-6865G 上实盘验证，几何校准准确不代表两种盘片的显色响应相同。

加入目录，并选择文件内容位于内圈或外圈：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json -f .\disc-files -p inner -l "MY_DISC"
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json --files .\disc-files --position outer
```

生成可绘区域蒙版，不需要提供 `-i`：

```powershell
python dvd_image.py -o mask.png -c disc.example.json
python dvd_image.py -o mask.png -c disc.example.json -f .\disc-files -p inner
python dvd_image.py -o mask.png -c disc.example.json --files .\disc-files --position outer
```

输出由 `-o` 的扩展名决定（不区分大小写）：`.iso` 生成镜像，`.png` 生成蒙版。
蒙版为无透明度的 1 位黑白 PNG：可绘图区为白色，盘外、中心不可绘区、
文件系统元数据、文件内容及其对齐保留区为黑色。
目录仅用于计算占用空间；蒙版模式不读取输入图片，不生成 ISO 或参数 JSON。
蒙版沿用 ISO 的完整盘片几何及绘图起止扇区，将边界表示为同心圆环。
实际扇区的螺旋分界与圆环略有差异，当前 #119 配置下不足 0.06 像素。

| 命令行选项 | 含义 |
|---|---|
| `-i` / `--input` | 输入 PNG 路径，用于生成 ISO |
| `-o` / `--output` | 输出 `.iso` 镜像或 `.png` 蒙版路径 |
| `-c` / `--config` | 盘片配置 JSON 路径 |
| `-f` / `--files` | 递归加入 ISO 的本地目录 |
| `-p` / `--position inner/outer` | 文件内容的内外圈位置，默认 inner |
| `-l` / `--volume-label` | ISO 卷标，默认 `DVD_IMAGE`，配合 `--files` 使用 |
| `-d` / `--dither-strength` | 灰度输出字节级抖动强度，0–1，默认 0.75；0 为采样后按 128 阈值判定黑白 |
| `-j` / `--jobs` | ISO 并行线程数，1–64；默认按逻辑 CPU 数选择，最多 64 |

物理参数不再作为命令行选项。外圈选项需要同时提供目录。
`--volume-label` 仅在使用 `--files` 创建文件系统时生效，目录可为空；不指定时卷标为 `DVD_IMAGE`。
使用 UDF 2.01 卷标，可完整保存 32 个英文或常用中日韩字符；上限为 126 个单字节字符或 63 个双字节字符，超出时截断。
灰度模式使用黑色 `0xA5`、白色 `0x92`，彩色模式按色表选择纹理字节序列。
完整帮助：`python dvd_image.py --help`。
生成 ISO 时显示进度，完成后显示输出路径；已有同名输出会直接覆盖。

## 文件系统与布局

DiscImageStudio 的 DVD 随盘文件使用 **ISO9660 + Joliet**；本项目改用 **UDF 2.01**，
具体区别及其他已确认的行为差异见下方[与源项目的行为差异](#与源项目的行为差异)。

提供目录后，ISO 包含可挂载的只读 **UDF 2.01** 文件系统，保留子目录和中日韩 Unicode 名称，
支持空文件及空目录。源目录的内容直接位于 ISO 根目录。

```text
inner：盘首元数据 → 目录与文件 → 对齐填充 → 绘图 → 盘尾备份元数据
outer：盘首元数据 → 对齐填充 → 绘图 → 目录与文件 → 盘尾备份元数据
```

卷识别信息、锚点和备份描述符位于盘首及盘尾，位置选项控制目录与文件分区。
文件系统及其保留区不参与绘图；UDF 绘图区按 32 扇区对齐，可供 DVD 编码器逐个 16 扇区块写入。
文件和元数据应能装入配置容量，并留下绘图区。文件占用区域不参与绘图；图像仍按完整盘片几何投影，
不会向剩余空间拉伸。未提供目录时，输出原来的纯绘图镜像，无可挂载文件系统；提供空目录也会生成文件系统。

单文件可超过 4 GiB，实际受盘片剩余容量限制。文件名最多 254 个单字节或 127 个双字节字符；
支持 Unicode BMP 字符，包含常用中日韩文字；不支持表情等补充平面字符或字节序标记，不会将名称替换为下划线。
随盘目录应由普通文件和目录组成，不包含符号链接、junction、重解析点或特殊设备文件。
输出位于随盘目录之外，生成期间保持所有源文件及目录不变。

## 与源项目的行为差异

以下以 [NOTICE.md](NOTICE.md) 记录的固定提交为对照：DiscImageStudio
`b86404fef9111d418b234449a6d5c7839ae03542` 提供 DVD 几何、扰码和文件系统参考，
DiscColorStudio `a34b7995e2734be6ba368291bd87d00a0e3ec2ff` 提供彩色编码及色表。

### 与 DiscImageStudio 的 DVD 路径相比

原项目的 DVD [文件系统写入器](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio/blob/b86404fef9111d418b234449a6d5c7839ae03542/src/DiscImageStudio.Iso/Iso9660ImageWriter.cs)
写入 ISO9660 + Joliet；UDF 原本用于其蓝光路径。本项目将原项目的
[UDF 写入器](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio/blob/b86404fef9111d418b234449a6d5c7839ae03542/src/DiscImageStudio.Iso/UdfImageWriter.cs)
移植到 Python，并用于 DVD 随盘文件。

| 项目 | DiscImageStudio | 本项目 |
|---|---|---|
| DVD 文件系统 | ISO9660 + Joliet | 指定 `--files` 后使用只读 UDF 2.01；不指定目录时仍为纯绘图镜像 |
| 卷标与单文件大小 | ISO9660 卷标最多 32 个字符、Joliet 卷标最多 16 个字符；单文件最多 4 GiB − 1 字节 | UDF 逻辑卷标最多 126 个单字节或 63 个双字节字符，32 个常用中日韩字符可完整保存；单文件可超过 4 GiB，受盘片剩余容量限制 |
| 文件系统保留区 | DVD 使用 ISO9660/Joliet 元数据及 16 扇区绘图对齐 | UDF 在盘首、盘尾保留锚点和描述符，绘图区按 32 扇区对齐；`--position` 移动整个目录与文件分区，蒙版同步排除这些保留区 |
| 输入与源文件检查 | 文件系统写入器检查源文件在规划后及写入期间是否变化 | 保留 UDF 编码、字段长度及可绘区容量检查；其他输入由使用者保证，不全面检测源文件变化，生成期间需保持源目录不变 |
| 操作范围 | 提供 GUI、工程管理、校准、预览、刻录及多种介质支持 | 只提供 DVD 的离线命令行转换，直接读取 PNG、物理参数 JSON 和随盘目录，输出 ISO、参数 JSON 或 PNG 蒙版；不访问光驱 |

### 与 DiscColorStudio 的彩色路径相比

彩色部分沿用源项目的色表和默认配方，但图片输入、布局及可配置范围有所不同。
对应实现可参见源项目的
[角度规划](https://github.com/Neo-626/DiscColorStudio/blob/a34b7995e2734be6ba368291bd87d00a0e3ec2ff/src/DiscColorStudio/AngleColorEngine.cs)
和 [DVD 编码入口](https://github.com/Neo-626/DiscColorStudio/blob/a34b7995e2734be6ba368291bd87d00a0e3ec2ff/src/DiscColorStudio/DvdImageEncoder.cs)。

| 项目 | DiscColorStudio | 本项目 |
|---|---|---|
| 图片布局与尺寸 | 使用 120 mm 画布，可分别摆放、缩放、旋转多个图层，并调整输出采样网格 | 将整张 PNG 作为盘心图像，边长映射到配置的数据区外径；读取现有像素，不提供图层编辑或额外的导出采样网格设置 |
| 透明度 | 按图层透明度与背景合成 | 忽略 PNG 的透明度，直接使用其 RGB 或灰度值；透明像素中保存的颜色也会参与编码 |
| 彩色与灰度模式 | 由图层模式及相关设置决定 | 按 PNG 格式自动分流：RGB、RGBA、调色板进入彩色路径，灰度、黑白格式进入灰度路径；RGB 图片即使仅含灰色像素也按彩色处理 |
| 观看方向 | 每个彩色图层单独规划观看方向，可让局部小图适配标定角度窗口 | 整张 PNG 共用一个观看方位。铺满环带的图案跨越 360°，超过色表 64°–124° 的窗口；窗口外的编码角度夹到端点，不能保证在同一姿态下整盘准确显色 |
| 颜色和纹理调节 | 提供明暗、HSL、条带平滑、自适应混色、DIS 暗部增强等调节项 | 固定条带平滑和自适应混色为 100%，关闭 DIS 暗部增强，沿用源项目默认配方库；明暗和 HSL 调整需预先处理输入 PNG，`--dither-strength` 只用于灰度路径 |
| 同名输出 | 彩色 ISO 生成入口拒绝覆盖已有目标镜像 | 成功生成临时文件后替换同名 ISO 和参数 JSON |

## 输入与输出

- PNG 中心对应盘心，方图边长覆盖数据区外径，四角及内半径以内不绘制。
- 输入约定为 8192 × 8192 单帧 PNG，使用 1 位黑白、8 位灰度/调色板或 RGB/RGBA/LA 模式。读取器按实际尺寸处理首帧，不预先缩放、二值化或进行图像级抖动。
- 灰度或黑白图片保留为 8 位灰度，保留 0–255 的全部灰度；彩色图片保留三个 RGB 通道，不预先转换为灰度。
- 灰度模式沿轨道计算每个输出码字的位置，双线性采样后按 `--dither-strength` 决定黑白纹理。整张图片仅含 0/255 时使用最近邻采样，保留已有黑白图案。
- 彩色模式按参考项目的像素取样规则读取颜色，再进行 RGB 四面体插值和连续角度插值；根据螺旋圈数选择纹理，按绝对输出字节位置保持纹理相位。
- 透明通道直接忽略，不与背景合成；例如透明白色按白处理，透明黑色按黑处理。
- `drawing.iso.json` 保存图片转换、采样及抖动规则、生成参数、绘图 LBA 范围、UDF 卷标、时间戳与分区布局及完整轨距多项式；彩色模式还记录自动观看方向和色表来源。
- 已有同名 ISO 及参数 JSON 会直接覆盖；输出不得与输入 PNG 或配置文件重合。
- 临时文件位于输出目录，成功后发布；失败或 Ctrl+C 清理本次临时输出。
- 使用单层 DVD 连续螺旋模型，不处理双层盘换层。
- 按逻辑 CPU 数并行采样（最多 64 线程）、分批写出，文件内容小缓冲复制，内存不随盘片容量增长。

## 输入约定（由使用者保证）

### 命令行与路径

- 每次提供 `--output`、`--config`，生成 ISO 时还需提供 `--input`；`--position` 只填写 `inner` 或 `outer`，选择 `outer` 时同时提供 `--files`。
- `--dither-strength` 为 0–1 的有限数值，省略时为 0.75。
- `--jobs` 使用 1–64 的整数；PNG 蒙版不使用该参数。
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

- 数据目录是普通文件和目录构成的有限树；每个目录的直接子目录数不超过 65534，文件名及卷标符合上述 UDF 字符与长度约定。
- 使用 `--files` 时容量至少为 640 个扇区。所有文件按 2048 字节扇区取整，加上 UDF 元数据、盘尾备份区及对齐空间后，仍至少留出 32 个绘图扇区；生成过程中不更改、删除或新增源文件和目录。
- 直接调用 Python 接口时，`workers` 省略或为 `None` 时自动选择线程数；显式指定时使用 1–64 的整数，`batch_blocks` 使用 1–128 的整数。
- 直接调用 `generate_payload` 时，图片为 C 连续布局的二维 `uint8` 灰度数组或三维 `uint8` RGB 数组，像素值为 0–255；几何对象使用 `DvdGeometry`。灰度图片可传入整图是否仅含 0/255 的 `binary_image` 标记，避免重复分类。
- `generate_payload` 的块范围位于配置容量之内；`DvdGeometry.positions` 的弧长数组为有限数，且在 `[0, L]` 内。
- 直接调用文件系统写入接口时，按规划顺序写入前缀、绘图区、后缀，边界使用布局计划中的 LBA。

可运行 `python -m unittest discover -s tests -v` 检查 UDF 描述符与校验和、Unicode 名称、内外圈文件、流式写入和 DVD 生成；测试不访问光驱。

许可证和来源见 [NOTICE.md](NOTICE.md) 与 [LICENSE](LICENSE)。
