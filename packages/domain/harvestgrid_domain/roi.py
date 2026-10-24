"""Tree region-of-interest and privacy-mask domain logic.

Implements the deterministic part of ``FR-HG-003``: an owner draws a tree ROI and
exclusion masks (neighbour property, public sidewalk, street, other unrelated areas) on a
real reference frame, a coordinator approves the resulting *view version*, and any view
change invalidates the version and pauses forecasting.

Two properties matter most for privacy (``AC-HG-007``, ``SEC-HG-003``):

* the only regions a downstream model may read are ROI cells that are not inside any
  exclusion mask, so neighbours, sidewalks, and passers-by never reach feature extraction;
* a view is authorized for forecasting only while owner and coordinator approvals apply to
  the current geometry version.

All geometry is expressed in normalized ``0..1`` frame coordinates so view versions stay
resolution independent. Masking is evaluated on cell centres with a fixed, documented
sampling resolution, which makes every decision reproducible.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

MIN_POLYGON_AREA = 1e-6
MIN_ROI_FRAME_FRACTION = 0.01
MIN_EFFECTIVE_ROI_FRACTION = 0.25
MAX_MASK_COVERAGE = 0.90
MAX_POLYGON_POINTS = 128
MAX_MASKS = 32
MAX_FRAME_DIMENSION = 8192
GEOMETRY_SAMPLES = 128
MAX_MASKING_CELLS = 4_194_304

ROI_ERROR_CODES = frozenset(
    {
        "approval_role_not_permitted",
        "coordinator_approval_requires_owner_approval",
        "frame_dimensions_invalid",
        "mask_kind_invalid",
        "max_masks_exceeded",
        "reference_crop_invalid",
        "required_field_missing",
        "roi_area_too_small",
        "roi_must_be_inside_frame",
        "roi_obscured_by_masks",
        "roi_polygon_degenerate",
        "roi_polygon_self_intersecting",
        "roi_polygon_too_many_points",
        "roi_polygon_too_small",
        "stale_view_version",
        "timestamp_requires_timezone",
        "view_already_approved",
        "view_already_rejected",
        "view_change_not_allowed",
        "view_not_draft",
    }
)


class RoiError(ValueError):
    """Deterministic camera-view or geometry failure."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        if code not in ROI_ERROR_CODES:
            raise ValueError(f"Unknown ROI error code: {code}")
        super().__init__(code)
        self.code = code
        self.detail = detail


class RoiAuthorizationError(RoiError, PermissionError):
    """Camera-view action attempted by an actor without that authority."""


class MaskKind(StrEnum):
    """Reviewed suppression categories; no person, face, plate, or vehicle detector exists."""

    NEIGHBOR_PROPERTY = "neighbor_property"
    PUBLIC_SIDEWALK = "public_sidewalk"
    STREET = "street"
    OTHER_UNRELATED = "other_unrelated"


class ViewStatus(StrEnum):
    DRAFT = "draft"
    OWNER_APPROVED = "owner_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        for value in (self.x, self.y):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RoiError("roi_polygon_degenerate", detail="non_numeric_point")
            if not math.isfinite(float(value)):
                raise RoiError("roi_polygon_degenerate", detail="non_finite_point")
        if not (0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0):
            raise RoiError("roi_must_be_inside_frame", detail=f"point={self.x},{self.y}")


@dataclass(frozen=True)
class BoundingBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def area(self) -> float:
        return self.width * self.height


def _cross(origin: Point, a: Point, b: Point) -> float:
    return (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x)


def _on_segment(point: Point, a: Point, b: Point) -> bool:
    tolerance = 1e-12
    if abs(_cross(a, b, point)) > tolerance:
        return False
    return (
        min(a.x, b.x) - tolerance <= point.x <= max(a.x, b.x) + tolerance
        and min(a.y, b.y) - tolerance <= point.y <= max(a.y, b.y) + tolerance
    )


