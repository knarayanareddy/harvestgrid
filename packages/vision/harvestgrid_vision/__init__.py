"""Visible-fruit detection and ``features_v1`` extraction boundary."""

from .detector import (
    Detection,
    DetectorError,
    Frame,
    FruitClusterDetector,
    HeuristicClusterDetector,
    InferenceBackendUnavailable,
    OnnxDetector,
)
from .features_v1 import (
    COLUMN_UNITS,
    FEATURE_VERSION,
    FEATURES_V1_COLUMNS,
    MISSING_VALUE,
    SCHEMA_VERSION,
    FeatureContractError,
    to_row,
    validate_row,
)
from .worker import (
    DEFAULT_GRID,
    OBSERVATION_SCHEMA_VERSION,
    ExtractionError,
    ExtractionRequest,
    FeatureExtractionWorker,
    Observation,
)

__all__ = [
    "COLUMN_UNITS",
    "DEFAULT_GRID",
    "FEATURES_V1_COLUMNS",
    "FEATURE_VERSION",
    "MISSING_VALUE",
    "OBSERVATION_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "Detection",
    "DetectorError",
    "ExtractionError",
    "ExtractionRequest",
    "FeatureContractError",
    "FeatureExtractionWorker",
    "Frame",
    "FruitClusterDetector",
    "HeuristicClusterDetector",
    "InferenceBackendUnavailable",
    "Observation",
    "OnnxDetector",
    "to_row",
    "validate_row",
]
