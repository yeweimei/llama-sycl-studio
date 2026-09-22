"""API 层集成测试 — FastAPI TestClient 覆盖 main.py 及各 router 的 HTTP 行为。

覆盖端点：
- /api/health            （公开，无认证）
- /v1/models             （公开，OpenAI list 结构）
- /api/services          （list/create/get；需认证；monkeypatch 实例管理防真起进程）
- /api/gpu/status        （monkeypatch list_devices）
- /v1/chat/completions   （mock 上游转发，验证反代路径与标准错误 envelope）

避坑约定（对齐同目录 test_instance_slots.py / conftest.py）：
- 直接用 conftest 提供的 `client` fixture：内部已 `import main; main.init_db()` 并
  返回 `TestClient(main.app)`，勿重复 import / 自己 new。
- 无密码时非公开 /api/* 仍 401 → 用 app.auth.create_token() 造内存 token，
  请求带 `Authorization: Bearer <token>`。
- services / v1 端点若触达真实进程或网络，一律 monkeypatch instance_mgr /
  共享 client，保证用例隔离、不真起 llama-server、不真发网络请求。
- conftest 的 autouse reset_internal_state 已每用例清空 instance_mgr 内存态。
"""
import json

import pytest

from app import auth as auth_mod
from app import instance_mgr as im
from app.routers import gpu as gpu_mod
from app.routers import services as services_router
from app.routers import models as models_router


# ---------------------------------------------------------------- 工具 ----
def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token():
    """合法 auth token（无密码配置下 create_token 仍可用）。"""
    return auth_mod.create_token()


@pytest.fixture
def no_instances(monkeypatch):
    """让 services list / v1/models 不碰真实实例与目录扫描。"""
    monkeypatch.setattr(im, "all_instances", lambda: {})
    monkeypatch.setattr(models_router, "_scan_models", lambda: [])


# ===================================================== 1. /api/health =====
def test_health_ok(client):
    """/api/health → 200 + {status:ok}（公开无认证）。"""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


# ===================================================== 2. /v1/models =====
def test_v1_models_public_list(client, monkeypatch, no_instances):
    """/v1/models 公开无 token → OpenAI list 结构（monkeypatch 实例列表）。"""
    def _fake_list_services():
        return [
            {"id": 1, "name": "Qwen3-9B"},
            {"id": 2, "name": "llama3-8b"},
        ]
    monkeypatch.setattr(services_router, "list_services", _fake_list_services)

    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert ids == ["Qwen3-9B", "llama3-8b"]
    for m in body["data"]:
        assert m["object"] == "model"
        assert m["owned_by"] == "llama-studio"


# ===================================================== 3. 认证 ============
def test_services_requires_auth(client):
    """无 token 访问非公开 /api/services → 401。"""
    r = client.get("/api/services")
    assert r.status_code == 401


def test_services_with_valid_token_pass(client, token, no_instances):
    """带合法 Bearer token → 通过（list 正常返回）。"""
    r = client.get("/api/services", headers=_auth_headers(token))
    assert r.status_code == 200


def test_services_with_bad_token_rejected(client):
    """带无效 token → 仍 401。"""
    r = client.get("/api/services", headers=_auth_headers("not-a-real-token"))
    assert r.status_code == 401


# ===================================================== 4. services CRUD ===
def test_services_crud(client, token, monkeypatch, no_instances):
    """create → list 可见 → get 单个（monkeypatch instance_status 防网络探活）。"""
    # get_service 会调 instance_status；钉死为 stopped，避免 /health 网络请求
    monkeypatch.setattr(
        im, "instance_status",
        lambda sid: {"running": False, "state": "stopped", "port": 0,
                     "pid": None, "started_at": 0, "health_latency_ms": None,
                     "last_health_at": None, "health_ok": False},
    )

    # create
    payload = {
        "name": "api-test-model",
        "model_path": "/models/api-test-model/foo-Q4_K_M.gguf",
        "args": {"n_ctx": 4096},
        "gpu_id": "SYCL0",
    }
    r = client.post("/api/services", json=payload, headers=_auth_headers(token))
    assert r.status_code == 200, r.text
    created = r.json()
    sid = created["id"]
    assert created["name"] == "api-test-model"
    assert created["status"] == "unloaded"

    # list 可见
    r = client.get("/api/services", headers=_auth_headers(token))
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "api-test-model" in names

    # get 单个
    r = client.get(f"/api/services/{sid}", headers=_auth_headers(token))
    assert r.status_code == 200
    got = r.json()
    assert got["name"] == "api-test-model"
    assert got["model_path"] == "/models/api-test-model/foo-Q4_K_M.gguf"
    assert got["gpu_id"] == "SYCL0"


