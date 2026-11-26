"""Prepare a pinned Nexent checkout and private configuration for the unified stack."""

import argparse
import base64
import hmac
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEXENT_TAG = "v2.8.5"
NEXENT_COMMIT = "628b53d66c875094eb6fa1ed3b2dff6590bad9e5"
NEXENT_REPO = "https://github.com/ModelEngine-Group/nexent.git"
ENV_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=.*$")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def prepare_vendor(directory: Path) -> None:
    if not directory.exists():
        git(
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--branch",
            NEXENT_TAG,
            NEXENT_REPO,
            str(directory),
        )
        git("-C", str(directory), "sparse-checkout", "set", "deploy")
    if git("-C", str(directory), "rev-parse", "HEAD") != NEXENT_COMMIT:
        raise RuntimeError("现有 Nexent 目录和固定版本不一致。请另选部署状态目录。")
    if not (directory / "deploy/env/.env.example").is_file():
        raise RuntimeError("Nexent 固定版本缺少部署文件，请检查该检出目录。")


def put_once(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def dotenv_assignment(key: str, value: str) -> str:
    # Single quotes preserve paths containing spaces or dollar signs.
    quoted = value.replace("'", "\\'")
    return f"{key}='{quoted}'"


def render_env(template: str, values: dict[str, str]) -> str:
    pending = dict(values)
    lines = []
    for line in template.splitlines():
        match = ENV_ASSIGNMENT.match(line)
        key = match.group(1) if match else None
        if key in values:
            lines.append(dotenv_assignment(key, values[key]))
            pending.pop(key, None)
        else:
            lines.append(line)
    lines.extend(dotenv_assignment(key, value) for key, value in pending.items())
    return "\n".join(lines) + "\n"


def supabase_jwt(secret: str, role: str) -> str:
    def encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    now = int(time.time())
    header = encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = encode(
        json.dumps(
            {
                "iss": "supabase",
                "role": role,
                "iat": now,
                "exp": now + 157_680_000,
            },
            separators=(",", ":"),
        ).encode("ascii")
    )
    signing_input = f"{header}.{payload}"
    signature = encode(hmac.digest(secret.encode("ascii"), signing_input.encode("ascii"), "sha256"))
    return f"{signing_input}.{signature}"


def nexent_values(vendor: Path, state: Path, web_port: int) -> dict[str, str]:
    image_source = vendor / "deploy/env/image-source.general.env"
    images = {}
    for line in image_source.read_text(encoding="utf-8").splitlines():
        if ENV_ASSIGNMENT.match(line):
            key, value = line.split("=", 1)
            images[key] = value.replace("${APP_VERSION}", NEXENT_TAG)

    jwt = secrets.token_hex(32)
    site = f"http://localhost:{web_port}"
    return {
        **images,
        "APP_VERSION": NEXENT_TAG,
        "ROOT_DIR": (state / "nexent-data").as_posix(),
        "NEXENT_USER_DIR": (state / "nexent-workspace").as_posix(),
        "NEXENT_WEB_PORT": str(web_port),
        "SITE_URL": site,
        "OAUTH_CALLBACK_BASE_URL": site,
        "CAS_CALLBACK_BASE_URL": site,
        "GOTRUE_SITE_URL": site,
        "NORTHBOUND_EXTERNAL_URL": "http://nexent-northbound:5013/api",
        "ELASTIC_PASSWORD": secrets.token_hex(24),
        "NEXENT_POSTGRES_PASSWORD": secrets.token_hex(24),
        "SUPABASE_POSTGRES_PASSWORD": secrets.token_hex(24),
        "NEXENT_SUPER_ADMIN_PASSWORD": secrets.token_hex(20) + "Aa1!",
        "DASHBOARD_PASSWORD": secrets.token_hex(24),
        "MINIO_ROOT_PASSWORD": secrets.token_hex(24),
        "MINIO_ACCESS_KEY": secrets.token_hex(10),
        "MINIO_SECRET_KEY": secrets.token_hex(24),
        "MINIO_SECURE": "false",
        "JWT_SECRET": jwt,
        "SUPABASE_KEY": supabase_jwt(jwt, "anon"),
        "SERVICE_ROLE_KEY": supabase_jwt(jwt, "service_role"),
        "SECRET_KEY_BASE": secrets.token_hex(32),
        "VAULT_ENC_KEY": secrets.token_hex(16),
        "INVITE_CODE": secrets.token_hex(16),
        "DOCKER_ENVIRONMENT": "true",
        "NEXENT_SANDBOX_DOCKER_IMAGE": images["NEXENT_SANDBOX_IMAGE"],
        "NEXENT_SANDBOX_FAILURE_POLICY": "error",
        "NEXENT_SANDBOX_NETWORK": "disabled",
        "NEXENT_SANDBOX_SHELL_POLICY": "disabled",
    }


def prepare(state: Path, fincourt_port: int, nexent_port: int) -> None:
    state.mkdir(parents=True, exist_ok=True)
    vendor = state / "nexent"
    prepare_vendor(vendor)
    data = state / "nexent-data"
    (state / "nexent-workspace").mkdir(exist_ok=True)
    for name in (
        "logs",
        "skills",
        "memory-provider-plugins",
        "project-config",
        "openssh-server/ssh-keys",
    ):
        (data / name).mkdir(parents=True, exist_ok=True)
    assets = vendor / "deploy/docker/assets"
    for relative in ("volumes/api/kong.yml", "scripts/sync_user_supabase2pg.py"):
        put_once(data / relative, (assets / relative).read_text(encoding="utf-8"), 0o644)

    env = vendor / "deploy/env"
    if not (env / ".env").exists():
        template = (env / ".env.example").read_text(encoding="utf-8")
        put_once(env / ".env", render_env(template, nexent_values(vendor, state, nexent_port)))
    put_once(env / "monitoring.env", "# No optional monitoring provider is configured.\n")

    put_once(
        state / "stack.env",
        render_env(
            "",
            {
                "FINCOURT_STACK_VENDOR_DIR": vendor.as_posix(),
                "FINCOURT_STACK_WEB_PORT": str(fincourt_port),
                "FINCOURT_STACK_DB_PASSWORD": secrets.token_hex(24),
                "FINCOURT_STACK_MCP_SECRET": secrets.token_hex(32),
                "FINCOURT_STACK_ADMIN_TOKEN": secrets.token_hex(32),
                "FINCOURT_STACK_SEED_EXAMPLES": "true",
                "FINCOURT_STACK_MODE": "fixture",
                "FINCOURT_STACK_NEXENT_AGENT": "fincourt-review",
                "FINCOURT_STACK_NEXENT_TOKEN": "",
            },
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="准备 FinCourt 和 Nexent 的统一编排")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "var/fincourt-stack")
    parser.add_argument("--fincourt-port", type=int, default=8080)
    parser.add_argument("--nexent-port", type=int, default=3000)
    args = parser.parse_args()
    ports = {args.fincourt_port, args.nexent_port}
    if len(ports) != 2 or any(port < 1024 or port > 65535 for port in ports):
        parser.error("请为两个网页指定不同且有效的端口（1024–65535）")
    state = args.state_dir.resolve()
    ignored = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", "--", str(state / "stack.env")],
        check=False,
        capture_output=True,
    )
    if ignored.returncode != 0 and state.is_relative_to(ROOT):
        parser.error("仓库内的部署状态目录必须由 Git 忽略。可使用默认的 var/fincourt-stack。")
    try:
        prepare(state, args.fincourt_port, args.nexent_port)
    except subprocess.CalledProcessError as error:
        raise SystemExit("无法检出固定的 Nexent 版本。请检查 Git 和上游仓库的连接。") from error
    print(f"已准备 {NEXENT_TAG}，使用配置文件 {state / 'stack.env'}")


if __name__ == "__main__":
    main()
