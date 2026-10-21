# Pygmalion · 写字机控制台

以纸面为主要编辑入口的写字机控制台。在 A4/A5 纸张上添加文本框和表格，直接编辑、拖放和调整尺寸，预览真实单线笔画后执行书写。无鉴权，保留机器控制与参数设置。

## 启动

需要 Python 3.11、Poetry、Node.js 22.12+ 与 pnpm。

```bash
poetry config virtualenvs.in-project true --local
poetry env use python3.11
poetry install
pnpm --dir src/web install --frozen-lockfile
pnpm --dir src/web run build
poetry run python -m pygmalion.commands.app serve
```

书写工作台：<http://127.0.0.1:8080/>。旧 `/admin` 地址也打开同一工作台。

默认监听 `0.0.0.0:8080`，局域网设备可通过本机 IP 访问；需要仅本机访问时使用 `--host 127.0.0.1`。

只演练书写队列：

```bash
poetry run python -m pygmalion.commands.app serve --dry-run
```

演练只作用于书写任务。机器页的检测、点动、抬落笔、原点设置和恢复操作始终针对实际设备，界面按实际接口结果反馈。

支持 `PYGMALION_HOST`、`PYGMALION_PORT`、`PYGMALION_DRY_RUN`、`PYGMALION_HOME`、`PYGMALION_FONTS_DIR`。运行数据根目录默认是仓库；使用 `--data-home` 指定另一目录时，默认从该目录下的 `model-bin/` 读取字体；也可用 `--fonts-dir` 或 `PYGMALION_FONTS_DIR` 独立指定字体数据集父目录，无需复制字库。字体只支持 Hugging Face Parquet 数据集，不再读取 JSON、CNC Draw 或内置几何/西文字库。`.env.example` 提供示例，VS Code 调试配置读取 `.env`。

内置字库使用标准 Hugging Face 数据集目录 `model-bin/cjk-medians/`，包含 `train.parquet`、`README.md` 和 `ARPHICPL.TXT`。Parquet 使用 Zstd 压缩和四位小数的定点整数，约 1.61 MiB，无损保留全部字形与笔顺。该目录已被 Git 忽略，新检出或部署时须单独提供，缺失时应用会提示缺字。

```python
from datasets import load_dataset

glyphs = load_dataset("model-bin/cjk-medians", split="train")
```

## 页面与行为

| 页面 | 路径 | 行为 |
| --- | --- | --- |
| 书写工作台 | `/`、`/admin` | 纸面编排、文本框、表格、合并拆分、固定外框自适应与真实笔画预览 |
| 机器控制 | `/admin/machine` | 可收起连接信息、串口检测、点动、抬落笔、原点、备份和恢复 |
| 参数设置 | `/admin/settings` | 软件参数保存；GRBL 指令预览、备份与确认写入 |

- 没有投稿、公开提交、队列或独立纸张校准页面。侧栏默认 44px 窄轨，可展开至 168px，导航文字 14px；头部 54px、底部任务栏 52px、紧凑控件 32px。
- 白垣沿用已批准的中性控制台底色；苍渊使用技能锁定的深渊、夜海、铸铁与初光。双主题共用 DOM 和语义变量，主题与导航宽度保存在浏览器。
- 初次打开为空文档，不创建示例数据。文档名称只用于保存和任务辨识，不参与纸面渲染。文本框和单元格仅支持横排，保留空行、换行、缩进，长行在各自区域内换行。
- 支持 A4 / A5 单张纸。元素超出页边距内的可写区域、文字溢出或缺字时禁止书写；不会自动缩字、分页或截断。表格自适应仅在主动点击时调整。
- 文本框、单元格和文档名称每次输入都会同步自动保存到当前浏览器，删除、粘贴、撤销、重做和其他文档修改同样立即保存；无需失焦、按确认键或等待笔画预览。刷新可恢复最近草稿，没有云端文档库。浏览器存储失败时保留当前文字并显示“重试保存”，也会在下一次修改时重试；失败期间离页会提醒。
- “保存”按钮额外导出 `.pygmalion.json` 文件；本机缓存失败时仍可导出副本。打开接受该 JSON 或 UTF-8 `.txt`。新建或打开将替换当前浏览器草稿，尚未导出文件的修改会要求确认。
- 输入文字时保留同一布局下上次成功的笔迹预览，新图加载完成后再替换，其他单元格不会随每次输入清空。等待期间提示正在更新；预览失败时保留旧图并提供重试，只有最新文档的预览校验通过后才能书写。新建、打开、清空或改变布局时不沿用旧图。
- 预览和落笔共用元素排版、表格线及真实字库笔画。页边距辅助线仅在 SVG 显示，不进入 G-code。书写时冻结完整文档、纸张参数、轨迹和机器参数；后续设置不影响已开始的任务。
- 只允许一个活动书写任务。进度明确为“指令发送进度”，100% 后仍等机器返回 Idle 才完成。中止停止后续发送；急停保留原有真实控制结果。程序重启不自动续写。
- 演练仅作用于书写任务；机器页的检测、点动、抬落笔和恢复仍为真实动作。浏览器与自动测试使用隔离运行目录及演练模式，不自动连接物理设备。
- 机器页通过 WebSocket 复用控制连接、推送坐标；离开机器页释放串口，断线不重发运动。连接抽屉折叠后仍显示已读取的状态和端口。
- 工作台使用 `/api/workbench/preview`、`/api/workbench/write` 与 `/api/workbench/table-fit`。旧数据库与任务执行器保留兼容：书写时内部创建关联记录，但不会将其标题或作者送入新排版器，也不会在 UI 暴露旧投稿系统。