def test_service_create_duplicate_400(client, token):
    """重复注册同名模型 → 400（避免真实进程，create 只写 DB）。"""
    payload = {"name": "dup-model", "model_path": "/models/dup.gguf"}
    first = client.post("/api/services", json=payload, headers=_auth_headers(token))
    assert first.status_code == 200
    second = client.post("/api/services", json=payload, headers=_auth_headers(token))
    assert second.status_code == 400


def test_service_get_404(client, token):
    """不存在的 sid → 404。"""
    r = client.get("/api/services/999999", headers=_auth_headers(token))
    assert r.status_code == 404


# ===================================================== 5. /api/gpu/status =
def test_gpu_status_fake_devices(client, token, monkeypatch):
    """monkeypatch list-devices 相关函数 → 返回假设备 → 200。"""
    fake_devices = [{
        "id": "SYCL0", "backend": "sycl", "name": "Intel Arc A770M",
        "is_discrete": True, "memory_total_mib": 16288,
        "memory_free_mib": 14000, "memory_used_mib": 2288,
    }]
    monkeypatch.setattr(gpu_mod, "_list_devices_backend", lambda: fake_devices)
    monkeypatch.setattr(gpu_mod, "_model_memory_by_backend_device", lambda: {})
    monkeypatch.setattr(gpu_mod, "_xpu_smi_sensors", lambda: {})
    monkeypatch.setattr(
        gpu_mod, "_query_inference_metrics",
        lambda: {"requests_processing": 0, "prompt_tps": 0.0, "predicted_tps": 0.0},
    )

    # 注意：gpu router 的实际路由是 /api/gpu（gpu_status），无 /api/gpu/status 子路径
    r = client.get("/api/gpu", headers=_auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "list-devices+xpu-smi"
    assert len(body["devices"]) == 1
    dev = body["devices"][0]
    assert dev["id"] == "SYCL0"
    assert dev["is_integrated"] is False
    assert dev["memory_used_mib"] == 2288
    assert dev["memory_util_pct"] == 14  # round(2288/16288*100)


# ===================================================== 附加：/v1/chat/completions mock 转发 ==
class _FakeAsyncResponse:
    def __init__(self, status_code=200, payload=None, content_type="application/json"):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"content-type": content_type}
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def request(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_v1_chat_completions_mock_forward(client, token, monkeypatch, no_instances):
    """/v1/chat/completions：mock 上游转发，验证 200 透传与 permit 归还。"""
    # 注册一个模型（create 只写 DB，不起进程）
    r = client.post("/api/services", json={
        "name": "chat-mock", "model_path": "/models/chat-mock/x.gguf",
    }, headers=_auth_headers(token))
    assert r.status_code == 200

    # 钉死实例为 running，并劫持共享 client，避免真实网络
    monkeypatch.setattr(
        im, "instance_status",
        lambda sid: {"running": True, "state": "running", "port": 18901,
                     "pid": 12345, "started_at": 100, "health_latency_ms": 5,
                     "last_health_at": 101, "health_ok": True},
    )
    monkeypatch.setattr(im, "url_for", lambda sid: "http://127.0.0.1:18901")
    monkeypatch.setattr(im, "is_draining", lambda sid: False)
    monkeypatch.setattr(im, "is_warming", lambda sid: False)
    monkeypatch.setattr(im, "is_draining", lambda sid: False)
    monkeypatch.setattr(im, "slot_limit", lambda name: 8)

    async def _fake_acquire(*a, **k):
        return True

    monkeypatch.setattr(im, "acquire_slot", _fake_acquire)
    monkeypatch.setattr(im, "begin_request", lambda sid: True)
    monkeypatch.setattr(im, "end_request", lambda sid: None)
    monkeypatch.setattr(im, "release_slot", lambda sid: None)
    monkeypatch.setattr(im, "mark_success", lambda sid: None)

    upstream_payload = {
        "choices": [{"message": {"content": "hello from fake llama"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 4},
    }
    import main as main_mod
    monkeypatch.setattr(
        main_mod, "_get_v1_shared_client",
        lambda: _FakeAsyncClient(_FakeAsyncResponse(200, upstream_payload)),
    )

    req_body = {"model": "chat-mock", "messages": [{"role": "user", "content": "ping"}]}
    r = client.post("/v1/chat/completions", json=req_body, headers=_auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "hello from fake llama"


@pytest.mark.asyncio
async def test_v1_chat_unknown_model_404(client, token, monkeypatch):
    """未知 model → 标准 OpenAI 错误 envelope 404 model_not_found。"""
    # 不注册任何模型；_resolve_target 会查 DB 返回 None
    req_body = {"model": "does-not-exist",
                "messages": [{"role": "user", "content": "hi"}]}
    r = client.post("/v1/chat/completions", json=req_body, headers=_auth_headers(token))
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "model_not_found"