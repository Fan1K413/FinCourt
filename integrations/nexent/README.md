# Nexent 联调

同一套 Compose 中启动 FinCourt 与 Nexent 的方式见 [统一部署](../../deploy/README.md)。

目前的兼容目标是 Nexent v2.8.5。以官方 Northbound SSE 合约实现适配器。已通过本地 MCP 会话及模拟 HTTP 响应验证协议闭环。平台、模型和网页端到端联调待完成。

- [Nexent 发布页](https://github.com/ModelEngine-Group/nexent/releases/tag/v2.8.5)
- [Northbound 对话](https://modelengine-group.github.io/nexent/en/integration/integration-out/northbound-api.html)
- [MCP 接入](https://modelengine-group.github.io/nexent/en/integration/integration-in/mcp.html)

## 待联调配置

- 业务 API 与工具进程连接同一个 FinCourt 数据库。将 FINCOURT_MCP_SECRET 设为至少 32 字节的随机密钥，双方使用同一个值。使用 scripts/fincourt.py mcp 启动本地工具入口 http://127.0.0.1:8766/mcp。
- Nexent 需能从它的运行环境访问工具入口。核对容器网络和传输配置，在专用 Agent 中启用 query_evidence、check_claim 并载入本目录的审查 Skill。验证 Agent 和子 Agent 的联网搜索、共享缓存、长期记忆、知识库与任意代码工具均已关闭。
- 将 FINCOURT_MODE、FINCOURT_NEXENT_BASE_URL、FINCOURT_NEXENT_API_TOKEN、FINCOURT_NEXENT_AGENT_NAME、FINCOURT_NEXENT_RELEASE 设在 FinCourt 进程环境。BASE_URL 应包括部署需要的代理前缀，适配器追加 /nb/v1/chat/run。使用 scripts/fincourt.py serve 开启网页。
- 适配器为每次运行创建新的对话，由短时签名上下文绑定资料快照。工具记录留存工具名、调用时间、事实 ID 和判断。完成一次真实调用后，记录部署版本、三个审查角色的配置及可复现的脱敏记录。