[Figma 白垣单页基线](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=8-1071) · [苍渊基线](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=8-3344)

## 纸面编排与表格

- 点击“添加文本框”或“添加表格”，单击文本框或单元格即可输入，无需双击。`Ctrl/Cmd + Enter` 或 `Escape` 结束编辑并保留已自动保存的文字；中文输入法组合过程中不会提前结束编辑。
- 按 `H` 进入小手模式，鼠标变为小手；按住鼠标拖动可平移纸面，按 `Esc` 回到普通选择模式。工具栏“小手 H”也可切换。平移不改变元素的毫米位置，文字输入和中文输入法不会触发 H 快捷键。
- 纸面区域支持 `Ctrl/Cmd + 滚轮` 缩放，以鼠标位置为中心，只改变预览视图；区域外保留浏览器行为。缩放范围为 10%–400%，可切换回“适合窗口”。
- 纸面上方和左侧提供毫米刻度尺，与纸张缩放、滚动同步。放大后显示 1 mm 小刻度；下方显示指针的 X/Y 毫米坐标，原点在纸张左上角。100% 采用标准 CSS 像素换算，不代表未经校准的显示器物理尺寸。
- 非文字输入时按 `Ctrl/Cmd + A` 全选当前纸面上的文本框、表格和矢量图，再按 `Backspace` 或 `Delete` 整批删除；`Esc` 取消选择，`Ctrl/Cmd + Z` 可一次恢复整批删除。输入框内仍保留原生的文字全选和删除行为，弹窗和书写冻结期间不会触发纸面删除。
- 选中元素后拖动上方手柄移动，拖动右下角调整尺寸；右侧可输入精确毫米值。Ctrl+Z 撤销、Ctrl+Y 或 Ctrl+Shift+Z 重做，Mac 可使用 Cmd+Z / Cmd+Shift+Z，工具栏按钮使用同一份历史。文字从进入编辑到失焦、Ctrl+Enter 或 Esc 结束编辑算一步，Esc 保留已输入文字；编辑中撤销也会整次回退并保留输入焦点。单次拖动、合并或自适应各算一步，历史在本次会话中保留最多 50 步。元素顺序即书写顺序。
- 表格支持增删行列、拖动分隔线和精确行高列宽调整，均保持外框不变。Shift 点击选取区域；Esc 结束文字编辑后可用 Shift 方向键扩展选区；也可使用“扩展单元格选区”完成触屏选择。
- 合并仅允许矩形区域，且最多一个单元格有文字；拆分后文字保留在左上格。删除含文字的行列前需要确认。单元格支持水平和垂直对齐。
- “自适应”固定表格位置、总宽和总高。当前内容已放得下时保留原布局；否则先在当前字号下调整行高、列宽，仍放不下时才以 0.1 mm 为步长缩小字号，最低 4 mm，绝不放大。合并格按跨行跨列空间参与计算；4 mm 仍放不下时保留原布局并指出溢出单元格。仅点击时执行。
- 属性数值可先清空再填写，支持小数；合法值输入后立即生效并自动保存。格式错误、越界值会显示字段提示，空值或未完成的小数在失焦/Enter 后提示。非法草稿不会覆盖已保存数值，并阻止保存文件、自适应和书写；Esc 恢复最近有效值。行高、列宽会为相邻行列保留至少 0.25 mm；切换对象、打开文档或撤销重做会重置未生效的草稿。
- 默认框线按最多 **5 mm** 一段绘制：每段完成后抬笔，从同一端点落笔续画，保持实线。参数设置中的“表格框线分段长度”可调整为 0.5–20 mm；只作用于表格线，任务冻结当时的设置。
- 默认实际书写表格线和文字；关闭“实际书写表格线”后只填写文字，编辑辅助网格仍可见。共享边只画一次，合并格内部不画线；无文字的空表也可单独绘制表格线。
- 文档保存为 `version: 2`，包含纸张设置和以毫米记录的 `elements`。旧 JSON、旧浏览器文档和 TXT 导入为一个文本框；旧竖排文档会转换为横排。旧投稿 API 保持兼容。
- 预览返回元素/单元格的布局与诊断；文字溢出、缺字和元素越界由服务端再次校验。自适应结果只应用于请求时的文档版本，编辑过程中返回的旧结果不会覆盖新内容。

