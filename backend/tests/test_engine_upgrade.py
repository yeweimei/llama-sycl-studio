"""引擎升级生命周期单元测试 — backend/app/routers/engine.py（批次一）。

覆盖：版本清洗、ELF 校验、二进制集备份/恢复（含符号链接）、以及升级/切换/回滚
后的优雅重启编排 `_restart_loaded_instances_after_engine_change`。

约定（避坑）：
- conftest 已把 LLAMA_STUDIO_DATA 指到一次性临时目录 → 模块级 BIN_DIR =
  Path(settings.data_dir)/"bin" 落在临时目录，文件函数可安全造真实文件。
- `_backup_current_set` / `_restore_set` / `_current_set_files` 都以
  `CURRENT_BIN.parent`（默认 /app）为工作目录 → monkeypatch engine.CURRENT_BIN
  指到 tmp_path 下的假 app 目录，避免写真实的 /app。
- `_restart_loaded_instances_after_engine_change` 内 `from app import instance_mgr`
  → monkeypatch 同一模块对象 app.instance_mgr.stop_instance/start_instance 即可。
  其内部 `time.sleep(2)` 用 monkeypatch 桩掉，避免用例拖慢 2s。
- **_sanitize_version 是清洗函数而非拒绝**：非法字符被替换为 `_`、首尾 . / _ 被
  strip；只有输入为 None 或清洗后为空才抛 HTTPException(400)。路径穿越/超长输入
  会被清洗成安全值、不会抛 400——测试断言源码的真实契约。
"""
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.database import get_conn
from app.routers import engine
from app import instance_mgr as im


# ===================================================== 工具 fixtures ====
@pytest.fixture
def app_base(monkeypatch, tmp_path):
    """把 CURRENT_BIN 指到 tmp_path 下的假 app 目录，engine._app_dir() 即该目录。"""
    base = tmp_path / "app"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(engine, "CURRENT_BIN", base / "llama-server")
    return base


def _insert_service(conn, name, model_path, status):
    conn.execute(
        "INSERT INTO services (name, model_path, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        (name, model_path, status, engine.now(), engine.now()),
    )


def _make_exec_file(path: Path, data: bytes):
    path.write_bytes(data)
    path.chmod(0o755)
    return path


# ==================================================== 1. 版本清洗 ======
class TestSanitizeVersion:
    def test_legal_b_version_passes_through(self):
        assert engine._sanitize_version("b10622") == "b10622"

    def test_non_b_alphanumeric_passes(self):
        # 允许 [A-Za-z0-9._-]，不强制 b 前缀（b 前缀校验在 upgrade 端点做）
        assert engine._sanitize_version("v1.2.3") == "v1.2.3"

    def test_none_raises_400(self):
        with pytest.raises(HTTPException) as ei:
            engine._sanitize_version(None)
        assert ei.value.status_code == 400

    def test_empty_raises_400(self):
        with pytest.raises(HTTPException) as ei:
            engine._sanitize_version("")
        assert ei.value.status_code == 400

    def test_only_separators_raises_400(self):
        # 全是 . / _ 时 strip 后为空 → 拒绝
        for bad in ("...", "___", ".__.", "///"):
            with pytest.raises(HTTPException) as ei:
                engine._sanitize_version(bad)
            assert ei.value.status_code == 400, bad

    def test_path_traversal_sanitized_not_rejected(self):
        # 源码是"清洗成安全值"而非拒绝：路径字符替换为 _，首尾 . strip
        out = engine._sanitize_version("../../b10622")
        assert "/" not in out
        assert not out.startswith(".")
        assert out == "b10622"

    def test_overlong_passthrough_no_400(self):
        # 源码无长度上限（长度限制在 UI/上游），超长合法串原样返回
        long_ver = "b1" + "2345" * 100
        assert engine._sanitize_version(long_ver) == long_ver

    def test_special_chars_replaced_with_underscore(self):
        # 源码 .strip("._") 会去掉尾部 / 首部的 _ 和 . ，故尾部下划线被剥掉
        assert engine._sanitize_version("b1:2@3# ") == "b1_2_3"
        assert engine._sanitize_version("b1:2@3#a") == "b1_2_3_a"


# ==================================================== 2. ELF 校验 ======
class TestIsValidElf:
    def test_elf_magic_executable_is_true(self, tmp_path):
        p = _make_exec_file(tmp_path / "llama-server", b"\x7fELF\x02\x01\x01")
        assert engine._is_valid_elf(p) is True

    def test_elf_magic_but_not_executable_is_false(self, tmp_path):
        p = tmp_path / "llama-server"
        p.write_bytes(b"\x7fELF\x02\x01\x01")  # 默认权限无执行位
        assert engine._is_valid_elf(p) is False

    def test_text_file_is_false(self, tmp_path):
        p = _make_exec_file(tmp_path / "note.txt", b"hello llama")
        assert engine._is_valid_elf(p) is False

    def test_nonexistent_is_false(self, tmp_path):
        assert engine._is_valid_elf(tmp_path / "missing") is False


