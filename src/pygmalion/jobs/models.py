from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobProgress:
    job_id: int
    submission_id: int
    status: str = "queued"  # queued | printing | done | failed | cancelled
    sent: int = 0
    total: int = 0
    message: str = ""
    overrides: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    @property
    def percent(self) -> float:
        return (self.sent / self.total * 100.0) if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "submission_id": self.submission_id,
            "status": self.status,
            "sent": self.sent,
            "total": self.total,
            "percent": round(self.percent, 1),
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
