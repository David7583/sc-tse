# SC-TSE · Integration MVP

[English](README.en.md)

SC-TSE（语义约束与低维拓扑参数化空间求解引擎）的本地 Integration MVP。它将现有计算主链连接到一个二维工作台：载入结构化项目、选择体块、手动修改中心坐标，再执行完整计算。

这是测试版，当前验收范围是交互入口与主链集成，不代表完整 SC-TSE 开发目标已经实现，也不代表工程成果已获专业审批。

## 当前能力

- 载入内置四体块合成案例，或符合契约的项目 JSON。
- 显示明确输入或后端计算的 Site、Block、RoadGraph 折线和 Phase4 道路中心线。
- 选择体块并编辑 `center_x / center_y`；编辑后立即清除旧计算结果。
- 点击“重新运行”执行唯一主链：`Precheck → Layout → Connectivity → RoadGraph → Phase4 → Access`。
- 显示 `COMPLETED / WAITING_FOR_EXTERNAL_UPDATE / ERROR`、阻断节点和关键指标。
- 间距违规时直接显示相关体块、实际间距和最低要求，并保留原始证据。

## 环境与安装

已验证 Windows、Python 3.12.7 和 Chrome。建议使用 Python 3.12；其他系统及 Python 版本尚未验收。应用运行不需要 Node.js、模型密钥、数据库或外部服务；首次安装依赖需要访问 Python 包源。

下载并解压仓库后，在仓库根目录打开 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
```

`requirements.txt` 声明两个直接依赖；`requirements-lock.txt` 保存本次验证使用的完整 Python 依赖版本。不要复制其他电脑的 `.venv`。

## 启动与试用

双击：`scripts/action/development/scripts/sc_tse/frontend/launch_sc_tse_integration_v0001.cmd`。

也可以从根目录启动：

```powershell
.\.venv\Scripts\python.exe -B scripts/action/development/scripts/sc_tse/frontend/run_sc_tse_integration_v0001.py --open
```

默认地址：`http://127.0.0.1:8770`。停止服务可在服务窗口按 Ctrl+C；启动失败会保留错误信息。

1. 默认载入四体块合成案例，点击“重新运行”，应显示 `COMPLETED`。
2. 选择 `B3_ADMIN`，保持 `center_x=75`，将 `center_y` 从 `130` 改为 `135`，再运行：布局和道路更新，Access 为 `PASS`。
3. 将 `center_y` 改为 `129`，再运行：与 B1 实际间距为 12 米，要求至少 13 米，Layout 阻断。
4. 恢复 `135` 并重新运行，可恢复完成状态。每次修改都需要显式点击按钮，不自动重算。

## 输入、状态和限制

项目示例：`scripts/action/development/scripts/sc_tse/frontend/integration_cases/gc_road_001_v0001.json`。这是合成验证数据，不是测量数据。

“打开项目 JSON”只接受匹配 `integration_schemas/contract_v0001.json` 的结构化项目，最大 2 MiB。它不是 CAD、自然语言或任意 JSON 导入器。对象 ID、坐标系、约束、Gate、连接请求和工程模板必须一致。

- 当前 UI 支持未旋转矩形体块与多边形场地，单位为米。
- 本轮所有体块坐标作为 `FIXED` 输入；系统校验并计算，不自动移动其他体块化解冲突。Gate 随所属体块保持原相对偏移。
- `COMPLETED` 表示本次六个节点通过；`WAITING_FOR_EXTERNAL_UPDATE` 表示需要用户更新输入；`ERROR` 表示请求、适配或传输等错误。未执行节点不显示伪造结果。
- 道路显示为计算得到的中心线，不是完整道路面或施工图。没有未经计算的绿化、停车或建筑细部。
- 不包含拖拽、实时或增量重算、3D、自动优化、多方案高级对比、复杂图层、WebSocket、队列或微服务。
- 目前不保存编辑后的项目；刷新页面会丢失未保存的编辑。原始案例保持只读。
- 临时工作流路径只用于本次追溯，不能作为持久恢复入口。公开支持的计算入口为 Integration API，不将内部通用 DAG 的其他 Provider 作为本发布包能力。

## 目录与接口

```text
config/action/config/                 主链配置
scripts/action/development/scripts/sc_tse/
  *.py                                主链依赖闭包，27 个核心脚本
  schemas/                            后端契约
  frontend/                           唯一当前前端、API、案例和启动器
tests/                                API 与可选浏览器验收
SOURCE_MANIFEST.json                   复制来源哈希与发布副本变更记录
PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md  原有源码格式文档及根目录识别标记
```

保留原相对目录结构及根标记，避免改写后端路径逻辑。`orchestrator_v0003`、`orchestration_state_v0002` 等较低版本是当前主链实际依赖，不是历史副本。旧 Showcase、归档、独立实验、数据库及历史运行结果均不包含。

前端只处理输入和展示。后端 API 将结构化请求适配到 `run_sc_tse_orchestrator_v0004.run_workflow`，无平行计算路径。

| 接口 | 用途 |
| --- | --- |
| GET `/api/project` | 获取内置项目及输入几何 |
| POST `/api/project` | 校验并载入项目 |
| POST `/api/run` | 执行完整主链并返回结构化结果 |

POST 需要 `Content-Type: application/json`、`X-SC-TSE: integration-v1` 和同源请求。服务只绑定 `127.0.0.1`；它是本地单用户工具，不用于公网部署。并发计算返回 409，无后台队列。

## 测试

无需启动 HTTP 服务即可运行 8 项接口测试：

```powershell
.\.venv\Scripts\python.exe -B tests/test_integration_mvp_v0001.py
```

可选浏览器测试需要 Node.js、npm、Chrome 和 Playwright；这些不是应用运行依赖：

```powershell
npm ci
$env:DEMO_BROWSER_CHANNEL="chrome"
$env:SC_TSE_TEST_OUTPUT=Join-Path $env:TEMP "sc-tse-browser-results"
npm test
```

先在另一个终端启动本项目服务。测试默认连接 8770；可用 `SC_TSE_TEST_URL` 指定测试服务地址。不要连接不同版本的服务。测试会验证载入、重算、坐标修改、间距阻断、恢复、错误响应、网络故障及页面布局，并把截图和报告写到指定目录。

## 常见问题

- 找不到 Python：安装 Python 3.12，并确认 `py -3.12 --version` 可用。
- `MissingRuntime`：从仓库根目录创建 `.venv`；保留原目录结构和根标记文件。
- 端口被占用：先停止旧服务，或修改 `frontend/integration_config_v0001.json` 中的端口。
- 页面仍旧：刷新后重新载入案例并运行。
- 修改坐标被阻断：查看具体约束证据；一些初始间距恰好位于硬约束下限。
- 缺少模块：用 `.venv` 内的 Python 安装锁定依赖，不要混用系统 Python。

## 发布与回退

发布内容仅是此目录。不要上传 `.venv`、`node_modules`、数据库、密钥、日志或测试产物；`.gitignore` 已配置常见排除项。不要上传发布准备目录中的测试环境和内部验收材料。

核心和前端运行脚本从已验证源文件复制，未迁移或修改原项目。测试副本只调整路径、基准读取与间距显示断言。`RELEASE_VALIDATION.md` 记录验收结果。许可证按项目所有者要求暂不添加。

回退：停止本副本服务并使用原项目；本发布包不修改原项目、数据库或原始数据。

发布副本的 CMD 启动器仅将 LF 换行为 CRLF，修复 Windows 命令解析；脚本逻辑保持一致。`.gitattributes` 保证克隆后保留 CMD 换行要求。启动器实际 dry-run 已通过，未输出解析错误。
