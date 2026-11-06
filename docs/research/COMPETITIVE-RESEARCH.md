# 同类开源项目调研：自托管 AI 助理 / IM 机器人 / Agent 控制台

> 调研日期：2026-11-06。对象：zhike 1.5.1（Python FastAPI + React 控制台，QQ OneBot v11 网关，ReAct 工具循环，混合检索，权限与审计）。
>
> 证据边界：结论来自各项目 GitHub 仓库的 README、`pyproject.toml`/`package.json`、Dockerfile、compose 与关键源码文件（下文标注路径与行号）。star 数为 GitHub API 当日快照。
> 环境限制：`raw.githubusercontent.com` 直连超时，源码经 `ghproxy.net` 镜像读取；镜像失败的文件不做推断。无法验证的一律标「不确定」。

---

## 0. 速览

| 项目 | 一句话定位 | 部署形态 | 桌面版 | CLI 入口 | 首次启动引导 | 许可 |
|---|---|---|---|---|---|---|
| Open WebUI | 自托管 LLM 聊天前端 + 工具/函数平台 | pip / Docker（5 种 tag）/ 桌面 | Electron | `open-webui serve`（Typer） | 首用户自动 admin + Web onboarding 标志 | 自定义（保留品牌） |
| Dify | LLM 应用与 Agent 工作流平台 | 仅 Docker Compose（47 个服务） | 无 | 无（靠 compose） | `/install` 一次性向导 + INIT_PASSWORD | 自定义（Apache 2.0 + 条件） |
| Flowise | 可视化 Agent/工作流搭建 | npm / Docker | 无 | `npx flowise start`（oclif） | 无向导，靠环境变量 | Apache-2.0，**已归档** |
| n8n | 工作流自动化 + AI Agent | npm / Docker / 一键脚本 | 无 | `n8n start` 等约 18 个子命令 | `POST /owner/setup` 首用户设 owner | Sustainable Use（非 OSI） |
| LibreChat | 多厂商 ChatGPT 替代前端 + Agent | Docker Compose（6 服务） | 无 | 无（`npm run backend`） | 首用户自动 ADMIN | MIT |
| LobeHub（原 Lobe Chat） | Agent 团队/工作台 | Docker / Vercel / 云模板 | Electron | 无（Next.js 服务） | 无向导，环境变量 | 自定义 |
| AnythingLLM | 本地优先的一体化 RAG + Agent | Docker / 桌面安装包 | 安装包（壳技术未证实） | 无 | 5 步 Web onboarding + 完成标志 | MIT |
| Langflow | 可视化 flow + MCP/API 服务端 | pip/uv / Docker / 桌面 | 官方桌面版（Win/macOS，技术栈未证实） | `langflow run`（Typer，24 个选项） | 超管密码环境变量，无向导 | MIT |
| NoneBot2 + nb-cli | Python 异步 IM 机器人框架 | pip（框架）/ CLI 独立包 | 无 | `nb`（独立包 nb-cli） | `nb create` 脚手架 | MIT |
| AstrBot / LangBot / Koishi | IM 机器人平台三个代表 | pip/uv tool / Docker / 桌面 | AstrBot=Tauri；Koishi=Go+WebView2；LangBot 无 | `astrbot init/run`、`uvx langbot`、`koishi` | CLI init + Web 面板 | AGPL / Apache / MIT |

star 快照：n8n 209.6k、Dify 160.5k、Open WebUI 157.9k、Langflow 156.4k、LobeHub 84.1k、AnythingLLM 68.0k、Flowise 55.6k、LibreChat 46.8k、AstrBot 46.2k、LangBot 18.7k、NoneBot2 7.8k、Koishi 6.3k。

**两个必须先说的结论**：
1. **Flowise 已归档**（README 顶部明写 "Flowise has been archived"，仓库 `archived=true`，最后推送 2026-08-13）。它可以作为历史设计参考，但不应作为学习主线，更不该照搬。
2. 10 个候选里 **只有 4 个提供官方桌面安装包**（Open WebUI、AnythingLLM、Langflow、AstrBot），且 **没有一个是把 FastAPI + 重依赖 Python 服务用 PyInstaller 单文件打的**——主流是 Electron/Tauri 外壳 + 内置运行时，或者干脆只发 Docker。

---

## 1. Open WebUI

**定位与核心能力**：自托管 LLM 聊天前端，把 Ollama 与任意 OpenAI 兼容端点统一成一个多用户工作台，并带 Tools/Functions/Pipelines 三类扩展。

**值得学习的具体设计**

- **CLI 与 Web 分工清晰**：`backend/open_webui/__init__.py` 用 Typer 定义 `serve(host, port)`；启动时若没有 `WEBUI_SECRET_KEY`，自动生成随机密钥写到 `.webui_secret_key` 并打印「已生成」——**首次启动零配置即可跑**。`pyproject.toml` 里 `[project.scripts] open-webui = "open_webui:app"`，`pip install open-webui && open-webui serve` 两条命令完成安装。这是 zhike 最该先抄的一条。
- **身份自举（bootstrap）**：`main.py:416` 若配置了 `WEBUI_ADMIN_EMAIL/PASSWORD` 就在启动时创建 admin 并关闭注册；否则 `main.py:2298` 用 `onboarding = not await Users.has_users()` 告诉前端「这是首次」。`routers/auths.py:589` 的注释点明了关键：**先按默认角色插入，再原子地数用户数，只有唯一用户才提升为 admin**，以此规避首用户注册的 TOCTOU 竞态。这是「首用户即管理员」的正确写法。
- **配置分层**：`config.py` 的 `PersistentConfig` 模式——每个配置项有环境变量默认值，同时可写 DB 覆盖；`routers/configs.py` 提供读写。等价于 zhike 现有的「代码默认 < env < DB」，但 Open WebUI 把「哪些键允许运行时改」做成了显式白名单，且前端配置页由 schema 驱动。
- **插件加载**：`utils/plugin.py` 把 DB 里存的工具源码写临时文件，`types.ModuleType` + `exec()` 进模块命名空间，模块名带 uuid（`tool_<id>_<uuid>`），执行完 `sys.modules.pop` 防泄漏；frontmatter 里的 `requirements` 会触发 `pip install`（`asyncio.to_thread` 避免阻塞事件循环）。**风险点**：这是「可执行代码即插件」的最危险形态，靠 `ENABLE_PLUGINS/ENABLE_TOOLS/ENABLE_FUNCTIONS`（`env.py:1182-1185`）三个开关 + `OFFLINE_MODE` 兜底。zhike 明确不做远程插件市场，正确做法是只借它的「模块隔离 + 失败不污染进程」，不借 auto-pip。
- **流式与实时**：Socket.IO（`python-socketio`）+ 会话池 `session_pool.py`（含 `cleanup_response`/`stream_wrapper`），断连后能取消上游请求，避免「用户关了页面，token 还在烧」。
- **部署矩阵**：同一份代码出 `:main`（含本地 AI 依赖）、`:slim`（外部 embedding/解析/语音）、`:cuda`、`:ollama`（内置 Ollama）、`:dev` 五种镜像，README 用表格解释差异。这是「一个产品多种部署画像」的范本。
- **可观测性**：Dockerfile 的 `HEALTHCHECK` 直接 `curl /health | jq -ne 'input.status == true'`；非 root（`USER $UID:$GID`）+ OpenShift 任意 UID 的权限硬化。

**部署与分发**：pip（Python 3.11–3.12，91 个依赖）、Docker 五 tag、桌面版 Electron（`open-webui/desktop`：electron 45.2.1 + electron-builder 26，Windows NSIS `open-webui-x64-setup.exe`、macOS dmg、Linux deb/AppImage）。

