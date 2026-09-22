"""pytest 契约层：环境隔离 + 共享 fixtures。

设计要点（并行 agent 勿改动，除非共识）：
1. app.config.Settings / app.database.DB 都是 **import 时固化 env** 的模块级单例，
   所以这里必须在**任何 app.* import 之前**设好 env，指向一次性临时数据目录。
2. 提供了 reset_internal_state fixture，清空 instance_mgr / self_heal 的内存态
   （_instances/_slot_guard/_slot_held_since/_state 等），避免用例间串扰。
3. fake_llama_server：把 LLAMA_SERVER_BIN 指到不存在的假二进制，防止单元测试
   真的拉起 llama-server（子进程相关用例应 monkeypatch subprocess/start_instance）。
"""
import os
import sys
import tempfile
from pathlib import Path

# ---- 必须在任何 app.* import 之前执行（import 期 env 固化）----
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="llama-studio-test-", dir="/tmp"))
_DATA_DIR = _TMP_ROOT / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ["LLAMA_STUDIO_DATA"] = str(_DATA_DIR)
os.environ["LLAMA_SERVER_BIN"] = "/nonexistent/llama-server-fake"
os.environ["WEBUI_PORT"] = "9199"
os.environ["LLAMA_INSTANCE_BASE_PORT"] = "18901"
# 关闭可能依赖真实硬件的自动探测
os.environ.setdefault("LLAMA_MODEL_DIR", str(_TMP_ROOT / "models"))

# 确保能 import app.* 和 main
sys.path.insert(0, str(_BACKEND_DIR))
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import pytest


@pytest.fixture(autouse=True)
def reset_internal_state():
    """每个用例前清空 agent 层内存态 + 临时库数据，防止跨用例串扰。"""
    try:
        from app import instance_mgr as im
        im._instances.clear()
        im._slot_guard.clear()
        im._slot_held_since.clear()
        im._active_requests.clear()
        im._draining.clear()
        im._last_request_at.clear() if hasattr(im, "_last_request_at") else None
        im._last_success_at.clear() if hasattr(im, "_last_success_at") else None
    except Exception:
        pass
    try:
        from app import self_heal as sh
        sh._state.clear()
    except Exception:
        pass
    # 清空临时 SQLite 库的所有表（services/api_keys/app_settings/...），避免 UNIQUE 冲突与残留
    try:
        from app.database import get_conn
        with get_conn() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for t in tables:
                try:
                    conn.execute(f'DELETE FROM "{t}"')
                except Exception:
                    pass
    except Exception:
        pass
    yield


@pytest.fixture
def init_db():
    """初始化（临时目录下的）SQLite 表。每个用例可调用保证表存在。"""
    def _init():
        from app.database import init_db
        init_db()
    _init()
    return _init


@pytest.fixture
def client(init_db):
    """FastAPI TestClient（带 auth 中间件；无密码配置时是开放的）。"""
    from fastapi.testclient import TestClient
    import main
    main.init_db()
    c = TestClient(main.app)
    return c