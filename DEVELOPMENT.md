# 开发记录

## 2026-11-26 · P0 数据链路

- 仓库原有内容为 README 和本地规格，无应用代码或依赖锁文件可复用。已建立 FastAPI、SQLAlchemy、Alembic、React 和 TypeScript 工程。
- 真公告和模拟资料使用同一套入库数据模型。原始文件按哈希归档，证据绑定解析版本和页内区间。两份观典防务公告的收入分别位于原快报第 1 页和更正公告第 2 页。源文件在本地保存。
- 通过 7 项 pytest 检查，覆盖真实 PDF 页码和原始字节、截止日期、同字段更正、不同金额单位、重复导入和引用校验。`pnpm build` 通过。
- Python 依赖用完整 pip 锁文件复现。使用本机 Python 3.10.6 验证。离线演示以 SQLite 启动，PostgreSQL Compose 已配置，当前主机缺少 Docker 和 PostgreSQL，P0-02 待实库验证。
- 启动入口为 `scripts/fincourt.py demo`。下一步完成 P1 观点核验和历史结果页面。

## 2026-11-26 · P1 观点审查

- 完成观点创建、受限 Decimal 计算、四类判断、证明的前提组合和循环校验。审查保存资料快照、引用、计算和缺失项。
- 通过 12 项 pytest 检查。真实 C1 和 C2 得到支持，C3 被适用证据反驳，C4 缺最终审计证据，C5 初始运行得到支持。核验金额为 118,937,200.00 元和 89,983,800.00 元。
- `pnpm build` 通过。实际启动演示后，用 Edge 无头测试核验网页引文高亮、选择更正后日期运行及读取原历史。
- `scripts/fincourt.py demo` 启动离线工作台，`scripts/fincourt.py import-demo` 导入真实案例。

## 2026-11-26 · P2 离线协议验证

- 接好官方 Northbound SSE 合约与 MCP SDK，使用运行专用签名上下文绑定资料快照。保留工具调用时间、事实 ID 和核验结果。
- 通过本地 MCP 会话与模拟 HTTP 传输测试，覆盖签名篡改、引用超范围、审查结果结构与缺少实际工具记录的错误。缺少配置时 `live` 明确返回错误。
- Nexent v2.8.5 为待联调版本。本机没有已配置的 Nexent 和模型，真实运行、角色配置、成本、取消与脱敏集成日志仍待验收。适配器配置见 [Nexent 联调](integrations/nexent/README.md)。

## 2026-11-26 · P3 更正与局部重审

- 字段级更正生成持久化事件，由事实变化、相同业务口径及观点依赖确定影响，再按推导顺序执行 fixture 重审。固定观点保留截止日，原件、快照和历史结果保留。
- 17 项 pytest 检查和前端构建通过。独立数据库的 Edge 无头测试验证了从网页导入更正、自动打开受影响结果及读取原历史。
- `scripts/fincourt.py demo` 启动。进入「变化影响」并选择「导入模拟更正」。真实模式在界面保留待重审标记，Nexent 调度待联调。

## 2026-11-26 · P4 本地知识版本

- 使用魅视科技公开协议，新增条件协议结构。投影区分备忘录上限、协议阶段、正式合同条件及净额法口径。仅实现本案例所需的声明式类型扩展。
- 20 项 pytest 检查和前端构建通过。真实 PDF 验证使用第 3 页金额与第 1 页条件。审批、回归失败、资料修订冲突、基准版本冲突、发布和保留原始材料的回滚均已实测。Edge 无头测试通过验证、审批、发布和回滚。
- `scripts/fincourt.py import-agreement` 生成来源可查的草稿。启动服务时配置本地维护者凭据，再到「知识版本」操作。数据写入与发布在 SQLite 验证，PostgreSQL 实库并发验证与 P0-02 一起保留。

## 2026-11-26 · P5 初步评测和复现

- 使用新的 Python 虚拟环境安装锁定依赖，在干净前端目录完成冻结锁安装和构建，并以新的 SQLite 实例复现浏览器流程。
- `eval/run.py --real` 实测 10 条构造观点，有时点核验 10/10，无时点基线 8/10。5 次局部重审和全量重跑对照分别运行 1 条和 9 条，对受影响观点给出相同结果。[评测报告](eval/REPORT.md)
- [五分钟演示](DEMO.md) 包含流程、系统架构、来源和已复核的公开赛题材料。约定规模的数据集、独立双人标注、公司级最终测试集、模型对照和账号内赛题细则仍待完成。
- 最终 22 项 pytest 检查通过，包括变更和补丁的 MCP 调用、Nexent 模拟传输超时和有限重试。Starlette 和 MCP SDK 各有一条上游警告。Ruff、前端构建和仓库差异检查通过。