**CLI / 首次启动**：`open-webui serve` 一条命令；密钥自动生成；Web 端首用户注册即成 admin，`onboarding` 标志驱动欢迎流程。桌面版则是「点击 Get Started → 桌面端自动下载并安装后端 → 启动 → 打开」，即**桌面壳负责装后端，不负责配置业务**。

**README 组织**：极短（132 行）。顶部一张 `open-webui-demo.gif` 作为唯一动图，紧接徽章墙，然后 Installation（Python / Docker / Desktop 三小节）、Ecosystem、Troubleshooting、Docs、Support、Security、Star History。**没有截图墙**——演示动图 + 文档站外链承担了全部说服工作。

---

## 2. Dify

**定位与核心能力**：把 LLM 应用/Agent/RAG 做成可编排、可发布的平台，自带插件市场与 API 服务端。

**值得学习的具体设计**

- **一次性初始化向导做成「有门禁的公开端点」**：`api/controllers/console/setup.py` 的 `POST /setup` **故意不鉴权**（首次引导没有会话），但叠加四道保险：仅自托管版（`only_edition_self_hosted`）、`SetupService` 的进程内锁、`setup_at is not None or has_tenants()` 的幂等拒绝（`SetupAlreadyCompletedError`）、以及初始化口令校验。`api/services/init_validation_service.py` 用 `hmac.compare_digest` 比 `INIT_PASSWORD`，并在校验前先查 `has_tenants()`，避免「已初始化实例被重新 setup」。这套「公开但一次性 + 口令 + 幂等」是 zhike 做首启向导最值得照搬的安全模型。
- **插件进程外隔离**：`docker-compose.yaml:617` 单独一个 `plugin_daemon` 容器（`langgenius/dify-plugin-daemon:0.6.14-local`），映射 5003 调试端口，插件通过 HTTP/WS 与主 API 通信。**插件崩溃不会带走主进程**，插件依赖冲突也不会污染主镜像。这是 zhike 网关 `PluginManifest.trust = "sandboxed"` 已有概念但尚未实现的形态。
- **部署即架构**：compose 里 47 个服务（含 profiles 与可选向量库：weaviate/qdrant/pgvector/chroma/milvus/opensearch…），把「选型」变成「改 `.env` 里的 profile」。代价是重——最小可用也要 api/worker/web/db/redis/sandbox 一串。
- **流式协议契约化**：`controllers/service_api/app/completion.py` 把 `response_mode: "blocking" | "streaming"` 写进 OpenAPI 描述，`streaming` 明确为 SSE；Agent 应用只允许 streaming。**协议是文档的一部分，不是前端猜的**。
- **健康检查与初始化分离**：`init_permissions` 是一次性 busybox 任务（`service_completed_successfully` 才启动 api），把「卷权限」这种一次性副作用从主进程剥离。

**部署与分发**：只发 Docker Compose（另有 Dify Cloud）。无桌面版、无 pip 包、无 CLI 安装器。

**CLI / 首次启动**：`git clone → cp .env.example .env → docker compose up -d → 打开 http://localhost/install → 填管理员账号密码 → 完成`。Dify 的「CLI」就是 compose；它把复杂度压在 Docker 里，用 Web 向导吸收业务配置。

**README 组织**：184 行。首图 `cover-v5-optimized.png` 是产品界面拼接封面（静态），另有 `providers-v5` 一张功能截图；其余是徽章、多语言 README 矩阵（20+ 语言）、feature 列表、Quick Start、License。**静态封面 + 少量关键截图**，不做 GIF。

---

## 3. Flowise（已归档，仅作历史参考）

**定位与核心能力**：拖拽式 LLM flow / Agent 搭建器，导出为 API 或嵌入应用。

**值得学习的具体设计**

- **SSE 流式中枢**：`packages/server/src/utils/SSEStreamer.ts` 用 `Map<chatId, Client>` 管理连接，另有 `observers: Map<sourceChatId, Set<observerId>>`——**同一份流可以被动广播给多个观察者**（多标签页同步、webhook 旁听、管理端影子查看、评测 harness），且有 `heartbeatInterval` 心跳保活。这个 observer 设计比 zhike 现在「一条 SSE 只服务一个浏览器」更进一步，且实现成本不高。
- **oclif 命令体系**：`packages/server/src/commands/start.ts` 极薄（`DataSource.init()` → `Server.start()`），把生命周期收在 `Server` 单例里，`stopProcess` 做优雅退出。CLI 只做编排，不做业务。
- **npm 全局安装 + `npx flowise start`**：Node 生态里最低摩擦的分发方式，无需 Docker。

**部署与分发**：npm 全局包（Node ≥20）、Docker、Docker Compose。

**CLI / 首次启动**：`npm install -g flowise` → `npx flowise start` → 打开 3000。无首次向导，配置全靠环境变量。

**README 组织**：243 行，顶部 logo + 演示 GIF（`flowise_agentflow.gif`）+ 目录；Quick Start / Docker / Developers / Env Variables / Docs 分节。**归档公告直接压在标题上方**，这点值得学：项目死亡要写在第一屏，而不是藏进 issue。

**对 zhike 的价值**：只取 SSEStreamer 的 observer 思路；其余不要学（项目已停更）。

---

## 4. n8n

**定位与核心能力**：可视化工作流自动化平台，1500+ 集成，近年加上 Agent 节点，定位「能跑生产的工作流」。

**值得学习的具体设计**

- **CLI 是一等公民**：`packages/cli/src/commands/` 下有 `start / worker / webhook / execute / execute-batch / export / import / list / audit / db / ldap / license / mfa / user-management / publish / unpublish`。其中 `user-management:reset` 明确是「把数据库恢复到默认 owner 状态」的运维逃生门——**产品内置自救命令**，而不是让用户手改数据库。zhike 目前完全没有这类入口。
- **首用户即 owner 的专用端点**：`controllers/owner.controller.ts` 的 `POST /owner/setup` 标了 `skipAuth: true`，注释写明「Promote a shell into the owner of the n8n instance」——**空实例 = shell，第一个 setup 请求把它变成 owner**，之后该端点自然失效（因为已有 owner）。这比「注册页 + 角色判断」更直白，也更适合单机自托管产品。
- **交互式启动**：`commands/start.ts` 支持 `--open/-o` 自动开浏览器，并在终端提示 `Press "o" to open in Browser.`——**CLI 不是冷冰冰的守护进程，它知道用户下一步要干什么**。
- **社区节点安装的工程护栏**：`modules/community-packages/community-packages.service.ts` 里，安装串行化（注释：install/update/remove 一次一个，避免共享磁盘状态被并发破坏）、`verifyIntegrity()` 校验包完整性、`checkIfVersionExistsOrThrow` 先查 registry、下载目录固定（`nodesDownloadDir`）、内置包禁止被覆盖。**插件安装是「有事务边界」的操作**。
- **配置即代码**：workflow 可 export/import JSON，CLI 能 `execute` 单个 workflow——把「可重复执行」做成产品能力。

**部署与分发**：npm（`npx n8n`）、Docker（`docker.n8n.io/n8nio/n8n`）、`curl -fsSL https://get.n8n.io | sh` 一键脚本。**无桌面版**（官方仓库无 desktop 目录，`n8n-desktop` 仓库 404）。

**CLI / 首次启动**：`npx n8n` 起服务 → 浏览器打开 → 首个访问者走 owner setup。README 只给两种启动方式（一键脚本 / docker run），细节全部外链 docs。

**README 组织**：极短（73 行）。顶部 banner JPG + 一张界面截图 PNG，然后 Key Capabilities、Quick Start、Resources、Support、License。**用一张截图讲清产品形态，不堆功能列表**。

---

## 5. LibreChat

