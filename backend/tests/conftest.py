import pytest
from fastapi.testclient import TestClient
from fincourt.api import create_app
from fincourt.database import Settings, migrate


@pytest.fixture
def workspace(tmp_path):
    config = Settings(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", tmp_path / "storage")
    app = create_app(config)
    engine = app.state.engine
    migrate(config, engine)
    with TestClient(app) as client:
        yield config, engine, client
