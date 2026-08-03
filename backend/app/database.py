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
                device TEXT DEFAULT '0',
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

            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sid INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT DEFAULT '',
                thinking TEXT DEFAULT '',
                created_at INTEGER
            );
            """
        )

    # Migrations: add columns if not exist
    with get_conn() as conn:
        preset_cols = [r[1] for r in conn.execute("PRAGMA table_info(model_presets)").fetchall()]
        if "mmap" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN mmap INTEGER DEFAULT 1")
        if "device" not in preset_cols:
            conn.execute("ALTER TABLE model_presets ADD COLUMN device TEXT DEFAULT 'SYCL0'")
        # 迁移旧 device 值: "0"->"SYCL0", "1"->"SYCL1"
        for old, new in [("0", "SYCL0"), ("1", "SYCL1")]:
            conn.execute("UPDATE model_presets SET device=? WHERE device=?", (new, old))
        # 也处理空值和 NULL
        conn.execute("UPDATE model_presets SET device='SYCL0' WHERE device IS NULL OR device=''")
        svc_cols = [r[1] for r in conn.execute("PRAGMA table_info(services)").fetchall()]
        if "gpu_id" not in svc_cols:
            conn.execute("ALTER TABLE services ADD COLUMN gpu_id TEXT DEFAULT ''")


def now() -> int:
    return int(time.time())
