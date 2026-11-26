"""Run FinCourt's Linux container processes under separate identities."""

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

API = 10001
MCP = 10002
MCP_STORAGE = "/app/var/mcp-storage"
EVIDENCE_DIR = Path("/app/var/write/storage")
SOURCES_DIR = Path("/app/var/source")
EVIDENCE_NAME = re.compile(r"[a-f0-9]{64}")


def prepare() -> None:
    evidence = Path(os.environ["FINCOURT_STORAGE_DIR"])
    os.chown(evidence, -1, MCP)
    evidence.chmod(0o2750)
    for path in evidence.iterdir():
        if EVIDENCE_NAME.fullmatch(path.name) and not path.is_symlink() and path.is_file():
            os.chown(path, -1, MCP)
            path.chmod(0o640)
    command = [sys.executable, "/app/scripts/fincourt.py", "migrate"]
    if os.environ.get("FINCOURT_SEED_EXAMPLES") == "true":
        command[-1:] = ["seed-fixtures", "--stage", "initial"]
    subprocess.run(command, check=True)


def health() -> None:
    with urlopen("http://127.0.0.1:8000/health", timeout=1):
        pass
    with socket.create_connection(("127.0.0.1", 8766), timeout=1):
        pass


def identity(uid: int, *groups: int) -> dict:
    return {"user": uid, "group": uid, "extra_groups": list(groups)}


def stop(_signum, _frame) -> None:
    raise SystemExit(0)


def supervise() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for directory, group in ((EVIDENCE_DIR, MCP), (SOURCES_DIR, API)):
        if directory.stat().st_uid != API:
            os.chown(directory, API, group)
    process_env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    python_env = dict(process_env, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    api_env = dict(os.environ, FINCOURT_EVIDENCE_GROUP_READ="1")
    db_password = quote(api_env.pop("FINCOURT_STACK_DB_PASSWORD"), safe="")
    api_env["FINCOURT_DATABASE_URL"] = (
        f"postgresql+psycopg://fincourt:{db_password}@fincourt-postgresql:5432/fincourt"
    )
    mcp_env = dict(
        python_env,
        FINCOURT_DATABASE_URL=api_env["FINCOURT_DATABASE_URL"],
        FINCOURT_MCP_SECRET=os.environ["FINCOURT_MCP_SECRET"],
        FINCOURT_MODE=os.environ.get("FINCOURT_MODE", "fixture"),
        FINCOURT_STORAGE_DIR=MCP_STORAGE,
        FINCOURT_MCP_BIND_HOST="0.0.0.0",
        FINCOURT_MCP_ALLOWED_HOSTS="127.0.0.1:8766",
    )
    subprocess.run(
        [sys.executable, __file__, "prepare"], env=api_env, check=True, **identity(API, MCP)
    )
    processes = []
    try:
        commands = (
            (
                "api",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "--app-dir",
                    "/app/backend/src",
                    "fincourt.api:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ],
                api_env,
                identity(API, MCP),
            ),
            ("mcp", [sys.executable, "/app/scripts/fincourt.py", "mcp"], mcp_env, identity(MCP)),
        )
        for name, command, env, options in commands:
            processes.append((name, subprocess.Popen(command, env=env, **options)))
        while True:
            for name, process in processes:
                if (status := process.poll()) is not None:
                    raise SystemExit(f"FinCourt {name} exited ({status}).")
            time.sleep(0.25)
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 8
        for _, process in processes:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "serve"
    {"serve": supervise, "prepare": prepare, "health": health}[action]()
