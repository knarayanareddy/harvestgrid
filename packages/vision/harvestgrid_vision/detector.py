"""Visible-fruit cluster detection behind a typed port.

Two implementations:

* :class:`HeuristicClusterDetector` is a deterministic, dependency-free baseline used by
  tests and by the fixture walking path. It groups "plum-like" cells (low value, high
  saturation) into clusters. It is labelled ``heuristic_fixture`` — it is **not** a
  trained model and must never be reported as one.
* :class:`OnnxDetector` is the production shape (ONNX Runtime + normalized RGB frame).
  When ``onnxruntime`` or the artifact is absent it raises
  :class:`InferenceBackendUnavailable` rather than silently substituting the heuristic,
  so an unsupported capability fails visibly (``AC-HG-005``).

Detections are filtered against the view's allowed-cell raster before they leave the
detector, so anything inside a privacy mask can never become a feature.
"""

from __future__ import annotations

import hashlib
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

MAX_FRAME_CELLS = 1_048_576
FRUIT_SATURATION_THRESHOLD = 0.45
FRUIT_VALUE_RANGE = (0.08, 0.72)
MIN_CLUSTER_CELLS = 4


class InferenceBackendUnavailable(RuntimeError):
    """Raised when a documented inference backend is not installed or configured."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class DetectorError(ValueError):
    """Invalid frame input for a detector."""


@dataclass(frozen=True)
class Frame:
    """Normalized synthetic or decoded frame; no raw media is retained by this module."""

    frame_id: str
    captured_at: datetime
    width: int
    height: int
    luminance: tuple[float, ...]
    saturation: tuple[float, ...]
    provenance: str = "fixture"

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1 or self.width * self.height > MAX_FRAME_CELLS:
            raise DetectorError(f"frame_dimensions_invalid: {self.width}x{self.height}")
        expected = self.width * self.height
        if len(self.luminance) != expected or len(self.saturation) != expected:
            raise DetectorError("frame_cell_count_mismatch")
        for values in (self.luminance, self.saturation):
            for value in values:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise DetectorError("frame_cells_must_be_numeric")
                if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                    raise DetectorError("frame_cells_must_be_normalized")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise DetectorError("captured_at_requires_timezone")

    def cell(self, column: int, row: int) -> tuple[float, float]:
        index = row * self.width + column
        return self.luminance[index], self.saturation[index]

    @property
    def frame_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(f"{self.width}x{self.height}".encode("ascii"))
        digest.update(bytes(round(value * 255) for value in self.luminance))
        digest.update(bytes(round(value * 255) for value in self.saturation))
        return digest.hexdigest()


@dataclass(frozen=True)
class Detection:
    cluster_id: str
    center_x: float
    center_y: float
    width: float
    height: float
    confidence: float

    @property
    def area_fraction(self) -> float:
        return self.width * self.height

    @property
    def within_frame(self) -> bool:
        return (
            0.0 <= self.center_x <= 1.0
            and 0.0 <= self.center_y <= 1.0
            and 0.0 < self.width <= 1.0
            and 0.0 < self.height <= 1.0
        )


class FruitClusterDetector(Protocol):
    """Inference port; versions are required so observations stay attributable."""

    @property
    def model_version(self) -> str: ...

    @property
    def data_version(self) -> str: ...

    @property
    def model_kind(self) -> str: ...

    def detect(
        self, frame: Frame, allowed: tuple[tuple[bool, ...], ...]
    ) -> tuple[Detection, ...]: ...


@dataclass(frozen=True)
class HeuristicClusterDetector:
    """Deterministic fixture baseline; never described as a trained detector."""

    model_version: str = "heuristic-ccl-v1"
    data_version: str = "no_training_data"
    model_kind: str = "heuristic_fixture"

    def detect(self, frame: Frame, allowed: tuple[tuple[bool, ...], ...]) -> tuple[Detection, ...]:
        if len(allowed) != frame.height or any(len(row) != frame.width for row in allowed):
            raise DetectorError("allowed_mask_dimensions_mismatch")
        visited = [[False] * frame.width for _ in range(frame.height)]
        clusters: list[Detection] = []

        def plum_like(column: int, row: int) -> bool:
            if not allowed[row][column]:
                return False
            value, saturation = frame.cell(column, row)
            return (
                saturation >= FRUIT_SATURATION_THRESHOLD
                and FRUIT_VALUE_RANGE[0] <= value <= FRUIT_VALUE_RANGE[1]
            )

        for row in range(frame.height):
            for column in range(frame.width):
                if visited[row][column] or not plum_like(column, row):
                    continue
                queue = deque([(column, row)])
                visited[row][column] = True
                members: list[tuple[int, int]] = []
                while queue:
                    current_column, current_row = queue.popleft()
                    members.append((current_column, current_row))
                    for delta_column, delta_row in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        neighbour_column = current_column + delta_column
                        neighbour_row = current_row + delta_row
                        if not (0 <= neighbour_column < frame.width):
                            continue
                        if not (0 <= neighbour_row < frame.height):
                            continue
                        if visited[neighbour_row][neighbour_column]:
                            continue
                        if plum_like(neighbour_column, neighbour_row):
                            visited[neighbour_row][neighbour_column] = True
                            queue.append((neighbour_column, neighbour_row))
                if len(members) < MIN_CLUSTER_CELLS:
                    continue
                columns = [member[0] for member in members]
                rows = [member[1] for member in members]
                min_column, max_column = min(columns), max(columns)
                min_row, max_row = min(rows), max(rows)
                saturation_mean = sum(frame.cell(item[0], item[1])[1] for item in members) / len(
                    members
                )
                # Confidence is a documented function of saturation and cluster size.
                size_term = min(1.0, len(members) / 24.0)
                confidence = round(min(0.99, 0.35 + 0.45 * saturation_mean + 0.19 * size_term), 4)
                clusters.append(
                    Detection(
                        cluster_id=(
                            "cluster-"
                            + hashlib.sha256(
                                f"{min_column}:{min_row}:{max_column}:{max_row}".encode()
                            ).hexdigest()[:12]
                        ),
                        center_x=round(((min_column + max_column + 1) / 2) / frame.width, 6),
                        center_y=round(((min_row + max_row + 1) / 2) / frame.height, 6),
                        width=round((max_column - min_column + 1) / frame.width, 6),
                        height=round((max_row - min_row + 1) / frame.height, 6),
                        confidence=confidence,
                    )
                )
        return tuple(sorted(clusters, key=lambda item: (item.center_x, item.center_y)))


class OnnxDetector:
    """Production Faster R-CNN/single-class ONNX detector shape (not executed here)."""

    model_kind: str
    model_version: str
    data_version: str

    def __init__(
        self,
        *,
        artifact_path: str,
        model_version: str,
        data_version: str,
        score_threshold: float = 0.4,
    ) -> None:
        try:
            import onnxruntime  # type: ignore[import-not-found]  # noqa: F401
        except ModuleNotFoundError:
            raise InferenceBackendUnavailable(
                "onnxruntime is not installed; refusing to substitute the heuristic fixture"
            ) from None
        self.artifact_path = artifact_path
        self.model_version = model_version
        self.data_version = data_version
        self.score_threshold = score_threshold
        raise InferenceBackendUnavailable(
            "ONNX artifact loading is not implemented in this slice; "
            "no fixture detector is substituted for live inference"
        )

    def detect(self, frame: Frame, allowed: tuple[tuple[bool, ...], ...]) -> tuple[Detection, ...]:
        raise InferenceBackendUnavailable("onnx detector is unavailable")