**定位与核心能力**：多厂商（OpenAI/Anthropic/Google/Bedrock/自定义端点）ChatGPT 替代品，带 Agents、MCP、代码解释器、多用户与租户隔离。

**值得学习的具体设计**

- **首用户自动 ADMIN 且明确单租户边界**：`api/server/services/AuthService.js:417-424` 的注释与实现——`userCount === 0 ? SystemRoles.ADMIN : SystemRoles.USER`，并特别注明「Only the first user in the unscoped, single-tenant deployment bootstraps ADMIN」。**把「首用户提权」限定在无租户上下文时发生**，这是多租户产品必须补的判断，zhike 未来做多租户时可直接引用这个条件。
- **`librechat.yaml` 版本化配置**：`librechat.example.yaml` 第一行 `version: 1.3.18`，配置结构带版本号，升级时可做迁移与兼容提示；`endpoints` 段用 custom 数组描述任意 OpenAI 兼容端点（含 modelSpecs、headers、区域）。**配置文件是契约，不是随手写的 env 堆**。
- **部署形态诚实**：compose 里 api / admin-panel / mongodb / meilisearch / vectordb(pgvector) / rag_api 六个服务，README 直接列出来，并提供 Railway/Zeabur/Sealos 一键部署按钮。
- **Agent 插件包启动加载**：README 提到 "Agent Plugins: bundle deployment Skills and MCP servers into startup-loaded packages"——**启动时加载、部署级打包**，与 zhike 想要的「静态、签名、opt-in」最接近。
- **可观测性**：Trace Viewer（按顺序看模型对话、工具轮次、成本）、OpenTelemetry 导出、Langfuse trace 身份白名单、client build ID 标记。**成本与轮次是可见的一等指标**，不是日志里的字符串。

**部署与分发**：Docker Compose（官方唯一推荐）；另有 Railway/Zeabur/Sealos 模板。无桌面版、无 pip/npm 包。

**CLI / 首次启动**：无 CLI。`cp .env.example .env → docker compose up -d → 打开 → 注册`，首个注册用户成为 ADMIN。**纯 env + 首用户提权**，没有向导。

**README 组织**：264 行，**零截图零 GIF**，纯文字：What's New（版本级 changelog 摘要）→ Features（超长列表）→ 部署按钮 → 文档外链。可信但不好看，属于「工程师写给工程师」的风格。

---

## 6. LobeHub（原 Lobe Chat）

**定位与核心能力**：从聊天前端演进为「Agent 团队/工作台」，强调多 Agent 协作、插件、桌面端与云端一体。

**值得学习的具体设计**

- **插件三阶段路线写在 README 里**（`lobe-README.md:356-358`）：Phase 1 把插件从主体拆成独立仓库、实现动态加载；Phase 2 解决插件使用的安全与稳定性、**更准确地呈现异常状态**、架构可维护性；Phase 3 插件认证与更高定制。**「异常状态要能被准确呈现」被单列为阶段目标**——这是插件系统最容易被忽略的一半。
- **桌面端是 monorepo 一等公民**：`apps/desktop` 下有 `electron-builder.mjs`、`electronClientServerIpc`、`native-deps.config.mjs`、`dev-app-update.yml`，构建脚本 `desktop:package:app:platform`。**桌面端与 Web 共用同一套 Next.js 产物**，不是另写一个应用。
- **工具以 workspace 包粒度拆分**：`packages/builtin-tool-*`（browser/calculator/memory/knowledge-base/…）各自独立包，工具即包，天然可懒加载与独立版本化。
- **多端产物**：Docker、Vercel、Zeabur、Sealos、阿里云；`build:spa` / `build:spa:mobile` / `build:spa:workbench` / `build:spa:share` 多入口构建，同一代码库出桌面/移动/分享页。

**部署与分发**：Docker（`lobehub/lobehub`）、Vercel/Zeabur/Sealos/阿里云模板、Electron 桌面（`apps/desktop`）。

**CLI / 首次启动**：无 CLI。README 的自托管路径是「选平台 → 填环境变量（至少 `OPENAI_API_KEY`）→ 部署」。无向导。

**README 组织**：625 行，本批最长。顶部 banner 图 + TOC + **大量 GitHub user-attachments 视频/GIF**（`lobe-README.md:80,115,124,134,143,164,183`），按「Operator / Create / Collaborate / Evolve」叙事分节，再进入 Self Hosting。**用产品叙事而不是功能清单组织 README**，适合面向非纯开发者。

---

## 7. AnythingLLM

**定位与核心能力**：本地优先的一体化 RAG + Agent 工作台，「自带模型、文档、向量库、Agent，装完即用」。

**值得学习的具体设计**

- **5 步 Web onboarding，且完成状态入 DB**：`frontend/src/pages/OnboardingFlow/Steps/` 下有 `Home`、`LLMPreference`、`UserSetup`、`DataHandling`、`Survey`；后端 `server/endpoints/system.js:101-113` 提供 `GET/POST /onboarding`，状态存 `SystemSettings`。**引导是可恢复、可跳过的多步流程，不是一次弹窗**。
- **老实例自动视为已完成（关键工程细节）**：`server/utils/boot/markOnboarded.js` 在启动时判断——只要检测到 `LLM_PROVIDER`、`VECTOR_DB`、`AUTH_TOKEN/JWT_SECRET` 或多用户模式任一存在，就把 `onboardingComplete` 置真并打印一次补丁日志。**升级不能让老用户重新看新手引导**，这是所有加向导的产品都会踩的坑，AnythingLLM 用 20 行代码解决了。
- **一个代码库两种产品形态**：Docker 版支持多用户/权限/白标/嵌入 widget，桌面版是单人版；README 用 `_Docker version only_` 标注差异，而不是含糊其辞。
- **引导步骤里带「数据如何处理」**：`DataHandling` 是独立一步，把 telemetry 与隐私选择放在用户第一次接触时，而不是埋在设置里。自托管产品尤其需要这一步建立信任。

**部署与分发**：Docker（`docker/docker-compose.yml`，单服务 + 卷挂载）、桌面安装包（官网 `anythingllm.com/download`，Mac/Win/Linux）、裸机脚本（`BARE_METAL.md`）。

**CLI / 首次启动**：无 CLI。桌面版装完即用；Docker 版首次打开走 5 步向导。**向导是产品的一部分，不是安装器的附属**。

**README 组织**：330 行。首屏一个 720p GIF（托管在 GitHub Release asset：`releases/download/v1.11.2/AnythingLLM720p.gif`）+ YouTube 演示链接折叠块，然后 Features、Download、Docker 部署按钮矩阵（Docker/AWS/GCP/DO/Render/Railway/RepoCloud/Elestio/Northflank/Sealos/Easypanel）。**GIF 放 release asset 而不是仓库**，避免仓库膨胀——zhike 的 `scripts/record_demo.py` 产出正好可以走这条路。

**不确定项**：桌面版的桌面壳技术栈未能在公开仓库验证（Mintplex-Labs 下未找到 desktop 仓库，官网只给安装包）。**不要写成 Electron**，只能说「安装包形态存在，壳技术未证实」。

---

## 8. Langflow

**定位与核心能力**：可视化 flow 搭建 + 每个 flow 自动暴露为 API/MCP 工具，Python 组件可写代码扩展。

**值得学习的具体设计**

