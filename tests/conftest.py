from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from pygmalion import config, storage
from pygmalion.jobs import service as printer
from pygmalion.machine import grbl
from pygmalion.writing import prepare as preparation
from pygmalion.web.app import WEB_DIST_DIR, create_app


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    original = deepcopy(config.CONFIG)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "data" / "poems.db")
    monkeypatch.setattr(printer, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(preparation, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(grbl, "BACKUP_DIR", tmp_path / "backups")
    config.CONFIG.clear()
    config.CONFIG.update(deepcopy(config.DEFAULTS))
    storage.init_db()
    yield tmp_path
    config.CONFIG.clear()
    config.CONFIG.update(original)


@pytest.fixture
def client(isolated_runtime):
    app = create_app(WEB_DIST_DIR, dry_run=True)
    with TestClient(app) as session:
        # API queue tests control the worker explicitly; no physical serial port opens.
        app.state.printer.stop()
        yield session
