from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from harvestgrid_domain import (
    CameraViewRequest,
    CameraViewSnapshot,
    CameraViewWorkflow,
    MaskKind,
    MaskRegion,
    Point,
    Polygon,
    RoiAuthorizationError,
    RoiError,
    ViewStatus,
    allowed_cells,
    crop_box,
    frame_coverage,
    mask_coverage_of_roi,
    view_document,
    view_fingerprint,
)

T0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
DIGEST = "a" * 64
ROI = Polygon.rectangle(0.2, 0.1, 0.7, 0.8)
# Neighbour fence panel on the left and the public sidewalk below the fruit line.
NEIGHBOR_MASK = MaskRegion(MaskKind.NEIGHBOR_PROPERTY, Polygon.rectangle(0.0, 0.0, 0.3, 1.0))
SIDEWALK_MASK = MaskRegion(MaskKind.PUBLIC_SIDEWALK, Polygon.rectangle(0.0, 0.7, 1.0, 1.0))


def view_request(
    *,
    roi: Polygon = ROI,
    masks: tuple[MaskRegion, ...] = (NEIGHBOR_MASK, SIDEWALK_MASK),
    digest: str = DIGEST,
    frame_width: int = 1920,
    frame_height: int = 1080,
    at: datetime = T0,
) -> CameraViewRequest:
    return CameraViewRequest(
        view_id="view-fixture",
        tree_id="tree-fixture",
        device_id="device-fixture",
        owner_id="owner-fixture",
        coordinator_id="coordinator-fixture",
        roi=roi,
        masks=masks,
        reference_crop_sha256=digest,
        frame_width=frame_width,
        frame_height=frame_height,
        at=at,
    )


def approved_workflow() -> CameraViewWorkflow:
    workflow = CameraViewWorkflow.create(view_request())
    workflow.owner_approve(actor_id="owner-fixture", expected_version=0, at=T0)
    workflow.approve(actor_id="coordinator-fixture", expected_version=1, at=T0)
    return workflow


def test_draft_owner_approval_and_coordinator_approval_activate_forecasting() -> None:
    workflow = CameraViewWorkflow.create(view_request())
    draft = workflow.snapshot
    assert draft.status is ViewStatus.DRAFT
    assert draft.forecasting_active is False
    assert draft.version == 0
    assert draft.owner_approved_by is None
    assert draft.reference_crop_sha256 == DIGEST
    assert [event.action for event in draft.audit] == ["view_drafted"]

    owner = workflow.owner_approve(actor_id="owner-fixture", expected_version=0, at=T0)
    assert owner.status is ViewStatus.OWNER_APPROVED
    assert owner.version == 1
    assert owner.owner_approved_by == "owner-fixture"
    assert owner.forecasting_active is False

    with pytest.raises(RoiError, match="stale_view_version"):
        workflow.approve(actor_id="coordinator-fixture", expected_version=0, at=T0)

    approved = workflow.approve(
        actor_id="coordinator-fixture", expected_version=1, at=T0 + timedelta(minutes=5)
    )
    assert approved.status is ViewStatus.APPROVED
    assert approved.version == 2
    assert approved.coordinator_approved_by == "coordinator-fixture"
    assert approved.forecasting_active is True
    assert [event.action for event in approved.audit] == [
        "view_drafted",
        "owner_approved_view",
        "coordinator_approved_view",
    ]
    assert [event.actor_id for event in approved.audit] == [
        "owner-fixture",
        "owner-fixture",
        "coordinator-fixture",
    ]

    # Replayed approvals are idempotent and never add versions.
    assert workflow.owner_approve(actor_id="owner-fixture", expected_version=99, at=T0) == approved
    assert workflow.approve(actor_id="coordinator-fixture", expected_version=99, at=T0) == approved


