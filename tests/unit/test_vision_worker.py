from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from harvestgrid_domain import (
    CameraViewRequest,
    CameraViewSnapshot,
    CameraViewWorkflow,
    MaskKind,
    MaskRegion,
    Polygon,
    ViewStatus,
)
from harvestgrid_vision import (
    FEATURE_VERSION,
    FEATURES_V1_COLUMNS,
    MISSING_VALUE,
    ArtifactError,
    Detection,
    DetectorError,
    ExtractionError,
    ExtractionRequest,
    FeatureContractError,
    FeatureExtractionWorker,
    Frame,
    HeuristicClusterDetector,
    OnnxArtifact,
    YoloOnnxDetector,
    to_row,
    validate_row,
)

T0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
GRID = (32, 24)
ROI = Polygon.rectangle(0.25, 0.15, 0.75, 0.75)
NEIGHBOR = MaskRegion(MaskKind.NEIGHBOR_PROPERTY, Polygon.rectangle(0.0, 0.0, 0.28, 1.0))


def approved_view(*, masks: tuple[MaskRegion, ...] = (NEIGHBOR,)) -> CameraViewSnapshot:
    workflow = CameraViewWorkflow.create(
        CameraViewRequest(
            view_id="view-fixture",
            tree_id="tree-fixture",
            device_id="device-fixture",
            owner_id="owner-fixture",
            coordinator_id="coordinator-fixture",
            roi=ROI,
            masks=masks,
            reference_crop_sha256="c" * 64,
            frame_width=1920,
            frame_height=1080,
            at=T0,
        )
    )
    workflow.owner_approve(actor_id="owner-fixture", expected_version=0, at=T0)
    return workflow.approve(actor_id="coordinator-fixture", expected_version=1, at=T0)


