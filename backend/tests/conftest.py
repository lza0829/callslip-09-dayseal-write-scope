import os
import sys
import tempfile
from pathlib import Path

# 必须在任何 app 模块导入前设定：config 在导入期读取该环境变量。
os.environ["CALLSLIP_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="callslip-test-"), "boot.db"
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个用例一个全新库：指向临时 DB 并重新种子。"""
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "t.db")
    from app.seed import seed_if_empty

    seed_if_empty()
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)