def _segments_intersect(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    d1 = _cross(b1, b2, a1)
    d2 = _cross(b1, b2, a2)
    d3 = _cross(a1, a2, b1)
    d4 = _cross(a1, a2, b2)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return (
        _on_segment(a1, b1, b2)
        or _on_segment(a2, b1, b2)
        or _on_segment(b1, a1, a2)
        or _on_segment(b2, a1, a2)
    )


@dataclass(frozen=True)
class Polygon:
    """Simple (non self-intersecting) polygon in normalized frame coordinates."""

    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) > MAX_POLYGON_POINTS:
            raise RoiError("roi_polygon_too_many_points", detail=f"points={len(self.points)}")
        if len(self.points) < 3:
            raise RoiError("roi_polygon_degenerate", detail=f"points={len(self.points)}")
        if self._self_intersects():
            raise RoiError("roi_polygon_self_intersecting", detail="polygon")
        if self.area < MIN_POLYGON_AREA:
            raise RoiError("roi_polygon_too_small", detail=f"area={self.area:.6f}")

    @classmethod
    def rectangle(cls, min_x: float, min_y: float, max_x: float, max_y: float) -> Polygon:
        return cls(
            (
                Point(min_x, min_y),
                Point(max_x, min_y),
                Point(max_x, max_y),
                Point(min_x, max_y),
            )
        )

    @property
    def area(self) -> float:
        total = 0.0
        count = len(self.points)
        for index in range(count):
            current = self.points[index]
            following = self.points[(index + 1) % count]
            total += current.x * following.y - following.x * current.y
        return abs(total) / 2.0

    @property
    def bbox(self) -> BoundingBox:
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        return BoundingBox(min(xs), min(ys), max(xs), max(ys))

    def contains(self, x: float, y: float) -> bool:
        """Ray-casting point test; a point exactly on an edge counts as inside."""

        inside = False
        count = len(self.points)
        for index in range(count):
            first = self.points[index]
            second = self.points[(index + 1) % count]
            if _on_segment(Point(x, y), first, second):
                return True
            if (first.y > y) != (second.y > y):
                intersection_x = first.x + (y - first.y) * (second.x - first.x) / (
                    second.y - first.y
                )
                if x < intersection_x:
                    inside = not inside
        return inside

    def _self_intersects(self) -> bool:
        count = len(self.points)
        for first in range(count):
            a1 = self.points[first]
            a2 = self.points[(first + 1) % count]
            for second in range(first + 1, count):
                # Adjacent edges always share a vertex; that is not a self-intersection.
                if (second + 1) % count == first or second == (first + 1) % count:
                    continue
                b1 = self.points[second]
                b2 = self.points[(second + 1) % count]
                if _segments_intersect(a1, a2, b1, b2):
                    return True
        return False


def frame_coverage(polygon: Polygon) -> float:
    """Deterministic estimate of the share of the frame covered by ``polygon``."""

    return _sample_union((polygon,), predicate=None) / (GEOMETRY_SAMPLES * GEOMETRY_SAMPLES)


def mask_coverage_of_roi(roi: Polygon, masks: Iterable[MaskRegion]) -> float:
    """Deterministic estimate of the share of ``roi`` covered by the union of ``masks``."""

    polygons = tuple(mask.polygon for mask in masks)
    if not polygons:
        return 0.0
    return _sample_union(polygons, predicate=roi) / max(1, _sample_union((roi,), None))


def _sample_union(polygons: tuple[Polygon, ...], predicate: Polygon | None) -> int:
    hits = 0
    for row in range(GEOMETRY_SAMPLES):
        y = (row + 0.5) / GEOMETRY_SAMPLES
        for column in range(GEOMETRY_SAMPLES):
            x = (column + 0.5) / GEOMETRY_SAMPLES
            if predicate is not None and not predicate.contains(x, y):
                continue
            if any(polygon.contains(x, y) for polygon in polygons):
                hits += 1
    return hits


@dataclass(frozen=True)
class MaskRegion:
    kind: MaskKind
    polygon: Polygon
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MaskKind):
            raise RoiError("mask_kind_invalid", detail=str(self.kind))
        if len(self.note) > 200:
            raise RoiError("required_field_missing", detail="mask_note")


@dataclass(frozen=True)
class CameraViewAuditEvent:
    version: int
    actor_id: str
    action: str
    at: datetime
    reason: str = ""