def test_approval_requires_separate_roles_and_owner_first() -> None:
    workflow = CameraViewWorkflow.create(view_request())
    with pytest.raises(RoiError, match="coordinator_approval_requires_owner_approval"):
        workflow.approve(actor_id="coordinator-fixture", expected_version=0, at=T0)
    with pytest.raises(PermissionError, match="approval_role_not_permitted"):
        workflow.owner_approve(actor_id="coordinator-fixture", expected_version=0, at=T0)
    with pytest.raises(PermissionError, match="approval_role_not_permitted"):
        workflow.approve(actor_id="owner-fixture", expected_version=0, at=T0)
    with pytest.raises(PermissionError, match="approval_role_not_permitted"):
        workflow.approve(actor_id="stranger-fixture", expected_version=0, at=T0)

    same_actor = CameraViewWorkflow.create(replace(view_request(), coordinator_id="owner-fixture"))
    same_actor.owner_approve(actor_id="owner-fixture", expected_version=0, at=T0)
    with pytest.raises(PermissionError, match="approval_role_not_permitted"):
        same_actor.approve(actor_id="owner-fixture", expected_version=1, at=T0)

    assert isinstance(RoiAuthorizationError("approval_role_not_permitted"), PermissionError)


def test_rejection_and_post_decision_states_are_committed() -> None:
    workflow = CameraViewWorkflow.create(view_request())
    workflow.owner_approve(actor_id="owner-fixture", expected_version=0, at=T0)
    rejected = workflow.reject(
        actor_id="coordinator-fixture",
        reason="mask misses the neighbour driveway",
        expected_version=1,
        at=T0,
    )
    assert rejected.status is ViewStatus.REJECTED
    assert rejected.rejection_reason == "mask misses the neighbour driveway"
    assert rejected.forecasting_active is False
    assert (
        workflow.reject(actor_id="coordinator-fixture", reason="other", expected_version=99, at=T0)
        == rejected
    )
    with pytest.raises(RoiError, match="view_already_rejected"):
        workflow.approve(actor_id="coordinator-fixture", expected_version=2, at=T0)

    approved = approved_workflow()
    with pytest.raises(RoiError, match="view_already_approved"):
        approved.reject(actor_id="coordinator-fixture", reason="late", expected_version=2, at=T0)
    # Owner approval replays are idempotent, but a paused view cannot be re-approved
    # without a fresh owner action on new geometry.
    assert approved.owner_approve(actor_id="owner-fixture", expected_version=99, at=T0) == (
        approved.snapshot
    )
    paused = approved.invalidate(
        actor_id="coordinator-fixture", reason="camera_moved", expected_version=2, at=T0
    )
    assert paused.status is ViewStatus.INVALIDATED
    with pytest.raises(RoiError, match="view_change_not_allowed"):
        approved.owner_approve(actor_id="owner-fixture", expected_version=3, at=T0)
    with pytest.raises(PermissionError, match="approval_role_not_permitted"):
        approved.reject(actor_id="owner-fixture", reason="self", expected_version=2, at=T0)
    with pytest.raises(RoiError, match="required_field_missing"):
        CameraViewWorkflow.create(view_request()).reject(
            actor_id="coordinator-fixture", reason="   ", expected_version=0, at=T0
        )