- **CLI 选项覆盖真实运维需求**：`src/backend/base/langflow/__main__.py` 的 `run` 命令有 `--host/--port/--workers/--worker-timeout/--env-file/--log-level/--log-file/--log-rotation/--open-browser/--backend-only/--ssl-cert-file-path/--ssl-key-file-path` 等 24 个选项，还有 `wait_for_server_ready` 健康探测与多 worker 安全检查（`ensure_multi_worker_safe`）。**CLI 不是参数透传，是把部署经验固化进命令**。
- **组件热加载 + fail-closed 缓存**：`src/lfx/src/lfx/interface/components.py` 中组件缓存有 `fully_loaded_components` 标记，注释写明「None means not yet loaded (fail-closed); {} means loaded, no components found」；`register_post_swap_hook` 在热替换完成后刷新缓存，且「热重载与首次发现并发时，重载结果会被折叠」——**热加载必须处理「加载到一半」的状态**，这是 zhike 做插件热更新时要抄的语义。
- **扩展分层**：`load_seed_extensions` / `load_installed_extensions` / `load_lfx_bundles_extensions` / `load_dev_extensions` 四类来源分开加载，`LFX_DEV` 支持布尔或模块白名单（`LFX_DEV=mistral,openai`）。**开发态与生产态的加载路径分离**。
- **安全默认**：正式发布包默认关闭自动登录，必须设 `LANGFLOW_SUPERUSER_PASSWORD`；开发分支才自动登录。**「方便」不能进生产包**。

**部署与分发**：`uv pip install langflow -U` + `uv run langflow run`、Docker、官方 Desktop（Windows/macOS，官网下载；技术栈未证实，第三方 E2E 仓库提到 IBM Langflow Desktop 用 Tauri，但**官方未证实**）。

**CLI / 首次启动**：`langflow run` 一条命令；`--open-browser` 自动开页面；无业务向导，超管密码走环境变量。

**README 组织**：109 行。顶部徽章 + 一段定位，然后**把 Desktop 放在 Quickstart 之前**（"easiest way to get started"），再给 uv 安装两条命令、run 命令、认证说明。**桌面版优先级高于 pip**，这是面向非开发者时的正确排序。


---

## 9. NoneBot2 生态（含 OneBot 适配器）

**定位与核心能力**：Python 异步 IM 机器人框架，插件 + 适配器 + 驱动器三层扩展，QQ 生态的事实标准底座（zhike 的网关就建在它上面）。

**值得学习的具体设计**

- **插件发现用「目录扫描 + 模块导入」而不是注册表**：`nonebot/plugin/manager.py` 用 `pkgutil.iter_modules(self.search_path)` 扫目录，跳过 `_` 开头；`importlib.import_module` 加载；`_module_name_to_plugin_id` 做 ID 归一；**重复插件直接抛错**（"Plugin already exists"），不静默覆盖。插件可以是 pip 包，也可以是目录——两种来源统一成同一套 `Plugin` 对象。
- **`require()` 声明跨插件依赖**：插件里 `require("other_plugin")` 建立依赖边，加载顺序由依赖图决定；`PluginMetadata` 携带 `name/description/usage/config/type/supported_adapters/extra`，控制台据此渲染插件卡片与配置表单。**元数据是插件的身份证，不是 README 的替代品**。
- **适配器注册是显式的**：`nonebot/internal/driver/abstract.py:89` 的 `Driver.register_adapter(adapter_cls)`，内部维护 `_adapters: dict[str, Adapter]`，重名拒绝、加载后打印 "Loaded adapters: ..."。**适配器是驱动器的一等公民，不是 if/else 分支**。
- **CLI 独立成包**：框架本身 `pyproject.toml` 没有 `[project.scripts]`，CLI 在 `nb-cli`（PyPI 包）里，能力包括 create/run/plugin/adapters/drivers 的搜索、安装、更新、卸载，以及脚手架模板。**框架与工具链解耦**，框架保持轻。
- **插件商店是独立服务**：`nb-cli` 从官方商店拉插件元数据，安装走 pip。**商店不在运行时进程里**。

**部署与分发**：pip（`nonebot2` + `nonebot-adapter-onebot`）、nb-cli（pip）、Docker（社区镜像）。无桌面版。

**CLI / 首次启动**：`pip install nb-cli` → `nb create`（交互式选模板/适配器/驱动器）→ `nb run`。**脚手架式引导**：先问清楚要什么，再生成项目，而不是生成一个空项目让用户自己填。

**README 组织**：302 行。顶部 logo + 一屏徽章（PyPI/QQ/Telegram/Discord/OneBot v11/v12 等），然后特性、安装、文档链接、生态项目。**徽章即信任状**，文档站承担全部教程。


---

## 10. AstrBot / LangBot / Koishi

### 10.1 AstrBot

**定位**：一体化 Agent 聊天机器人平台，QQ/微信/飞书/钉钉/Telegram/Slack 多平台 + 1000+ 插件 + WebUI + Agent 沙箱。

**值得学习的具体设计**

- **两步式 CLI，明确区分「装」与「跑」**：`uv tool install astrbot --python 3.12` → `astrbot init`（只首次执行，创建 `data/config`、`data/plugins`、`data/temp` 与 `.astrbot` 标记文件，支持 `-y` 跳过确认）→ `astrbot run`。`astrbot/cli/commands/cmd_init.py` 里每个目录都打印 `Created` 或 `Directory exists`，**幂等且可读**；`cmd_run.py` 支持 `--reload/-r`、`--port/-p`、`--reset-password`。`pyproject.toml` 的 `[project.scripts] astrbot = "astrbot.cli.__main__:cli"`。
- **插件加载的失败隔离**：`astrbot/core/star/star_manager.py:1076` 的 `load()` 对每个插件单独 `try/except`，失败写入 `failed_plugin_dict` 并 `_cleanup_plugin_state`，**一个插件坏掉不影响其余插件与主进程**；插件目录下的 `requirements.txt` 有依赖恢复流程（`_import_plugin_with_dependency_recovery`），支持 `ImportDependencyRecoveryMode` 多种策略；`_conf_schema.json` 定义插件配置 schema，UI 自动渲染；`watchfiles` 在 `ASTRBOT_RELOAD=1` 时热重载，未安装则明确警告「热重载不可用」而不是假装成功。
- **内置插件受保护**：`reserved` 标记的内置插件不可卸载，与用户插件分开路径（`astrbot/builtin_stars/` vs `data/plugins/`）。**系统能力不能被用户插件市场污染**。
- **桌面版是 Tauri + CPython sidecar**：`AstrBot-desktop` README 明示 `Tauri 2.10.0` + `Runtime: CPython 3.12`，稳定版/夜间版双通道，数据目录 `~/.astrbot`，AppImage 支持应用内自更新。**Rust 外壳 + Python 运行时**，避开了 PyInstaller 的坑。
- **启动参数先于核心导入**：`main.py` 顶层先用 argparse 解析 `--reset-password`，把结果写进环境变量，**再 import 核心模块**——因为核心导入时就读取该变量。这个顺序细节决定「重置密码」能不能生效。

**部署**：`uv tool install`、Docker/Docker Compose、桌面安装包（Win/macOS/Linux AppImage）、宝塔/1Panel/CasaOS、源码。

**首次启动**：`astrbot init` → `astrbot run` → 打开 WebUI；首次登录密码可来自 `ASTRBOT_DASHBOARD_INITIAL_PASSWORD`，忘记可 `--reset-password`。**CLI 负责环境，Web 负责业务**。

**README**：244 行。logo + 一张产品截图 + 多语言链接 + 徽章；Quick Start 按「云一键 / uv 一键 / Docker / 桌面 / 启动器 / 更多」排序，**把最轻的路径放最前**。

### 10.2 LangBot

**定位**：生产级多平台 Agent IM 机器人平台（Discord/Telegram/Slack/LINE/QQ/微信/飞书/钉钉/KOOK…），带插件运行时、知识库、多 Pipeline。

**值得学习的具体设计**