## 工程结构

```text
src/pygmalion/commands/app.py Typer 入口、环境变量、日志与 Uvicorn 启动
src/pygmalion/api/app.py     FastAPI 工厂、生命周期与静态前端托管
src/pygmalion/api/v1/        HTTP 路由、WebSocket 协议及消息队列
src/pygmalion/servicer/      业务服务、资源上下文与协议无关的服务错误
src/pygmalion/schemas/       共享 Pydantic 文档、元素和请求模型
src/pygmalion/fonts/         字库发现、Hugging Face 数据集与字形来源
src/pygmalion/writing/       排版、手写效果、平滑路径、SVG 预览与离线生成
src/pygmalion/machine/       设备控制器、串口会话与互斥、机器坐标、G-code 和 GRBL
src/pygmalion/submissions/   投稿存取
src/pygmalion/jobs/          任务记录、进度与单线程执行队列
src/pygmalion/storage.py     SQLite 连接、建表与迁移
src/pygmalion/config.py      应用配置与运行目录
src/web/src/                React / TypeScript 页面、共享组件与语义样式
src/web/public/design/      Figma 导出的原始 SVG 图标和状态标记
model-bin/cjk-medians/       Hugging Face Parquet 字库、数据集说明与授权（Git 忽略）
data/                      本地运行数据库（Git 忽略）
licenses/                  内置字库的授权条款
tests/                     业务、任务和机器协议边界检查
```

`commands` 是统一启动入口：`poetry run app serve`（或 `poetry run python -m pygmalion.commands.app serve`）。Uvicorn 只在 `commands/app.py` 内启动，加载 `pygmalion.api.app:app`。后端调试也通过该命令入口读取 `.env`。

`api`、`servicer`、`schemas` 同级。调用顺序为 `api.v1 → servicer → writing / fonts / jobs / machine / repository`：路由负责 HTTP 和 WebSocket 的输入输出，服务负责业务编排，设备控制器负责串口、锁、点动、备份与指令发送。任务冻结、后台线程和进度仍由 `jobs` 管理。HTTP 和 WebSocket 共用设备服务；WebSocket 不调用 HTTP 路由。共享模型只包含数据、校验和派生属性，纸张配置转换留在 `writing`。

`v1` 仅是 Python 路由包版本，现有 `/api/...` 地址保持不变。原 Python `pygmalion.web` 已移除；浏览器前端仍位于 `src/web`。导入应用或调用工厂不会创建数据库、启动工作线程或打开串口；进入应用生命周期后，先检查前端构建，再初始化运行资源，退出或启动失败时统一清理。

