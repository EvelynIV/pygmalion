from __future__ import annotations
import json
from typing import Any
from ..storage import connect, now, JOB_STATUSES


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