def test_geometry_change_invalidates_approvals_and_pauses_forecasting() -> None:
    workflow = approved_workflow()
    new_roi = Polygon.rectangle(0.25, 0.15, 0.75, 0.85)
    changed = workflow.change_view(
        actor_id="owner-fixture",
        roi=new_roi,
        masks=(NEIGHBOR_MASK,),
        reference_crop_sha256="b" * 64,
        frame_width=1920,
        frame_height=1080,
        expected_version=2,
        at=T0 + timedelta(days=1),
    )
    assert changed.version == 3
    assert changed.status is ViewStatus.DRAFT
    assert changed.forecasting_active is False
    assert changed.invalidation_reason == "view_geometry_changed"
    assert changed.owner_approved_by is None
    assert changed.coordinator_approved_by is None
    assert changed.reference_crop_sha256 == "b" * 64
    assert changed.roi is new_roi
    assert changed.masks == (NEIGHBOR_MASK,)
    assert changed.audit[-1].action == "view_geometry_changed"
    assert changed.audit[-1].reason == "view_geometry_changed"

    # A retried identical change is a no-op, not another version.
    replay = workflow.change_view(
        actor_id="owner-fixture",
        roi=new_roi,
        masks=(NEIGHBOR_MASK,),
        reference_crop_sha256="b" * 64,
        frame_width=1920,
        frame_height=1080,
        expected_version=3,
        at=T0 + timedelta(days=2),
    )
    assert replay == changed

    with pytest.raises(RoiError, match="stale_view_version"):
        workflow.change_view(
            actor_id="owner-fixture",
            roi=ROI,
            masks=(),
            reference_crop_sha256=DIGEST,
            frame_width=1920,
            frame_height=1080,
            expected_version=0,
            at=T0,
        )
    with pytest.raises(PermissionError, match="approval_role_not_permitted"):
        workflow.change_view(
            actor_id="coordinator-fixture",
            roi=ROI,
            masks=(),
            reference_crop_sha256=DIGEST,
            frame_width=1920,
            frame_height=1080,
            expected_version=3,
            at=T0,
        )

    # Re-approval is required before forecasting resumes.
    workflow.owner_approve(actor_id="owner-fixture", expected_version=3, at=T0)
    resumed = workflow.approve(actor_id="coordinator-fixture", expected_version=4, at=T0)
    assert resumed.status is ViewStatus.APPROVED
    assert resumed.forecasting_active is True


def test_invalidation_pauses_forecasting_and_is_idempotent() -> None:
    workflow = approved_workflow()
    paused = workflow.invalidate(
        actor_id="coordinator-fixture",
        reason="camera_moved",
        expected_version=2,
        at=T0 + timedelta(hours=1),
    )
    assert paused.status is ViewStatus.INVALIDATED
    assert paused.forecasting_active is False
    assert paused.invalidation_reason == "camera_moved"
    assert (
        workflow.invalidate(
            actor_id="coordinator-fixture", reason="other", expected_version=99, at=T0
        )
        == paused
    )
    owner_paused = approved_workflow().invalidate(
        actor_id="owner-fixture", reason="consent_revoked", expected_version=2, at=T0
    )
    assert owner_paused.status is ViewStatus.INVALIDATED
    with pytest.raises(PermissionError, match="approval_role_not_permitted"):
        approved_workflow().invalidate(
            actor_id="volunteer-fixture", reason="camera_moved", expected_version=2, at=T0
        )
    with pytest.raises(RoiError, match="required_field_missing"):
        approved_workflow().invalidate(
            actor_id="owner-fixture", reason="  ", expected_version=2, at=T0
        )


@pytest.mark.parametrize(
    ("points", "code"),
    [
        ((Point(0.1, 0.1), Point(0.2, 0.2)), "roi_polygon_degenerate"),
        (
            (
                Point(0.1, 0.1),
                Point(0.4, 0.4),
                Point(0.4, 0.1),
                Point(0.1, 0.4),
            ),
            "roi_polygon_self_intersecting",
        ),
        (
            (
                Point(0.10, 0.10),
                Point(0.1001, 0.10),
                Point(0.1001, 0.1001),
            ),
            "roi_polygon_too_small",
        ),
    ],
)
def test_polygon_validation_rejects_degenerate_geometry(
    points: tuple[Point, ...], code: str
) -> None:
    with pytest.raises(RoiError, match=code):
        Polygon(points)


