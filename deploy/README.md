# 部署 FinCourt

根目录的 [compose.yaml](../compose.yaml) 启动两个容器。`fincourt-app` 提供网页、API 和证据 MCP，`fincourt-postgresql` 保存业务数据。两个模式共用这份编排。Nexent 通过官方安装脚本独立部署。

## 面板部署

1. 将根目录的 [compose.yaml](../compose.yaml) 导入面板。默认拉取 GitHub Actions 发布的 GHCR 镜像。
2. 将数据库密码、MCP 签名密钥和维护令牌填入面板的环境变量区域。也可以把生成的 `var/compose.env` 内容导入面板，或保存为编排目录下的 `.env`。
3. 点击部署。网页默认由宿主机的 `127.0.0.1:8080` 提供，MCP 使用 `127.0.0.1:8766/mcp`。域名与 TLS 在面板的 Nginx 中设置。

在开发电脑上可生成一套随机凭据。已有 `var/compose.env` 会保留。

```powershell
python deploy/prepare.py
```

服务器需要 Docker Engine、Compose 2.23.1 或更新版本、编排文件和环境变量。可手动填写下列三个变量。数据库密码沿用现有数据库账号的密码，MCP 签名密钥至少 32 字节。[Compose 内嵌配置](https://docs.docker.com/reference/compose-file/configs/)

| 环境变量 | 用途 |
| --- | --- |
| `FINCOURT_STACK_DB_PASSWORD` | FinCourt 数据库账号密码 |
| `FINCOURT_STACK_MCP_SECRET` | 审查上下文签名密钥，供 FinCourt 的 API 和 MCP 共享 |
| `FINCOURT_STACK_ADMIN_TOKEN` | 知识版本维护令牌 |

通过 `FINCOURT_STACK_WEB_PORT` 和 `FINCOURT_STACK_MCP_PORT` 可修改宿主机端口。GHCR 包为私有时，使用有 `read:packages` 权限的 GitHub classic PAT 登录后拉取。[GHCR 登录说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

## 命令行启动

在编排目录准备好 `.env` 后运行。使用开发电脑生成的文件时，将 `--env-file var/compose.env` 加在 `docker compose` 后。

```sh
docker compose config --quiet
docker compose pull fincourt-app
docker compose up -d
```

通过 Actions 测试的镜像发布到 GHCR 的 `main` 和 `sha-<完整提交 SHA>`。用 `FINCOURT_STACK_IMAGE` 指定镜像版本。[构建工作流](../.github/workflows/build.yml)

## 数据与升级

编排目录下的 `data/fincourt/` 保存 PostgreSQL、证据和来源文件。MCP 进程通过只读挂载访问证据。首次启动自动创建业务数据库和执行迁移。

已有 PostgreSQL 17 绑定目录时，继续使用 `data/fincourt/postgresql/` 和原数据库密码。旧版若使用 Docker 命名卷，先备份，再将 FinCourt 数据还原到新目录。旧合并栈若已启动过 Nexent，先备份 Nexent 数据，再按官方迁移流程切换到独立部署。更新编排后可停止旧 Nexent 容器，原数据目录可继续保留。

## 接入独立 Nexent

1. 在 Nexent 自己的部署目录执行官方 `bash deploy.sh docker`，选择包含 `application` 的组件组合。登录 Nexent，配置模型 Key，创建并发布 FinCourt 审查 Agent，生成北向 API Key。[官方 Docker 部署](https://modelengine-group.github.io/nexent/zh/quick-start/installation.html) · [北向 API](https://modelengine-group.github.io/nexent/en/integration/integration-out/northbound-api.html)
2. 用面板的 Nginx 为北向 API 提供 HTTPS 地址。使用官方 v2.8.5 的默认端口时，将 `/nb/v1/chat/` 原样转发至 Nexent 宿主机的 5013 端口。若 Nginx 与 Nexent 在同一台宿主机上，对应配置如下。
3. 将 FinCourt 域名的 `/mcp` 转发到宿主机的 `127.0.0.1:8766`。在 Nexent 注册 `https://fincourt.example.com/mcp` 并进行连通性测试。为 Agent 启用 `query_evidence` 和 `check_claim`，加载 [审查 Skill](../integrations/nexent/earnings-review/SKILL.md) 后重新发布。[Nexent MCP 接入](https://modelengine-group.github.io/nexent/en/integration/integration-in/mcp.html)
4. 在 FinCourt 面板中设置下列环境变量并重新部署 `fincourt-app`。在工作台发起一次审查，确认运行模式为 live 且记录实际 MCP 调用。

| FinCourt 环境变量 | 值 |
| --- | --- |
| `FINCOURT_STACK_MODE` | `live` |
| `FINCOURT_STACK_NEXENT_BASE_URL` | 北向 API 的 HTTPS 根地址，例如 `https://nexent-api.example.com` |
| `FINCOURT_STACK_NEXENT_TOKEN` | Nexent 北向 API Key |
| `FINCOURT_STACK_NEXENT_AGENT` | 已发布的 Agent 名称 |
| `FINCOURT_STACK_NEXENT_RELEASE` | 实际部署的版本，例如 `v2.8.5` |

示例配置分别放入 Nexent API 域名和 FinCourt 域名的 HTTPS `server` 块。Nginx 需要能访问对应的宿主机端口。两段转发都关闭响应缓冲以支持流式传输。

```nginx
# Nexent API 域名
location ^~ /nb/v1/chat/ {
    proxy_pass http://127.0.0.1:5013;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 90s;
}

# FinCourt 域名
location = /mcp {
    proxy_pass http://127.0.0.1:8766;
    proxy_http_version 1.1;
    proxy_set_header Host 127.0.0.1:8766;
    proxy_set_header Connection "";
    proxy_buffering off;
}
```

改过宿主机 MCP 端口时，同步修改第二段的 `proxy_pass`。`Host` 仍使用应用所校验的 `127.0.0.1:8766`。默认 `fixture` 模式可以直接验证离线流程，无需 Nexent 或模型 Key。