@dataclass(frozen=True)
class CameraViewSnapshot:
    view_id: str
    tree_id: str
    device_id: str
    version: int
    roi: Polygon
    masks: tuple[MaskRegion, ...]
    reference_crop_sha256: str
    frame_width: int
    frame_height: int
    status: ViewStatus
    created_by: str
    created_at: datetime
    owner_approved_by: str | None
    owner_approved_at: datetime | None
    coordinator_approved_by: str | None
    coordinator_approved_at: datetime | None
    rejection_reason: str | None
    invalidation_reason: str | None
    audit: tuple[CameraViewAuditEvent, ...] = ()

    @property
    def forecasting_active(self) -> bool:
        """Forecasting (and therefore collection for features) needs an approved version."""

        return self.status is ViewStatus.APPROVED

    @property
    def mask_coverage_of_roi(self) -> float:
        return mask_coverage_of_roi(self.roi, self.masks)

    @property
    def effective_roi_fraction(self) -> float:
        return max(0.0, 1.0 - self.mask_coverage_of_roi)

    @property
    def privacy_mask_required(self) -> bool:
        """A view with no exclusion mask is allowed but flagged for privacy review."""

        return not self.masks


@dataclass(frozen=True)
class CameraViewRequest:
    view_id: str
    tree_id: str
    device_id: str
    owner_id: str
    coordinator_id: str
    roi: Polygon
    masks: tuple[MaskRegion, ...]
    reference_crop_sha256: str
    frame_width: int
    frame_height: int
    at: datetime


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RoiError("timestamp_requires_timezone", detail=field_name)
    return value.astimezone(UTC)


