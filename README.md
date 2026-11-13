# DVD 图片转 ISO

将 **8192 × 8192 PNG** 转为 DVD 盘面绘图 ISO，可同时加入目录中的文件。
也可根据盘片配置和文件布局生成 **8192 × 8192 黑白可绘区域蒙版**。
灰度、黑白格式的图片保留 8 位灰度，沿光盘轨道采样后再按输出字节抖动为黑白。
RGB、RGBA、调色板等彩色格式的 PNG 支持基于 [DiscColorStudio](https://github.com/Neo-626/DiscColorStudio) 的实测彩色与周期梯保色编码。
可直接使用未抖动的原图，无需预先转换为黑白点阵。
采用 [DiscImageStudio](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio) 的 DVD dispersion 几何与扰码算法，几何总容量及物理参数来自 JSON 配置，实际可用比例由命令行指定。

程序完全脱机，只读取本地图片、配置和随盘文件，输出 ISO 及参数 JSON，或 PNG 蒙版。
不联网、不查询或打开光驱、不读取盘片容量、不执行刻录。

程序假定输入符合下述约定，参数范围及库调用的数据布局由使用者保证，不另做运行时参数校验。
命令行保留 `add_argument` 中的类型、可选值及必填声明；不适用当前模式的参数直接忽略。
UDF 写入器检查名称编码、描述符字段长度及布局完整性，代码包含数值求解的收敛保护及文件 I/O 错误处理。

## 安装

需要 Windows x64、64 位 Python 3.10+，以及 requirements.txt 中的 NumPy、Pillow。
不需要安装 Disc Image Studio、DiscColorStudio、.NET 或外部 ISO 制作工具。

项目文件放在同一目录，包含以下运行资源：

- 基础编码：`dvd_kernel.dll`、`dvd_kernel.py`、`dvd_geometry.py` 和 `dvd_image.py`。
- 实测彩色：`dvd_color.py`、`continuous-color-map.json` 和 `continuous-color-lut.bin.gz`，两份色表合计约 54.6 MB。
- 周期梯：`dvd_ladder.py`、`dvd_ladder.dll` 和 `ladder-model.json`。
- 随盘文件：`udf.py`。

安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

离线安装时，提前准备匹配目标 Python 和系统的 wheel 目录：

```powershell
python -m pip install --no-index --find-links .\wheels -r requirements.txt
```

使用附带 DLL 运行时无需编译器。开发时如需重新编译周期梯 DLL，在项目目录的
**x64 Native Tools Command Prompt for VS 2022** 执行：

```bat
cl /nologo /O2 /fp:strict /MT /LD dvd_ladder.c /Fedvd_ladder.dll
```

`dvd_ladder.c` 包含 `dvd_kernel.c` 以复用几何求解，二者源码应放在同一目录。

## 配置文件

[CMDR47G-CFMWM03-6865G.json](CMDR47G-CFMWM03-6865G.json)：这是一个配置文件的示例。

`total_sectors` 必填，是用于几何标定的完整盘片 **2048 字节用户数据扇区数**，须为 16 的倍数。
程序不会从设备读取容量，也不自动扣除保留扇区。
实际输出容量通过命令行可用比例控制。
缩短输出不会重算几何总轨长或缩放输入图：PNG 始终映射到配置的完整数据区外径，蒙版显示实际剩余可绘区。

下表列出程序在省略可选字段时使用的默认值：

| 配置字段 | 默认值 | 含义 |
|---|---:|---|
| `total_sectors` | 必填 | 完整盘片容量，单位为 2048 字节扇区 |
| `inner_radius_mm` | 24 | 数据记录区内半径，mm |
| `outer_radius_mm` | 58 | 数据记录区外半径，mm |
| `channel_bit_nm` | 133.3 | 通道位长度，nm |
| `start_angle_deg` | 0 | 起始角度，0 向右，正角度逆时针 |
| `direction` | `cw` | 顺时针 `cw` 或逆时针 `ccw` |
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

| 命令行选项 | 含义 |
|---|---|
| `-i` / `--input` | 输入 PNG 路径，生成 ISO 时提供；蒙版模式忽略 |
| `-o` / `--output` | 必填；`.iso` 生成镜像，`.png` 生成蒙版，扩展名不区分大小写 |
| `-c` / `--config` | 必填；盘片容量及物理参数 JSON 路径 |
| `-f` / `--files` | 递归加入 ISO 的本地目录；蒙版模式用于计算占用区域 |
| `-p` / `--position inner/outer` | 文件位置：内圈或外圈，默认 `inner`；无 `--files` 时忽略 |
| `-b` / `--bleed` | 可用容量比例，有限数值 `0 < bleed <= 1`，默认 `1` 表示全盘可用 |
| `-l` / `--volume-label` | UDF 卷标，默认 `DVD_IMAGE`；仅带 `--files` 的 ISO 生效 |
| `-d` / `--dither-strength` | 灰度字节级抖动强度，0–1 的有限数值，默认 0.75；0 为采样后按 128 阈值判定黑白；仅灰度 ISO 生效 |
| `-j` / `--jobs` | ISO 并行线程数，1–64；默认按逻辑 CPU 数选择，最多 64；蒙版忽略 |
| `--read-compatibility` | 实验性灰度读取保护，默认关闭；仅灰度 ISO 生效 |
| `--color-mode measured/ladder` | 实测彩色（默认）或周期梯保色；仅彩色 ISO 生效 |
| `--ladder-grid` | 周期梯配方网格边长，16–8192 的整数，默认 1024；仅周期梯生效 |
| `--disc-rotation` | 观看时盘面顺时针旋转角度，有限数值 `[0, 360)`，默认 0°；仅周期梯生效 |
| `--observer-azimuth` | 从图片指向观察者的方位，有限数值 `[0, 360)`，默认 180°；仅周期梯生效 |
| `--view-balance` | 邻近视角权重，0–1 的有限数值，默认 1；0 更侧重中心视角；仅周期梯生效 |

完整帮助：`python dvd_image.py --help`。

只生成绘图镜像：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json
```

灰度抖动按输出字节的绝对位置生成确定的相位，不会在批次、线程或绘图区起点重置。

图片格式自动决定灰度或彩色编码，彩色模式可另选周期梯：

| 图片的颜色格式 | 编码方式 |
|---|---|
| RGB、RGBA、调色板等彩色格式 | 保留或展开为 RGB，按 `--color-mode` 选择实测彩色或周期梯 |
| 黑白、灰度、带透明度的灰度格式 | 保留原灰度采样与字节级黑白抖动 |

分流依据图片格式，不检查像素颜色；RGB、RGBA、调色板图片即使只含黑白或灰色像素，也使用彩色算法。
调色板先展开为 RGB，透明度直接忽略。需要灰度编码时，将图片保存为黑白或灰度格式。

实测彩色模式的条带平滑和自适应混色均为 100%，DIS 暗部增强关闭，
在可绘区域自动寻找观看方向，在生成进度开始前输出观看角度，并将选择结果写入 ISO 的参数 JSON。
图片按配置的数据区外径映射，校准参数由盘片 JSON 提供。

内置色表基于 Verbatim 95058 DVD-R、4×刻录条件，实测角度范围为 64°–124°。
整盘图案覆盖的方位超过该范围时，无法在同一姿态下同时准确呈现全部颜色，需要旋转盘片观察不同区域。
色表的显色效果受盘片型号与刻录条件影响，几何校准准确不代表显色响应相同。

加入目录，并选择文件内容位于内圈或外圈：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json -f .\disc-files -p inner -l "MY_DISC"
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json --files .\disc-files --position outer
```

读取兼容保护示例：

```powershell
python dvd_image.py -i grayscale.png -o drawing.iso -c my-disc.json --read-compatibility
```

每个绘图扇区的第 0、16、…、2032 字节，目标扰码纹理值非 `A5/9B` 时替换为 `9B`，再补偿扰码。
纯白每个 2048 字节绘图扇区会改变 128 字节，纯黑不变，文件和 UDF 元数据不受影响。
读取兼容保护可能改变纹理和对比度，效果需按实际盘片和光驱确认。

无随盘目录时，参数 JSON 中的文件位置记录为 `inner`。
可用比例按扇区数计算，实际输出容量向下对齐到 16 扇区：

```text
output_sectors = int(total_sectors * bleed) // 16 * 16
```

比例独立于文件位置，对纯绘图、内圈文件、外圈文件和 PNG 蒙版均生效。
它表示可用扇区占完整盘片的比例，对应半径由轨道几何换算。
例如，使用 95% 的容量生成带外圈文件的镜像及配套蒙版：

```powershell
python dvd_image.py -i picture.png -o drawing.iso -c my-disc.json -f .\disc-files -p outer -b 0.95
python dvd_image.py -o mask.png -c my-disc.json -f .\disc-files -p outer -b 0.95
```

可用比例小于 1 时，镜像缩短，文件布局安排在可用容量内；完整几何及 PNG 的映射比例保持不变。

选择周期梯并指定观看方式：

```powershell
python dvd_image.py -i picture.png -o ladder.iso -c my-disc.json --color-mode ladder --ladder-grid 1024 --disc-rotation 0 --observer-azimuth 180 --view-balance 1
```

周期梯使用 30 种 20.4T–8T 记录周期，按 67 圈混排，以部分亮度和色彩还原度换取更宽的观看角度。
整张 PNG 作为盘心图层，按盘心参考观看场景计算配方。
观察方位采用右 0°、下 90°、左 180°、上 270°。盘面摆位与观察者方位独立设置，
观看设置不会修改 JSON 中的几何起始角。

配方拟合在网格单元中采样输入颜色，随后逐 DVD 码字沿轨道编码；较大的网格保留更多细节，同时增加计算量。
配方字段占用约 `grid² × 16` 字节（1024 约 16 MiB，8192 约 1 GiB），另加源图与模型内存。
配方网格是周期梯的独立采样设置；实测彩色和灰度路径使用原始 PNG 分辨率。
模型绑定 `channel_bit_nm = 133.3`，由使用者保证配置与模型一致。
输出参数 JSON 记录模型、配方网格、观看设置及完整几何，可据此复现。
周期梯显色效果尚未在本项目中实盘验证。

生成可绘区域蒙版：

```powershell
python dvd_image.py -o mask.png -c CMDR47G-CFMWM03-6865G.json
python dvd_image.py -o mask.png -c CMDR47G-CFMWM03-6865G.json -f .\disc-files -p inner
python dvd_image.py -o mask.png -c CMDR47G-CFMWM03-6865G.json --files .\disc-files --position outer
```

蒙版为无透明度的 1 位黑白 PNG：可绘图区为白色，盘外、中心不可绘区、
文件系统元数据、文件内容及其对齐保留区为黑色。
蒙版不生成 ISO 或参数 JSON。
蒙版沿用 ISO 的完整盘片几何及绘图起止扇区，将边界表示为同心圆环。
实际扇区的螺旋分界与圆环略有差异。

灰度模式使用黑色 `0xA5`、白色 `0x92`，实测彩色模式按色表选择纹理字节序列，周期梯模式按模型选择记录周期。
生成 ISO 时显示进度，完成后显示输出路径。

## 文件系统与布局

提供目录后，ISO 包含可挂载的只读 **UDF 2.01** 文件系统，保留子目录和中日韩 Unicode 名称，
支持空文件及空目录。源目录的内容直接位于 ISO 根目录。

UDF 逻辑卷标可完整保存 32 个英文或常用中日韩字符；上限为 126 个单字节字符或 63 个双字节字符，超出时截断。

```text
inner：盘首元数据 → 目录与文件 → 对齐填充 → 绘图 → 盘尾备份元数据
outer：盘首元数据 → 对齐填充 → 绘图 → 目录与文件 → 盘尾备份元数据
```

卷识别信息、锚点和备份描述符位于盘首及盘尾，位置选项控制目录与文件分区。
文件系统及其保留区不参与绘图；UDF 绘图区按 32 扇区对齐，可供 DVD 编码器逐个 16 扇区块写入。
文件和元数据应能装入配置容量，并留下绘图区。文件占用区域不参与绘图；图像仍按完整盘片几何投影，
不会向剩余空间拉伸。未提供目录时，输出纯绘图镜像，无可挂载文件系统；提供空目录也会生成文件系统。

单文件可超过 4 GiB，实际受盘片剩余容量限制。文件名最多 254 个单字节或 127 个双字节字符；
支持 Unicode BMP 字符，包含常用中日韩文字；不支持表情等补充平面字符或字节序标记，不会将名称替换为下划线。
随盘目录应由普通文件和目录组成，不包含符号链接、junction、重解析点或特殊设备文件。
输出位于随盘目录之外，生成期间保持所有源文件及目录不变。

## 与源项目的行为差异

以下比较源项目的 DVD 图片生成路径与本工具的当前行为。源码来源和参考提交见 [NOTICE.md](NOTICE.md)。

### 与 DiscImageStudio 的 DVD 路径相比

两者使用相同的 DVD dispersion 几何与扰码原理，文件系统、容量控制和输入约定有所不同。

| 项目 | DiscImageStudio | 本项目 |
|---|---|---|
| DVD 文件系统 | 随盘文件使用 ISO9660 + Joliet | 提供 `--files` 时使用只读 UDF 2.01；不提供目录时输出纯绘图镜像 |
| 卷标与单文件大小 | DVD 卷标受 Joliet 的 16 个 UTF-16 码元限制，超出拒绝生成；单文件最多 4 GiB − 1 字节 | UDF 逻辑卷标最多 126 个单字节或 63 个双字节字符，超出截断；单文件可超过 4 GiB，受输出容量约束 |
| 内外圈文件布局 | 目录和文件系统元数据位于内圈，外圈选项移动文件内容；绘图区按 16 扇区对齐 | `-p` 控制整个目录与文件分区的位置，盘首和盘尾保留 UDF 元数据；绘图区按 32 扇区对齐，蒙版排除全部占用区 |
| 可用容量与外缘预留 | 可写容量使用绝对扇区数；有随盘目录且文件放在外圈时，可启用固定 95% 的外缘预留上限 | `-b` 按总扇区数的比例控制输出，默认全盘可用；适用于纯绘图、内外圈文件和蒙版，不自动扣除保留扇区 |
| 缩容后的图片映射 | 默认按可用容量对应的有效外径适配图片；可通过源图外径选项固定采样尺度 | 图片始终按配置的完整数据区外径映射，缩短输出时裁减可绘范围，保持图案比例 |
| 参数与源文件检查 | 校验几何、容量和并行度；文件写入器检查源文件存在、大小及修改时间等 | 假定参数正确，不适用当前模式的选项直接忽略；保留 UDF 编码、字段和布局完整性检查及 I/O 错误处理，生成期间由使用者保持源目录不变 |
| 读取兼容保护 | 周期保护作用于明确归属绘图的字节，精确纹理流不允许叠加保护 | 周期保护仅用于灰度绘图，彩色模式忽略该选项 |
| 工具范围 | 提供多介质 GUI、工程管理、校准、预览和刻录，也提供离线 CLI | 提供 Windows x64 的离线 DVD 图片转换与蒙版生成，不访问光驱 |

DiscImageStudio 的 DVD 预设在未指定可写扇区数时，采用预留 608 扇区并向下对齐的回退规则；
已指定可写值的预设直接使用该值，不重复扣减。
对应实现见源项目的 [DVD 生成与采样设置](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio/blob/619ec7505ac8955f35fe2f353ebda74b6ea90dda/src/DiscImageStudio.Dvd/DvdStreamingGenerator.cs)
和 [ISO9660/Joliet 写入器](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio/blob/619ec7505ac8955f35fe2f353ebda74b6ea90dda/src/DiscImageStudio.Iso/Iso9660ImageWriter.cs)。

### 与 DiscColorStudio 的彩色路径相比

本项目随附的两份实测色表与周期梯模型，与源项目中的对应资源逐字节一致。
图片布局、采样网格和观看设置的使用方式存在以下差异。

| 项目 | DiscColorStudio | 本项目 |
|---|---|---|
| 彩色方案默认值 | 新建工程默认周期梯，已有工程按保存的方案加载 | 彩色 PNG 默认使用实测色表，通过 `--color-mode ladder` 选择周期梯 |
| 校准与图片布局 | 导入 DIS 校准工程，在 120 mm 画布上摆放、缩放和旋转多个图层 | 从 JSON 读取几何参数，将整张 PNG 视为盘心图像，边长映射到完整数据区外径 |
| 透明度 | 按图片 alpha、图层不透明度及背景合成 | 忽略 alpha，透明像素保存的 RGB 或灰度值也参与编码 |
| 灰度与彩色选择 | 由图层模式决定，可组合不同模式的图层 | 按 PNG 格式自动分流；RGB 图片即使仅含灰色像素也按彩色处理，灰度图片忽略彩色方案选项 |
| 采样精度 | 可分别设置图片图层的采样网格，并按图层精度生成配方 | 灰度和实测彩色直接采样原始 PNG；周期梯全图共用一个配方网格，默认边长 1024，由 `--ladder-grid` 指定 |
| 实测彩色观看方向 | 每个彩色图层单独规划或设置观看方向，可将局部图案适配实测角度窗口 | 整张 PNG 共用一个自动观看方位，规划范围排除文件占用区及不可用外缘；超过色表角度范围的位置夹到端点 |
| 周期梯观看设置 | 每张图片独立设置盘面摆位、观察者方向，并提供姿态模拟 | 整张 PNG 共用盘面摆位、观察者方位及视角平衡设置，按盘心参考场景编码，不提供姿态预览 |
| 颜色和纹理调节 | 提供明暗、颜色调整、条带平滑、自适应混色及 DIS 暗部增强等图层设置 | 实测模式固定条带平滑和自适应混色为 100%，关闭 DIS 暗部增强；周期梯可调视角平衡，明暗及颜色调整通过输入 PNG 完成 |
| 输出容量 | DVD 编码入口按工程几何中的扇区数输出完整镜像 | 完整几何与输出容量分开，使用 `-b` 指定可用比例，也可加入 UDF 随盘文件 |
| 同名输出与操作范围 | ISO 生成入口拒绝覆盖已有目标；提供图层编辑、模拟预览及光驱刻录流程 | 成功写入临时文件后替换同名 ISO 和参数 JSON，仅生成本地文件 |

整盘图案跨越 360°，实测色表的有效角度窗口有限；整张 PNG 共用观看设置时，
不能保证同一姿态下全盘准确显色。周期梯以部分亮度和色彩还原度换取更宽的观看范围。
对应实现见源项目的 [工程与图层配置](https://github.com/Neo-626/DiscColorStudio/blob/ef44b13694e9873fc47ea9e0040ec5af502995ae/src/DiscColorStudio/Model.cs)、
[实测观看角规划](https://github.com/Neo-626/DiscColorStudio/blob/ef44b13694e9873fc47ea9e0040ec5af502995ae/src/DiscColorStudio/AngleColorEngine.cs)
和 [DVD 编码入口](https://github.com/Neo-626/DiscColorStudio/blob/ef44b13694e9873fc47ea9e0040ec5af502995ae/src/DiscColorStudio/DvdImageEncoder.cs)。

## 输入与输出

- PNG 中心对应盘心，方图边长覆盖数据区外径，四角及内半径以内不绘制。
- 输入约定为 8192 × 8192 单帧 PNG，使用 1 位黑白、8 位灰度/调色板或 RGB/RGBA/LA 模式。读取器按实际尺寸处理首帧；灰度/实测彩色不预先缩放、二值化或进行图像级抖动。周期梯使用独立的配方网格。
- 灰度或黑白图片保留为 8 位灰度，保留 0–255 的全部灰度；彩色图片保留三个 RGB 通道，不预先转换为灰度。
- 灰度模式沿轨道计算每个输出码字的位置，双线性采样后按 `--dither-strength` 决定黑白纹理。整张图片仅含 0/255 时使用最近邻采样，保留已有黑白图案。
- 实测彩色模式按向下取整规则采样像素，再进行 RGB 四面体插值和连续角度插值；根据螺旋圈数选择纹理，按绝对输出字节位置保持纹理相位。
- 周期梯按 67 圈绝对排程选取载波，逐扇区处理 91 字节 frame 的保护字节和尾部裁剪，再补偿扰码；更换线程或批次大小不重置载波相位。
- 透明通道直接忽略，不与背景合成；例如透明白色按白处理，透明黑色按黑处理。
- `drawing.iso.json` 保存图片转换、采样及抖动规则、可用比例与输出容量、绘图 LBA 范围、UDF 卷标、时间戳与分区布局及完整轨距多项式；彩色模式还记录自动观看方向和色表来源。
- 已有同名 ISO 及参数 JSON 会直接覆盖；输出不得与输入 PNG 或配置文件重合。
- 临时文件位于输出目录，成功后发布；失败或 Ctrl+C 清理运行产生的临时输出。
- 使用单层 DVD 连续螺旋模型，不处理双层盘换层。
- 并行采样、分批写出，文件内容小缓冲复制，内存不随盘片容量增长。

## 输入约定（由使用者保证）

### 命令行与路径

- 输入图片和配置为现存、可读的本地普通文件，随盘目录为现存、可读的本地普通目录。不使用网络路径、设备路径、保留设备名或备用数据流。
- 输出父目录已存在且可写，空闲空间足够；生成 ISO 时需容纳完整新镜像及参数 JSON。
- 输出文件及 ISO 的同名 JSON 不得覆盖输入图片或配置，也不得放入随盘目录。
- 已有同名 ISO/JSON 会在新镜像生成成功后替换；生成新镜像期间，旧成品占用的磁盘空间仍会保留。
- 图片为 8192 × 8192 单帧 PNG，使用上面列出的 1/8 位模式；透明度按已说明的规则忽略。

### JSON 与物理参数

- 顶层为 JSON 对象，字段不重复；仅使用参数表中的字段。
- 提供整数 `total_sectors`；其他数值字段使用 JSON 数字，不使用布尔值或数字字符串。
- 内外半径满足 `0 < inner_radius_mm < outer_radius_mm <= 60`，单位 mm。
- 总扇区数为正数且能被 16 整除，单位为 2048 字节。`channel_bit_nm` 为正数，周期梯模型要求 133.3；`direction` 为 `cw` 或 `ccw`。
- `psn_offset` 为非负整数且能被 16 整除，或者对应的 `0x` 十六进制字符串；`psn_offset + total_sectors - 1 <= 0xFFFFFF`。
- 几何参数均为有限数值；起始角度换算为弧度后仍为有限数值。
- 总轨长 `L = total_sectors * 38688 * channel_bit_nm * 1e-6`（mm）大于内外半径差。
- 相对轨距多项式 `1 + c1*x + c2*x² + c3*x³` 在整个 `0 <= x <= 1` 内严格为正，包括区间内部的极小值。

### 随盘文件与库调用

- 数据目录是普通文件和目录构成的有限树；每个目录的直接子目录数不超过 65534，文件名及卷标符合上述 UDF 字符与长度约定。
- 应用可用比例后，纯绘图镜像至少包含一个 16 扇区块；使用 `--files` 时输出容量至少为 640 个扇区。所有文件按 2048 字节扇区取整，加上 UDF 元数据、盘尾备份区及对齐空间后，仍至少留出 32 个绘图扇区；生成过程中不更改、删除或新增源文件和目录。
- 直接调用 Python 接口时，`workers` 省略或为 `None` 时自动选择线程数；显式指定时使用 1–64 的整数，`batch_blocks` 使用 1–128 的整数。
- 直接调用 `generate_payload` 时，图片为 C 连续布局的二维 `uint8` 灰度数组或三维 `uint8` RGB 数组，像素值为 0–255；几何对象使用 `DvdGeometry`。灰度图片可传入整图是否仅含 0/255 的 `binary_image` 标记，避免重复分类。
- 周期梯库调用先用 `dvd_ladder.prepare_ladder(...)` 拟合一次，再将同一 `ladder_profile` 连同 `color_mode="ladder"` 传入各次 `generate_payload`，避免每个批次重新拟合；观看设置和网格在拟合时指定。
- `generate_payload` 的块范围位于输出可用容量之内；`DvdGeometry.positions` 的弧长数组为有限数，且在 `[0, L]` 内。
- 直接调用文件系统写入接口时，按规划顺序写入前缀、绘图区、后缀，边界使用布局计划中的 LBA。

许可证和来源见 [NOTICE.md](NOTICE.md) 与 [LICENSE](LICENSE)。
