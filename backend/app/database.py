"""SQLite 数据库 - 存服务配置、API keys、下载任务"""
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
                name TEXT UNIQUE NOT NULL,          -- 服务名（也用作容器名）
                model_path TEXT NOT NULL,           -- 模型路径（容器内 /models/...）
                port INTEGER UNIQUE NOT NULL,       -- 对外端口
                args TEXT NOT NULL DEFAULT '{}',    -- JSON: llama-server 参数
                gpu_devices TEXT NOT NULL DEFAULT '[]', -- JSON: 使用的显卡设备列表
                api_key TEXT,                       -- OpenAI API key（可选）
                status TEXT DEFAULT 'stopped',      -- running/stopped/error
                container_id TEXT,
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
                source TEXT NOT NULL,               -- huggingface / modelscope
                repo_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                local_path TEXT NOT NULL,
                status TEXT DEFAULT 'downloading',  -- downloading/done/error
                progress REAL DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                downloaded_bytes INTEGER DEFAULT 0,
                created_at INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                args TEXT NOT NULL DEFAULT '{}',    -- JSON: 参数模板
                created_at INTEGER
            );
            """
        )
        # 旧库迁移：services 表补 gpu_devices 列
        cols = {r[1] for r in conn.execute("PRAGMA table_info(services)").fetchall()}
        if "gpu_devices" not in cols:
            conn.execute("ALTER TABLE services ADD COLUMN gpu_devices TEXT NOT NULL DEFAULT '[]'")


def now() -> int:
    return int(time.time())