## 2026-11-26 · 前端阅读布局

- 调整观点、资料、更正与知识版本页面。采用暖纸色、衬线标题和统一的阅读版式。计算、原文、历史和更正前后直接呈现，技术字段可展开。
- 增加轻动效、原文高亮定位和窄屏横向观点列表。系统设为减少动态效果时停用视觉动画。
- 22 项后端测试与前端构建通过。Edge 验证桌面、390 px 和 320 px 下的引文定位、自动重审、历史记录与减少动态效果。桌面另验 PDF 表单导入及知识版本发布和回滚。已更新 README 截图。

## 2026-11-26 · 统一部署编排

- 统一编排引入固定的 Nexent v2.8.5 官方配置，加上 FinCourt API、MCP、业务数据库和初始化任务。同一部署通过私网互通，Northbound 网关使用内部 HTTPS。
- 成功准备上游文件及本地配置。复跑保持已有配置，Compose 规范与服务引用静态检查通过，内部 HTTPS 握手、MCP Host 校验及 24 项 pytest 检查通过。镜像仓库确认了主要 Nexent 和 FinCourt 构建所用镜像标签。
- 本机缺少 Docker CLI，整套容器启动、Nexent 登录初始化与真实模型调用待在部署环境验证。[统一部署](deploy/README.md)

## 2026-11-26 · 默认编排与 GitHub Actions

- 将完整编排移至根目录 `compose.yaml`，原单库配置保留为 `compose.dev.yaml`。
- 构建工作流在 `main` 推送和 PR 时运行。它会检查 Python、校验完整编排、构建镜像，并在 PostgreSQL 上验证离线更正重审和 MCP 初始化。
- 本地 24 项测试通过。已校验 Compose 结构、引用路径、Actions 工作流及其 Bash 语法，并核对 Linux AMD64 与 Python 3.12 的锁定依赖轮子。Docker 构建及容器冒烟测试待首次 Actions 运行确认。

## 2026-11-26 · 部署镜像与容器精简