- **`uvx langbot` 一行启动**：无需 clone、无需 venv，`uvx` 临时环境直接跑，`http://localhost:5300`。**这是 Python 产品目前最低摩擦的分发方式**，zhike 完全可以照做（前提是先补齐 `[project.scripts]` 与可安装的包结构）。
- **插件运行时进程外 + 双端口协议**：插件由独立 Plugin Runtime 管理，控制端口 5400（主程序 WS 连接）、调试端口 5401，生产可切 stdio；`LANGBOT_PLUGIN_RUNTIME_CONTROL_TOKEN` 保护暴露的端口。插件以 `.lbpkg` 包分发，安装支持本地上传与市场。**插件是「被管理的子进程」，不是被 import 的模块**——安全边界最清晰。
- **CLI 与 Core 解耦**：`lbctl`（Go 写的独立 CLI）通过 HTTP API + API Key 管理运行中的实例，支持多 context、能力发现、资源管理、异步任务等待、Docker Compose 部署生命周期。**CLI 不需要和 Core 同语言、同进程**。
- **Agent 友好是一等需求**：仓库自带 `AGENTS.md`（符号链接到 `CLAUDE.md`）、`skills/`、`llms.txt`，并规定「API 变更必须同步 MCP server 与 skills」。**文档面向的两类读者：人和 coding agent**。

**部署**：`uvx langbot`、Docker Compose（`--profile all`）、Zeabur/Railway、Kubernetes、宝塔。

**首次启动**：`uvx langbot` → 打开 Web 面板 → 面板里配模型/平台/插件。无 CLI init，**「一条命令起服务 + 面板里做一切」**。

**README**：194 行。logo + 一句定位 + 多语言 + 徽章；一张 dashboard 截图；Quick Start 四选项（Cloud/uvx/Docker/云模板）；然后用大表格列平台与模型支持。**表格化能力清单**，适合选型对比。

### 10.3 Koishi

**定位**：跨平台、可扩展的 TypeScript 聊天机器人框架，插件市场 + Web 控制台 + 桌面启动器。

**值得学习的具体设计**

- **控制台即安装器**：插件市场在控制台内，点安装即从 npm 拉包并热加载；`packages/loader/src/index.ts:157-173` 的 `Loader.import()` 做模块缓存，`fullReload()` 支持整进程重载。**用户不需要知道 npm 的存在**。
- **`create-koishi` 脚手架**：`npm create koishi` 交互式生成项目（npm 上 `create-koishi` latest 6.4.0），选模板/插件/数据库，生成后 `koishi` 命令启动（`packages/koishi/package.json` 的 `bin: bin.js`）。
- **桌面启动器用 Go + WebView2**：`koishi-desktop` 仓库语言为 Go，`packages/` 下有 `webview2-cmake`、`shellwin`、`shellmac`、`shelllinux`、`instwin`——**用系统 WebView 而不是打包 Chromium**，安装包体积小一个数量级。
- **插件热重载**：README 明示「开发插件时只需轻点保存即可热重载」，这是 Koishi 对开发者最核心的卖点之一。

**部署**：npm（`koishi`）、`create-koishi`、桌面启动器（Win/macOS/Linux）、Docker（社区）。

**首次启动**：`npm create koishi` → `koishi` → 控制台装插件。**脚手架 + 控制台市场**，没有独立向导。

**README**：56 行，极简：logo + 三句定位（开箱即用/生态丰富/专为开发者）+ 快速起步外链 + 许可。**把 README 当名片而不是说明书**。


---

## 11. 四个专题结论（直接回答）

### 11.1 自托管 AI 应用的首次启动引导，业界标准做法是什么？

**没有单一标准，但有一个稳定的组合拳：CLI 负责「装与跑」，Web 负责「配与验」，env 只做高级覆盖。**

三种形态及代表：

| 形态 | 代表 | 适用 | 代价 |
|---|---|---|---|
| 纯环境变量 | LibreChat、LobeHub、n8n（配置侧） | 云原生/开发者产品、配置项多且稳定 | 首次体验差，文档必须极准；配置错要到运行时才炸 |
| 一次性 Web 向导 | Dify（`/install` + INIT_PASSWORD）、Open WebUI（首用户 admin + onboarding 标志）、AnythingLLM（5 步 flow） | 面向非开发者、配置项少而关键（管理员、模型、数据目录） | 要做「未初始化」状态机、幂等、并发/重放防护、老实例迁移 |
| CLI init + run | AstrBot（`init` → `run`）、nb-cli（`create` → `run`） | 开发者/服务器场景 | 用户要开终端；Windows 用户体验差 |

**成熟产品的实际选择是「CLI + Web 向导」并用**：AstrBot 和 Open WebUI 都是这样——CLI 保证进程能起来（端口、数据目录、密钥），Web 向导保证业务能配通（模型、渠道、首个对话）。纯 env 只在大规模部署或纯云产品里成立。

**四个可复用的机制细节**（这是比「做个向导」更重要的部分）：

1. **首用户即管理员，且要防 TOCTOU**：Open WebUI `routers/auths.py:589` 的做法是「先按默认角色插入 → 再数用户数 → 唯一用户才提权」；LibreChat `AuthService.js:417` 额外限定「unscoped single-tenant」。直接照抄。
2. **一次性端点必须有第二道门**：Dify 的 `/setup` 不鉴权但要求 `INIT_PASSWORD`（`hmac.compare_digest`），并在 `has_tenants()` 后拒绝再次初始化。公开 ≠ 无保护。
3. **完成状态要落库，且要能推断老实例**：AnythingLLM 的 `markOnboarded.js` 用「检测到任意一项配置存在 → 视为已完成」避免老用户升级后被重新引导。**加向导的最大事故是让存量用户重看一遍。**
4. **密钥自动生成并只打印一次**：Open WebUI 没有 `WEBUI_SECRET_KEY` 时自动生成写文件；zhike 现在要求用户手填 `ADMIN_TOKEN`，这是首启摩擦的最大来源（见 §12）。

### 11.2 打包成 Windows exe，主流 AI 应用怎么做？各自代价

先修正一个前提：**这批产品里没有一个是「FastAPI + 重依赖 Python 服务 → PyInstaller 单文件 exe」**。主流只有三条路：

| 路线 | 代表 | 做法 | 代价 |
|---|---|---|---|
| **只发 Docker** | Dify、LibreChat、n8n、LangBot（主推） | 用户装 Docker；Windows 用 Docker Desktop/WSL2 | 对目标用户要求最高；但构建/升级/隔离最省心；**这是服务器型产品的主流答案** |
| **Electron 外壳 + 内置/下载运行时** | Open WebUI Desktop（electron 45 + electron-builder 26 + NSIS）、LobeHub（`apps/desktop`）、AnythingLLM（未证实） | 壳负责 UI、更新、进程管理；后端可以是打包的二进制、也可以是「首次点击下载并安装」 | 安装包 100–200MB 起；内存占用高；但 UI 一致、自动更新成熟（electron-updater）；**首次启动要联网装后端**（Open WebUI 明说 Internet required） |
| **Tauri 外壳 + Python sidecar** | AstrBot Desktop（Tauri 2.10 + CPython 3.12） | Rust 壳 + 系统 WebView + 内嵌 Python 运行时；数据目录 `~/.astrbot`；stable/nightly 双通道 | 包体比 Electron 小、内存低；但 Rust 构建链、sidecar 生命周期、跨平台签名都要自己维护；**对 Python 依赖（native 扩展）仍要逐个验证** |
| **PyInstaller / Nuitka** | 本批未见用于「服务型 AI 应用」；多见于 CLI 小工具 | `--onefile` 或 `--onedir` | **onefile 每次启动解压到 temp，启动慢、杀软误报率高、native 依赖（sqlite-vec/torch/ffmpeg）易漏**；onedir 更稳但目录丑；**不建议作为 zhike 的第一版方案** |

