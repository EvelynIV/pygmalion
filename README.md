# Pygmalion · 写字机控制台

以单页书写工作台为中心的通用写字机控制台。直接编辑正文、预览真实单线字形并安排当前书写任务；无鉴权，保留机器控制与参数设置。UI 对齐 Figma 单页 V1。

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
| 书写工作台 | `/`、`/admin` | 文档名称、正文、单页预览、字体、方向、字号、间距、页边距与对齐 |
| 机器控制 | `/admin/machine` | 可收起连接信息、串口检测、点动、抬落笔、原点、备份和恢复 |
| 参数设置 | `/admin/settings` | 软件参数保存；GRBL 指令预览、备份与确认写入 |

- 没有投稿、公开提交、队列或独立纸张校准页面。侧栏默认 44px 窄轨，可展开至 168px，导航文字 14px；头部 54px、底部任务栏 52px、紧凑控件 32px。
- 白垣沿用已批准的中性控制台底色；苍渊使用技能锁定的深渊、夜海、铸铁与初光。双主题共用 DOM 和语义变量，主题与导航宽度保存在浏览器。
- 初次打开为空文档，不创建示例数据。文档名称只用于保存和任务辨识，不参与纸面渲染。只写正文，保留空行、换行、缩进；长行按纸面可用范围换行。
- V1 支持 A4 / A5 单张纸。超出范围时提示并禁用书写，不自动缩字、分页或截断；缺字、空白、预览失败同样禁止书写，服务端再次验证。
- 保存会在当前浏览器保留最近一份文档，并导出 `.pygmalion.json` 文件。打开接受该 JSON 或 UTF-8 `.txt`；没有云端文档库。新建或打开前如有未保存修改，会要求确认；保存失败可重试。
- 预览和落笔共用正文排版及真实字库笔画。页边距辅助线仅在 SVG 显示，不进入 G-code。书写时冻结正文、纸张参数、轨迹和机器参数；后续设置不影响已开始的任务。
- V1 只允许一个活动书写任务。进度明确为“指令发送进度”，100% 后仍等机器返回 Idle 才完成。中止停止后续发送；急停保留原有真实控制结果。程序重启不自动续写。
- 演练仅作用于书写任务；机器页的检测、点动、抬落笔和恢复仍为真实动作。浏览器与自动测试使用隔离运行目录及演练模式，不自动连接物理设备。
- 机器页通过 WebSocket 复用控制连接、推送坐标；离开机器页释放串口，断线不重发运动。连接抽屉折叠后仍显示已读取的状态和端口。
- 本次新增 `/api/workbench/preview` 与 `/api/workbench/write`。旧数据库与任务执行器保留兼容：书写时内部创建关联记录，但不会将其标题或作者送入新排版器，也不会在 UI 暴露旧投稿系统。

[Figma 白垣单页基线](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=8-1071) · [苍渊基线](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=8-3344)

## 工程结构

```text
src/pygmalion/commands/app.py  Typer CLI 与基础日志
src/pygmalion/web/app.py      FastAPI 生命周期、API 与静态前端托管
src/pygmalion/web/api.py      业务接口，保留参考项目 API 字段
src/pygmalion/fonts/         字库发现、Hugging Face 数据集与字形来源
src/pygmalion/writing/       排版、手写效果、平滑路径、SVG 预览与离线生成
src/pygmalion/machine/       机器坐标转换、G-code 与 GRBL 通信
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

浏览器检查使用本机 Chrome，Poetry 需在 PATH 中。测试服务使用 8097 端口、`.cache/e2e` 独立数据目录和任务演练模式，自动复制内置字库；不会调用真实运动接口。检查覆盖桌面 1024 / 1440 / 1920px、伸缩导航、双主题、文档保存与打开、确认对话框键盘恢复、真实笔画预览、单页溢出、缺字、预览失败恢复、演练中止、软件参数保存与 WebSocket 控制边界。

验收命令见上方；测试覆盖通用正文与兼容接口，不连接实体机器。实体机器的行程、坐标、抬落笔与急停效果仍需连接设备后实机验证。

## 设计交接与来源

[Figma 设计文件](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=8-1071) · [桌面原型](https://www.figma.com/proto/m1toyRcCag4OOsJNMebFDb?node-id=8-1071&starting-point-node-id=8-1071&scaling=scale-down) · [共享侧栏组件](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=77-1421)

实现对齐单页 V1 的三列编辑工作台与三项导航。设计稿示例正文、端口和进度只用于布局，代码初次打开为空文档，设备与任务状态按实际数据呈现。MANAS 组织操作关系，苍渊·白垣负责主题角色；纸面实际笔画取代稿中的文字排版示意。

业务逻辑迁入自 `handwriting-plotter`，改用 Poetry / Typer / FastAPI / React 架构并删除鉴权。保留原项目 MIT 授权；内置汉字笔画数据的 Arphic 授权见 `licenses/ARPHICPL.TXT`，数据集目录也附有副本。不复制原项目的投稿数据库或机器配置。

## 字体数据集约定

递归扫描 `model-bin`（或指定的字体目录）下所有 `train.parquet`，支持 `model-bin/arp244/cjk-medians/train.parquet` 等多层目录。每套字体通过 `datasets.load_dataset` 读取。数据集目录名即字体 ID，自动出现在字体列表中；不同层级出现同名目录时，以相对字体根目录的路径作为 ID，避免重名。默认选择 `cjk-medians`；所选数据集缺字时，仅由其他数据集补充。

每行代表一个字符：`char` 为字符，`lengths` 为各笔画的点数，`x`、`y` 为按笔画顺序展平的整数坐标。坐标除以 10000 后为 em 单位，y 向下；横纵坐标数量必须一致，且等于笔画点数之和。

汉字、西文、数字和标点都必须由数据集提供；缺失时提示缺字，单页书写入口禁止执行。没有非数据集字体回退，也不需要 Windows、Java 或 CNC Draw 加载器。当前加载的是本地 Parquet 文件；从 Hugging Face 下载后放入上述目录即可，不会自动拉取远程仓库。

可通过环境变量或启动参数指定字体目录，例如：

```bash
PYGMALION_FONTS_DIR=/srv/fonts poetry run python -m pygmalion.commands.app serve
# 或
poetry run python -m pygmalion.commands.app serve --fonts-dir /srv/fonts
```

此时读取 `/srv/fonts/<字体ID>/train.parquet`。相对路径以运行数据根目录为基准；未设置或空值使用 `model-bin`。`.env` 不会由普通 CLI 自动读取；可由 VS Code 调试配置载入，或先在 Bash 中执行 `set -a; source .env; set +a`。
