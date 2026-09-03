"""SQLite 数据库 - 存服务配置、API keys、下载任务、模型预设"""
import sqlite3
import json
import time
from pathlib import Path
from typing import Optional

from app.config import settings

DB = settings.db_path


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """建表（幂等）"""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                model_path TEXT NOT NULL,
                args TEXT NOT NULL DEFAULT '{}',
                gpu_id TEXT DEFAULT '',
                api_key TEXT,
                status TEXT DEFAULT 'unloaded',
                created_at INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                key TEXT UNIQUE NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS download_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                local_path TEXT NOT NULL,
                status TEXT DEFAULT 'downloading',
                progress REAL DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                downloaded_bytes INTEGER DEFAULT 0,
                created_at INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                args TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS model_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT UNIQUE NOT NULL,
                ctx_size INTEGER DEFAULT 8192,
                temp REAL DEFAULT 0.7,
                threads INTEGER DEFAULT 8,
                batch_size INTEGER DEFAULT 2048,
                ubatch_size INTEGER DEFAULT 512,
                parallel INTEGER DEFAULT 4,
                cache_type_k TEXT DEFAULT 'q8_0',
                cache_type_v TEXT DEFAULT 'q8_0',
                flash_attn INTEGER DEFAULT 1,
                jinja INTEGER DEFAULT 1,
                n_gpu_layers INTEGER DEFAULT 99,
                mmap INTEGER DEFAULT 1,
                device TEXT DEFAULT 'auto',
                cpu_moe INTEGER DEFAULT 0,
                cpu_moe_layers INTEGER DEFAULT 0,
                mtp INTEGER DEFAULT 0,
                mtp_model TEXT DEFAULT '',
                mtp_n_max INTEGER DEFAULT 3,
                spec_draft_type_k TEXT DEFAULT '',
                spec_draft_type_v TEXT DEFAULT '',
                rope_scaling TEXT DEFAULT '',
                rope_scale REAL,
                yarn_orig_ctx INTEGER,
                reasoning TEXT DEFAULT '',
                reasoning_budget INTEGER,
                reasoning_effort TEXT DEFAULT '',
                extra_args TEXT DEFAULT '{}',
                created_at INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS model_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT UNIQUE NOT NULL,
                tags TEXT DEFAULT '[]',
                custom_tags TEXT DEFAULT '[]',
                updated_at INTEGER
            );

            -- 已删除模型墓碑：阻止 router 自动注册时复活（硬删除后的持久标记）
            CREATE TABLE IF NOT EXISTS deleted_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS api_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT UNIQUE NOT NULL,
                request_count INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_prefill_ms INTEGER DEFAULT 0,
                total_decode_ms INTEGER DEFAULT 0,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS api_request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT NOT NULL,
                stream INTEGER DEFAULT 0,
                ok INTEGER DEFAULT 1,
                status_code INTEGER DEFAULT 200,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_ms INTEGER DEFAULT 0,
                prefill_ms INTEGER DEFAULT 0,
                decode_ms INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                endpoint TEXT DEFAULT '',
                method TEXT DEFAULT 'POST',
                created_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_api_request_logs_created ON api_request_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_api_request_logs_model ON api_request_logs(model_name);

            -- 对话内容日志（chat_proxy 记录的输入/输出/thinking，最近 1000 条）
            CREATE TABLE IF NOT EXISTS chat_api_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT NOT NULL,
                stream INTEGER DEFAULT 0,
                user_message TEXT DEFAULT '',
                response TEXT DEFAULT '',
                thinking TEXT DEFAULT '',
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_ms INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                status_code INTEGER DEFAULT 200,
                error TEXT DEFAULT '',
                created_at INTEGER,
                finished_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_chat_api_logs_created ON chat_api_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_chat_api_logs_status ON chat_api_logs(status);

            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sid INTEGER NOT NULL,
                session_id INTEGER DEFAULT 0,
                role TEXT NOT NULL,
                content TEXT DEFAULT '',
                thinking TEXT DEFAULT '',
                created_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sid INTEGER NOT NULL,
                title TEXT DEFAULT '新会话',
                created_at INTEGER,
                updated_at INTEGER
            );
            """
        )

    # Migrations: add columns if not exist
    with get_conn() as conn:
        preset_cols = [r[1] for r in conn.execute("PRAGMA table_info(model_presets)").fetchall()]
        # api_request_logs 端点列（2026-08-25：API 端点统计）
        log_cols = [r[1] for r in conn.execute("PRAGMA table_info(api_request_logs)").fetchall()]
        if "endpoint" not in log_cols:
            conn.execute("ALTER TABLE api_request_logs ADD COLUMN endpoint TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_request_logs_endpoint ON api_request_logs(endpoint)")
        if "method" not in log_cols:
            conn.execute("ALTER TABLE api_request_logs ADD COLUMN method TEXT DEFAULT 'POST'")
        # download_tasks 错误信息列（2026-08-25：下载失败原因持久化）
        dl_cols = [r[1] for r in conn.execute("PRAGMA table_info(download_tasks)").fetchall()]
        if "error" not in dl_cols:
            conn.execute("ALTER TABLE download_tasks ADD COLUMN error TEXT DEFAULT ''")
        # api_stats 成功率/错误列（2026-08-25：端点统计聚合）
        stats_cols = [r[1] for r in conn.execute("PRAGMA table_info(api_stats)").fetchall()]
        if "ok_count" not in stats_cols:
            conn.execute("ALTER TABLE api_stats ADD COLUMN ok_count INTEGER DEFAULT 0")
        if "fail_count" not in stats_cols:
            conn.execute("ALTER TABLE api_stats ADD COLUMN fail_count INTEGER DEFAULT 0")
        # 迁移兼容：旧行（迁移前无 ok/fail 统计）按 request_count 补齐成功率
        conn.execute("UPDATE api_stats SET ok_count = request_count WHERE ok_count = 0 AND fail_count = 0")
        if "mmap" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN mmap INTEGER DEFAULT 1")
        if "device" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN device TEXT DEFAULT 'SYCL0'")
        if "rope_scaling" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN rope_scaling TEXT DEFAULT ''")
        if "rope_scale" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN rope_scale REAL")
        if "yarn_orig_ctx" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN yarn_orig_ctx INTEGER")
        if "reasoning" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN reasoning TEXT DEFAULT ''")
        if "reasoning_budget" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN reasoning_budget INTEGER")
        if "reasoning_effort" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN reasoning_effort TEXT DEFAULT ''")
        # 模板单套化（2026-08-26 晚：device 语义化后模板后端无关，去掉 backend 分套）
        # 旧库（UNIQUE(model_name, backend) 双后端分套）→ 重建为 UNIQUE(model_name) 单套，
        # 每模型保留 updated_at 最新的一套（其余参数由该套统一承载）
        if "backend" in preset_cols:
            conn.execute("ALTER TABLE model_presets RENAME TO model_presets_multi")
            conn.execute("""CREATE TABLE model_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT UNIQUE NOT NULL,
                ctx_size INTEGER DEFAULT 8192,
                temp REAL DEFAULT 0.7,
                threads INTEGER DEFAULT 8,
                batch_size INTEGER DEFAULT 2048,
                ubatch_size INTEGER DEFAULT 512,
                parallel INTEGER DEFAULT 4,
                cache_type_k TEXT DEFAULT 'q8_0',
                cache_type_v TEXT DEFAULT 'q8_0',
                flash_attn INTEGER DEFAULT 1,
                jinja INTEGER DEFAULT 1,
                n_gpu_layers INTEGER DEFAULT 99,
                mmap INTEGER DEFAULT 1,
                device TEXT DEFAULT 'auto',
                cpu_moe INTEGER DEFAULT 0,
                cpu_moe_layers INTEGER DEFAULT 0,
                mtp INTEGER DEFAULT 0,
                mtp_model TEXT DEFAULT '',
                mtp_n_max INTEGER DEFAULT 3,
                spec_draft_type_k TEXT DEFAULT '',
                spec_draft_type_v TEXT DEFAULT '',
                rope_scaling TEXT DEFAULT '',
                rope_scale REAL,
                yarn_orig_ctx INTEGER,
                reasoning TEXT DEFAULT '',
                reasoning_budget INTEGER,
                reasoning_effort TEXT DEFAULT '',
                extra_args TEXT DEFAULT '{}',
                created_at INTEGER,
                updated_at INTEGER
            )""")
            multi_cols = [r[1] for r in conn.execute("PRAGMA table_info(model_presets_multi)").fetchall()]
            # 去掉 backend 列，其余列全部搬入（含 id，保持引用稳定）
            col_list = ", ".join(c for c in multi_cols if c != "backend")
            # 每 model_name 保留 updated_at 最新（平局取 id 最大）的一套
            conn.execute(f"""
                INSERT INTO model_presets ({col_list})
                SELECT {col_list} FROM model_presets_multi m
                WHERE m.id = (
                    SELECT m2.id FROM model_presets_multi m2
                    WHERE m2.model_name = m.model_name
                    ORDER BY m2.updated_at DESC, m2.id DESC LIMIT 1
                )
            """)
            conn.execute("DROP TABLE model_presets_multi")
            # 重建后刷新列列表（新表含全部列，避免后续 ADD COLUMN 冲突）
            preset_cols = [r[1] for r in conn.execute("PRAGMA table_info(model_presets)").fetchall()]
        # 补齐此前遗漏的列（presets.py INSERT 已引用但从未加迁移/建表，导致新库建预设报错）
        if "cpu_moe" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN cpu_moe INTEGER DEFAULT 0")
        if "cpu_moe_layers" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN cpu_moe_layers INTEGER DEFAULT 0")
        if "mtp" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN mtp INTEGER DEFAULT 0")
        if "mtp_model" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN mtp_model TEXT DEFAULT ''")
        if "mtp_n_max" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN mtp_n_max INTEGER DEFAULT 3")
        if "spec_draft_type_k" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN spec_draft_type_k TEXT DEFAULT ''")
        if "spec_draft_type_v" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN spec_draft_type_v TEXT DEFAULT ''")
        # device 语义化（后端无关）：SYCL0/Vulkan1→discrete，SYCL1/Vulkan0→integrated，旧数字 0/1 同理
        conn.execute("UPDATE model_presets SET device='discrete' WHERE device IN ('SYCL0','Vulkan1','0')")
        conn.execute("UPDATE model_presets SET device='integrated' WHERE device IN ('SYCL1','Vulkan0','1')")
        # 其余未知/空值 → auto
        conn.execute("UPDATE model_presets SET device='auto' WHERE device IS NULL OR device='' OR device NOT IN ('auto','discrete','integrated')")
        svc_cols = [r[1] for r in conn.execute("PRAGMA table_info(services)").fetchall()]
        if "gpu_id" not in svc_cols:
            conn.execute("ALTER TABLE services ADD COLUMN gpu_id TEXT DEFAULT ''")
        if "hidden" not in svc_cols:
            conn.execute("ALTER TABLE services ADD COLUMN hidden INTEGER DEFAULT 0")
        if "idle_unload_min" not in svc_cols:
            conn.execute("ALTER TABLE services ADD COLUMN idle_unload_min INTEGER DEFAULT 0")
        if "last_used_at" not in svc_cols:
            conn.execute("ALTER TABLE services ADD COLUMN last_used_at INTEGER DEFAULT 0")
        # 迁移：旧软删除(hidden=1)记录 -> 墓碑表，然后物理删除（硬删除策略）
        try:
            hidden_rows = conn.execute(
                "SELECT name FROM services WHERE hidden=1"
            ).fetchall()
            for hr in hidden_rows:
                conn.execute(
                    "INSERT OR IGNORE INTO deleted_models (name, created_at) VALUES (?,?)",
                    (hr["name"], now()),
                )
            conn.execute("DELETE FROM services WHERE hidden=1")
        except Exception:
            pass  # 表或列不存在时跳过
        # 迁移: chat_history 加 session_id 列
        ch_cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_history)").fetchall()]
        if "session_id" not in ch_cols:
            conn.execute("ALTER TABLE chat_history ADD COLUMN session_id INTEGER DEFAULT 0")


def now() -> int:
    return int(time.time())