**对 zhike 的具体判断**：

- zhike 的依赖里有 `sqlite-vec`、可选 `sentence-transformers`（torch）、`faster-whisper`、`edge-tts`，还有 NoneBot + OneBot 网关两个进程。**PyInstaller onefile 打这套东西是负收益**：包体 500MB+、首启解压十几秒、native DLL 漏一个就整机不可用。
- **性价比最高的第一版是「便携目录 + 嵌入 Python 运行时 + 启动脚本」**：下载 `python-build-standalone`（或直接复用用户已装的 Python）、`pip install --target` 到 `runtime/`、前端 `dist` 预构建、`zhike.bat` 调 `runtime\python.exe -m core.cli run`。产物是 zip，解压即用，不需要管理员权限，升级就是覆盖目录。**这不是「exe」，但对 Windows 用户体感接近绿色版**，且工程量约为 PyInstaller 方案的 1/3。
- **要真正的安装包/自动更新，再上 Tauri sidecar**（AstrBot 已验证这条路可行）；Electron 是最后选项（包体与内存代价最大，除非要复用现成 Web UI 生态）。
- **签名是隐藏成本**：没有代码签名证书，Windows SmartScreen 会拦；PyInstaller 产物尤其容易被 Defender 误报。**先想清楚分发渠道（GitHub Release / 国内网盘 / 官网）再决定要不要买证书。**

### 11.3 IM 机器人框架里，插件/适配器的动态加载有哪些成熟模式？

按隔离强度从弱到强：

| 模式 | 代表 | 机制 | 适用 |
|---|---|---|---|
| **同进程 import** | NoneBot2（`pkgutil.iter_modules` + `importlib.import_module`）、AstrBot（`data/plugins/<dir>` + importlib + 失败隔离）、Koishi（npm 包 + `Loader.import` 缓存） | 插件是 Python/JS 模块；加载时执行顶层代码；靠目录约定 + 元数据 + try/except 隔离失败 | 可信插件、单机自托管、插件作者=使用者 |
| **DB 源码 + exec** | Open WebUI（`utils/plugin.py`：临时文件 + `types.ModuleType` + `exec` + uuid 模块名） | 插件源码存 DB，运行时编译执行；靠 `ENABLE_PLUGINS` 总开关 + `OFFLINE_MODE` 限制 | 快速迭代、用户即开发者；**安全边界最弱** |
| **包管理器 + 完整性校验** | n8n 社区节点（npm pack → 校验 integrity → 固定下载目录 → 串行安装 + DB 记录） | 插件是 npm 包；安装是「有事务边界」的操作；内置包不可覆盖 | 生态型产品、需要版本与依赖管理 |
| **独立进程 + RPC** | LangBot Plugin Runtime（WS 控制 5400 / 调试 5401 / 生产 stdio，`LANGBOT_PLUGIN_RUNTIME_CONTROL_TOKEN`）、Dify plugin_daemon（独立容器） | 插件跑在子进程/容器，通过 WS/HTTP 通信；崩溃不传染；可单独限权 | 不可信插件、多租户、要沙箱 |
| **进程 + 沙箱** | AstrBot Agent Sandbox（代码/Shell 在隔离环境执行，会话级资源复用） | 工具调用进沙箱，主进程只拿结果 | 执行型工具（shell/code） |

**四条跨模式共识**（zhike 的 `PluginManifest` 已具备前两条，缺后两条）：

1. **清单先行**：name/version/entry/权限声明/依赖声明。NoneBot 的 `PluginMetadata`、AstrBot 的 `_conf_schema.json`、zhike 的 `PluginManifest` 都是这个思路。
2. **失败隔离**：一个插件 import 失败必须只影响它自己（AstrBot 的 `failed_plugin_dict` + cleanup 是最干净的实现）。
3. **配置 schema 化**：插件自带配置 schema，UI 自动渲染，而不是让用户改 JSON。
4. **加载语义要定义「半加载」**：Langflow 的 fail-closed 缓存与 post-swap hook 是正面教材；**没有明确定义的加载器一定会在热更新时出幽灵状态**。

**对 zhike 的取舍**：ROADMAP 明确「不做插件市场、不做远程安装」，所以**不要抄 Open WebUI 的 exec + auto-pip**，也不要抄 Koishi 的 npm 市场。应该走「NoneBot 式目录扫描 + AstrBot 式失败隔离 + 静态签名清单 + 默认 deny」的组合。zhike 的 `gateway/adapters/plugins.py` 已有 `trust: in-process | sandboxed` 与 `permissions{actions,fs,shell}` 的声明模型——**缺的是把它接进 `gateway/bot.py` 的启动流程**，目前只有单测在用它。

### 11.4 多厂商 LLM 备援与冷却，业界成熟实现参考

**唯一值得作为「参考实现」精读的是 LiteLLM Router**（`litellm/router.py` + `litellm/router_utils/cooldown_handlers.py`），它把这件事拆成了五个正交维度：

1. **备援链分层**：`fallbacks`（通用）、`context_window_fallbacks`（上下文超限时换大窗口模型）、`content_policy_fallbacks`（内容策略拒答时换模型）、`default_fallbacks`。**不是一条平铺的列表，而是按失败原因走不同分支**。
2. **重试与备援分离**：`num_retries`（同一 deployment 重试）vs `max_fallbacks`（换 deployment，默认上限 5）。混在一起会导致「同一个坏端点被重试 3 次才换」。
3. **冷却阈值化**：`allowed_fails`（默认 **3** 次失败才进冷却）+ `cooldown_time`（默认 **5s**），部署级可覆盖；支持按异常类型配 `allowed_fails_policy`（如 `RateLimitErrorAllowedFails` 单独设阈值）。
4. **429 特殊处理**：`cooldown_handlers.py:307` 对 429 单独冷却；但**单 deployment 的模型组不冷却**（`is_single_deployment_model_group` 判断），否则「只有一个模型时把它冷却了 = 服务直接不可用」。这是最容易写错的边界。
5. **冷却状态共享**：失败计数存 Router 的 `DualCache`（配 Redis 时全 worker 共享），键为 `deployment:{id}:allowed_fails:{suffix}`，TTL = cooldown_time。**多进程部署下，冷却必须是共享状态**。
6. **路由策略**：`simple-shuffle` / `least-busy` / `usage-based` / `latency-based` / `cost-based`；`enable_weighted_failover` 在 simple-shuffle 下失败后按权重重选，再走跨组 fallback。

**zhike 现状对照**（`core/llm.py`）：

| 能力 | zhike 现状 | 差距 |
|---|---|---|
| 并行竞速 | 已有：主候选 + `USER_LLM_HEDGE_SECONDS`（默认 10s）后拉起备胎，至多 2 路并行，先成功者胜 | 比 LiteLLM 的串行 fallback 更激进，是真实优势（尾延迟更低） |
| 内容级备援 | 已有：空答案/截断/解析失败都算候选失败，换下一个 | LiteLLM 没有，这是 zhike 的差异化 |
| 429 短冷却 | 已有：`USER_LLM_RATE_LIMIT_COOLDOWN_SECONDS` 默认 5s，取 `retry-after`，封顶 60s | 与 LiteLLM 语义一致，保留 |
| 失败阈值 | **无**：任何一次普通失败立刻冷却 `USER_LLM_COOLDOWN_SECONDS`（默认 120s） | 应为「连续 3 次失败才冷却」，否则单次网络抖动就把一个可用端点踢出 2 分钟 |
| 冷却状态 | **进程内 dict**（`_COOLDOWN`），多 worker 各自为政 | 应落 SQLite/共享缓存；zhike Docker 用 `--workers 1` 暂时没事，但这是定时炸弹 |
| 冷却可见性 | 只有日志 warning | 应暴露 `/api/providers/health`（剩余冷却秒数、最近错误类型），控制台可见 |
| 按错误类型策略 | 只有 429 与「其他」两档 | 至少再分：超时/连接失败/401/5xx/内容为空，各自阈值不同 |