- `main` 的 CI 通过测试后使用同一份冒烟镜像发布 `main` 和提交 SHA 标签。根编排拉取 GHCR 镜像，CI 通过独立覆盖文件构建。
- FinCourt 合并 TLS 和数据库初始化。Nexent 文档处理改为按需启动。完整部署默认 16 个常驻服务和 3 个一次性任务。仅运行 fixture 时为 3 个常驻服务和 1 个一次性任务，继续使用同一份根编排。
- 本地 24 项 pytest、Ruff、Compose 规范及依赖检查通过。11 段 Actions 脚本通过 Bash 语法检查，模拟验证了当前提交发布 `main` 标签和旧提交仅发布 SHA 标签。
- 首轮 Actions 在 `0df1309` 上完成 Python 检查、镜像构建、PostgreSQL fixture 更正重审和 MCP 冒烟测试。GHCR 上的 `main` 与提交 SHA 标签指向同一镜像摘要。完整 Nexent 部署和真实模型调用仍待验证。[Actions 运行记录](https://github.com/Fan1K413/FinCourt/actions/runs/45770282976) · [部署命令](deploy/README.md)

## 2026-11-26 · 单文件部署

- `deploy/standalone.py` 生成面板可导入的单份 Compose，内嵌凭据、Nexent 配置和 SQL，使用已发布的 GHCR 应用镜像。服务数据落在 Docker 命名卷上。
- 本地通过 Compose 结构检查，确认没有外部 Compose 引用、`.env` 或项目目录挂载。实际执行了内嵌解包命令并逐文件比对 22 个 SQL 文件，重跑拒绝覆盖原有凭据。24 项 pytest 和 Ruff 通过。本机缺少 Docker CLI，面板实际启动仍待验证。已增加 CI 的 Compose 校验步骤。[面板导入步骤](deploy/README.md#从容器面板导入)

## 2026-11-26 · 合并 FinCourt 应用容器

- FinCourt 应用容器内运行 API、MCP 和 Nginx。Nginx 在回环地址提供到 Nexent 的 HTTPS，应用启动时执行数据库迁移。同一编排的完整部署为 14 个常驻容器，fixture 为 2 个。Nexent 的两个一次性任务仍由 Compose 调度。
- 单文件可沿用上一版部署凭据和端口。在 Linux 独立挂载命名空间实测旧证据对 MCP 可读，两个写入路径均被拒绝。Compose 规范、内嵌 SQL 文件比对、24 项 pytest 和 Ruff 均通过。CI 已加入 Nginx TLS、MCP 请求和只读挂载测试。实际镜像构建、TLS 运行和 GHCR 发布待本版本首次 Actions 验证。[部署说明](deploy/README.md)
- 容器显示名按 `fincourt-*`、`nexent-*` 和 `nexent-supabase-*` 统一。已核对单文件的 17 个容器名均唯一，服务名、连接别名、端口和凭据与改名前一致。

## 2026-11-26 · 服务名称和数据目录

- Compose 服务名统一与容器名一致，例如 `fincourt-app`、`fincourt-postgresql` 和 `nexent-redis`。保留 Nexent 依赖的内部地址。持久化数据改用编排目录下的 `./data`，MCP 只读挂载证据目录，Nexent 沙箱通过 Docker 使用相同的宿主机目录。
- 仓库编排和单文件的 Compose 结构、服务依赖及网络别名检查通过。单文件沿用相对路径、端口和原凭据。26 项 pytest 和 Ruff 通过。WSL 验证 Supabase 初始化会补齐镜像原始配置，保留已有加密密钥，两次运行结果一致。容器实际部署和真实沙箱调用待 Docker 环境验收。[数据目录说明](deploy/README.md#数据目录)

## 2026-11-26 · 统一根目录编排

- 根目录 `compose.yaml` 是面板、命令行和 CI 共用的部署文件。它内嵌 Nexent 配置和 SQL，凭据由面板环境变量或 `.env` 提供。本地开发数据库用同一文件的 `dev-db` 配置档。
- 验证了 18 个必填变量与凭据生成器一致、Git 跟踪的 YAML 无部署凭据、已有数据库密码和 Supabase JWT 保持一致。Compose 结构、26 项 pytest 与 Ruff 通过。整套容器和 GHCR 发布待本版本首次 Actions 验证。[面板部署](deploy/README.md#面板部署)

## 2026-11-26 · 精简 Compose 文件

- 将 Nexent 的 221 项公共环境配置合并，将 22 个 SQL 文件放入 FinCourt 镜像。管理员初始化只接收所需变量。根目录仍是一份部署编排，由 5,448 行降至 2,546 行。
- Compose 结构、展开后的服务配置与固定版本的 SQL 比对通过。Linux 实测应用启动可解包所有 SQL。26 项 pytest 与 Ruff 通过。完整部署默认 14 个常驻容器，新镜像构建和完整启动待 Actions 验证。[部署说明](deploy/README.md)

## 2026-11-26 · 按需启用 Nexent

- 默认只运行 fixture 的 FinCourt 和业务 PostgreSQL。通过 `nexent`、`nexent-headless` 在同一编排选择带设置页面的 Nexent 或已配置 Agent 的调用链。Nexent 明确使用 `full` 登录和租户模式。
- 移除独立开发数据库和 Nexent 文档处理。FinCourt 和 Nexent 业务库共用 PostgreSQL 17 实例，各使用独立的普通账号及数据库。旧版集群设有保护检查，沿用旧数据需要逻辑备份和还原。
- 配置检查确认 fixture、完整 Nexent、无 Web 调用三种启动方式分别为 2、13、12 个常驻容器。WSL 临时目录验证了 PostgreSQL 入口会保留并拒绝打开旧集群。26 项 pytest 通过。

## 2026-11-26 · FinCourt 双容器部署

- 根目录编排只运行 `fincourt-app` 和 `fincourt-postgresql`。Nexent 使用官方脚本独立安装。FinCourt 通过 HTTPS 调用其北向 API，并通过面板反向代理向 Nexent 提供 MCP。移除应用镜像中的 Nginx、Nexent SQL 和初始化脚本。沿用 FinCourt 的绑定目录和凭据。
- 通过 Compose 规范、两服务结构、端口绑定和 12 段 Actions 脚本的语法检查。24 项 pytest、Ruff 和前端生产构建通过。随内嵌 Nexent 工作目录适配器移除了 2 个对应测试。实测凭据生成可重复运行并保留旧文件，MCP 初始化返回 HTTP 200，未知 Host 返回 HTTP 421。
- 在编排目录设置环境变量后运行 `docker compose up -d`。本机缺少 Docker，镜像构建和双容器冒烟测试待 Actions 执行。还需通过独立 Nexent 与真实模型完成网页审查，核对 MCP 调用记录。[部署与联调步骤](deploy/README.md)
