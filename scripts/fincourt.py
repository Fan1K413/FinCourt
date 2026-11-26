import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from fincourt.changes import confirm_event  # noqa: E402
from fincourt.database import migrate, settings  # noqa: E402
from fincourt.demo import load_demo_claims, load_disclosures  # noqa: E402


def frontend_build() -> None:
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if not pnpm:
        raise SystemExit("未找到 pnpm，请先准备前端依赖")
    subprocess.run([pnpm, "build"], cwd=ROOT / "frontend", check=True)


def main():
    parser = argparse.ArgumentParser(description="FinCourt 本地开发入口")
    parser.add_argument(
        "command",
        choices=[
            "migrate",
            "seed-fixtures",
            "import-demo",
            "import-agreement",
            "demo",
            "serve",
            "test",
            "mcp",
        ],
    )
    parser.add_argument("--stage", choices=["initial", "correction", "all"], default=None)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()

    if args.command == "test":
        test_root = ROOT / "var" / "test-runs"
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as directory:
            result = subprocess.call(
                [sys.executable, "-m", "pytest", "--basetemp", str(Path(directory) / "pytest")],
                cwd=ROOT / "backend",
            )
        raise SystemExit(result)

    config = settings()
    if args.command == "demo" and config.mode != "fixture":
        raise SystemExit("离线演示需要 FINCOURT_MODE=fixture")
    engine = config.engine()
    if args.command == "mcp":
        from fincourt.mcp_tools import create_mcp

        secret = os.environ.get("FINCOURT_MCP_SECRET", "").encode()
        try:
            create_mcp(config, secret, engine, port=args.port or 8766).run(
                transport="streamable-http"
            )
        finally:
            engine.dispose()
        return

    migrate(config, engine)
    if args.command == "import-agreement":
        from fincourt.agreement import import_agreement

        with engine.begin() as connection:
            candidate = import_agreement(connection, config.storage_dir, download=True)
        print(f"{candidate['id']} · {candidate['status']}")
    if args.command in {"seed-fixtures", "import-demo", "demo"}:
        kind = "REAL" if args.command == "import-demo" else "SIMULATED"
        stage = args.stage or ("initial" if args.command == "demo" else "all")
        with engine.begin() as connection:
            rows = load_disclosures(
                connection, config.storage_dir, kind, stage, download=kind == "REAL"
            )
            load_demo_claims(connection, config.storage_dir, kind)
            for item in rows:
                if item["disclosure_type"] in {"CORRECTION", "UPDATE"}:
                    confirm_event(connection, item["id"], config.storage_dir, mode=config.mode)
        for item in rows:
            print(f"{item['id']} · {item['source_kind']} · {item['sha256']}")
    engine.dispose()

    if args.command in {"demo", "serve"}:
        import uvicorn

        frontend_build()
        uvicorn.run("fincourt.api:app", host="127.0.0.1", port=args.port or 8000)


if __name__ == "__main__":
    main()
