"""Notification dispatch route: coordinator-approved volunteer pickup alerts."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Response, status
from harvestgrid_notifications import (
    AlertKind,
    DispatchAuthorization,
    NotificationError,
    PickupAlert,
    TemplateError,
)

from ..deps import ContainerDep, CoordinatorDep, IdempotencyHeader, IdempotentRequest
from ..problems import FieldProblem, forbidden, unprocessable
from ..schemas import (
    DispatchOutcomeModel,
    NoticeDigest,
    NotificationDispatchRequest,
    NotificationDispatchResponse,
)
from ..workers import completed_job, digest_payload

ROUTE_DISPATCH = "/v1/notifications/dispatch"

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


def _outcomes(document: dict[str, Any]) -> list[DispatchOutcomeModel]:
    return [DispatchOutcomeModel.model_validate(item) for item in document["outcomes"]]


@router.post(
    "/dispatch",
    response_model=NotificationDispatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Dispatch coordinator-approved volunteer pickup notices",
    responses={
        200: {"description": "Dispatch evaluated; per-recipient outcomes returned."},
        400: {"description": "Missing or invalid Idempotency-Key."},
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "Actor lacks the coordinator role."},
        409: {"description": "Idempotency-Key reused with a different request."},
        422: {"description": "The request or a rendered notice was rejected."},
    },
)
async def dispatch_notices(
    response: Response,
    container: ContainerDep,
    actor: CoordinatorDep,
    request_body: NotificationDispatchRequest,
    idempotency_key: IdempotencyHeader = None,
) -> NotificationDispatchResponse | dict[str, Any]:
    """Render and send pickup notices for volunteers who opted in to SMS.

    The coordinator's own identity is used as ``approved_by``, so a caller cannot claim that
    someone else approved the dispatch. Volunteer phone numbers are resolved from the
    directory rather than accepted from the request, and the glean's restricted details are
    used only as leak markers: they are compared against the rendered notice and never
    returned.
    """

    payload = request_body.model_dump(mode="json")
    now = container.now()
    idempotent = IdempotentRequest.open(
        actor=actor, route=ROUTE_DISPATCH, header_value=idempotency_key, payload=payload
    )
    replay = idempotent.replay(container.idempotency, now=now)
    if replay is not None:
        replayed_status, replayed_body = replay
        response.status_code = replayed_status
        response.headers["Idempotent-Replay"] = "true"
        return replayed_body

    profiles = []
    missing_fields: list[FieldProblem] = []
    for index, volunteer_id in enumerate(request_body.volunteer_ids):
        profile = container.volunteer(volunteer_id, actor.organization_id)
        if profile is None:
            missing_fields.append(FieldProblem(path=f"/volunteer_ids/{index}", code="not_found"))
        else:
            profiles.append(profile)
    if missing_fields:
        raise unprocessable(
            "volunteer_not_found",
            "One or more volunteers are not in this organization's directory.",
            tuple(missing_fields),
        )

    restricted = container.restricted_for(request_body.glean_id, actor.organization_id)
    markers = restricted.markers() if restricted is not None else ()

    alert = PickupAlert(
        glean_id=request_body.glean_id,
        crop_label=request_body.crop_label,
        coarse_area_label=request_body.coarse_area_label,
        window_start=request_body.window_start,
        window_end=request_body.window_end,
        access_constraints=tuple(request_body.access_constraints),
        safety_notes=tuple(request_body.safety_notes),
        kind=AlertKind(request_body.kind),
    )
    authorization = DispatchAuthorization(
        glean_id=request_body.glean_id,
        coordinator_id=actor.actor_id,
        approved_by=actor.actor_id,
        approved_at=request_body.approved_at,
        owner_approval_current=request_body.owner_approval_current,
        glean_state=request_body.glean_state,
    )

    service = container.dispatch_service(actor.organization_id)
    ledger_before = len(container.ledger.attempts())
    try:
        report = await asyncio.to_thread(
            service.dispatch,
            alert=alert,
            recipients=tuple(profile.contact for profile in profiles),
            authorization=authorization,
            restricted_markers=markers,
            now=now,
        )
    except NotificationError as error:
        if error.code == "dispatch_not_authorized":
            raise forbidden(
                "dispatch_not_authorized",
                "Coordinator approval, current owner approval, and a dispatchable glean "
                "state are required before any notice is sent.",
            ) from None
        raise unprocessable("notification_rejected", str(error)) from None
    except TemplateError as error:
        raise unprocessable(
            "notice_rejected", f"The notice could not be rendered safely ({error.code})."
        ) from None

    new_attempts = container.ledger.attempts()[ledger_before:]
    digests = {
        attempt.volunteer_id: attempt.body_sha256
        for attempt in new_attempts
        if attempt.body_sha256 is not None
    }
    document = report.as_dict()
    notices = [
        NoticeDigest(
            volunteer_id=outcome.volunteer_id,
            status=outcome.status,
            body_sha256=digests.get(outcome.volunteer_id),
        )
        for outcome in report.outcomes
    ]

    job = completed_job(
        kind="notification_dispatch",
        result=document,
        clock=container.clock,
        provenance=container.provenance,
        started_at=now,
    )
    container.jobs.add(job)

    result = NotificationDispatchResponse(
        glean_id=report.glean_id,
        kind=request_body.kind,
        template_version=report.template_version,
        at=report.at,
        provenance=container.provenance,
        job_id=job.job_id,
        request_sha256=idempotent.request_sha256,
        result_sha256=digest_payload(document),
        sent=report.sent,
        skipped=report.skipped,
        deferred=report.deferred,
        failed=report.failed,
        outcomes=_outcomes(document),
        notices=notices,
    ).model_dump(mode="json")
    idempotent.remember(container.idempotency, status=status.HTTP_200_OK, body=result, now=now)
    return result


__all__ = ["ROUTE_DISPATCH", "dispatch_notices", "router"]