---

## 12. zhike 基线核对（做建议前的差距清单）

| 维度 | 现状（有证据） | 差距 |
|---|---|---|
| CLI | 无 `[project.scripts]`、无 `[build-system]`、无 `core/__main__.py`；`pip show zhike` 显示未安装；README 要求 `python -m venv` + `pip install -r` + `npm ci && npm run build` + `uvicorn` 四步 | 无法 `pip install`/`uvx`；没有 `zhike init/run/doctor` |
| exe | 全仓库无 pyinstaller/nuitka/electron/tauri 引用；只有 Docker 与源码两条路 | 没有便携包/安装包 |
| 首次启动 | `ADMIN_TOKEN` 必须手填（`.env.example:45`），为空则管理接口 503；`CHANNEL_TOKEN` 同理；本机回环可用 `/api/bootstrap/local-token` 自动取令牌（`core/main.py:1166`，TokenGate 已实现），这是**优于多数竞品**的一点 | 缺「生成令牌 → 选模型 → 连 QQ → 自检」的向导；缺「已完成」标志；缺老实例自动迁移 |
| 启动器 | `scripts/launcher.py` 本地网页（8020）能拉仓库/起核心/起网关/装 NapCat Shell，但页面无「装依赖」按钮，而 `launcher.py:92` 的报错却让用户「先点『装依赖』」——**文案指向不存在的功能** | 启动器与 CLI 职责重叠但不完整；应合并为 `zhike` 子命令 |
| 配置 | 三层（默认 < env < DB）+ `config_version` 缓存失效 + 控制台热更新（`core/config.py`、`core/config_store.py`） | 已对齐 Open WebUI 的 PersistentConfig 思路，**这块不用改** |
| LLM 备援 | 竞速 + 内容级备援 + 429 短冷却 + 120s 冷却（`core/llm.py`，9 个单测覆盖） | 缺失败阈值、跨进程共享、健康视图（见 §11.4） |
| 网关插件 | `PluginManifest` + `PluginRegistry` + 权限声明 + 单测（`gateway/adapters/plugins.py`、`tests/test_adapters.py`） | **未接入启动流程**：`gateway/bot.py` 没有调用 `load_manifest`/`PluginRegistry` |
| 流式 | SSE（`core/main.py:923` `_sse`，事件名 meta/ack/delta/done），前端按事件名解析 | 事件无版本号、无序号；无 observer/多端订阅 |
| README | 14 张截图表格 + 能力表 + 诚实的限制；`scripts/record_demo.py` 能录 webm/mp4，但 `docs/media` 不存在 | 首屏无动图/GIF；Quick Start 是四步长命令，不是三条 |
| 测试 | 937 个用例、68 个测试文件 | 基础扎实，不是瓶颈 |

---

## 13. 可直接融合到 zhike 的 10 条设计建议（按投入产出排序）

> 排序依据：`(对「装得上、跑得通、看得懂」的边际提升) / (工程量 + 维护成本)`。P0 建议在 1.5.x 内做，P1 在 1.6，P2 进 2.0。

### 1. 补一条 `zhike` 命令：`init / run / doctor / config / version`（P0，投入 S–M）

**做什么**：
- `pyproject.toml` 增加 `[build-system]`（hatchling）与 `[project.scripts] zhike = "core.cli:main"`；新增 `core/cli.py`（argparse 即可，不引入 Typer/Click 新依赖）。
- `zhike init`：创建 `.env`（从 `.env.example` 复制）、**自动生成 `ADMIN_TOKEN`/`CHANNEL_TOKEN`/`EXECUTOR_TOKEN` 并只在首次打印一次**、创建数据目录；幂等（已存在则跳过并说明），带 `--yes`。
- `zhike run`：`--host/--port/--open/--reload`，内部 `uvicorn.run("core.main:app")`；`--open` 用 `webbrowser.open`（n8n 的 `--open` 同款）。
- `zhike doctor`：见第 3 条。
- `zhike version`：读 `core/version.py`，与 `VERSION` 不一致时报警。

**为什么先做**：它同时解决「没有 CLI」「首次必须手改 .env」「README 四步太长」三个问题，且是所有后续能力（向导自检、便携包、故障排查）的公共底座。参考 Open WebUI `__init__.py`（Typer + 密钥自动生成）与 AstrBot `cmd_init.py`（幂等目录创建 + 逐项打印）。

**验收**：干净目录 `pip install -e . && zhike init --yes && zhike run` 能起服务；`zhike init` 跑两次不覆盖已有 `.env`；`zhike --help` 五条子命令齐全。

### 2. Web 首次启动向导（5 步，可跳过、可恢复）（P0，投入 M）

**做什么**：新增 `GET /api/setup/status` 与 `POST /api/setup/complete`（**仅回环 + 一次性 + 已有用户则 403**，照 Dify `setup.py` 的门禁模型）；`meta` 表写 `onboarded_at`；前端 `SetupWizard`：
1. 管理员口令（已由 `zhike init` 生成则只展示「已配置」）
2. 模型厂商（复用 `ProviderCenter` 的 discover/test：填 base_url + key → 探测模型 → 选主模型/备援）
3. QQ 渠道（展示 `qq_doctor` 的结论 + 扫码入口；可跳过）
4. 自检（`/healthz`、`/readyz`、一次真实对话、检索一次）
5. 完成（写 `onboarded_at`，跳转 Dashboard）

**关键细节（别漏）**：老实例启动时若检测到 `config` 表非空或 `.env` 里已有 `ADMIN_TOKEN`，自动写 `onboarded_at` 并跳过向导（AnythingLLM `markOnboarded.js` 的做法）；向导每步可跳过、状态存 DB，刷新不丢。

**验收**：全新库首访进向导；完成后再访问不进；已有库升级后不进；并发两个标签页只有一个能完成（幂等）。

### 3. `zhike doctor`：把散落的 5 个自检合成一张表（P0，投入 S）

**做什么**：`core/diagnostics.py`，聚合：
- Python/Node 版本、`.venv` 是否存在、`frontend/dist` 是否构建
- 端口占用（8010/3001/6099/8020）
- `ADMIN_TOKEN`/`CHANNEL_TOKEN` 是否为空、core 与 gateway 是否一致
- LLM 端点连通性 + 模型列表 + 各候选冷却状态
- SQLite 可写、schema 版本、知识库索引是否可用
- NapCat 进程/端口/版本匹配

输出人类可读表格 + `--json`（给启动器和 CI 用）；`/api/doctor` 暴露同一份结果，启动器与向导复用。**把 `scripts/launcher.py` 里已重复三次的 `port_up`/dist 检查/版本检查收编**。

**为什么高 ROI**：zhike 的故障大多不是代码错，是「环境没配好」；一个命令把「猜」变成「看」。参考 n8n 把运维命令做进 CLI、Open WebUI 的 `/health`。

### 4. README 首屏改造：一张 GIF + 三条命令（P0，投入 S）

**做什么**：
- 用现有 `scripts/record_demo.py` 录 20–30 秒真实流程（控制台 → QQ 收消息 → 工具执行 → 审计页），输出 `docs/media/demo.gif`（或 mp4 + poster），首屏放在标题下。AnythingLLM 把 GIF 放 release asset、Open WebUI 用 CDN GIF，都是为了避免仓库膨胀——zhike 可先放 `docs/media`，超过 5MB 再迁 release。
- Quick Start 压到三条：`pip install zhike`（第 1 条落地后）→ `zhike init` → `zhike run`；Docker 与源码两条折叠进 `<details>`。
- 保留「诚实的限制」——这是 zhike 相对竞品的信任资产，不要为了好看删掉。

