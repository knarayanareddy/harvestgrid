"""Request and response models for the forecasting and notification routes.

Requests are strict (``extra="forbid"``) so a typo is a 422 rather than a silently ignored
field. Responses carry ``provenance`` so a fixture record can never be mistaken for a live one.
Prohibited content is rejected at the boundary: no route accepts an address, access note, raw
media reference, or phone number.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GleanStateLiteral = Literal["invited", "confirmed", "cancelled"]
AlertKindLiteral = Literal["invitation", "reminder", "cancellation"]
OutcomeStatusLiteral = Literal["sent", "skipped", "deferred", "failed"]
ConfidenceLiteral = Literal["low", "medium"]
StageLiteral = Literal[
    "dormant",
    "bud_break",
    "bloom",
    "fruit_set",
    "pit_hardening",
    "veraison",
    "harvest_ripe",
    "past_ripe",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- forecasting


class ForecastTriggerRequest(StrictModel):
    """A trigger runs the organization-wide weather tick.

    There are deliberately no options: the tick already respects each tree's poll interval,
    consent state, and failure backoff, so a caller cannot force a tree to be reprocessed.
    """


class TreeFailureModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    tree_id: str
    detail: str


class AdjustmentModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    tree_id: str
    cultivar_id: str
    previous_stage: str
    current_stage: str
    direction: str
    material: bool


class TickResultModel(BaseModel):
    """Summary of one scheduler tick, minus anything location- or person-related."""

    model_config = ConfigDict(extra="allow")

    trees_considered: int
    trees_due: int
    observations_new: int
    observations_duplicate: int
    projections_updated: int
    projections_unchanged: int
    adjustments: list[AdjustmentModel] = Field(default_factory=list)
    failed: list[TreeFailureModel] = Field(default_factory=list)
    skipped_gated: list[str] = Field(default_factory=list)
    skipped_backoff: list[str] = Field(default_factory=list)
    skipped_cap: list[str] = Field(default_factory=list)
    budget_breaches: list[str] = Field(default_factory=list)
    review_recommended: list[str] = Field(default_factory=list)


class ForecastTriggerResponse(BaseModel):
    job_id: str
    kind: str
    status: str
    submitted_at: datetime
    completed_at: datetime | None
    result_sha256: str | None
    provenance: str
    result: TickResultModel | None


class ReviewState(BaseModel):
    required: bool
    experimental: bool
    confidence: ConfidenceLiteral
    prior_review_status: str
    reason_codes: list[str]


class HarvestWindow(BaseModel):
    available: bool
    open_now: bool
    start: date | None
    end: date | None


class ProjectionDetail(BaseModel):
    """Ripening projection passthrough; ``extra="allow"`` keeps every contract field."""

    model_config = ConfigDict(extra="allow")

    cultivar_id: str
    stage: StageLiteral
    accumulated_gdd: float
    stage_progress: float
    daily_gdd_rate: float | None
    days_observed: int
    missing_days: int
    experimental: bool
    confidence: ConfidenceLiteral
    human_readiness_review_required: bool
    internal_ripeness_inferred: bool
    food_safety_inferred: bool


class ProjectionResponse(BaseModel):
    tree_id: str
    cultivar_id: str
    coarse_geohash: str
    version: int
    updated_at: datetime
    fingerprint: str
    provenance: str
    review: ReviewState
    harvest_window: HarvestWindow
    projection: ProjectionDetail


# --------------------------------------------------------------------------- notifications


class NotificationDispatchRequest(StrictModel):
    """Coordinator-approved dispatch. Volunteer phones are resolved server-side."""

    glean_id: str = Field(min_length=1, max_length=200)
    glean_state: GleanStateLiteral
    owner_approval_current: bool
    approved_at: datetime
    kind: AlertKindLiteral = "invitation"
    crop_label: str = Field(min_length=1, max_length=64)
    coarse_area_label: str = Field(min_length=1, max_length=120)
    window_start: datetime
    window_end: datetime
    access_constraints: list[str] = Field(default_factory=list, max_length=8)
    safety_notes: list[str] = Field(default_factory=list, max_length=8)
    volunteer_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("approved_at", "window_start", "window_end")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must include a timezone offset")
        return value

    @field_validator("access_constraints", "safety_notes")
    @classmethod
    def _bounded_notes(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item.strip():
                raise ValueError("entries must not be blank")
            if len(item) > 80:
                raise ValueError("entries must be at most 80 characters")
        return value

    @field_validator("volunteer_ids")
    @classmethod
    def _unique_volunteers(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("volunteer_ids must not repeat")
        return value

    @model_validator(mode="after")
    def _ordered_window(self) -> NotificationDispatchRequest:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class DispatchOutcomeModel(BaseModel):
    volunteer_id: str
    status: OutcomeStatusLiteral
    reason: str
    message_id: str | None = None
    segments: int = 0


class NoticeDigest(BaseModel):
    """Digest of a rendered notice; the notice text itself never leaves the service."""

    volunteer_id: str
    status: OutcomeStatusLiteral
    body_sha256: str | None = None


class NotificationDispatchResponse(BaseModel):
    glean_id: str
    kind: AlertKindLiteral
    template_version: str
    at: datetime
    provenance: str
    job_id: str
    request_sha256: str
    result_sha256: str
    sent: int
    skipped: int
    deferred: int
    failed: int
    outcomes: list[DispatchOutcomeModel]
    notices: list[NoticeDigest]


class HealthResponse(BaseModel):
    status: str
    provenance: str
    demo_mode: bool
    worker_backend: str


class ProblemField(BaseModel):
    path: str
    code: str


class ProblemDocument(BaseModel):
    """RFC 9457 shape, documented in OpenAPI for client code generation."""

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    instance: str
    request_id: str
    fields: list[ProblemField] | None = None


def as_problem_document(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if value is not None}