def test_polygon_validation_rejects_out_of_frame_and_oversized_polygons() -> None:
    with pytest.raises(RoiError, match="roi_must_be_inside_frame"):
        Polygon.rectangle(-0.1, 0.1, 0.5, 0.5)
    with pytest.raises(RoiError, match="roi_must_be_inside_frame"):
        Polygon.rectangle(0.1, 0.1, 1.2, 0.5)
    with pytest.raises(RoiError, match="roi_polygon_degenerate") as non_finite:
        Polygon((Point(0.1, 0.1), Point(float("nan"), 0.2), Point(0.3, 0.3)))
    assert non_finite.value.detail == "non_finite_point"
    with pytest.raises(RoiError, match="roi_polygon_too_many_points"):
        Polygon(tuple(Point(0.5, 0.5) for _ in range(129)))

    # Rectangle helpers and containment behave predictably.
    square = Polygon.rectangle(0.2, 0.2, 0.6, 0.6)
    assert square.contains(0.4, 0.4) is True
    assert square.contains(0.8, 0.8) is False
    assert square.contains(0.2, 0.4) is True  # boundary counts as inside
    assert square.area == pytest.approx(0.16)
    assert square.bbox.width == pytest.approx(0.4)
    assert crop_box(approved_workflow().snapshot).area == pytest.approx(0.35)


def test_view_input_validation_rejects_bad_references_frames_and_masks() -> None:
    with pytest.raises(RoiError, match="reference_crop_invalid"):
        CameraViewWorkflow.create(view_request(digest="not-a-digest"))
    with pytest.raises(RoiError, match="frame_dimensions_invalid"):
        CameraViewWorkflow.create(view_request(frame_width=8, frame_height=8))
    with pytest.raises(RoiError, match="frame_dimensions_invalid"):
        CameraViewWorkflow.create(view_request(frame_width=9000, frame_height=1080))
    with pytest.raises(RoiError, match="roi_area_too_small"):
        CameraViewWorkflow.create(
            view_request(roi=Polygon.rectangle(0.0, 0.0, 0.05, 0.05), masks=())
        )
    with pytest.raises(RoiError, match="roi_obscured_by_masks"):
        CameraViewWorkflow.create(
            view_request(
                roi=ROI,
                masks=(
                    MaskRegion(MaskKind.NEIGHBOR_PROPERTY, Polygon.rectangle(0.0, 0.0, 0.75, 0.85)),
                ),
            )
        )
    with pytest.raises(RoiError, match="max_masks_exceeded"):
        CameraViewWorkflow.create(
            view_request(
                masks=tuple(
                    MaskRegion(
                        MaskKind.OTHER_UNRELATED,
                        Polygon.rectangle(index / 64, 0.95, index / 64 + 0.01, 0.99),
                    )
                    for index in range(33)
                )
            )
        )
    with pytest.raises(RoiError, match="mask_kind_invalid"):
        MaskRegion("neighbor_property", ROI)  # type: ignore[arg-type]
    with pytest.raises(RoiError, match="required_field_missing"):
        MaskRegion(MaskKind.STREET, ROI, note="x" * 201)
    with pytest.raises(RoiError, match="required_field_missing"):
        CameraViewWorkflow.create(replace(view_request(), view_id="  "))
    with pytest.raises(RoiError, match="required_field_missing"):
        CameraViewWorkflow.create(replace(view_request(), coordinator_id=""))
    with pytest.raises(RoiError, match="timestamp_requires_timezone"):
        CameraViewWorkflow.create(view_request(at=datetime(2026, 10, 24, 9, 0)))
    with pytest.raises(RoiError, match="timestamp_requires_timezone"):
        CameraViewWorkflow.create(view_request()).owner_approve(
            actor_id="owner-fixture", expected_version=0, at=datetime(2026, 10, 24, 9, 0)
        )


def test_mask_coverage_and_privacy_flags_are_measurable() -> None:
    snapshot = approved_workflow().snapshot
    # ROI 0.35; neighbour band 0.1 x 0.7 = 0.07, sidewalk band 0.5 x 0.1 = 0.05,
    # overlapping by 0.01 -> union 0.11 -> 0.314 of the ROI.
    assert mask_coverage_of_roi(snapshot.roi, snapshot.masks) == pytest.approx(0.314, abs=0.02)
    assert snapshot.effective_roi_fraction == pytest.approx(0.686, abs=0.02)
    assert snapshot.privacy_mask_required is False
    assert frame_coverage(snapshot.roi) == pytest.approx(0.35, abs=0.01)
    assert mask_coverage_of_roi(snapshot.roi, ()) == 0.0

    unmasked = CameraViewWorkflow.create(view_request(masks=()))
    assert unmasked.snapshot.masks == ()
    assert unmasked.snapshot.privacy_mask_required is True
    assert unmasked.snapshot.effective_roi_fraction == 1.0
    assert unmasked.snapshot.status is ViewStatus.DRAFT


