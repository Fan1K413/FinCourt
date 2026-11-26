import os
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_dir: Path
    mode: str = "fixture"

    def engine(self) -> Engine:
        result = create_engine(self.database_url, pool_pre_ping=True)
        if result.dialect.name == "sqlite":

            @event.listens_for(result, "connect")
            def enable_foreign_keys(dbapi_connection, _record):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return result


def settings() -> Settings:
    root = ROOT / "var"
    return Settings(
        os.environ.get("FINCOURT_DATABASE_URL")
        or f"sqlite:///{(root / 'fincourt.sqlite3').as_posix()}",
        Path(os.environ.get("FINCOURT_STORAGE_DIR") or root / "storage").resolve(),
        os.environ.get("FINCOURT_MODE", "fixture"),
    )


def migrate(config: Settings, engine: Engine) -> None:
    if engine.dialect.name == "sqlite" and engine.url.database not in {None, ":memory:"}:
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    config.storage_dir.mkdir(parents=True, exist_ok=True)
    alembic = Config(str(ROOT / "backend" / "alembic.ini"))
    alembic.attributes["engine"] = engine
    command.upgrade(alembic, "head")
