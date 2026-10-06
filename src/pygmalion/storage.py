"""
数据层 —— SQLite 存储投稿的诗词和打印记录。

设计要点：
  submissions  一次投稿就是一行，投稿人只看得到自己的提交结果
  print_jobs   一次"让机器去写"就是一行。同一首诗可以反复写（写废了重写），
               所以打印状态不能放在 submissions 上，否则会丢掉历史。

状态流转：
  submission.status: pending -> queued -> printing -> done / failed
  这是从最新一条 print_job 汇总出来的冗余字段，方便管理页直接筛选。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from .config import DATA_DIR

DB_PATH = DATA_DIR / "poems.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    author       TEXT    NOT NULL,
    title        TEXT    NOT NULL DEFAULT '',
    body         TEXT    NOT NULL,
    note         TEXT    NOT NULL DEFAULT '',
    direction    TEXT    NOT NULL DEFAULT 'vertical',
    colophon     TEXT    NOT NULL DEFAULT 'none',
    signature    TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'pending',
    created_at   TEXT    NOT NULL,
    client_ip    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS print_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL ,
    status        TEXT    NOT NULL DEFAULT 'queued',
    gcode_path    TEXT    NOT NULL DEFAULT '',
    error         TEXT    NOT NULL DEFAULT '',
    started_at    TEXT,
    finished_at   TEXT,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_submission ON print_jobs(submission_id);
CREATE INDEX IF NOT EXISTS idx_sub_status      ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_sub_created     ON submissions(created_at DESC);
"""

# 允许的状态值，防止拼写错误写进库里
SUB_STATUSES = ("pending", "queued", "printing", "done", "failed")
JOB_STATUSES = ("queued", "printing", "done", "failed", "cancelled")


def now() -> str:
    """本地时间的 ISO 字符串，精确到秒。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """打开数据库连接。WAL 模式让 Web 请求和打印线程可以并发读写。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 后加的列。老数据库升级时用 ALTER TABLE 补上，已有数据原样保留。
# 值是列的默认值表达式。
_MIGRATIONS: dict[str, dict[str, str]] = {
    "submissions": {
        "font": "TEXT NOT NULL DEFAULT ''",  # 作者选的字库 id，空=默认
    },
    "print_jobs": {
        "snapshot": "TEXT NOT NULL DEFAULT '{}'",
        "sent": "INTEGER NOT NULL DEFAULT 0",
        "total": "INTEGER NOT NULL DEFAULT 0",
        "dry_run": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    """
    给已存在的表补新列。

    不引入迁移框架 —— 这个项目就一张主表，ALTER TABLE ADD COLUMN 足够了。
    重复执行是安全的：先查 PRAGMA 看列在不在，在就跳过。
    """
    for table, columns in _MIGRATIONS.items():
        try:
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    """建表并补齐新增的列。可以重复调用。"""
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
