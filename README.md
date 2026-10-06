# Pygmalion · 诗词自动书写

用于诗词投稿、单线字形排版、书写任务队列与 GRBL 实体机器控制的工作区。管理入口直接打开，所有页面共用紧凑侧栏和固定任务状态栏。

## 启动

需要 Python 3.11、Poetry、Node.js 22.12+ 与 pnpm。

```powershell
poetry config virtualenvs.in-project true --local
poetry env use python3.11
poetry install
pnpm --dir src/web install --frozen-lockfile
pnpm --dir src/web run build
poetry run python -m pygmalion.commands.app serve
```

成员投稿：<http://127.0.0.1:8080/>；管理工作区：<http://127.0.0.1:8080/admin>。

需要同一局域网成员访问时，启动命令增加 `--host 0.0.0.0`；管理页显示本机发现的局域网投稿地址，可复制分享。

只演练书写队列：

```powershell
poetry run python -m pygmalion.commands.app serve --dry-run
```

演练只作用于书写任务。机器页的检测、点动、抬落笔、原点设置和恢复操作始终针对实际设备，界面按实际接口结果反馈。

支持 `PYGMALION_HOST`、`PYGMALION_PORT`、`PYGMALION_DRY_RUN`、`PYGMALION_HOME`。运行数据根目录默认是仓库；使用 `--data-home` 指定另一目录时，字库位于该目录的 `data/fonts/`。`.env.example` 提供示例，VS Code 调试配置读取 `.env`。

## 页面与行为

| 页面 | 路径 | 行为 |
| --- | --- | --- |
| 成员投稿 | `/` | 姓名、诗题、正文、字库、方向、落款与署名 |
| 投稿结果 | `/result/:id` | 投稿编号、真实笔画预览、缺字与加载提示 |
| 投稿管理 | `/admin` | 状态筛选、统计、排版入口、删除和投稿地址分享 |
| 排版工作台 | `/admin/layout/:id` | 字体、方向、落款、字号、间距、手写感与入队 |
| 书写队列 | `/admin/queue` | 当前任务、排队、完成、失败和中止记录 |
| 机器控制 | `/admin/machine` | 串口检测、坐标、点动、抬落笔、原点、备份和恢复 |
| 纸张校准 | `/admin/calibration` | 纸张规格、刻度区、拖动与数值偏移、越界检查和独立保存 |
| 参数设置 | `/admin/settings` | 软件参数保存；GRBL 指令预览、备份与确认写入 |

- 无登录页、口令、会话或鉴权接口；侧栏底部为“返回投稿”。
- 侧栏 208px、菜单 36px、图标 16px、按钮 32px；主内容最大宽 1140px。白昼 / 夜间共用语义颜色、字体和组件结构，主题选择保存在浏览器。
- 投稿和任务数量、网络地址、设备状态、发送进度均来自接口。仓库启动时不创建示例投稿。
- “指令发送进度”是已发送指令占比。真实任务在机器返回 Idle 后才标记完成。
- 入队时冻结生成的轨迹、纸张映射和机器参数。预览与书写使用同一套解析与随机种子；后续参数修改不会影响已入队任务。
- 缺字具体列出；最终字形仍在加载或全部字符无法绘制时禁用书写。排版自动缩小时显示要求字号与实际字号。
- 中止停止发送后续指令，缓冲区中的动作可能继续数秒；急停使用正在打开的串口发送实时停止与复位指令。重新书写创建新任务。
- 删除投稿保留独立的冻结任务与历史，不自动停止书写。程序重启后未完成任务标记为已中止，不自动重发运动指令。
- 校准只保存配置，不移动机器。软件参数与校准独立保存；控制板参数必须显式确认，且先完成备份才写入。
- 小屏保留全部业务入口，排版切换预览 / 参数，校准支持触控拖动、键盘和等价数值输入。

## 工程结构

```text
src/pygmalion/commands/app.py  Typer CLI 与基础日志
src/pygmalion/web/app.py      FastAPI 生命周期、API 与静态前端托管
src/pygmalion/web/api.py      业务接口，保留参考项目 API 字段
src/pygmalion/core/          SQLite、字库、排版、轨迹、GRBL 与单线程任务队列
src/web/src/                React / TypeScript 页面、共享组件与语义样式
src/web/public/design/      Figma 导出的原始 SVG 图标和状态标记
data/fonts/                中文单线字库与授权文件
tests/                     业务、任务和机器协议边界检查
```

FastAPI 使用 [官方 frontend 托管方式](https://fastapi.tiangolo.com/tutorial/frontend/) 提供 `src/web/dist` 和 SPA 页面；未知 `/api` 请求返回 404，静态资源和 OpenAPI 不经过 SPA 回退。前端未构建时，启动会明确提示执行 `pnpm run build`。

运行生成的 `config.json`、`data/poems.db`、`output/`、`backups/` 均已忽略。清华源保留为补充源；本次其部分下载返回 403，安装使用官方 PyPI，并提交 Poetry 与 pnpm 锁文件。

## 开发与验证

```powershell
poetry run python -m pygmalion.commands.app serve --dry-run
pnpm --dir src/web run dev
```

Vite `/api` 代理到 `127.0.0.1:8080`。VS Code 的 `backend` 调试先执行 `web: build`；`CLI` 调试启动 Typer。

```powershell
poetry run pytest -q
poetry run ruff check src/pygmalion tests --select F
pnpm --dir src/web run lint
pnpm --dir src/web run build
pnpm --dir src/web run test:e2e
```

浏览器检查使用本机 Chrome，Poetry 需在 PATH 中。测试服务使用 8097 端口、`.cache/e2e` 独立数据目录和任务演练模式，自动复制内置字库；不会调用真实运动接口。检查覆盖 320 / 390 / 768 / 1024 / 1440 / 1920px、长中文、共享导航、双主题、删除对话框键盘恢复、真实投稿入队、校准越界、软件参数保存与设备不可用反馈。

本轮已通过 18 项后端检查、5 项浏览器流程，以及 lint、类型检查和构建。实体机器的行程、坐标、抬落笔与急停效果仍需连接设备后实机验证。

## 设计交接与来源

[Figma 设计文件](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=8-874) · [桌面原型](https://www.figma.com/proto/m1toyRcCag4OOsJNMebFDb?node-id=8-874&starting-point-node-id=8-874&scaling=scale-down) · [共享侧栏组件](https://www.figma.com/design/m1toyRcCag4OOsJNMebFDb?node-id=77-1421)

121 处设计稿侧栏已同步至五种选中状态的共享组件。设计中展示的数量、地址和进度用于说明布局，代码按实际数据呈现。实现以 MANAS 操作关系组织连续工作表面，桌面视觉采用用户最新要求的中性灰 Console 语义扩展。

业务逻辑迁入自 `handwriting-plotter`，改用 Poetry / Typer / FastAPI / React 架构并删除鉴权。保留原项目 MIT 授权；内置汉字笔画数据的 Arphic 授权见 `data/fonts/ARPHICPL.TXT`。不复制原项目的投稿数据库、机器配置或私有 GFont 运行环境。