# ============================================ 3. 文件集备份/恢复 ======
class TestAtomicReplace:
    def test_replaces_and_sets_exec(self, tmp_path):
        dst = tmp_path / "llama-server"
        dst.write_bytes(b"old")
        src = tmp_path / "new"
        src.write_bytes(b"\x7fELF-new")
        src.chmod(0o600)
        engine._atomic_replace(src, dst)
        assert dst.read_bytes() == b"\x7fELF-new"
        assert os.access(str(dst), os.X_OK)
        # 不留临时残留
        assert list(tmp_path.glob(".*.tmp.*")) == []


class TestCopyEntry:
    def test_copy_regular_file(self, tmp_path):
        src = tmp_path / "libllama.so"
        src.write_bytes(b"libdata")
        engine._copy_entry(src, tmp_path / "dst.so")
        assert (tmp_path / "dst.so").read_bytes() == b"libdata"

    def test_copy_symlink_recreates_link_not_target(self, tmp_path):
        target = tmp_path / "real.so"
        target.write_bytes(b"real")
        link = tmp_path / "libllama.so"
        link.symlink_to(target.name)
        dst = tmp_path / "copy.so"
        engine._copy_entry(link, dst)
        assert dst.is_symlink()
        assert os.readlink(str(dst)) == target.name


class TestRestoreEntry:
    def test_restore_file_via_atomic_replace(self, tmp_path):
        app = tmp_path / "app"
        app.mkdir()
        (app / "llama-server").write_bytes(b"tampered")
        src = tmp_path / "llama-server"
        src.write_bytes(b"\x7fELF-good")
        engine._restore_entry(src, app)
        assert (app / "llama-server").read_bytes() == b"\x7fELF-good"

    def test_restore_symlink(self, tmp_path):
        app = tmp_path / "app"
        app.mkdir()
        (app / "libllama.so").write_bytes(b"tampered")  # 原普通文件
        src = tmp_path / "libllama.so"
        src.symlink_to("libllama.so.1")  # 备份里是符号链接
        engine._restore_entry(src, app)
        dst = app / "libllama.so"
        assert dst.is_symlink()
        assert os.readlink(str(dst)) == "libllama.so.1"


class TestReplaceFromDir:
    def test_replaces_matching_entries_and_skips_others(self, tmp_path):
        app = tmp_path / "app"
        app.mkdir()
        src = tmp_path / "pkg"
        src.mkdir()
        # 应被替换：llama-server、lib* 普通文件 + 一个 SONAME 符号链接
        _make_exec_file(src / "llama-server", b"\x7fELF-v9")
        (src / "libllama.so.1").write_bytes(b"lib-v9")
        (src / "libllama.so").symlink_to("libllama.so.1")
        # 不应被替换：无关文件
        (src / "README.md").write_bytes(b"readme")
        # 目标 app 里放旧内容
        (app / "llama-server").write_bytes(b"old-bin")
        (app / "libllama.so.1").write_bytes(b"old-lib")
        (app / "README.md").write_bytes(b"old-readme")

        replaced = engine._replace_from_dir(src, app)
        assert replaced == 3  # llama-server + libllama.so.1 + libllama.so
        assert (app / "llama-server").read_bytes() == b"\x7fELF-v9"
        assert (app / "libllama.so.1").read_bytes() == b"lib-v9"
        assert (app / "libllama.so").is_symlink()
        assert os.readlink(str(app / "libllama.so")) == "libllama.so.1"
        # 无关文件保持原样（不受替换影响）
        assert (app / "README.md").read_bytes() == b"old-readme"


class TestBackupRestoreSet:
    def _build_set(self, app_base):
        _make_exec_file(app_base / "llama-server", b"\x7fELF-current")
        (app_base / "libllama.so.1").write_bytes(b"lib-current")
        (app_base / "libllama.so").symlink_to("libllama.so.1")

    def test_backup_current_set_roundtrip_with_symlink(self, app_base):
        self._build_set(app_base)
        version = "b10622"
        dest = engine._backup_current_set(version)
        assert dest.name == version and dest.is_dir()
        # 备份里符号链接仍为符号链接
        assert (dest / "llama-server").read_bytes() == b"\x7fELF-current"
        assert (dest / "libllama.so.1").read_bytes() == b"lib-current"
        assert (dest / "libllama.so").is_symlink()
        assert os.readlink(str(dest / "libllama.so")) == "libllama.so.1"

        # 篡改 app 集 → restore 还原（含符号链接）
        (app_base / "llama-server").write_bytes(b"TAMPERED")
        (app_base / "libllama.so.1").write_bytes(b"TAMPERED-lib")
        engine._restore_set(version)
        assert (app_base / "llama-server").read_bytes() == b"\x7fELF-current"
        assert (app_base / "libllama.so.1").read_bytes() == b"lib-current"
        assert (app_base / "libllama.so").is_symlink()
        assert os.readlink(str(app_base / "libllama.so")) == "libllama.so.1"

    def test_backup_is_idempotent(self, app_base):
        self._build_set(app_base)
        d1 = engine._backup_current_set("b10622")
        # 篡改 app，再次备份不应覆盖已有备份
        (app_base / "llama-server").write_bytes(b"TAMPERED")
        d2 = engine._backup_current_set("b10622")
        assert d1 == d2
        assert (d1 / "llama-server").read_bytes() == b"\x7fELF-current"

    def test_restore_empty_backup_raises(self, app_base):
        version = "b99999"
        dest = engine.BIN_DIR / version
        dest.mkdir(parents=True, exist_ok=True)
        with pytest.raises(RuntimeError):
            engine._restore_set(version)

    def test_restore_missing_version_404(self, app_base):
        # 既非新格式目录也非旧格式单文件 → 404
        with pytest.raises(HTTPException) as ei:
            engine._restore_set("b88888")
        assert ei.value.status_code == 404

    def test_restore_legacy_single_file(self, app_base, monkeypatch):
        # 旧格式：BIN_DIR/llama-server-bXXX 单文件直接覆盖 CURRENT_BIN
        legacy = engine.BIN_DIR / "llama-server-b77777"
        legacy.write_bytes(b"\x7fELF-legacy")
        engine._restore_set("b77777")
        assert engine.CURRENT_BIN.read_bytes() == b"\x7fELF-legacy"
        assert os.access(str(engine.CURRENT_BIN), os.X_OK)


