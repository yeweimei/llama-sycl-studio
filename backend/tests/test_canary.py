"""Canary：验证契约层可用（env 隔离 + init_db + instance_mgr import）。"""
from app.config import settings


def test_env_isolated_to_tmp():
    assert "llama-studio-test-" in str(settings.data_dir)
    assert settings.model_dir.endswith("/models")


def test_import_instance_mgr():
    from app import instance_mgr
    assert hasattr(instance_mgr, "acquire_slot")
    assert hasattr(instance_mgr, "stalled_sids")