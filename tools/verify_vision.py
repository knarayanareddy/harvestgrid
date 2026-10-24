#!/usr/bin/env python3
"""Verify an installed vision artifact end to end.

The script loads a digest-verified ONNX artifact through :class:`YoloOnnxDetector`, runs it on a
real photograph, and repeats the run behind a privacy ROI polygon plus an exclusion mask built by
``harvestgrid_domain.roi.allowed_cells``. It reports how many boxes each stage keeps and drops, so
the detector *and* the privacy filter are exercised against real weights rather than a fixture.

Example::

    python tools/fetch_weights.py fetch --artifact fruit-quality-yolov8n-320-v1 \\
        --accept-license AGPL-3.0-only
    python tools/verify_vision.py --image /path/to/fruit.jpg --report /tmp/verification.json

The image is never copied into the repository: the report records its SHA-256 instead. Detection
geometry is reported at three decimal places, which is too coarse to locate a tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "vision"))
sys.path.insert(0, str(ROOT / "packages" / "domain"))

from harvestgrid_domain.roi import (  # noqa: E402
    CameraViewSnapshot,
    MaskKind,
    MaskRegion,
    Point,
    Polygon,
    ViewStatus,
    allowed_cells,
)
from harvestgrid_vision.detector import Detection, Frame  # noqa: E402
from harvestgrid_vision.onnx_detector import YoloOnnxDetector  # noqa: E402
from harvestgrid_vision.weights import (  # noqa: E402
    WEIGHTS_DIR,
    InstalledArtifact,  # noqa: E402
    WeightManifestError,
    artifact_target,
    load_manifest,
    to_onnx_artifact,
)

DEFAULT_ARTIFACT = "fruit-quality-yolov8n-320-v1"
DEFAULT_GRID = (320, 240)
ROI_INSET = 0.08
MASK_REGION = ((0.52, 0.0), (1.0, 0.0), (1.0, 0.46), (0.52, 0.46))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frame_from_image(path: Path, grid: tuple[int, int]) -> Frame:
    """Decode a photograph into the normalized RGB/luminance/saturation planes a Frame needs."""

    try:
        from PIL import Image
    except ModuleNotFoundError:  # pragma: no cover - pillow is a dev dependency
        raise SystemExit("pillow is required: uv sync (it is a dev dependency)") from None
    width, height = grid
    with Image.open(path) as handle:
        resized = handle.convert("RGB").resize((width, height))
    pixels = list(resized.getdata())
    rgb: list[float] = []
    luminance: list[float] = []
    saturation: list[float] = []
    for red, green, blue in pixels:
        red_n, green_n, blue_n = red / 255.0, green / 255.0, blue / 255.0
        rgb.extend((red_n, green_n, blue_n))
        luminance.append(0.2126 * red_n + 0.7152 * green_n + 0.0722 * blue_n)
        high = max(red_n, green_n, blue_n)
        low = min(red_n, green_n, blue_n)
        saturation.append(0.0 if high == 0.0 else (high - low) / high)
    return Frame(
        frame_id=f"verify-{_digest(path)[:12]}",
        captured_at=datetime.now(UTC),
        width=width,
        height=height,
        luminance=tuple(luminance),
        saturation=tuple(saturation),
        provenance="verification_sample",
        rgb=tuple(rgb),
    )


def privacy_view(
    width: int, height: int, mask_region: tuple[tuple[float, float], ...] = MASK_REGION
) -> CameraViewSnapshot:
    """An ROI polygon inset from the frame edge plus one exclusion mask (neighbour property)."""

    now = datetime.now(UTC)
    roi = Polygon(
        (
            Point(ROI_INSET, ROI_INSET),
            Point(1.0 - ROI_INSET, ROI_INSET),
            Point(1.0 - ROI_INSET, 1.0 - ROI_INSET),
            Point(ROI_INSET, 1.0 - ROI_INSET),
        )
    )
    mask = MaskRegion(
        kind=MaskKind.NEIGHBOR_PROPERTY,
        polygon=Polygon(tuple(Point(x, y) for x, y in mask_region)),
        note="verification-only exclusion mask",
    )
    return CameraViewSnapshot(
        view_id="verify-view",
        tree_id="verify-tree",
        device_id="verify-device",
        version=1,
        roi=roi,
        masks=(mask,),
        reference_crop_sha256=_digest_bytes(b"verification-reference-crop"),
        frame_width=width,
        frame_height=height,
        status=ViewStatus.APPROVED,
        created_by="verifier",
        created_at=now,
        owner_approved_by="verifier",
        owner_approved_at=now,
        coordinator_approved_by=None,
        coordinator_approved_at=None,
        rejection_reason=None,
        invalidation_reason=None,
    )


def _digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _boxes(detections: tuple[Detection, ...]) -> list[dict[str, Any]]:
    return [
        {
            "cluster_id": detection.cluster_id,
            "center": [round(detection.center_x, 3), round(detection.center_y, 3)],
            "size": [round(detection.width, 3), round(detection.height, 3)],
            "confidence": round(detection.confidence, 3),
        }
        for detection in detections
    ]


def _full_frame_mask(width: int, height: int) -> tuple[tuple[bool, ...], ...]:
    return tuple(tuple(True for _ in range(width)) for _ in range(height))


def run(
    *,
    artifact_id: str,
    image: Path,
    grid: tuple[int, int],
    mask_region: tuple[tuple[float, float], ...] = MASK_REGION,
    sample_note: str = "",
) -> dict[str, Any]:
    manifest = load_manifest()
    entry = manifest.artifact(artifact_id)
    target = artifact_target(entry, WEIGHTS_DIR)
    if not target.is_file():
        raise SystemExit(
            f"{target.name} is not installed; run: python tools/fetch_weights.py fetch "
            f"--artifact {artifact_id}"
        )
    sidecar = target.with_name(f"{artifact_id}.provenance.json")
    installed = InstalledArtifact(
        artifact_id=artifact_id,
        path=target,
        provenance_path=sidecar,
        sha256=_digest(target),
        size_bytes=target.stat().st_size,
        install_source="local:verify",
    )
    detector = YoloOnnxDetector(artifact=to_onnx_artifact(installed, manifest))
    frame = frame_from_image(image, grid)
    view = privacy_view(frame.width, frame.height, mask_region)

    unmasked = detector.detect(frame, _full_frame_mask(frame.width, frame.height))
    roi_only = detector.detect(
        frame, allowed_cells(replace(view, masks=()), width=frame.width, height=frame.height)
    )
    masked = detector.detect(frame, allowed_cells(view, width=frame.width, height=frame.height))

    return {
        "verified_at": datetime.now(UTC).isoformat(),
        "artifact_id": artifact_id,
        "artifact_sha256": installed.sha256,
        "artifact_size_bytes": installed.size_bytes,
        "artifact_verified": True,
        "architecture": entry.architecture,
        "quantization": entry.quantization,
        "input_size": entry.input_size,
        "class_names": list(entry.class_names),
        "license": entry.license,
        "approved_for_operational_use": entry.approved_for_operational_use,
        "model_card": entry.model_card,
        "sample": {
            "sha256": _digest(image),
            "width": frame.width,
            "height": frame.height,
            "grid": list(grid),
            "committed": False,
            "note": sample_note,
        },
        "privacy_view": {
            "roi_polygon": [[point.x, point.y] for point in view.roi.points],
            "mask_polygon": [[point.x, point.y] for point in view.masks[0].polygon.points],
            "mask_kind": str(view.masks[0].kind),
            "mask_coverage_of_roi": round(view.mask_coverage_of_roi, 3),
        },
        "results": {
            "full_frame": {
                "detections": len(unmasked),
                "boxes": _boxes(unmasked),
            },
            "roi_only": {
                "detections": len(roi_only),
                "dropped_by_roi": len(unmasked) - len(roi_only),
            },
            "roi_minus_mask": {
                "detections": len(masked),
                "dropped_by_mask": len(roi_only) - len(masked),
                "boxes": _boxes(masked),
            },
        },
        "privacy_evidence": {
            "masked_boxes_removed": len(roi_only) - len(masked),
            "kept_boxes_outside_roi": sum(
                1
                for detection in masked
                if not view.roi.contains(detection.center_x, detection.center_y)
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an installed vision artifact end to end")
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT)
    parser.add_argument("--image", type=Path, required=True, help="real photograph to run")
    parser.add_argument("--report", type=Path, default=None, help="write the JSON report here")
    parser.add_argument(
        "--grid",
        default=f"{DEFAULT_GRID[0]}x{DEFAULT_GRID[1]}",
        help="frame grid WxH, e.g. 320x240",
    )
    parser.add_argument(
        "--sample-note",
        default="",
        help="one line describing where the sample came from (the image itself is not committed)",
    )
    parser.add_argument(
        "--mask",
        default=",".join(f"{x},{y}" for x, y in MASK_REGION),
        help="exclusion mask rectangle as x1,y1,x2,y2 in normalized frame coordinates",
    )
    arguments = parser.parse_args(argv)
    try:
        width, height = (int(part) for part in arguments.grid.lower().split("x"))
    except ValueError:
        parser.error("--grid must look like 320x240")
    if not arguments.image.is_file():
        parser.error(f"image not found: {arguments.image}")
    try:
        x1, y1, x2, y2 = (float(part) for part in arguments.mask.split(","))
    except ValueError:
        parser.error("--mask must look like 0.5,0,1,0.4")
    mask_region = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))

    try:
        report = run(
            artifact_id=arguments.artifact,
            image=arguments.image,
            grid=(width, height),
            mask_region=mask_region,
            sample_note=arguments.sample_note,
        )
    except WeightManifestError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 2

    summary = report["results"]
    digest = report["artifact_sha256"][:16]
    sample = report["sample"]
    print(f"artifact {report['artifact_id']} sha256={digest}... verified")
    print(f"sample {sample['width']}x{sample['height']} sha256={sample['sha256'][:16]}...")
    print(f"full frame: {summary['full_frame']['detections']} detections")
    print(
        f"privacy ROI: {summary['roi_only']['detections']} kept, "
        f"{summary['roi_only']['dropped_by_roi']} outside the ROI polygon"
    )
    print(
        f"ROI minus exclusion mask: {summary['roi_minus_mask']['detections']} kept, "
        f"{summary['roi_minus_mask']['dropped_by_mask']} inside the masked neighbour area"
    )
    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"report written to {arguments.report}")
    return 0 if summary["roi_minus_mask"]["detections"] > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