**验收**：首屏 10 秒内能看懂「这是什么、怎么跑、长什么样」。

### 5. 便携版 Windows 包：先 zip + 嵌入运行时，不碰 PyInstaller onefile（P1，投入 M–L）

**做什么**：
- `packaging/` 目录：脚本下载 python-build-standalone（Windows x64）、`pip install --target runtime/site-packages -r requirements.txt`、复制预构建 `frontend/dist`、生成 `zhike.bat`（调 `runtime\python.exe -m core.cli run --open`）。
- 产物：`zhike-1.5.x-win64.zip`，解压即用；首次运行调 `zhike init`（自动生成令牌 + 打开向导）。
- 第二阶段再评估 Tauri sidecar（AstrBot 已验证）；Electron 只在需要复用现成桌面壳能力时考虑。
- **明确不做**：PyInstaller `--onefile`（启动解压、杀软误报、native 依赖漏打包）；无签名证书前不投 SmartScreen。

**代价说明**：zip 方案约 150–250MB（主要是 torch 可选依赖）；把 `sentence-transformers`/`faster-whisper` 做成可选下载，基础包可压到 60–80MB。

### 6. LLM 备援对齐 LiteLLM 语义：阈值 + 共享状态 + 健康视图（P1，投入 M）

**做什么**（改 `core/llm.py`，新增 `core/provider_health.py`）：
- `_COOLDOWN` 从「失败即冷却」改为「连续失败计数 ≥ `USER_LLM_ALLOWED_FAILS`（默认 3）才冷却」；成功一次清零。
- 冷却状态落 SQLite 表 `provider_health(base_url, model, fails, cooldown_until, last_error, updated_at)`，多 worker 共享；启动时清理过期行。
- 按错误类型分策略：`timeout/connect` 3 次、`5xx` 3 次、`401/403` 立即冷却并标记「凭据问题」、`429` 沿用 5s 短冷却、`empty/truncated` 计 1 次。
- 新增 `GET /api/providers/health`：每个候选的 `state(ok|cooling|disabled)`、`cooldown_remaining`、`last_error`、`success_rate_1h`；控制台 ProviderCenter 显示状态点与倒计时。
- 保留现有竞速与内容级备援（这是优势，不要为了「像 LiteLLM」改成串行）。

**验收**：单次超时不再把端点踢出 2 分钟；两个 worker 看到同一冷却状态；429 仍走 5s；`tests/test_llm_failover.py` 增 3 个用例（阈值、共享、401 立即冷却）。

### 7. 把 `PluginRegistry` 接进网关启动流程（P1，投入 M）

**做什么**（改 `gateway/bot.py` + `gateway/adapters/plugins.py`）：
- 启动时扫描 `plugins/`（可用 `GATEWAY_PLUGIN_DIR` 覆盖），`load_manifest` 校验 → `PluginRegistry.load` → 按 `entry` 用 `importlib` 加载；**每个插件独立 try/except，失败只记录 `failed_plugins` 并继续**（AstrBot 的 `failed_plugin_dict` 模式）。
- 权限门放在 `core/actions/service.execute`：插件声明之外的 action 一律拒绝（zhike 已有 `check_action` 的雏形，补上「运行时按插件身份鉴权」）。
- 不做远程安装、不做市场、不做 auto-pip；插件目录默认空，文档只给模板。
- 加载结果暴露到 `zhike doctor` 与 `/api/doctor`。

**验收**：放一个坏插件（import 抛异常）进目录，网关仍能起、其余插件正常、doctor 显示失败原因；未声明权限的 action 被拒并有审计行。

### 8. 统一「降级可见」：每条外部依赖一条降级阶梯（P1，投入 S–M）

**做什么**：新增 `core/degrade.py` 定义依赖的降级链与当前档位：
- LLM：主 → 备 → 规则引擎（已有）
- 检索：向量 → FTS5 → bigram（已有）
- 搜索：ddgs → bing → tavily/serper/searxng（已有）→ 关闭并告知
- 语音：faster-whisper → 外部命令 → 关闭
- QQ：NapCat → 无渠道（Web 仍可用）

每次降级发一条结构化事件（`EV_DEGRADED`，带 `dependency/from/to/reason`），写 `events_log` + Prometheus counter + SSE 推给控制台；Dashboard 显示「当前降级项」而不是让用户从「怎么不回话」里猜。参考 Open WebUI 的 `OFFLINE_MODE` 与 zhike 已有的 `no_model` 静默策略——**降级可以静默执行，但不能静默存在**。

### 9. SSE 事件版本化 + 多端订阅（P2，投入 M）

**做什么**：
- `core/main.py:923` 的 `_sse` 改成统一信封：`{"v":1,"seq":N,"ts":...,"type":"delta|tool|done|error","data":{...}}`；前端按 `v` 做兼容，未知 `type` 忽略而不是崩。
- 参考 Flowise `SSEStreamer` 的 observer：`ws_hub` 增加「按 session_id 订阅」的只读通道，坐席工作台/多标签页可旁听同一轮对话；上游断开时取消 LLM 请求（Open WebUI `session_pool` 的思路）。
- ROADMAP 已列「结构化流式事件」，这条是把验收标准写实。

### 10. 控制台「健康」页：把已有指标变成可操作信息（P2，投入 S–M）

**做什么**：Dashboard 增加一块（或独立页）：
- 各 LLM 候选状态（第 6 条的数据）
- 网关 ingest 重试队列深度（`_INGEST_RETRY_MAX_TASKS` 当前值/上限）
- 提醒调度延迟（最老待发提醒的等待秒数）
- 检索索引状态（FTS5/vec 可用性、文档数）
- 最近 24h 降级事件（第 8 条）

数据全部来自已有 `metrics.py`/`events_log`/`store`，**不加新依赖**。这是「可观测性」里 ROI 最高的一层：不需要接 Grafana，用户打开控制台就能回答「它现在健康吗」。

---

## 14. 不确定项与未验证

1. **AnythingLLM 桌面端技术栈**：公开仓库无 desktop 源码，官网只提供安装包；不能断言 Electron。**未证实**。
2. **Langflow Desktop 技术栈**：官方 README 只写「包含全部依赖」，未写壳技术；第三方 E2E 仓库提到 IBM Langflow Desktop 用 Tauri，但**官方未证实**。
3. **LobeHub 的许可**：GitHub API 返回 `NOASSERTION`，README 未在首屏给出明确 SPDX；商用前需单独读 LICENSE。
4. **Open WebUI 的许可**：自定义许可，要求保留品牌；**只可借鉴设计，不可复制代码**。
5. **Dify 的许可**：Apache 2.0 + 附加条件；同样只借鉴设计。
6. **star 数与活跃度**：为 2026-11-06 单日快照，会变化；Flowise 的 `archived=true` 是当日事实。
7. **zhike 的 `--workers 1`**：Dockerfile 明确 `--workers 1`，所以当前冷却状态不共享不会立即出问题；但一旦水平扩展或多 worker 启动，第 6 条就从「优化」变成「必需」。

---

## 15. 一句话总结

zhike 的**内核**（ReAct 循环、分级权限、哈希链审计、混合检索、内容级备援）不输这批项目中的任何一个；差距集中在**交付层**：没有可安装的包、没有 CLI、没有首次向导、没有 Windows 分发、插件加载器只写在单测里、降级状态不可见。按本文 10 条推进，前 4 条（CLI、向导、doctor、README）就能把「装得上、跑得通」的体验拉到与 Open WebUI/AstrBot 同一水平，且不需要引入任何新框架。