def _required(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise RoiError("required_field_missing", detail=field_name)
    return text


def _valid_reference_digest(value: str) -> str:
    digest = value.strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RoiError("reference_crop_invalid", detail="sha256")
    return digest


def _validate_view_inputs(
    *,
    roi: Polygon,
    masks: tuple[MaskRegion, ...],
    reference_crop_sha256: str,
    frame_width: int,
    frame_height: int,
) -> None:
    if len(masks) > MAX_MASKS:
        raise RoiError("max_masks_exceeded", detail=f"masks={len(masks)}")
    _valid_reference_digest(reference_crop_sha256)
    if (
        frame_width < 16
        or frame_height < 16
        or frame_width > MAX_FRAME_DIMENSION
        or frame_height > MAX_FRAME_DIMENSION
    ):
        raise RoiError("frame_dimensions_invalid", detail=f"{frame_width}x{frame_height}")
    if frame_coverage(roi) < MIN_ROI_FRAME_FRACTION:
        raise RoiError("roi_area_too_small", detail=f"area={roi.area:.6f}")
    coverage = mask_coverage_of_roi(roi, masks)
    if coverage > MAX_MASK_COVERAGE or 1.0 - coverage < MIN_EFFECTIVE_ROI_FRACTION:
        raise RoiError("roi_obscured_by_masks", detail=f"coverage={coverage:.3f}")


def view_fingerprint(roi: Polygon, masks: Iterable[MaskRegion]) -> str:
    """Stable geometry hash; identical geometry never bumps a view version."""

    payload = {
        "roi": [[round(point.x, 6), round(point.y, 6)] for point in roi.points],
        "masks": sorted(
            (
                {
                    "kind": mask.kind.value,
                    "points": [
                        [round(point.x, 6), round(point.y, 6)] for point in mask.polygon.points
                    ],
                }
                for mask in masks
            ),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def allowed_cells(
    view: CameraViewSnapshot, *, width: int, height: int
) -> tuple[tuple[bool, ...], ...]:
    """Raster of frame cells a model may read: ROI minus every exclusion mask.

    Cell centres are tested, so the result is deterministic for a given view and grid.
    """

    if width < 1 or height < 1 or width * height > MAX_MASKING_CELLS:
        raise RoiError("frame_dimensions_invalid", detail=f"{width}x{height}")
    rows: list[tuple[bool, ...]] = []
    for row in range(height):
        y = (row + 0.5) / height
        cells: list[bool] = []
        for column in range(width):
            x = (column + 0.5) / width
            inside_roi = view.roi.contains(x, y)
            excluded = inside_roi and any(mask.polygon.contains(x, y) for mask in view.masks)
            cells.append(inside_roi and not excluded)
        rows.append(tuple(cells))
    return tuple(rows)


def crop_box(view: CameraViewSnapshot) -> BoundingBox:
    """Normalized bounding box of the ROI, used to crop before any model call."""

    return view.roi.bbox


class CameraViewWorkflow:
    """Owner-drawn view versions with coordinator approval and pause-on-change semantics."""

    def __init__(self, *, coordinator_id: str, snapshot: CameraViewSnapshot) -> None:
        self._coordinator_id = _required(coordinator_id, "coordinator_id")
        self._snapshot = snapshot
        self._fingerprint = view_fingerprint(snapshot.roi, snapshot.masks)

    @classmethod
    def create(cls, request: CameraViewRequest) -> CameraViewWorkflow:
        view_id = _required(request.view_id, "view_id")
        tree_id = _required(request.tree_id, "tree_id")
        device_id = _required(request.device_id, "device_id")
        owner_id = _required(request.owner_id, "owner_id")
        coordinator_id = _required(request.coordinator_id, "coordinator_id")
        created_at = _aware(request.at, "view.at")
        _validate_view_inputs(
            roi=request.roi,
            masks=request.masks,
            reference_crop_sha256=request.reference_crop_sha256,
            frame_width=request.frame_width,
            frame_height=request.frame_height,
        )
        snapshot = CameraViewSnapshot(
            view_id=view_id,
            tree_id=tree_id,
            device_id=device_id,
            version=0,
            roi=request.roi,
            masks=request.masks,
            reference_crop_sha256=_valid_reference_digest(request.reference_crop_sha256),
            frame_width=request.frame_width,
            frame_height=request.frame_height,
            status=ViewStatus.DRAFT,
            created_by=owner_id,
            created_at=created_at,
            owner_approved_by=None,
            owner_approved_at=None,
            coordinator_approved_by=None,
            coordinator_approved_at=None,
            rejection_reason=None,
            invalidation_reason=None,
            audit=(CameraViewAuditEvent(0, owner_id, "view_drafted", created_at),),
        )
        return cls(coordinator_id=coordinator_id, snapshot=snapshot)

    @property
    def snapshot(self) -> CameraViewSnapshot:
        return self._snapshot

    @property
    def coordinator_id(self) -> str:
        return self._coordinator_id

    def _require_version(self, expected_version: int) -> None:
        if expected_version != self._snapshot.version:
            raise RoiError("stale_view_version", detail=str(expected_version))

    def _require_owner(self, actor_id: str) -> None:
        if actor_id != self._snapshot.created_by:
            raise RoiAuthorizationError("approval_role_not_permitted", detail="owner")

    def _require_coordinator(self, actor_id: str) -> None:
        if actor_id != self._coordinator_id or actor_id == self._snapshot.created_by:
            raise RoiAuthorizationError("approval_role_not_permitted", detail="coordinator")

    def _record(
        self,
        updated: CameraViewSnapshot,
        *,
        actor_id: str,
        action: str,
        at: datetime,
        reason: str = "",
        fingerprint: str | None = None,
    ) -> CameraViewSnapshot:
        timestamp = _aware(at, "view.at")
        version = self._snapshot.version + 1
        event = CameraViewAuditEvent(version, actor_id, action, timestamp, reason)
        self._snapshot = replace(updated, version=version, audit=self._snapshot.audit + (event,))
        if fingerprint is not None:
            self._fingerprint = fingerprint
        return self._snapshot

    def owner_approve(
        self, *, actor_id: str, expected_version: int, at: datetime
    ) -> CameraViewSnapshot:
        self._require_owner(actor_id)
        # Owner approval is committed for this version; replays never churn versions.
        if self._snapshot.status in {ViewStatus.OWNER_APPROVED, ViewStatus.APPROVED}:
            return self._snapshot
        self._require_version(expected_version)
        if self._snapshot.status is not ViewStatus.DRAFT:
            raise RoiError("view_change_not_allowed", detail=self._snapshot.status.value)
        return self._record(
            replace(
                self._snapshot,
                status=ViewStatus.OWNER_APPROVED,
                owner_approved_by=actor_id,
                owner_approved_at=_aware(at, "view.at"),
            ),
            actor_id=actor_id,
            action="owner_approved_view",
            at=at,
        )

    def approve(self, *, actor_id: str, expected_version: int, at: datetime) -> CameraViewSnapshot:
        self._require_coordinator(actor_id)
        if self._snapshot.status is ViewStatus.APPROVED:
            return self._snapshot
        if self._snapshot.status is ViewStatus.REJECTED:
            raise RoiError("view_already_rejected", detail=self._snapshot.view_id)
        self._require_version(expected_version)
        if (
            self._snapshot.status is not ViewStatus.OWNER_APPROVED
            or self._snapshot.owner_approved_by is None
        ):
            raise RoiError("coordinator_approval_requires_owner_approval", detail="owner")
        return self._record(
            replace(
                self._snapshot,
                status=ViewStatus.APPROVED,
                coordinator_approved_by=actor_id,
                coordinator_approved_at=_aware(at, "view.at"),
            ),
            actor_id=actor_id,
            action="coordinator_approved_view",
            at=at,
        )

    def reject(
        self, *, actor_id: str, reason: str, expected_version: int, at: datetime
    ) -> CameraViewSnapshot:
        self._require_coordinator(actor_id)
        if self._snapshot.status is ViewStatus.REJECTED:
            return self._snapshot
        self._require_version(expected_version)
        if self._snapshot.status is ViewStatus.APPROVED:
            raise RoiError("view_already_approved", detail=self._snapshot.view_id)
        return self._record(
            replace(
                self._snapshot,
                status=ViewStatus.REJECTED,
                rejection_reason=_required(reason, "reason"),
            ),
            actor_id=actor_id,
            action="coordinator_rejected_view",
            at=at,
            reason=reason,
        )

    def change_view(
        self,
        *,
        actor_id: str,
        roi: Polygon,
        masks: tuple[MaskRegion, ...],
        reference_crop_sha256: str,
        frame_width: int,
        frame_height: int,
        expected_version: int,
        at: datetime,
    ) -> CameraViewSnapshot:
        """Apply new geometry, invalidate approvals, and pause forecasting.

        Identical geometry and reference are a no-op so retries never churn versions.
        """

        self._require_owner(actor_id)
        self._require_version(expected_version)
        fingerprint = view_fingerprint(roi, masks)
        digest = _valid_reference_digest(reference_crop_sha256)
        if fingerprint == self._fingerprint and digest == self._snapshot.reference_crop_sha256:
            return self._snapshot
        _validate_view_inputs(
            roi=roi,
            masks=masks,
            reference_crop_sha256=reference_crop_sha256,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        return self._record(
            replace(
                self._snapshot,
                roi=roi,
                masks=masks,
                reference_crop_sha256=digest,
                frame_width=frame_width,
                frame_height=frame_height,
                status=ViewStatus.DRAFT,
                owner_approved_by=None,
                owner_approved_at=None,
                coordinator_approved_by=None,
                coordinator_approved_at=None,
                rejection_reason=None,
                invalidation_reason="view_geometry_changed",
            ),
            actor_id=actor_id,
            action="view_geometry_changed",
            at=at,
            reason="view_geometry_changed",
            fingerprint=fingerprint,
        )

    def invalidate(
        self, *, actor_id: str, reason: str, expected_version: int, at: datetime
    ) -> CameraViewSnapshot:
        """Pause forecasting, e.g. after camera movement or a revoked consent."""

        if actor_id not in {self._snapshot.created_by, self._coordinator_id}:
            raise RoiAuthorizationError("approval_role_not_permitted", detail="invalidate")
        if self._snapshot.status is ViewStatus.INVALIDATED:
            return self._snapshot
        self._require_version(expected_version)
        return self._record(
            replace(
                self._snapshot,
                status=ViewStatus.INVALIDATED,
                invalidation_reason=_required(reason, "reason"),
            ),
            actor_id=actor_id,
            action="view_invalidated",
            at=at,
            reason=reason,
        )


def view_document(view: CameraViewSnapshot) -> dict[str, object]:
    """Non-sensitive projection of a view version for API/storage adapters."""

    return {
        "contract_version": "camera-view-v1",
        "view_id": view.view_id,
        "tree_id": view.tree_id,
        "device_id": view.device_id,
        "version": view.version,
        "status": view.status.value,
        "roi": [[point.x, point.y] for point in view.roi.points],
        "masks": [
            {
                "kind": mask.kind.value,
                "polygon": [[point.x, point.y] for point in mask.polygon.points],
            }
            for mask in view.masks
        ],
        "reference_crop_sha256": view.reference_crop_sha256,
        "frame": {"width": view.frame_width, "height": view.frame_height},
        "effective_roi_fraction": round(view.effective_roi_fraction, 4),
        "forecasting_active": view.forecasting_active,
        "privacy_mask_required": view.privacy_mask_required,
        "person_or_vehicle_detection_enabled": False,
        "exact_address_included": False,
        "raw_media_included": False,
    }
