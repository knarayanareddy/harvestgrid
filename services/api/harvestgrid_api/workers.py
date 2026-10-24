"""Background job submission for the forecasting and notification workers.

The API never runs a tick or an SMS fan-out on the request thread's behalf without recording a
job. Two runners implement the same port:

* :class:`InlineJobRunner` executes the job immediately and records the completed job. This is
  the default for local and fixture operation, and it keeps the API deterministic.
* :class:`BrokerJobRunner` hands the callable to a Celery/RQ style backend. When the backend
  or its broker is not configured it raises :class:`WorkerBackendUnavailable`, which the routes
  translate into ``503 worker_backend_unavailable`` rather than pretending a job was queued.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from .ids import new_ulid


class WorkerBackendUnavailable(RuntimeError):
    """No worker backend (broker, celery, rq) is installed or configured."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    kind: str
    status: str
    submitted_at: datetime
    completed_at: datetime | None
    result: dict[str, Any] | None
    result_sha256: str | None
    provenance: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result_sha256": self.result_sha256,
            "provenance": self.provenance,
            "result": self.result,
        }


def digest_payload(payload: dict[str, Any]) -> str:
    """SHA-256 over a canonical JSON encoding; used for job and response digests."""

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class JobRunner(Protocol):
    def submit(self, *, kind: str, work: Callable[[], dict[str, Any]]) -> JobRecord: ...


@dataclass
class InMemoryJobRegistry:
    """Bounded job history for operators; a fixture store, not a durable queue."""

    jobs: dict[str, JobRecord] = field(default_factory=dict)
    max_jobs: int = 200

    def add(self, record: JobRecord) -> JobRecord:
        self.jobs[record.job_id] = record
        if len(self.jobs) > self.max_jobs:
            oldest = sorted(self.jobs.values(), key=lambda item: item.submitted_at)[0]
            self.jobs.pop(oldest.job_id, None)
        return record

    def get(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def all(self) -> tuple[JobRecord, ...]:
        """Every recorded job, oldest first."""

        return tuple(sorted(self.jobs.values(), key=lambda item: item.submitted_at))

    def recent(self, limit: int = 20) -> tuple[JobRecord, ...]:
        ordered = sorted(self.jobs.values(), key=lambda item: item.submitted_at, reverse=True)
        return tuple(ordered[:limit])


def completed_job(
    *,
    kind: str,
    result: dict[str, Any],
    clock: Callable[[], datetime],
    provenance: str,
    started_at: datetime | None = None,
) -> JobRecord:
    """Build a completed job record for work executed inline by the route."""

    submitted_at = (started_at or clock()).astimezone(UTC)
    return JobRecord(
        job_id=new_ulid(clock),
        kind=kind,
        status="completed",
        submitted_at=submitted_at,
        completed_at=clock().astimezone(UTC),
        result=result,
        result_sha256=digest_payload(result),
        provenance=provenance,
    )


@dataclass
class InlineJobRunner:
    """Runs the job on the request thread and records the completed job."""

    clock: Callable[[], datetime]
    registry: InMemoryJobRegistry
    provenance: str = "fixture"

    def submit(self, *, kind: str, work: Callable[[], dict[str, Any]]) -> JobRecord:
        submitted_at = self.clock().astimezone(UTC)
        job_id = new_ulid(self.clock)
        result = work()
        record = JobRecord(
            job_id=job_id,
            kind=kind,
            status="completed",
            submitted_at=submitted_at,
            completed_at=self.clock().astimezone(UTC),
            result=result,
            result_sha256=digest_payload(result),
            provenance=self.provenance,
        )
        return self.registry.add(record)


@dataclass
class BrokerJobRunner:
    """Hands work to a broker-backed worker; fails visibly when none is configured."""

    broker_url: str | None
    clock: Callable[[], datetime]
    registry: InMemoryJobRegistry
    provenance: str = "live"

    def submit(self, *, kind: str, work: Callable[[], dict[str, Any]]) -> JobRecord:
        if not (self.broker_url or "").strip():
            raise WorkerBackendUnavailable(
                "no forecasting/notification broker is configured; set FORECASTING_BROKER_URL"
            )
        try:
            from celery import Celery  # type: ignore[import-not-found]  # noqa: F401
        except ModuleNotFoundError:
            raise WorkerBackendUnavailable(
                "celery is not installed; the broker-backed worker cannot accept jobs"
            ) from None
        raise WorkerBackendUnavailable(
            "broker submission is not implemented in this slice; use the inline runner"
        )