# ====================== 4. 升级/切换/回滚后的优雅重启编排 ==============
def _seeded_services(init_db, *rows):
    """rows: (name, model_path, status)。返回连接供后续断言。"""
    init_db()
    conn = get_conn()
    for r in rows:
        _insert_service(conn, *r)
    conn.commit()
    return conn


class TestRestartLoadedInstances:
    def test_stop_start_only_loaded(self, monkeypatch, init_db):
        conn = _seeded_services(
            init_db,
            ("alpha", "/m/alpha.gguf", "loaded"),
            ("beta", "/m/beta.gguf", "loaded"),
            ("idle", "/m/idle.gguf", "unloaded"),
        )
        stopped, started = [], []
        im.stop_instance = lambda sid, graceful=True, drain_timeout=30.0: stopped.append(sid) or {"stopped": True}
        im.start_instance = lambda sid, name, model_path: started.append((sid, name, model_path)) or {"ok": True}
        monkeypatch.setattr(engine.time, "sleep", lambda s: None)

        res = engine._restart_loaded_instances_after_engine_change()

        # 仅 loaded 两个被停 + 起；unloaded 完全不碰
        assert res["stopped"] == ["alpha", "beta"]
        # 成功返回无 error 键（只有异常兜底路径才带 error）
        assert "error" not in res
        assert res["skipped"] == []
        assert sorted(s[0] for s in started) == [conn.execute(
            "SELECT id FROM services WHERE name='alpha'").fetchone()["id"],
            conn.execute("SELECT id FROM services WHERE name='beta'").fetchone()["id"]]
        # started 形如 {"id","name","ok":True}
        assert all(s["ok"] is True for s in res["started"])
        assert {s["name"] for s in res["started"]} == {"alpha", "beta"}
        assert sorted(sid for sid in stopped) == [
            conn.execute("SELECT id FROM services WHERE name='alpha'").fetchone()["id"],
            conn.execute("SELECT id FROM services WHERE name='beta'").fetchone()["id"]]
        # 计数对：每个 loaded 都 stop 一次 + start 一次
        assert len(stopped) == 2 and len(started) == 2

    def test_no_loaded_returns_skipped(self, monkeypatch, init_db):
        conn = _seeded_services(init_db, ("idle", "/m/idle.gguf", "unloaded"))
        im.stop_instance = lambda *a, **k: pytest.fail("不应 stop 未加载实例")
        im.start_instance = lambda *a, **k: pytest.fail("不应 start 未加载实例")
        monkeypatch.setattr(engine.time, "sleep", lambda s: None)

        res = engine._restart_loaded_instances_after_engine_change()
        assert res == {"stopped": [], "started": [], "skipped": [{"reason": "no_loaded"}], "error": None} or \
               (res["stopped"] == [] and res["started"] == [] and res["skipped"] == [{"reason": "no_loaded"}])

    def test_start_failure_recorded_ok_false(self, monkeypatch, init_db):
        conn = _seeded_services(init_db, ("bad", "/m/bad.gguf", "loaded"))
        im.stop_instance = lambda *a, **k: {"stopped": True}
        def _fail(sid, name, model_path):
            raise RuntimeError("failed to start")
        im.start_instance = _fail
        monkeypatch.setattr(engine.time, "sleep", lambda s: None)

        res = engine._restart_loaded_instances_after_engine_change()
        assert res["stopped"] == ["bad"]
        assert res["started"][0]["ok"] is False
        assert res["started"][0]["name"] == "bad"
        assert "failed to start" in res["started"][0]["error"]

    def test_db_error_returns_error_dict(self, monkeypatch, init_db):
        import app.routers.engine as eng
        def _boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(eng, "get_conn", _boom)
        res = engine._restart_loaded_instances_after_engine_change()
        assert res["stopped"] == [] and res["started"] == [] and res["skipped"] == []
        assert res["error"] is not None and "实例重启编排异常" in res["error"]