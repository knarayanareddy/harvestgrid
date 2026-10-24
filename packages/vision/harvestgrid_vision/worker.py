"""Feature-extraction worker (``FR-HG-006``).

Pipeline per observation, all deterministic:

1. authorize the view (approved ROI/mask version) and build the allowed-cell raster;
2. screen frame quality (darkness, contrast, ROI coverage, camera registration);
3. run the detector only on allowed cells, so masked neighbours never contribute;
4. compute count/area/colour/occlusion features with model, data, and feature versions;
5. reject unsuitable views with explicit reason codes instead of emitting a confident
   forecast input, and hash the observation for provenance.

No internal-ripeness, disease, or food-safety attribute is produced: colour features are
descriptive only (``SEC-HG-005``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from harvestgrid_domain.roi import CameraViewSnapshot, RoiError, allowed_cells

from .detector import (
    Detection,
    DetectorError,
    Frame,
    FruitClusterDetector,
    HeuristicClusterDetector,
)
from .features_v1 import FEATURE_VERSION, SCHEMA_VERSION, to_row

OBSERVATION_SCHEMA_VERSION = 1
MIN_QUALITY_SCORE = 0.35
MIN_ROI_COVERAGE = 0.15
MIN_REGISTRATION_SCORE = 0.60
MIN_LUMINANCE = 0.05
MIN_CONTRAST = 0.06
MAX_GRID_CELLS = 65_536
DEFAULT_GRID = (32, 24)


class ExtractionError(ValueError):
    """Invalid extraction request (never a silent fallback)."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        codes = {
            "view_not_approved",
            "grid_dimensions_invalid",
            "provenance_invalid",
            "version_missing",
        }
        if code not in codes:
            raise ValueError(f"Unknown extraction error code: {code}")
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ExtractionRequest:
    view: CameraViewSnapshot
    frame_id: str
    width: int
    height: int
    luminance: tuple[float, ...]
    saturation: tuple[float, ...]
    captured_at: datetime
    extracted_at: datetime
    detected_at_grid: tuple[int, int] = DEFAULT_GRID
    registration_score: float | None = None
    area_change_ratio: float | None = None
    provenance: str = "fixture"


@dataclass(frozen=True)
class Observation:
    observation_id: str
    tree_id: str
    view_id: str
    view_version: int
    frame_id: str
    captured_at: datetime
    extracted_at: datetime
    feature_version: str
    schema_version: int
    detector_model_version: str
    detector_data_version: str
    detector_model_kind: str
    provenance: str
    status: str
    rejection_reasons: tuple[str, ...]
    quality_flags: tuple[str, ...]
    visible_fruit_count: int
    count_lower: int
    count_upper: int
    detection_confidence_mean: float | None
    area_p10: float | None
    area_p50: float | None
    area_p90: float | None
    area_change_ratio: float | None
    colour_saturation_mean: float | None
    colour_value_mean: float | None
    occlusion_fraction: float
    roi_coverage: float
    registration_score: float | None
    quality_score: float
    allowed_cell_count: int
    masked_cell_count: int
    frame_sha256: str
    observation_sha256: str
    internal_ripeness_inferred: bool = False
    food_safety_inferred: bool = False
    disease_inferred: bool = False

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def human_review_required(self) -> bool:
        """Low-quality observations always route to review; never a confident claim."""

        return not self.accepted or self.quality_score < 0.75

    def feature_row(self) -> tuple[float, ...] | None:
        """``features_v1`` row, or ``None`` for a rejected observation."""

        if not self.accepted:
            return None
        return to_row(self.feature_dict())

    def feature_dict(self) -> dict[str, float | None]:
        return {
            "visible_fruit_count": float(self.visible_fruit_count),
            "count_lower": float(self.count_lower),
            "count_upper": float(self.count_upper),
            "detection_confidence_mean": self.detection_confidence_mean,
            "detection_area_p10": self.area_p10,
            "detection_area_p50": self.area_p50,
            "detection_area_p90": self.area_p90,
            "area_change_ratio": self.area_change_ratio,
            "colour_saturation_mean": self.colour_saturation_mean,
            "colour_value_mean": self.colour_value_mean,
            "occlusion_fraction": self.occlusion_fraction,
            "quality_score": self.quality_score,
            "roi_coverage": self.roi_coverage,
            "registration_score": self.registration_score,
            "day_of_year": float(self.captured_at.timetuple().tm_yday),
            "observation_hour_utc": float(self.captured_at.astimezone(UTC).hour),
        }

    def provenance_document(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "observation_sha256": self.observation_sha256,
            "frame_sha256": self.frame_sha256,
            "feature_version": self.feature_version,
            "schema_version": self.schema_version,
            "detector_model_version": self.detector_model_version,
            "detector_data_version": self.detector_data_version,
            "detector_model_kind": self.detector_model_kind,
            "view_id": self.view_id,
            "view_version": self.view_version,
            "provenance": self.provenance,
            "status": self.status,
            "rejection_reasons": list(self.rejection_reasons),
            "quality_flags": list(self.quality_flags),
            "visible_fruit_count": self.visible_fruit_count,
            "count_interval": [self.count_lower, self.count_upper],
            "internal_ripeness_inferred": False,
            "food_safety_inferred": False,
            "disease_inferred": False,
            "raw_media_included": False,
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)


