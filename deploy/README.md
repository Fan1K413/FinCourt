# 统一部署

这一套 Compose 部署 Nexent v2.8.5、FinCourt 网页/API、MCP 和两边的存储服务。FinCourt 迁移、内部 TLS 证书和 Nexent 管理员由启动任务准备。需要 Docker Engine、Docker Compose 2.24.4 或更新版本、Python 3.10+ 和 Git。[Nexent 部署要求](https://modelengine-group.github.io/nexent/en/quick-start/installation.html) · [Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/#replace-value)

## 启动

根目录的 `compose.yaml` 默认拉取 `ghcr.io/fan1k413/fincourt:main`。`main` 推送通过 Python 检查及容器冒烟测试后，Actions 将同一镜像发布到 GHCR，并为它添加 `sha-<完整提交 SHA>` 标签。首次发布的 GHCR 包默认为私有。私有包需先执行 `docker login ghcr.io`，使用有权读取该包且具备 `read:packages` 权限的 GitHub classic PAT。[构建工作流](../.github/workflows/build.yml) · [GHCR 登录说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

在仓库根目录运行。

```powershell
python deploy/prepare.py
docker compose --env-file var/fincourt-stack/stack.env pull fincourt-api
docker compose --env-file var/fincourt-stack/stack.env up -d
docker compose --env-file var/fincourt-stack/stack.env ps -a
```

首次准备固定版本的 Nexent 文件和这套编排自己的配置。重复准备保留已有 `stack.env`、Nexent 环境文件及密码。默认网页入口是 [FinCourt](http://localhost:8080) 和 [Nexent](http://localhost:3000)，均绑定到本机。首次准备时可用 `--fincourt-port` 和 `--nexent-port` 指定其他端口。

更新时再次执行 `pull fincourt-api` 和 `up -d`。网页/API、MCP 和启动任务共用同一镜像。部署指定提交时，在 `stack.env` 设置 `FINCOURT_STACK_IMAGE=ghcr.io/fan1k413/fincourt:sha-<完整提交 SHA>`，也可填镜像摘要。

## 容器数量

完整部署默认有 16 个常驻服务。12 个来自 Nexent，涵盖应用服务、搜索、缓存、存储和 Supabase 登录。FinCourt 侧有网页/API、MCP、数据库和内部 HTTPS 网关。另外有 3 个一次性启动任务。Nexent 文档处理按需启用，用 `--profile nexent-data` 启动即可。[Nexent 服务架构](https://modelengine-group.github.io/nexent/en/quick-start/installation.html#service-architecture)

只跑演示时，保持 `FINCOURT_STACK_MODE=fixture`，按上面的步骤拉取镜像后运行下列命令。此次启动包含 3 个常驻容器和 1 个初始化任务。FinCourt 导入演示样例，无需 Nexent 和模型 Key。

```powershell
docker compose --env-file var/fincourt-stack/stack.env up -d fincourt-api fincourt-mcp
```

本地开发的单库配置见 [compose.dev.yaml](../compose.dev.yaml)。

## 接入模型

1. 使用 `suadmin@nexent.com` 登录 Nexent。初始密码保存在 `var/fincourt-stack/nexent/deploy/env/.env` 的 `NEXENT_SUPER_ADMIN_PASSWORD` 中。创建租户和租户管理员，再用租户管理员登录。
2. 在 Nexent 配置模型 API Key。注册远程 MCP 服务 `http://fincourt-mcp:8766/mcp`，为审查 Agent 启用 `query_evidence`、`check_claim`，载入本项目的业绩公告审查 Skill，并发布 Agent。对审查 Agent 和子 Agent 关闭通用搜索、共享记忆、额外知识库及任意代码工具。[接入预案](../integrations/nexent/README.md) · [Nexent MCP 接入](https://modelengine-group.github.io/nexent/en/integration/integration-in/mcp.html)
3. 在 `var/fincourt-stack/stack.env` 填入 `FINCOURT_STACK_NEXENT_AGENT`、`FINCOURT_STACK_NEXENT_TOKEN`，设置 `FINCOURT_STACK_MODE=live`，以同一套编排重新加载 API。

```powershell
docker compose --env-file var/fincourt-stack/stack.env up -d fincourt-api
```

在工作台运行一条观点审查，检查结果详情里的两条工具调用记录。模型配置和 Agent 发布完成后才能验证真实调用。部署状态目录位于 Git 忽略范围内。
