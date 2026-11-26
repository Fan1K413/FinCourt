"""Generate a private environment file for the root Compose project."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 FinCourt 编排使用的私有环境变量文件")
    parser.add_argument("--output", type=Path, default=ROOT / "var/compose.env")
    parser.add_argument("--fincourt-port", type=int, default=8080)
    parser.add_argument("--mcp-port", type=int, default=8766)
    args = parser.parse_args()
    ports = {args.fincourt_port, args.mcp_port}
    if len(ports) != 2 or any(not 1024 <= port <= 65535 for port in ports):
        parser.error("请为网页和 MCP 指定不同且有效的端口（1024–65535）")
    output = args.output.resolve()
    if (
        output.is_relative_to(ROOT)
        and subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-q", "--", str(output)], check=False
        ).returncode
        != 0
    ):
        parser.error("仓库内的凭据文件必须被 Git 忽略。可使用默认的 var/compose.env。")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        print(f"保留已有环境变量文件 {output}")
        return
    values = {
        "FINCOURT_STACK_DB_PASSWORD": secrets.token_hex(24),
        "FINCOURT_STACK_MCP_SECRET": secrets.token_hex(32),
        "FINCOURT_STACK_ADMIN_TOKEN": secrets.token_hex(32),
        "FINCOURT_STACK_WEB_PORT": str(args.fincourt_port),
        "FINCOURT_STACK_MCP_PORT": str(args.mcp_port),
    }
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.writelines(f"{key}={value}\n" for key, value in values.items())
    print(f"已生成环境变量文件 {output}")


if __name__ == "__main__":
    main()
