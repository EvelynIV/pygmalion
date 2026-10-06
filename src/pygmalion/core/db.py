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
import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

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


# --------------------------------------------------------------------------
# 投稿
# --------------------------------------------------------------------------


def add_submission(
    author: str,
    title: str,
    body: str,
    note: str = "",
    direction: str = "vertical",
    colophon: str = "none",
    signature: str = "",
    client_ip: str = "",
    font: str = "",
) -> int:
    """新增一条投稿，返回它的 id。"""
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO submissions
               (author, title, body, note, direction, colophon, signature,
                status, created_at, client_ip, font)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                author,
                title,
                body,
                note,
                direction,
                colophon,
                signature,
                now(),
                client_ip,
                font,
            ),
        )
        return int(cur.lastrowid)


def list_submissions(
    status: str | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    """列出投稿，最新的在前。status 为 None 时返回全部。"""
    sql = """
        SELECT s.*,
               (SELECT COUNT(*) FROM print_jobs j WHERE j.submission_id = s.id) AS job_count,
               (SELECT MAX(j.finished_at) FROM print_jobs j
                 WHERE j.submission_id = s.id AND j.status = 'done')       AS last_printed_at
          FROM submissions s
    """
    params: list[Any] = []
    if status:
        sql += " WHERE s.status = ?"
        params.append(status)
    sql += " ORDER BY s.created_at DESC, s.id DESC LIMIT ?"
    params.append(limit)

    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def get_submission(sub_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM submissions WHERE id = ?", (sub_id,)
        ).fetchone()
        return dict(row) if row else None


def set_submission_status(sub_id: int, status: str, job_id: int | None = None) -> None:
    if status not in SUB_STATUSES:
        raise ValueError(f"非法状态: {status}")
    with connect() as conn:
        if job_id is None:
            conn.execute(
                "UPDATE submissions SET status = ? WHERE id = ?", (status, sub_id)
            )
        else:
            conn.execute(
                "UPDATE submissions SET status=? WHERE id=? AND NOT EXISTS "
                "(SELECT 1 FROM print_jobs WHERE submission_id=? AND id>?)",
                (status, sub_id, sub_id, job_id),
            )


def delete_submission(sub_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM submissions WHERE id = ?", (sub_id,))


def stats() -> dict[str, int]:
    """管理页顶部的统计数字。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM submissions GROUP BY status"
        ).fetchall()
    out = {s: 0 for s in SUB_STATUSES}
    for r in rows:
        out[r["status"]] = r["n"]
    out["total"] = sum(out[s] for s in SUB_STATUSES)
    return out


# --------------------------------------------------------------------------
# 打印任务
# --------------------------------------------------------------------------


def add_job(submission_id: int, gcode_path: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO print_jobs (submission_id, status, gcode_path, created_at)
               VALUES (?, 'queued', ?, ?)""",
            (submission_id, gcode_path, now()),
        )
        return int(cur.lastrowid)


def update_job(
    job_id: int,
    status: str,
    error: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
    gcode_path: str | None = None,
) -> None:
    if status not in JOB_STATUSES:
        raise ValueError(f"非法任务状态: {status}")
    sets, params = ["status = ?"], [status]
    if error:
        sets.append("error = ?")
        params.append(error)
    if started_at:
        sets.append("started_at = ?")
        params.append(started_at)
    if finished_at:
        sets.append("finished_at = ?")
        params.append(finished_at)
    if gcode_path is not None:
        sets.append("gcode_path = ?")
        params.append(gcode_path)
    params.append(job_id)
    with connect() as conn:
        conn.execute(f"UPDATE print_jobs SET {', '.join(sets)} WHERE id = ?", params)


def list_jobs(submission_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM print_jobs WHERE submission_id = ? ORDER BY id DESC",
            (submission_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def freeze_job(
    job_id: int, submission: dict, overrides: dict, total: int, dry_run: bool
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE print_jobs SET snapshot=?, total=?, dry_run=? WHERE id=?",
            (
                json.dumps(
                    {
                        "title": submission["title"],
                        "author": submission["author"],
                        "overrides": overrides,
                    },
                    ensure_ascii=False,
                ),
                total,
                int(dry_run),
                job_id,
            ),
        )


def save_progress(job_id: int, sent: int, total: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE print_jobs SET sent=?, total=? WHERE id=?", (sent, total, job_id)
        )


def all_job_records(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM print_jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item.update(json.loads(item.pop("snapshot")))
        item["job_id"] = item["id"]
        item["percent"] = (
            round(item["sent"] / item["total"] * 100, 1) if item["total"] else 0
        )
        item["message"] = item["error"]
        item["dry_run"] = bool(item["dry_run"])
        result.append(item)
    return result


def interrupt_unfinished_jobs() -> None:
    """重启后不自动重发机器运动指令。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT submission_id FROM print_jobs WHERE status IN ('printing', 'queued')"
        ).fetchall()
        conn.execute(
            "UPDATE print_jobs SET status='cancelled', error='程序重启，任务已中止', finished_at=? WHERE status IN ('printing', 'queued')",
            (now(),),
        )
        for row in rows:
            conn.execute(
                "UPDATE submissions SET status='pending' WHERE id=?",
                (row["submission_id"],),
            )