def _observation_sha256(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExtractionError("version_missing", detail=field_name)
    return value.astimezone(UTC)


@dataclass
class FeatureExtractionWorker:
    """Deterministic observation builder over an approved camera view."""

    detector: FruitClusterDetector = field(default_factory=lambda: HeuristicClusterDetector())

    def extract(self, request: ExtractionRequest) -> Observation:
        view = request.view
        if not view.forecasting_active:
            raise ExtractionError("view_not_approved", detail=f"{view.view_id}:{view.status.value}")
        if not request.provenance.strip():
            raise ExtractionError("provenance_invalid")
        if not self.detector.model_version.strip() or not self.detector.data_version.strip():
            raise ExtractionError("version_missing", detail="detector_versions")
        grid_width, grid_height = request.detected_at_grid
        if grid_width < 4 or grid_height < 4 or grid_width * grid_height > MAX_GRID_CELLS:
            raise ExtractionError("grid_dimensions_invalid", detail=f"{grid_width}x{grid_height}")
        captured_at = _aware(request.captured_at, "captured_at")
        extracted_at = _aware(request.extracted_at, "extracted_at")
        if extracted_at < captured_at:
            raise ExtractionError("version_missing", detail="extracted_before_capture")

        try:
            allowed = allowed_cells(view, width=grid_width, height=grid_height)
        except RoiError as error:
            raise ExtractionError("view_not_approved", detail=error.code) from None

        frame = _frame(request, grid_width, grid_height)
        allowed_total = sum(1 for row in allowed for cell in row if cell)
        frame_total = grid_width * grid_height
        roi_coverage = round(allowed_total / frame_total, 6)

        luminance_in_frame = [
            frame.luminance[index]
            for index in range(len(frame.luminance))
            if allowed[index // grid_width][index % grid_width]
        ]
        allowed_saturation = [
            frame.saturation[index]
            for index in range(len(frame.saturation))
            if allowed[index // grid_width][index % grid_width]
        ]
        quality_flags: list[str] = []
        rejection_reasons: list[str] = []
        if not luminance_in_frame:
            luminance_mean = 0.0
            luminance_spread = 0.0
        else:
            luminance_mean = sum(luminance_in_frame) / len(luminance_in_frame)
            luminance_spread = max(luminance_in_frame) - min(luminance_in_frame)
        if luminance_mean < MIN_LUMINANCE:
            quality_flags.append("too_dark")
            rejection_reasons.append("too_dark")
        if luminance_spread < MIN_CONTRAST:
            quality_flags.append("low_contrast")
            rejection_reasons.append("low_contrast")
        if roi_coverage < MIN_ROI_COVERAGE:
            quality_flags.append("roi_coverage_low")
            rejection_reasons.append("roi_coverage_low")
        registration = request.registration_score
        if registration is None:
            quality_flags.append("registration_unverified")
        elif registration < MIN_REGISTRATION_SCORE:
            quality_flags.append("camera_moved")
            rejection_reasons.append("camera_moved")
        if view.privacy_mask_required:
            quality_flags.append("no_privacy_mask")

        detections: tuple[Detection, ...] = ()
        if not rejection_reasons:
            try:
                detections = self.detector.detect(frame, allowed)
            except DetectorError as error:
                raise ExtractionError("view_not_approved", detail=str(error)) from None

        # Detections are re-checked against the mask: a detector can never widen access.
        permitted = [
            detection
            for detection in detections
            if _inside_allowed(detection, allowed, grid_width, grid_height)
        ]
        blocked = len(detections) - len(permitted)
        if blocked:
            quality_flags.append("detection_outside_allowed_roi")

        areas = [detection.area_fraction for detection in permitted]
        confidences = [detection.confidence for detection in permitted]
        area_p50 = _percentile(areas, 0.50) if areas else None
        lower = sum(1 for value in confidences if value >= 0.5)
        upper = len(permitted)
        count = lower
        if areas:
            # Wide detections are more likely than thin ones to merge fruit, so the
            # interval leans on the high-confidence subset rather than inventing a count.
            count = max(0, round(len(permitted) * (sum(confidences) / max(1, len(confidences)))))
            lower = min(count, lower)
        confidence_mean = round(sum(confidences) / len(confidences), 6) if confidences else None
        saturation_mean = (
            round(sum(allowed_saturation) / len(allowed_saturation), 6)
            if allowed_saturation and detections
            else None
        )
        value_mean = (
            round(sum(luminance_in_frame) / len(luminance_in_frame), 6)
            if luminance_in_frame and detections
            else None
        )
        masked_cell_count = sum(1 for row in allowed for cell in row if not cell)
        occlusion_fraction = round(min(1.0, 1.0 - roi_coverage) if roi_coverage < 1.0 else 0.0, 6)
        quality_score = _quality_score(
            luminance_spread=luminance_spread,
            roi_coverage=roi_coverage,
            registration=registration,
            confidence_mean=confidence_mean,
            flags=quality_flags,
        )
        if quality_score < MIN_QUALITY_SCORE and not rejection_reasons:
            rejection_reasons.append("quality_below_threshold")
        status = "rejected" if rejection_reasons else "accepted"
        if not permitted and status == "accepted":
            quality_flags.append("no_visible_fruit_detected")

        payload: dict[str, object] = {
            "frame_sha256": frame.frame_sha256,
            "view": [view.view_id, view.version],
            "detector": [self.detector.model_version, self.detector.data_version],
            "feature_version": FEATURE_VERSION,
            "count": count,
            "areas": areas,
            "status": status,
            "flags": quality_flags,
        }
        digest = _observation_sha256(payload)
        return Observation(
            observation_id=f"obs-{digest[:20]}",
            tree_id=view.tree_id,
            view_id=view.view_id,
            view_version=view.version,
            frame_id=request.frame_id,
            captured_at=captured_at,
            extracted_at=extracted_at,
            feature_version=FEATURE_VERSION,
            schema_version=SCHEMA_VERSION,
            detector_model_version=self.detector.model_version,
            detector_data_version=self.detector.data_version,
            detector_model_kind=self.detector.model_kind,
            provenance=request.provenance.strip(),
            status=status,
            rejection_reasons=tuple(sorted(rejection_reasons)),
            quality_flags=tuple(sorted(quality_flags)),
            visible_fruit_count=count,
            count_lower=max(0, lower),
            count_upper=max(lower, upper),
            detection_confidence_mean=confidence_mean,
            area_p10=_percentile(areas, 0.10) if areas else None,
            area_p50=area_p50,
            area_p90=_percentile(areas, 0.90) if areas else None,
            area_change_ratio=request.area_change_ratio,
            colour_saturation_mean=saturation_mean,
            colour_value_mean=value_mean,
            occlusion_fraction=occlusion_fraction,
            roi_coverage=roi_coverage,
            registration_score=registration,
            quality_score=quality_score,
            allowed_cell_count=allowed_total,
            masked_cell_count=masked_cell_count,
            frame_sha256=frame.frame_sha256,
            observation_sha256=digest,
        )


def _frame(request: ExtractionRequest, width: int, height: int) -> Frame:
    return Frame(
        frame_id=request.frame_id,
        captured_at=request.captured_at.astimezone(UTC),
        width=width,
        height=height,
        luminance=request.luminance,
        saturation=request.saturation,
        provenance=request.provenance,
    )


def _inside_allowed(
    detection: Detection,
    allowed: tuple[tuple[bool, ...], ...],
    width: int,
    height: int,
) -> bool:
    column = min(width - 1, max(0, int(detection.center_x * width)))
    row = min(height - 1, max(0, int(detection.center_y * height)))
    return allowed[row][column]


def _quality_score(
    *,
    luminance_spread: float,
    roi_coverage: float,
    registration: float | None,
    confidence_mean: float | None,
    flags: list[str],
) -> float:
    score = 0.30 * min(1.0, luminance_spread / 0.35)
    score += 0.25 * min(1.0, roi_coverage / 0.80)
    score += 0.25 * (registration if registration is not None else 0.35)
    score += 0.20 * (confidence_mean if confidence_mean is not None else 0.0)
    for flag in flags:
        if flag in {"too_dark", "low_contrast", "camera_moved", "roi_coverage_low"}:
            score -= 0.25
        elif flag == "registration_unverified":
            score -= 0.05
    return round(max(0.0, min(1.0, score)), 6)