def test_allowed_cells_exclude_neighbours_and_sidewalks_before_any_model_call() -> None:
    view = approved_workflow().snapshot
    width, height = 40, 20
    grid = allowed_cells(view, width=width, height=height)
    assert len(grid) == height
    assert all(len(row) == width for row in grid)

    allowed = sum(1 for row in grid for cell in row if cell)

    def cell_at(x: float, y: float) -> bool:
        return grid[min(height - 1, int(y * height))][min(width - 1, int(x * width))]

    # Neighbour property (left of x=0.3) and the sidewalk band (below y=0.7) are never
    # readable, even where they overlap the tree ROI.
    assert cell_at(0.25, 0.4) is False
    assert cell_at(0.65, 0.75) is False
    assert cell_at(0.05, 0.05) is False
    assert cell_at(0.5, 0.4) is True
    assert 0 < allowed < width * height

    with pytest.raises(RoiError, match="frame_dimensions_invalid"):
        allowed_cells(view, width=0, height=10)
    with pytest.raises(RoiError, match="frame_dimensions_invalid"):
        allowed_cells(view, width=4096, height=4096)


def test_allowed_cells_are_subset_monotonic_when_masks_are_added() -> None:
    base = approved_workflow().snapshot
    extra_mask = MaskRegion(MaskKind.STREET, Polygon.rectangle(0.45, 0.1, 0.75, 0.35))
    masked = replace(base, masks=(*base.masks, extra_mask))
    base_grid = allowed_cells(base, width=32, height=16)
    masked_grid = allowed_cells(masked, width=32, height=16)
    for row_index, row in enumerate(masked_grid):
        for column_index, cell in enumerate(row):
            if cell:
                assert base_grid[row_index][column_index] is True
    assert sum(1 for row in masked_grid for cell in row if cell) < sum(
        1 for row in base_grid for cell in row if cell
    )


def test_view_fingerprint_changes_only_with_geometry() -> None:
    first = view_fingerprint(ROI, (NEIGHBOR_MASK, SIDEWALK_MASK))
    assert first == view_fingerprint(ROI, (NEIGHBOR_MASK, SIDEWALK_MASK))
    assert first != view_fingerprint(ROI, (NEIGHBOR_MASK,))
    assert first != view_fingerprint(
        Polygon.rectangle(0.2, 0.1, 0.7, 0.79), (NEIGHBOR_MASK, SIDEWALK_MASK)
    )
    assert len(first) == 64


def test_view_document_is_a_non_sensitive_projection() -> None:
    view: CameraViewSnapshot = approved_workflow().snapshot
    document = view_document(view)
    assert document["contract_version"] == "camera-view-v1"
    assert document["status"] == "approved"
    assert document["forecasting_active"] is True
    assert document["person_or_vehicle_detection_enabled"] is False
    assert document["exact_address_included"] is False
    assert document["raw_media_included"] is False
    assert document["privacy_mask_required"] is False
    assert document["frame"] == {"width": 1920, "height": 1080}
    assert isinstance(document["masks"], list)
    assert {mask["kind"] for mask in document["masks"]} == {
        "neighbor_property",
        "public_sidewalk",
    }
    serialized = repr(document)
    assert "access_note" not in serialized
    assert "latitude" not in serialized


def test_unknown_roi_error_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown ROI error code"):
        RoiError("invented_view_failure")
    error = RoiError("roi_area_too_small", detail="area=0.0001")
    assert error.code == "roi_area_too_small"
    assert error.detail == "area=0.0001"
    assert isinstance(error, ValueError)