FastAPI 使用 [官方 frontend 托管方式](https://fastapi.tiangolo.com/tutorial/frontend/) 提供 `src/web/dist` 和 SPA 页面；未知 `/api` 请求返回 404，静态资源和 OpenAPI 不经过 SPA 回退。前端未构建时，启动会明确提示执行 `pnpm run build`。

运行生成的 `config.json`、`data/poems.db`、`output/`、`backups/` 均已忽略。清华源保留为补充源；本次其部分下载返回 403，安装使用官方 PyPI，并提交 Poetry 与 pnpm 锁文件。

## 开发与验证

```bash
poetry run python -m pygmalion.commands.app serve --dry-run
pnpm --dir src/web run dev
```

Vite `/api` 代理到 `127.0.0.1:8080`。VS Code 的 `backend` 调试先执行 `web: build`；`CLI` 调试启动 Typer。

```bash
poetry run pytest -q
poetry run ruff check src/pygmalion tests --select F
pnpm --dir src/web run lint
pnpm --dir src/web run build
pnpm --dir src/web run test:e2e
```

浏览器检查使用 Playwright Chromium（首次执行 `pnpm --dir src/web exec playwright install chromium`），Poetry 需在 PATH 中。测试服务使用 8097 端口、`.cache/e2e` 独立数据目录和任务演练模式，递归查找并复制本地数据集字库；不会调用真实运动接口。检查覆盖 390 / 1024 / 1440 / 1920px、双主题、纸面拖放缩放、逐字自动保存、整次编辑撤销重做、中文输入法与缓存失败恢复、慢响应下预览连续显示及过期图片保护、表格合并拆分、自适应及旧响应保护、文档迁移与保存打开、键盘焦点恢复、溢出与缺字、预览失败恢复、演练中止、软件参数保存和 WebSocket 控制边界。

验收命令见上方；测试覆盖通用正文与兼容接口，不连接实体机器。实体机器的行程、坐标、抬落笔与急停效果仍需连接设备后实机验证。

## 设计交接与来源

[Figma 设计文件](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=8-1071) · [桌面原型](https://www.figma.com/proto/m1toyRcCag4OOsJNMebFDb?node-id=8-1071&starting-point-node-id=8-1071&scaling=scale-down) · [共享侧栏组件](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=77-1421)

应用壳层和主题沿用单页 V1 基线；工作台已改为中央纸面编辑和右侧属性检查器，旧设计稿的左侧正文栏不再使用。保留三项导航。设计稿示例正文、端口和进度只用于布局，代码初次打开为空文档，设备与任务状态按实际数据呈现。MANAS 组织操作关系，苍渊·白垣负责主题角色；纸面实际笔画取代稿中的文字排版示意。

业务逻辑迁入自 `handwriting-plotter`，改用 Poetry / Typer / FastAPI / React 架构并删除鉴权。保留原项目 MIT 授权；内置汉字笔画数据的 Arphic 授权见 `licenses/ARPHICPL.TXT`，数据集目录也附有副本。不复制原项目的投稿数据库或机器配置。

## 字体数据集约定

递归扫描 `model-bin`（或指定的字体目录）下所有 `train.parquet`，支持 `model-bin/arp244/cjk-medians/train.parquet` 等多层目录。每套字体通过 `datasets.load_dataset` 读取。数据集目录名即字体 ID，自动出现在字体列表中；不同层级出现同名目录时，以相对字体根目录的路径作为 ID，避免重名。默认选择 `cjk-medians-plus`（中文单线·扩展）；未安装时使用实际发现的字库。显式配置的默认字体优先；所选数据集缺字时，仅由其他数据集补充。

每行代表一个字符：`char` 为字符，`lengths` 为各笔画的点数，`x`、`y` 为按笔画顺序展平的整数坐标。坐标除以 10000 后为 em 单位，y 向下；横纵坐标数量必须一致，且等于笔画点数之和。可选布尔列 `preserve_origin` 默认 `false`，沿用原字库墨迹居中；设为 `true` 时保留统一字格原点，用于新增字形的基线、大小写和标点位置。旧数据集无需修改。

### 离线生成扩展字库

原始 `cjk-medians` 的 9,574 字不包含数字、英文字母和常用标点。执行以下命令生成一份独立新字库，保留原始汉字数据；输出目录必须尚不存在：

```bash
poetry run python -m pygmalion.commands.app build-font \
  --base model-bin/arp244/cjk-medians/train.parquet \
  --output model-bin/cjk-medians-plus
```

命令补入 223 个字符，共 9,797 字：可打印 ASCII、全角 ASCII、常用中文标点及 `±×÷≤≥≠≈∞√°℃‰¥￥€£`。数字、字母和部分数学符号取自 CC0 的 NormStroke 单线字形，其余由几何中心线补绘；没有将轮廓当成两条书写笔画。源文件与哈希固定在代码中，生成过程不下载字体。

输出包括 `train.parquet`、适合 Hugging Face 数据集使用的中文 `README.md`、`ARPHICPL.TXT`、`SOURCES.md`、覆盖与来源报告 `coverage.json`，以及 OpenCV 渲染的 `glyphs-4mm.png` / `glyphs-8mm.png`。基础字形保留原坐标及笔顺；新字形按统一基线归一化，缺字符会导致生成失败。OpenCV 用于离线字样检查，服务加载字体时仍只读取 Parquet。`model-bin` 被 Git 忽略，部署时复制或另行托管新数据集；本命令不会上传到 Hugging Face。

汉字、西文、数字和标点都必须由数据集提供；缺失时提示缺字，单页书写入口禁止执行。没有非数据集字体回退，也不需要 Windows、Java 或 CNC Draw 加载器。当前加载的是本地 Parquet 文件；从 Hugging Face 下载后放入上述目录即可，不会自动拉取远程仓库。

可通过环境变量或启动参数指定字体目录，例如：

```bash
PYGMALION_FONTS_DIR=/srv/fonts poetry run python -m pygmalion.commands.app serve
# 或
poetry run python -m pygmalion.commands.app serve --fonts-dir /srv/fonts
```

此时读取 `/srv/fonts/<字体ID>/train.parquet`。相对路径以运行数据根目录为基准；未设置或空值使用 `model-bin`。`.env` 不会由普通 CLI 自动读取；可由 VS Code 调试配置载入，或先在 Bash 中执行 `set -a; source .env; set +a`。

### 导入 SVG 矢量图

在纸面工具栏点击 **导入 SVG**，选择本机 `.svg` 文件。矢量图会作为独立对象插入纸面，可以与文本框、表格混排；选中后可拖动位置、输入毫米尺寸、修改名称或删除。默认锁定宽高比，可在属性栏解除。导入和编辑接入即时本机保存、撤销／重做及文档文件导入导出。

- 支持 `path`、直线、折线、多边形、矩形（含圆角）、圆和椭圆，以及分组变换、`viewBox` 和常见长度单位。无单位尺寸按 96 DPI 换算；使用实际图形边界作为对象尺寸，去掉 SVG 外围空白，超出纸面可写范围时等比缩小。纯水平或垂直线的空轴提供 1 mm 选择外框。
- **轮廓／实心填充**：新导入含填充区域的 SVG 默认实心填充，按 `nonzero` 或 `evenodd` 规则保留孔洞，以往返扫描线填充字形内部，再描绘边界；跨空白区间抬笔。纯线稿和旧文档仍按轮廓绘制。线稿建议设置 `fill="none"`，填充区域的开放边界按 SVG 规则闭合。
- 属性栏可配置笔迹宽度（默认 0.3 mm）、填充间距（默认 0.24 mm）和角度（默认 0°）。建议间距不超过实际笔迹宽度的 80%，缩放后仍按毫米间距重新计算。预览显示与 G-code 相同的行笔路径，并按配置笔宽显示；机器的真实墨迹宽度由实际笔决定，小于笔宽的孔洞可能无法保留。
- 旧文件中丢失的填充区域无法自动恢复，请重新导入带填充信息的 SVG。公式需先转成含填充区域的字形路径；这会绘制实心印刷体，不需要字库，也不是手写笔顺恢复。
- 每个独立子路径连续落笔绘制，子路径之间抬笔；不套用表格框线每 5 mm 抬笔的设置。曲线按最终毫米尺寸离散，几何误差上限为 0.05 mm。预览与 G-code 共用同一组笔迹。
- 文字须先在矢量编辑器中转为路径。位图、`use` 引用、样式表、虚线、裁剪、蒙版、滤镜和渐变等暂不支持，导入会明确报错；脚本与外部资源不会执行或加载。隐藏、完全透明的图形不绘制。
- 单个 SVG 最大 1 MB、1000 个图形、10000 个路径段；整页规范化矢量路径文本最大 1 MB，每个矢量对象最多生成 100000 个离散点。复杂图形请先简化。自动保存空间不足会提示错误；失败导入保留当前文档。
- 纯矢量图绘制无需字体数据集。导入不会启动机器；检查纸面预览和设备参数后，使用现有“开始书写”入口下发。

SVG 解析使用 [svgelements](https://github.com/meerk40t/svgelements)，XML 入口使用 defusedxml；后端绘图模块独立于服务编排和机器传输。
