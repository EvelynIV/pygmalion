from __future__ import annotations
from typing import Any
from ..storage import connect, now, SUB_STATUSES


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
