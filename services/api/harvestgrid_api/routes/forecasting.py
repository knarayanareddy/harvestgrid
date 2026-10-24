"""Forecasting routes: trigger a ripening update and read a tree's projection."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from fastapi import APIRouter, Path, Response, status
from harvestgrid_forecasting import TickReport
from harvestgrid_forecasting.ripening import validate_coarse_geohash

from ..deps import (
    ContainerDep,
    CoordinatorDep,
    IdempotencyHeader,
    IdempotentRequest,
    ProjectionReaderDep,
)
from ..problems import not_found, unavailable
from ..schemas import (
    ForecastTriggerRequest,
    ForecastTriggerResponse,
    HarvestWindow,
    ProjectionDetail,
    ProjectionResponse,
    ReviewState,
    TickResultModel,
)
from ..workers import WorkerBackendUnavailable

ROUTE_TRIGGER = "/v1/forecasting/trigger"
ROUTE_PROJECTION = "/v1/forecasting/{tree_id}/projection"

router = APIRouter(prefix="/v1/forecasting", tags=["forecasting"])

TREE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


def _tick_summary(report: TickReport) -> dict[str, Any]:
    """Only IDs and counters leave the worker; no coordinates, addresses, or payloads."""

    payload = report.as_dict()
    return {
        "trees_considered": payload["trees_considered"],
        "trees_due": payload["trees_due"],
        "observations_new": payload["observations_new"],
        "observations_duplicate": payload["observations_duplicate"],
        "projections_updated": payload["projections_updated"],
        "projections_unchanged": payload["projections_unchanged"],
        "adjustments": payload["adjustments"],
        "failed": payload["failed"],
        "skipped_gated": payload["skipped_gated"],
        "skipped_backoff": payload["skipped_backoff"],
        "skipped_cap": payload["skipped_cap"],
        "budget_breaches": payload["budget_breaches"],
        "review_recommended": payload["review_recommended"],
    }


@router.post(
    "/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ForecastTriggerResponse,
    summary="Run the scheduled weather ripening update",
    responses={
        202: {"description": "Tick accepted and completed by the configured worker."},
        400: {"description": "Missing or invalid Idempotency-Key."},
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "Actor lacks the coordinator role."},
        409: {"description": "Idempotency-Key reused with a different request."},
        503: {"description": "No worker backend is available."},
    },
)
async def trigger_forecast_update(
    response: Response,
    container: ContainerDep,
    actor: CoordinatorDep,
    idempotency_key: IdempotencyHeader = None,
    body: ForecastTriggerRequest | None = None,
) -> ForecastTriggerResponse | dict[str, Any]:
    """Run one scheduler tick for the actor's organization.

    The tick is submitted to the configured worker runner. Inline operation completes before
    the response; a broker-backed runner would return the queued job instead. Either way a job
    record is created, and a repeated request with the same ``Idempotency-Key`` reuses it
    instead of polling the weather provider twice.
    """

    payload = body.model_dump(mode="json") if body is not None else {}
    now = container.now()
    idempotent = IdempotentRequest.open(
        actor=actor, route=ROUTE_TRIGGER, header_value=idempotency_key, payload=payload
    )
    replay = idempotent.replay(container.idempotency, now=now)
    if replay is not None:
        replayed_status, replayed_body = replay
        response.status_code = replayed_status
        response.headers["Idempotent-Replay"] = "true"
        return replayed_body

    runner = container.runner_for(actor.organization_id)
    scheduler = container.scheduler_for(actor.organization_id)
    try:
        job = await asyncio.to_thread(
            runner.submit,
            kind="forecast_ripening_tick",
            work=lambda: _tick_summary(scheduler.tick()),
        )
    except WorkerBackendUnavailable as error:
        raise unavailable("worker_backend_unavailable", error.detail) from None

    result = job.as_dict()
    document = ForecastTriggerResponse(
        job_id=result["job_id"],
        kind=result["kind"],
        status=result["status"],
        submitted_at=result["submitted_at"],
        completed_at=result["completed_at"],
        result_sha256=result["result_sha256"],
        provenance=result["provenance"],
        result=TickResultModel.model_validate(job.result) if job.result is not None else None,
    ).model_dump(mode="json")
    idempotent.remember(
        container.idempotency, status=status.HTTP_202_ACCEPTED, body=document, now=now
    )
    return document


@router.get(
    "/{tree_id}/projection",
    response_model=ProjectionResponse,
    summary="Read a tree's current harvest-window projection",
    responses={
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "Actor lacks a permitted role."},
        404: {"description": "Tree is unknown to this organization or has no projection."},
    },
)
async def read_projection(
    container: ContainerDep,
    actor: ProjectionReaderDep,
    tree_id: str = Path(pattern=TREE_ID_PATTERN, max_length=200),
) -> ProjectionResponse:
    """Return the latest projection for a tree in the actor's organization.

    Another organization's tree returns the same 404 as a missing tree, so tree existence never
    leaks across organizations. A tree that has never been polled also returns 404 with a
    distinct code, because "no projection yet" is different from "not yours".
    """

    store = container.scoped_store(actor.organization_id)
    subscription = store.subscription(tree_id)
    if subscription is None:
        raise not_found("tree_not_found", "No such tree in this organization.")
    record = store.latest_projection(tree_id)
    if record is None:
        raise not_found(
            "projection_not_available",
            "This tree has no ripening projection yet; trigger a forecast update first.",
        )

    projection = record.projection
    document = projection.as_dict()
    window_start: date | None = projection.projected_harvest_start
    window_end: date | None = projection.projected_harvest_end
    today = container.now().date()
    return ProjectionResponse(
        tree_id=record.tree_id,
        cultivar_id=record.cultivar_id,
        coarse_geohash=validate_coarse_geohash(projection.coarse_geohash),
        version=record.version,
        updated_at=record.updated_at,
        fingerprint=record.fingerprint,
        provenance=container.provenance,
        review=ReviewState(
            required=projection.human_readiness_review_required,
            experimental=projection.experimental,
            confidence=projection.confidence,
            prior_review_status=projection.prior_review_status,
            reason_codes=list(projection.reason_codes),
        ),
        harvest_window=HarvestWindow(
            available=window_start is not None and window_end is not None,
            open_now=window_start is not None
            and window_end is not None
            and window_start <= today <= window_end,
            start=window_start,
            end=window_end,
        ),
        projection=ProjectionDetail.model_validate(document),
    )
