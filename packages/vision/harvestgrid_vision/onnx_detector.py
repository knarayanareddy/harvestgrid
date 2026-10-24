"""Real ONNX Runtime detector for a single-class fruit model (YOLOv8-nano shape).

This module is the production inference path. It performs artifact verification, YOLOv8
letterbox preprocessing, session execution, box decoding, class-agnostic NMS, and privacy
masking of every detection before it can become a feature row.

Two honesty rules are built in:

* The artifact must already exist on disk with a matching SHA-256. Nothing is downloaded at
  import time, and :class:`ArtifactError` is raised before a session is created if the file
  is missing or has drifted.
* When ``onnxruntime`` is not installed the detector raises
  :class:`InferenceBackendUnavailable`; it never falls back to the heuristic fixture.

No pretrained fruit checkpoint ships in this repository. ``packages/vision/weights`` holds a
manifest describing the artifact that must be supplied, and ``tools/fetch_weights.py``
verifies whatever is supplied.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .detector import Detection, DetectorError, Frame, InferenceBackendUnavailable

YOLO_INPUT_SIZE = 640
ALLOWED_INPUT_SIZES = (320, 416, 512, 640)
ALLOWED_QUANTIZATION = ("fp32", "fp16", "int8", "uint8")
LETTERBOX_FILL = 114.0 / 255.0
DEFAULT_SCORE_THRESHOLD = 0.35
DEFAULT_IOU_THRESHOLD = 0.45
DEFAULT_MAX_DETECTIONS = 50
MIN_ALLOWED_BOX_FRACTION = 0.5
ARTIFACT_ERROR_CODES: tuple[str, ...] = (
    "artifact_missing",
    "artifact_empty",
    "artifact_digest_mismatch",
    "artifact_digest_invalid",
    "artifact_metadata_invalid",
)


class ArtifactError(RuntimeError):
    """Model artifact is absent, empty, or does not match its recorded digest."""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in ARTIFACT_ERROR_CODES:
            raise AssertionError(f"unknown artifact error code: {code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class OnnxArtifact:
    """A verified-on-demand model artifact plus its provenance."""

    path: Path
    sha256: str
    model_version: str
    data_version: str
    class_names: tuple[str, ...] = ("fruit",)
    input_size: int = YOLO_INPUT_SIZE
    quantization: str = "fp32"
    training_provenance: str = "unavailable"
    approved_for_operational_use: bool = False

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ArtifactError("artifact_digest_invalid", "sha256")
        if not self.model_version.strip() or not self.data_version.strip():
            raise ArtifactError("artifact_metadata_invalid", "versions_required")
        if self.quantization not in ALLOWED_QUANTIZATION:
            raise ArtifactError("artifact_metadata_invalid", f"quantization={self.quantization}")
        if self.input_size not in ALLOWED_INPUT_SIZES:
            raise ArtifactError("artifact_metadata_invalid", f"input_size={self.input_size}")
        if not self.class_names:
            raise ArtifactError("artifact_metadata_invalid", "class_names")
        if not self.training_provenance.strip():
            raise ArtifactError("artifact_metadata_invalid", "training_provenance")

    def verify(self) -> Path:
        """Check existence, size, and digest; returns the resolved path."""

        path = self.path
        if not path.is_file():
            raise ArtifactError("artifact_missing", str(path.name))
        if path.stat().st_size <= 0:
            raise ArtifactError("artifact_empty", str(path.name))
        actual = file_sha256(path)
        if actual != self.sha256:
            raise ArtifactError("artifact_digest_mismatch", f"expected={self.sha256[:12]}")
        return path

    def provenance(self) -> dict[str, Any]:
        return {
            "model_kind": "onnx_yolov8",
            "model_version": self.model_version,
            "data_version": self.data_version,
            "artifact_sha256": self.sha256,
            "quantization": self.quantization,
            "input_size": self.input_size,
            "class_names": list(self.class_names),
            "training_provenance": self.training_provenance,
            "approved_for_operational_use": self.approved_for_operational_use,
            "raw_media_included": False,
            "exact_coordinates_included": False,
        }


@dataclass(frozen=True)
class DecodedBox:
    """Box in normalized frame coordinates plus its score and class index."""

    center_x: float
    center_y: float
    width: float
    height: float
    score: float
    class_index: int

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(frozen=True)
class LetterboxGeometry:
    scale: float
    pad_x: float
    pad_y: float
    resized_width: int
    resized_height: int
    input_size: int

    def to_normalized(self, box: DecodedBox, frame_width: int, frame_height: int) -> DecodedBox:
        center_x = (box.center_x - self.pad_x) / self.scale
        center_y = (box.center_y - self.pad_y) / self.scale
        width = box.width / self.scale
        height = box.height / self.scale
        return DecodedBox(
            center_x=min(1.0, max(0.0, center_x / frame_width)),
            center_y=min(1.0, max(0.0, center_y / frame_height)),
            width=min(1.0, max(0.0, width / frame_width)),
            height=min(1.0, max(0.0, height / frame_height)),
            score=box.score,
            class_index=box.class_index,
        )


def letterbox_geometry(
    width: int, height: int, input_size: int = YOLO_INPUT_SIZE
) -> LetterboxGeometry:
    """Aspect-preserving scale and centring padding used by YOLO preprocessing."""

    if width < 1 or height < 1:
        raise DetectorError("frame_dimensions_invalid")
    scale = min(input_size / width, input_size / height)
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    pad_x = (input_size - resized_width) / 2
    pad_y = (input_size - resized_height) / 2
    return LetterboxGeometry(
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
        resized_width=resized_width,
        resized_height=resized_height,
        input_size=input_size,
    )


def _as_matrix(output: Sequence[Any], class_count: int) -> list[list[float]]:
    """Normalize ``(C, N)`` and ``(N, C)`` YOLO layouts into ``N`` rows of ``4 + classes``."""

    if not output:
        return []
    head = output[0]
    if not isinstance(head, (list, tuple)):
        raise DetectorError("onnx_output_shape_invalid")
    channels = 4 + class_count
    if len(output) == channels:
        # Channels-first ``(C, N)``: YOLOv8's documented export layout.
        columns = len(head)
        if any(not isinstance(row, (list, tuple)) or len(row) != columns for row in output):
            raise DetectorError("onnx_output_shape_invalid")
        return [
            [float(output[channel][index]) for channel in range(channels)]
            for index in range(columns)
        ]
    if all(isinstance(row, (list, tuple)) and len(row) == channels for row in output):
        return [[float(value) for value in row] for row in output]
    raise DetectorError("onnx_output_shape_invalid")


def _unwrap_output(raw: Any) -> Sequence[Any]:
    """Accept ``[1, C, N]``, ``[C, N]``, ``[1, N, C]`` and ``[N, C]`` numpy arrays."""

    values = raw.tolist() if hasattr(raw, "tolist") else raw
    if (
        isinstance(values, (list, tuple))
        and len(values) == 1
        and isinstance(values[0], (list, tuple))
    ):
        nested = values[0]
        if nested and isinstance(nested[0], (list, tuple)):
            return nested
        return values
    if not isinstance(values, (list, tuple)):
        raise DetectorError("onnx_output_shape_invalid")
    return values


def decode_predictions(
    raw: Any,
    *,
    class_count: int,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    max_detections: int = DEFAULT_MAX_DETECTIONS,
) -> tuple[DecodedBox, ...]:
    """Decode YOLOv8 output into scored boxes, then apply class-agnostic NMS."""

    if class_count < 1:
        raise DetectorError("class_count_invalid")
    if not 0.0 <= score_threshold <= 1.0:
        raise DetectorError("score_threshold_invalid")
    if not 0.0 < iou_threshold <= 1.0:
        raise DetectorError("iou_threshold_invalid")
    rows = _as_matrix(_unwrap_output(raw), class_count)
    candidates: list[DecodedBox] = []
    for row in rows:
        scores = row[4:]
        if not scores:
            continue
        best = max(range(len(scores)), key=lambda index: scores[index])
        score = scores[best]
        if not math.isfinite(score) or score < score_threshold:
            continue
        center_x, center_y, width, height = row[0], row[1], row[2], row[3]
        if width <= 0.0 or height <= 0.0:
            continue
        candidates.append(
            DecodedBox(
                center_x=center_x,
                center_y=center_y,
                width=width,
                height=height,
                score=min(1.0, max(0.0, score)),
                class_index=best,
            )
        )
    return _non_max_suppression(
        candidates, iou_threshold=iou_threshold, max_detections=max_detections
    )


def intersection_over_union(first: DecodedBox, second: DecodedBox) -> float:
    first_left = first.center_x - first.width / 2
    first_top = first.center_y - first.height / 2
    second_left = second.center_x - second.width / 2
    second_top = second.center_y - second.height / 2
    inter_left = max(first_left, second_left)
    inter_top = max(first_top, second_top)
    inter_right = min(first_left + first.width, second_left + second.width)
    inter_bottom = min(first_top + first.height, second_top + second.height)
    inter_width = max(0.0, inter_right - inter_left)
    inter_height = max(0.0, inter_bottom - inter_top)
    intersection = inter_width * inter_height
    if intersection <= 0.0:
        return 0.0
    union = first.area + second.area - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def _non_max_suppression(
    boxes: list[DecodedBox], *, iou_threshold: float, max_detections: int
) -> tuple[DecodedBox, ...]:
    ordered = sorted(boxes, key=lambda item: (-item.score, item.center_x, item.center_y))
    kept: list[DecodedBox] = []
    for candidate in ordered:
        if len(kept) >= max_detections:
            break
        if all(intersection_over_union(candidate, other) < iou_threshold for other in kept):
            kept.append(candidate)
    return tuple(kept)


def allowed_fraction(box: DecodedBox, allowed: tuple[tuple[bool, ...], ...]) -> float:
    """Fraction of sampled cells inside the allowed (masked) region."""

    rows = len(allowed)
    if rows == 0:
        return 0.0
    columns = len(allowed[0])
    left = max(0, int(math.floor((box.center_x - box.width / 2) * columns)))
    right = min(columns, int(math.ceil((box.center_x + box.width / 2) * columns)))
    top = max(0, int(math.floor((box.center_y - box.height / 2) * rows)))
    bottom = min(rows, int(math.ceil((box.center_y + box.height / 2) * rows)))
    total = 0
    inside = 0
    for row in range(top, bottom):
        for column in range(left, right):
            total += 1
            if allowed[row][column]:
                inside += 1
    if total == 0:
        return 0.0
    return inside / total


@dataclass
class YoloOnnxDetector:
    """ONNX Runtime detector; requires an RGB frame and a verified artifact."""

    artifact: OnnxArtifact
    score_threshold: float = DEFAULT_SCORE_THRESHOLD
    iou_threshold: float = DEFAULT_IOU_THRESHOLD
    max_detections: int = DEFAULT_MAX_DETECTIONS
    session: Any = None
    input_name: str | None = None

    model_kind: str = "onnx_yolov8"

    def __post_init__(self) -> None:
        if not 0.0 <= self.score_threshold <= 1.0:
            raise DetectorError("score_threshold_invalid")
        if not 0.0 < self.iou_threshold <= 1.0:
            raise DetectorError("iou_threshold_invalid")
        if self.max_detections < 1:
            raise DetectorError("max_detections_invalid")

    @property
    def model_version(self) -> str:
        return self.artifact.model_version

    @property
    def data_version(self) -> str:
        return self.artifact.data_version

    def provenance(self) -> dict[str, Any]:
        return self.artifact.provenance()

    def _ensure_session(self) -> Any:
        if self.session is not None:
            return self.session
        # Artifact checks come first so a missing/ drifted file fails the same way whether or
        # not the runtime happens to be installed.
        path = self.artifact.verify()
        try:
            import onnxruntime  # type: ignore[import-untyped]
        except ModuleNotFoundError:
            raise InferenceBackendUnavailable(
                "onnxruntime is not installed; refusing to substitute the heuristic fixture"
            ) from None
        options = onnxruntime.SessionOptions()
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = onnxruntime.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        return self.session

    def _input_name(self, session: Any) -> str:
        if self.input_name is not None:
            return self.input_name
        inputs = session.get_inputs()
        if not inputs:
            raise DetectorError("onnx_model_has_no_inputs")
        self.input_name = str(inputs[0].name)
        return self.input_name

    def _tensor(self, frame: Frame) -> Any:
        if frame.rgb is None:
            raise DetectorError("frame_rgb_required")
        import numpy

        size = self.artifact.input_size
        geometry = letterbox_geometry(frame.width, frame.height, size)
        tensor = numpy.full((3, size, size), LETTERBOX_FILL, dtype=numpy.float32)
        offset_x = int(round(geometry.pad_x))
        offset_y = int(round(geometry.pad_y))
        for target_row in range(geometry.resized_height):
            source_row = min(
                frame.height - 1,
                int(math.floor(target_row * frame.height / geometry.resized_height)),
            )
            for target_column in range(geometry.resized_width):
                source_column = min(
                    frame.width - 1,
                    int(math.floor(target_column * frame.width / geometry.resized_width)),
                )
                base = (source_row * frame.width + source_column) * 3
                row = offset_y + target_row
                column = offset_x + target_column
                if row >= size or column >= size:
                    continue
                pixels = frame.rgb
                if pixels is None:  # pragma: no cover - guarded by _tensor()
                    raise DetectorError("frame_rgb_required")
                tensor[0, row, column] = pixels[base]
                tensor[1, row, column] = pixels[base + 1]
                tensor[2, row, column] = pixels[base + 2]
        return numpy.expand_dims(tensor, axis=0)

    def detect(self, frame: Frame, allowed: tuple[tuple[bool, ...], ...]) -> tuple[Detection, ...]:
        if len(allowed) != frame.height or any(len(row) != frame.width for row in allowed):
            raise DetectorError("allowed_mask_dimensions_mismatch")
        session = self._ensure_session()
        geometry = letterbox_geometry(frame.width, frame.height, self.artifact.input_size)
        outputs = session.run(None, {self._input_name(session): self._tensor(frame)})
        if not outputs:
            raise DetectorError("onnx_model_returned_no_outputs")
        boxes = decode_predictions(
            outputs[0],
            class_count=len(self.artifact.class_names),
            score_threshold=self.score_threshold,
            iou_threshold=self.iou_threshold,
            max_detections=self.max_detections,
        )
        detections: list[Detection] = []
        for box in boxes:
            normalized = geometry.to_normalized(box, frame.width, frame.height)
            if not 0.0 < normalized.width <= 1.0 or not 0.0 < normalized.height <= 1.0:
                continue
            # Privacy mask is applied before a detection can leave the detector.
            if allowed_fraction(normalized, allowed) < MIN_ALLOWED_BOX_FRACTION:
                continue
            detections.append(
                Detection(
                    cluster_id=(
                        "onnx-"
                        + hashlib.sha256(
                            (
                                f"{normalized.center_x:.4f}:{normalized.center_y:.4f}:"
                                f"{normalized.width:.4f}:{normalized.height:.4f}"
                            ).encode()
                        ).hexdigest()[:12]
                    ),
                    center_x=round(normalized.center_x, 6),
                    center_y=round(normalized.center_y, 6),
                    width=round(normalized.width, 6),
                    height=round(normalized.height, 6),
                    confidence=round(normalized.score, 4),
                )
            )
        return tuple(sorted(detections, key=lambda item: (item.center_x, item.center_y)))