def plum_grid(
    *,
    width: int = 32,
    height: int = 24,
    clusters: tuple[tuple[int, int, int], ...] = ((9, 3, 6), (16, 7, 6), (9, 12, 4)),
    background: tuple[float, float] = (0.55, 0.10),
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Synthetic frame: dark, saturated squares on a bright, desaturated background."""

    luminance = [background[0]] * (width * height)
    saturation = [background[1]] * (width * height)
    for start_column, start_row, size in clusters:
        for row in range(start_row, min(height, start_row + size)):
            for column in range(start_column, min(width, start_column + size)):
                index = row * width + column
                luminance[index] = 0.30
                saturation[index] = 0.80
    return tuple(luminance), tuple(saturation)


def request(
    view: CameraViewSnapshot | None = None,
    *,
    luminance: tuple[float, ...] | None = None,
    saturation: tuple[float, ...] | None = None,
    registration_score: float | None = 0.9,
    extracted_at: datetime = T0 + timedelta(minutes=1),
    provenance: str = "fixture",
    area_change_ratio: float | None = None,
) -> ExtractionRequest:
    default_luminance, default_saturation = plum_grid()
    return ExtractionRequest(
        view=view or approved_view(),
        frame_id="frame-fixture-1",
        width=GRID[0],
        height=GRID[1],
        luminance=luminance if luminance is not None else default_luminance,
        saturation=saturation if saturation is not None else default_saturation,
        captured_at=T0,
        extracted_at=extracted_at,
        detected_at_grid=GRID,
        registration_score=registration_score,
        area_change_ratio=area_change_ratio,
        provenance=provenance,
    )


def test_accepted_observation_has_counts_areas_colour_and_full_provenance() -> None:
    observation = FeatureExtractionWorker().extract(request())

    assert observation.accepted is True
    assert observation.status == "accepted"
    assert observation.tree_id == "tree-fixture"
    assert observation.view_id == "view-fixture"
    assert observation.view_version == 2
    assert observation.feature_version == FEATURE_VERSION == "features_v1"
    assert observation.detector_model_kind == "heuristic_fixture"
    assert observation.detector_data_version == "no_training_data"
    assert observation.visible_fruit_count == 3
    assert observation.count_lower <= observation.visible_fruit_count
    assert observation.count_upper >= observation.visible_fruit_count
    assert observation.detection_confidence_mean is not None
    assert 0.0 < observation.detection_confidence_mean <= 1.0
    assert observation.area_p10 is not None
    assert observation.area_p10 <= observation.area_p50 <= observation.area_p90
    assert observation.colour_saturation_mean is not None
    assert observation.colour_value_mean is not None
    assert observation.occlusion_fraction == pytest.approx(1.0 - observation.roi_coverage)
    assert observation.registration_score == 0.9
    assert 0.0 <= observation.quality_score <= 1.0
    assert observation.human_review_required is True  # fixture baseline: always review
    assert observation.provenance == "fixture"
    assert len(observation.observation_sha256) == 64
    assert len(observation.frame_sha256) == 64
    assert observation.observation_id.startswith("obs-")

    document = observation.provenance_document()
    assert document["internal_ripeness_inferred"] is False
    assert document["food_safety_inferred"] is False
    assert document["disease_inferred"] is False
    assert document["raw_media_included"] is False
    assert document["count_interval"] == [observation.count_lower, observation.count_upper]

    row = observation.feature_row()
    assert row is not None
    validate_row(row)
    assert len(row) == len(FEATURES_V1_COLUMNS)
    features = dict(zip(FEATURES_V1_COLUMNS, row, strict=True))
    assert features["visible_fruit_count"] == 3.0
    assert features["day_of_year"] == 297.0
    assert features["observation_hour_utc"] == 9.0
    assert features["registration_score"] == pytest.approx(0.9)


def test_extraction_is_deterministic_for_identical_inputs() -> None:
    worker = FeatureExtractionWorker()
    first = worker.extract(request())
    second = worker.extract(request())
    assert first.observation_sha256 == second.observation_sha256
    assert first.feature_row() == second.feature_row()
    changed = worker.extract(request(area_change_ratio=1.25))
    assert changed.observation_sha256 == first.observation_sha256  # area change is context
    assert changed.feature_dict()["area_change_ratio"] == 1.25


def test_masked_neighbour_cells_can_never_become_detections() -> None:
    # A dense neighbour "tree" sits entirely inside the exclusion mask (x < 0.28).
    luminance, saturation = plum_grid(clusters=((1, 2, 6), (18, 10, 4)))
    observation = FeatureExtractionWorker().extract(
        request(luminance=luminance, saturation=saturation)
    )
    assert observation.accepted is True
    assert observation.visible_fruit_count == 1
    assert observation.masked_cell_count > 0
    assert observation.allowed_cell_count == sum(
        1
        for row in range(GRID[1])
        for column in range(GRID[0])
        if ROI.contains((column + 0.5) / GRID[0], (row + 0.5) / GRID[1])
        and not NEIGHBOR.polygon.contains((column + 0.5) / GRID[0], (row + 0.5) / GRID[1])
    )


def test_rejected_frames_never_emit_feature_rows() -> None:
    dark = request(luminance=tuple(0.01 for _ in range(GRID[0] * GRID[1])))
    dark_observation = FeatureExtractionWorker().extract(dark)
    assert dark_observation.accepted is False
    assert "too_dark" in dark_observation.rejection_reasons
    assert dark_observation.feature_row() is None

    flat = request(
        luminance=tuple(0.5 for _ in range(GRID[0] * GRID[1])),
        saturation=tuple(0.5 for _ in range(GRID[0] * GRID[1])),
    )
    flat_observation = FeatureExtractionWorker().extract(flat)
    assert flat_observation.accepted is False
    assert "low_contrast" in flat_observation.rejection_reasons
    assert flat_observation.visible_fruit_count == 0
    assert flat_observation.detection_confidence_mean is None


def test_camera_movement_pauses_feature_inputs() -> None:
    moved = FeatureExtractionWorker().extract(request(registration_score=0.2))
    assert moved.accepted is False
    assert "camera_moved" in moved.rejection_reasons
    assert moved.feature_row() is None
    assert moved.human_review_required is True

    unverified = FeatureExtractionWorker().extract(request(registration_score=None))
    assert "registration_unverified" in unverified.quality_flags
    assert unverified.registration_score is None
    row = unverified.feature_row()
    assert row is not None
    features = dict(zip(FEATURES_V1_COLUMNS, row, strict=True))
    assert features["registration_score"] == MISSING_VALUE  # explicit sentinel, not 0.0


def test_unapproved_or_invalidated_views_cannot_be_extracted() -> None:
    draft = CameraViewWorkflow.create(
        CameraViewRequest(
            view_id="view-draft",
            tree_id="tree-fixture",
            device_id="device-fixture",
            owner_id="owner-fixture",
            coordinator_id="coordinator-fixture",
            roi=ROI,
            masks=(NEIGHBOR,),
            reference_crop_sha256="c" * 64,
            frame_width=1920,
            frame_height=1080,
            at=T0,
        )
    ).snapshot
    with pytest.raises(ExtractionError, match="view_not_approved"):
        FeatureExtractionWorker().extract(request(draft))

    paused = replace(approved_view(), status=ViewStatus.INVALIDATED)
    with pytest.raises(ExtractionError, match="view_not_approved"):
        FeatureExtractionWorker().extract(request(paused))


def test_request_validation_rejects_bad_versions_grids_and_provenance() -> None:
    with pytest.raises(ExtractionError, match="provenance_invalid"):
        FeatureExtractionWorker().extract(request(provenance="   "))
    with pytest.raises(ExtractionError, match="version_missing"):
        FeatureExtractionWorker().extract(request(extracted_at=T0 - timedelta(seconds=1)))
    with pytest.raises(ExtractionError, match="grid_dimensions_invalid"):
        FeatureExtractionWorker().extract(replace(request(), detected_at_grid=(2, 2)))
    with pytest.raises(ExtractionError, match="grid_dimensions_invalid"):
        FeatureExtractionWorker().extract(replace(request(), detected_at_grid=(400, 400)))
    with pytest.raises(ExtractionError, match="version_missing"):
        FeatureExtractionWorker().extract(
            replace(request(), captured_at=datetime(2026, 10, 24, 9, 0))
        )
    with pytest.raises(ExtractionError, match="version_missing"):
        FeatureExtractionWorker(detector=HeuristicClusterDetector(model_version=" ")).extract(
            request()
        )


def test_frame_validation_rejects_malformed_inputs() -> None:
    with pytest.raises(DetectorError, match="frame_dimensions_invalid"):
        Frame(
            frame_id="f",
            captured_at=T0,
            width=0,
            height=1,
            luminance=(),
            saturation=(),
        )
    with pytest.raises(DetectorError, match="frame_cell_count_mismatch"):
        Frame(
            frame_id="f",
            captured_at=T0,
            width=4,
            height=4,
            luminance=(0.5,),
            saturation=(0.5,),
        )
    with pytest.raises(DetectorError, match="frame_cells_must_be_normalized"):
        Frame(
            frame_id="f",
            captured_at=T0,
            width=2,
            height=2,
            luminance=(0.5, 1.5, 0.5, 0.5),
            saturation=(0.5, 0.5, 0.5, 0.5),
        )
    with pytest.raises(DetectorError, match="captured_at_requires_timezone"):
        Frame(
            frame_id="f",
            captured_at=datetime(2026, 10, 24, 9, 0),
            width=2,
            height=2,
            luminance=(0.5, 0.5, 0.5, 0.5),
            saturation=(0.5, 0.5, 0.5, 0.5),
        )


def test_detector_rejects_mismatched_allowed_masks() -> None:
    luminance, saturation = plum_grid()
    frame = Frame(
        frame_id="f",
        captured_at=T0,
        width=GRID[0],
        height=GRID[1],
        luminance=luminance,
        saturation=saturation,
    )
    detector = HeuristicClusterDetector()
    with pytest.raises(DetectorError, match="allowed_mask_dimensions_mismatch"):
        detector.detect(frame, tuple(tuple(True for _ in range(GRID[0])) for _ in range(3)))

    empty_mask = tuple(tuple(False for _ in range(GRID[0])) for _ in range(GRID[1]))
    assert detector.detect(frame, empty_mask) == ()


def test_onnx_detector_requires_a_verified_artifact(tmp_path: Path) -> None:
    luminance = tuple(0.5 for _ in range(GRID[0] * GRID[1]))
    saturation = tuple(0.1 for _ in range(GRID[0] * GRID[1]))
    artifact = OnnxArtifact(
        path=tmp_path / "plum-detector-int8.onnx",
        sha256="a" * 64,
        model_version="plum-yolov8n-int8-v1",
        data_version="plum-consented-2026-10",
        quantization="int8",
        training_provenance="owner_consented_plum_dataset_reviewed",
    )
    detector = YoloOnnxDetector(artifact=artifact)
    assert detector.model_version == "plum-yolov8n-int8-v1"
    with pytest.raises(ArtifactError, match="artifact_missing"):
        detector.detect(
            Frame(
                frame_id="f",
                captured_at=T0,
                width=GRID[0],
                height=GRID[1],
                luminance=luminance,
                saturation=saturation,
            ),
            tuple(tuple(True for _ in range(GRID[0])) for _ in range(GRID[1])),
        )


def test_features_v1_contract_enforces_column_order_and_sentinels() -> None:
    assert FEATURES_V1_COLUMNS[0] == "visible_fruit_count"
    assert FEATURES_V1_COLUMNS[-1] == "observation_hour_utc"
    assert MISSING_VALUE == -1.0
    row = to_row({"visible_fruit_count": 2, "day_of_year": 297})
    assert len(row) == len(FEATURES_V1_COLUMNS)
    assert row[0] == 2.0
    assert row[FEATURES_V1_COLUMNS.index("registration_score")] == MISSING_VALUE
    with pytest.raises(FeatureContractError, match="unknown feature columns"):
        to_row({"invented_column": 1})
    with pytest.raises(FeatureContractError, match="negative value"):
        to_row({"visible_fruit_count": -3})
    with pytest.raises(FeatureContractError, match="ratio above 1.0"):
        to_row({"quality_score": 1.4})
    with pytest.raises(FeatureContractError, match="expected"):
        validate_row((1.0, 2.0))


def test_detection_geometry_is_normalized() -> None:
    luminance, saturation = plum_grid(clusters=((4, 4, 4),))
    frame = Frame(
        frame_id="f",
        captured_at=T0,
        width=GRID[0],
        height=GRID[1],
        luminance=luminance,
        saturation=saturation,
    )
    allowed = tuple(tuple(True for _ in range(GRID[0])) for _ in range(GRID[1]))
    (detection,) = HeuristicClusterDetector().detect(frame, allowed)
    assert isinstance(detection, Detection)
    assert detection.within_frame is True
    assert 0.0 < detection.center_x < 1.0
    assert 0.0 < detection.area_fraction < 1.0
    assert detection.cluster_id.startswith("cluster-")
    assert detection.confidence > 0.5


def test_worker_rejects_when_detector_reports_malformed_grid() -> None:
    class BrokenDetector:
        model_version = "broken-v1"
        data_version = "none"
        model_kind = "broken"

        def detect(self, frame: Frame, allowed: object) -> tuple[Detection, ...]:
            raise DetectorError("allowed_mask_dimensions_mismatch")

    with pytest.raises(ExtractionError, match="view_not_approved"):
        FeatureExtractionWorker(detector=BrokenDetector()).extract(request())


def test_no_fruit_still_produces_a_valid_zero_count_observation() -> None:
    luminance = tuple(0.55 for _ in range(GRID[0] * GRID[1]))
    saturation = tuple(0.10 for _ in range(GRID[0] * GRID[1]))
    observation = FeatureExtractionWorker().extract(
        request(luminance=luminance, saturation=saturation)
    )
    # Uniform frames are rejected for low contrast; add texture without plum colour.
    textured = tuple(
        0.25 + 0.55 * ((index % GRID[0]) / GRID[0]) for index in range(GRID[0] * GRID[1])
    )
    observation = FeatureExtractionWorker().extract(
        request(luminance=textured, saturation=saturation)
    )
    assert observation.visible_fruit_count == 0
    assert observation.count_upper == 0
    assert observation.detection_confidence_mean is None
    row = observation.feature_row()
    assert row is not None
    assert row[0] == 0.0
