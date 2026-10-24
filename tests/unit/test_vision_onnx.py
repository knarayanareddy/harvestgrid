import hashlib
import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from harvestgrid_vision import (
    ArtifactError,
    Detection,
    DetectorError,
    Frame,
    InferenceBackendUnavailable,
    OnnxArtifact,
    YoloOnnxDetector,
    allowed_fraction,
    decode_predictions,
    file_sha256,
    letterbox_geometry,
)
from harvestgrid_vision.weights import (
    MANIFEST_PATH,
    WEIGHTS_DIR,
    WeightManifestError,
    fetch_remote_artifact,
    install_local_artifact,
    load_manifest,
    to_onnx_artifact,
)

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 7, 2, 11, 0, tzinfo=UTC)
ONNXRUNTIME_AVAILABLE = importlib.util.find_spec("onnxruntime") is not None
ONNX_AVAILABLE = importlib.util.find_spec("onnx") is not None


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def artifact(path: Path, payload: bytes = b"fake-onnx-bytes") -> OnnxArtifact:
    path.write_bytes(payload)
    return OnnxArtifact(
        path=path,
        sha256=sha(payload),
        model_version="plum-yolov8n-int8-v1",
        data_version="plum-consented-2026-10",
        quantization="int8",
        training_provenance="synthetic_test_only",
    )


def rgb_frame(width: int = 8, height: int = 8) -> Frame:
    cells = width * height
    return Frame(
        frame_id="frame-rgb",
        captured_at=T0,
        width=width,
        height=height,
        luminance=tuple(0.5 for _ in range(cells)),
        saturation=tuple(0.4 for _ in range(cells)),
        rgb=tuple(0.5 for _ in range(cells * 3)),
        provenance="fixture",
    )


class FakeSession:
    """Labelled fixture session; returns a canned YOLOv8-shaped output."""

    def __init__(self, output: object) -> None:
        self.output = output
        self.calls: list[tuple[object, object]] = []

    def get_inputs(self) -> list[object]:
        return [types.SimpleNamespace(name="images")]

    def run(self, _outputs: object, feeds: dict[str, object]) -> list[object]:
        self.calls.append((None, feeds))
        return [self.output]


# --------------------------------------------------------------------------- artifacts


def test_artifact_verification_detects_every_failure_mode(tmp_path: Path) -> None:
    good = artifact(tmp_path / "model.onnx")
    assert good.verify() == good.path
    assert good.provenance()["raw_media_included"] is False
    assert good.provenance()["approved_for_operational_use"] is False

    missing = OnnxArtifact(
        path=tmp_path / "absent.onnx",
        sha256="a" * 64,
        model_version="v1",
        data_version="d1",
        training_provenance="synthetic_test_only",
    )
    with pytest.raises(ArtifactError, match="artifact_missing"):
        missing.verify()

    empty = tmp_path / "empty.onnx"
    empty.write_bytes(b"")
    with pytest.raises(ArtifactError, match="artifact_empty"):
        OnnxArtifact(
            path=empty,
            sha256="a" * 64,
            model_version="v1",
            data_version="d1",
            training_provenance="synthetic_test_only",
        ).verify()

    drifted = OnnxArtifact(
        path=good.path,
        sha256="b" * 64,
        model_version="v1",
        data_version="d1",
        training_provenance="synthetic_test_only",
    )
    with pytest.raises(ArtifactError, match="artifact_digest_mismatch"):
        drifted.verify()


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"sha256": "not-a-digest"}, "artifact_digest_invalid"),
        ({"model_version": " "}, "artifact_metadata_invalid"),
        ({"data_version": ""}, "artifact_metadata_invalid"),
        ({"quantization": "fp4"}, "artifact_metadata_invalid"),
        ({"input_size": 999}, "artifact_metadata_invalid"),
        ({"class_names": ()}, "artifact_metadata_invalid"),
        ({"training_provenance": " "}, "artifact_metadata_invalid"),
    ],
)
def test_artifact_metadata_validation(
    tmp_path: Path, overrides: dict[str, object], code: str
) -> None:
    values: dict[str, object] = {
        "path": tmp_path / "model.onnx",
        "sha256": "a" * 64,
        "model_version": "v1",
        "data_version": "d1",
        "training_provenance": "synthetic_test_only",
    }
    values.update(overrides)
    with pytest.raises(ArtifactError) as error:
        OnnxArtifact(**values)  # type: ignore[arg-type]
    assert error.value.code == code


def test_file_sha256_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "model.onnx"
    path.write_bytes(b"payload")
    assert file_sha256(path) == hashlib.sha256(b"payload").hexdigest()


# --------------------------------------------------------------------------- decoding


def test_decode_channels_first_yolov8_output() -> None:
    # (C=5, N=3): four box coordinates plus one class score.
    output = [
        [100.0, 300.0, 500.0],  # cx
        [100.0, 300.0, 500.0],  # cy
        [40.0, 40.0, 40.0],  # w
        [40.0, 40.0, 40.0],  # h
        [0.90, 0.20, 0.75],  # score
    ]
    boxes = decode_predictions(output, class_count=1, score_threshold=0.5)
    assert len(boxes) == 2
    assert [round(box.score, 2) for box in boxes] == [0.9, 0.75]
    assert boxes[0].class_index == 0


def test_decode_rows_layout_and_thresholding() -> None:
    output = [
        [100.0, 100.0, 40.0, 40.0, 0.10],
        [200.0, 200.0, 40.0, 40.0, 0.80],
    ]
    boxes = decode_predictions(output, class_count=1, score_threshold=0.5)
    assert len(boxes) == 1
    assert boxes[0].center_x == 200.0


def test_decode_applies_class_agnostic_nms() -> None:
    output = [
        [100.0, 100.0, 40.0, 40.0, 0.90],
        [104.0, 104.0, 40.0, 40.0, 0.70],
        [400.0, 400.0, 40.0, 40.0, 0.60],
    ]
    boxes = decode_predictions(output, class_count=1, iou_threshold=0.45)
    assert len(boxes) == 2
    assert boxes[0].score == pytest.approx(0.9)
    assert boxes[1].center_x == 400.0


def test_decode_respects_max_detections_and_ignores_degenerate_boxes() -> None:
    output = [
        [10.0, 10.0, 40.0, 40.0, 0.9],
        [20.0, 300.0, 40.0, 40.0, 0.8],
        [30.0, 30.0, 0.0, 40.0, 0.7],
        [400.0, 400.0, 40.0, 40.0, 0.6],
    ]
    boxes = decode_predictions(output, class_count=1, max_detections=2)
    assert len(boxes) == 2


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"class_count": 0}, "class_count_invalid"),
        ({"score_threshold": 2.0}, "score_threshold_invalid"),
        ({"iou_threshold": 0.0}, "iou_threshold_invalid"),
    ],
)
def test_decode_validates_parameters(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(DetectorError, match=match):
        decode_predictions([[1.0, 2.0, 3.0, 4.0, 0.9]], **{"class_count": 1, **kwargs})


def test_decode_rejects_malformed_shapes() -> None:
    with pytest.raises(DetectorError, match="onnx_output_shape_invalid"):
        decode_predictions([1.0, 2.0], class_count=1)
    with pytest.raises(DetectorError, match="onnx_output_shape_invalid"):
        decode_predictions([[1.0, 2.0, 3.0]], class_count=1)
    assert decode_predictions([], class_count=1) == ()


def test_decode_accepts_numpy_like_objects() -> None:
    class ArrayLike:
        def tolist(self) -> list[list[list[float]]]:
            return [[[100.0, 100.0, 40.0, 40.0, 0.9]]]

    boxes = decode_predictions(ArrayLike(), class_count=1)
    assert len(boxes) == 1


# --------------------------------------------------------------------------- geometry


def test_letterbox_geometry_pads_a_wide_frame() -> None:
    geometry = letterbox_geometry(1280, 720, 640)
    assert geometry.scale == pytest.approx(0.5)
    assert (geometry.resized_width, geometry.resized_height) == (640, 360)
    assert geometry.pad_x == pytest.approx(0.0)
    assert geometry.pad_y == pytest.approx(140.0)
    with pytest.raises(DetectorError, match="frame_dimensions_invalid"):
        letterbox_geometry(0, 10)


def test_letterbox_maps_boxes_back_to_normalized_frame_coordinates() -> None:
    from harvestgrid_vision.onnx_detector import DecodedBox

    geometry = letterbox_geometry(1280, 720, 640)
    box = DecodedBox(
        center_x=320.0, center_y=320.0, width=64.0, height=64.0, score=0.9, class_index=0
    )
    normalized = geometry.to_normalized(box, 1280, 720)
    assert normalized.center_x == pytest.approx(0.5)
    assert normalized.center_y == pytest.approx(0.5)
    assert normalized.width == pytest.approx(0.1)
    assert normalized.height == pytest.approx(64 / 0.5 / 720)


def test_allowed_fraction_matches_the_privacy_mask() -> None:
    from harvestgrid_vision.onnx_detector import DecodedBox

    allowed = (
        (True, True, False, False),
        (True, True, False, False),
        (False, False, False, False),
        (False, False, False, False),
    )
    inside = DecodedBox(0.25, 0.25, 0.4, 0.4, 0.9, 0)
    outside = DecodedBox(0.85, 0.85, 0.2, 0.2, 0.9, 0)
    assert allowed_fraction(inside, allowed) == pytest.approx(1.0)
    assert allowed_fraction(outside, allowed) == pytest.approx(0.0)
    assert allowed_fraction(inside, ()) == 0.0


# --------------------------------------------------------------------------- detector


def test_detector_requires_an_rgb_frame(tmp_path: Path) -> None:
    detector = YoloOnnxDetector(
        artifact=artifact(tmp_path / "model.onnx"),
        session=FakeSession([[100.0, 100.0, 40.0, 40.0, 0.9]]),
    )
    frame = Frame(
        frame_id="no-rgb",
        captured_at=T0,
        width=8,
        height=8,
        luminance=tuple(0.5 for _ in range(64)),
        saturation=tuple(0.2 for _ in range(64)),
    )
    with pytest.raises(DetectorError, match="frame_rgb_required"):
        detector.detect(frame, tuple(tuple(True for _ in range(8)) for _ in range(8)))


def test_detector_decodes_and_clips_to_the_privacy_mask(tmp_path: Path) -> None:
    output = [
        [320.0, 320.0, 40.0, 40.0, 0.90],  # centre: allowed
        [40.0, 40.0, 30.0, 30.0, 0.85],  # top-left: masked neighbour
    ]
    detector = YoloOnnxDetector(
        artifact=artifact(tmp_path / "model.onnx"), session=FakeSession(output)
    )
    allowed = tuple(tuple(column >= 3 for column in range(8)) for _ in range(8))
    detections = detector.detect(rgb_frame(), allowed)
    assert len(detections) == 1
    kept = detections[0]
    assert isinstance(kept, Detection)
    assert kept.cluster_id.startswith("onnx-")
    assert kept.confidence == pytest.approx(0.9)
    assert 0.4 <= kept.center_x <= 0.6
    assert detector.model_version == "plum-yolov8n-int8-v1"
    assert detector.model_kind == "onnx_yolov8"


def test_detector_rejects_mask_and_output_mismatches(tmp_path: Path) -> None:
    detector = YoloOnnxDetector(artifact=artifact(tmp_path / "model.onnx"), session=FakeSession([]))
    with pytest.raises(DetectorError, match="allowed_mask_dimensions_mismatch"):
        detector.detect(rgb_frame(), ((True, True),))
    empty = YoloOnnxDetector(
        artifact=artifact(tmp_path / "model2.onnx"), session=FakeSession([[1.0]])
    )
    with pytest.raises(DetectorError, match="onnx_model_returned_no_outputs"):
        empty.session = types.SimpleNamespace(
            get_inputs=lambda: [types.SimpleNamespace(name="images")],
            run=lambda _outputs, _feeds: [],
        )
        empty.detect(rgb_frame(), tuple(tuple(True for _ in range(8)) for _ in range(8)))


def test_detector_validates_its_configuration(tmp_path: Path) -> None:
    path = tmp_path / "model.onnx"
    with pytest.raises(DetectorError, match="score_threshold_invalid"):
        YoloOnnxDetector(artifact=artifact(path), score_threshold=2.0)
    with pytest.raises(DetectorError, match="iou_threshold_invalid"):
        YoloOnnxDetector(artifact=artifact(path), iou_threshold=0.0)
    with pytest.raises(DetectorError, match="max_detections_invalid"):
        YoloOnnxDetector(artifact=artifact(path), max_detections=0)


@pytest.mark.skipif(ONNXRUNTIME_AVAILABLE, reason="onnxruntime installed")
def test_detector_fails_visibly_without_onnxruntime(tmp_path: Path) -> None:
    detector = YoloOnnxDetector(artifact=artifact(tmp_path / "model.onnx"))
    with pytest.raises(InferenceBackendUnavailable):
        detector.detect(rgb_frame(), tuple(tuple(True for _ in range(8)) for _ in range(8)))


# --------------------------------------------------------------------------- weights


def test_shipped_manifest_blocks_the_fruit_detector() -> None:
    manifest = load_manifest()
    assert manifest.manifest_version == "vision-weights-v1"
    primary = manifest.artifact("plum-yolov8n-int8-v1")
    assert primary.status == "blocked"
    assert primary.downloadable is False
    assert primary.expected_sha256 is None
    assert primary.approved_for_operational_use is False
    assert any("fabricate" in reason for reason in primary.blocked_reasons)
    assert any("release assets" in reason for reason in primary.blocked_reasons)
    assert manifest.artifact("synthetic-shape-smoke-v1").status == "test_only"
    assert MANIFEST_PATH.is_file()
    assert WEIGHTS_DIR.is_dir()
    with pytest.raises(WeightManifestError, match="manifest_artifact_unknown"):
        manifest.artifact("no-such-artifact")


def ready_manifest(path: Path, *, digest: str, size: int, url: str | None = None) -> Path:
    document = {
        "manifest_version": "vision-weights-v1",
        "updated": "2026-10-24",
        "artifacts": [
            {
                "artifact_id": "plum-ready-v1",
                "role": "primary_fruit_detector",
                "status": "ready",
                "format": "onnx",
                "architecture": "yolov8n",
                "quantization": "int8",
                "input_size": 640,
                "class_names": ["plum_cluster"],
                "expected_sha256": digest,
                "expected_size_bytes": size,
                "download_url": url,
                "training_provenance": "reviewed_dataset_dsm-2026-10",
                "approved_for_operational_use": True,
                "blocked_reasons": [],
                "acceptance_requirements": ["reviewed model card"],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_blocked_artifact_cannot_be_installed_or_fetched(tmp_path: Path) -> None:
    manifest = load_manifest()
    payload = b"onnx-bytes"
    source = tmp_path / "source.onnx"
    source.write_bytes(payload)
    with pytest.raises(WeightManifestError, match="artifact_not_installable"):
        install_local_artifact(
            manifest=manifest,
            artifact_id="plum-yolov8n-int8-v1",
            source=source,
            sha256=sha(payload),
            provenance="attempt",
            output_dir=tmp_path,
        )
    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest, artifact_id="plum-yolov8n-int8-v1", output_dir=tmp_path
        )
    assert error.value.code == "artifact_not_downloadable"
    assert "blocked" in (error.value.detail or "")


def test_install_local_artifact_verifies_digest_and_writes_provenance(tmp_path: Path) -> None:
    payload = b"synthetic-onnx-payload"
    source = tmp_path / "trained.onnx"
    source.write_bytes(payload)
    manifest = load_manifest(
        ready_manifest(tmp_path / "manifest.json", digest=sha(payload), size=len(payload))
    )
    installed = install_local_artifact(
        manifest=manifest,
        artifact_id="plum-ready-v1",
        source=source,
        sha256=sha(payload),
        provenance="self-trained on reviewed dataset",
        output_dir=tmp_path / "weights",
        installed_at=T0,
    )
    assert installed.path.read_bytes() == payload
    assert installed.size_bytes == len(payload)
    provenance = json.loads(installed.provenance_path.read_text())
    assert provenance["sha256"] == sha(payload)
    assert provenance["install_source"] == "self-trained on reviewed dataset"
    assert provenance["installed_at"].startswith("2026-07-02")

    descriptor = to_onnx_artifact(installed, manifest)
    assert descriptor.quantization == "int8"
    assert descriptor.approved_for_operational_use is True
    assert descriptor.verify() == installed.path


def test_install_refuses_bad_digest_existing_target_and_bad_inputs(tmp_path: Path) -> None:
    payload = b"synthetic-onnx-payload"
    source = tmp_path / "trained.onnx"
    source.write_bytes(payload)
    manifest = load_manifest(
        ready_manifest(tmp_path / "manifest.json", digest=sha(payload), size=len(payload))
    )
    with pytest.raises(WeightManifestError, match="install_digest_conflicts_with_manifest"):
        install_local_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            source=source,
            sha256="c" * 64,
            provenance="x",
            output_dir=tmp_path / "weights",
        )
    tampered = tmp_path / "tampered.onnx"
    tampered.write_bytes(b"other-bytes")
    with pytest.raises(WeightManifestError, match="install_digest_mismatch"):
        install_local_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            source=tampered,
            sha256=sha(payload),
            provenance="tampered",
            output_dir=tmp_path / "weights",
        )
    with pytest.raises(WeightManifestError, match="install_digest_invalid"):
        install_local_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            source=source,
            sha256="short",
            provenance="x",
            output_dir=tmp_path / "weights",
        )
    with pytest.raises(WeightManifestError, match="install_provenance_required"):
        install_local_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            source=source,
            sha256=sha(payload),
            provenance=" ",
            output_dir=tmp_path / "weights",
        )
    output = tmp_path / "weights"
    install_local_artifact(
        manifest=manifest,
        artifact_id="plum-ready-v1",
        source=source,
        sha256=sha(payload),
        provenance="first install",
        output_dir=output,
    )
    with pytest.raises(WeightManifestError, match="install_target_exists"):
        install_local_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            source=source,
            sha256=sha(payload),
            provenance="second install",
            output_dir=output,
        )
    forced = install_local_artifact(
        manifest=manifest,
        artifact_id="plum-ready-v1",
        source=source,
        sha256=sha(payload),
        provenance="forced reinstall",
        output_dir=output,
        force=True,
    )
    assert forced.path.exists()
    with pytest.raises(WeightManifestError, match="install_source_missing"):
        install_local_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            source=tmp_path / "absent.onnx",
            sha256=sha(payload),
            provenance="missing",
            output_dir=output,
            force=True,
        )


def test_fetch_verifies_downloaded_bytes(tmp_path: Path) -> None:
    payload = b"downloaded-onnx"
    manifest = load_manifest(
        ready_manifest(
            tmp_path / "manifest.json",
            digest=sha(payload),
            size=len(payload),
            url="https://example.invalid/plum.onnx",
        )
    )

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self, _limit: int) -> bytes:
            return self.body

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    installed = fetch_remote_artifact(
        manifest=manifest,
        artifact_id="plum-ready-v1",
        output_dir=tmp_path / "weights",
        opener=lambda _url, timeout: Response(payload),
    )
    assert installed.sha256 == sha(payload)
    assert installed.install_source.startswith("download:https://")

    with pytest.raises(WeightManifestError, match="artifact_digest_mismatch"):
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            output_dir=tmp_path / "other",
            opener=lambda _url, timeout: Response(b"tampered_onnxXX"),
        )
    with pytest.raises(WeightManifestError, match="artifact_size_mismatch"):
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            output_dir=tmp_path / "other",
            opener=lambda _url, timeout: Response(payload + b"extra"),
        )


def test_fetch_rejects_a_non_https_url(tmp_path: Path) -> None:
    payload = b"insecure"
    manifest = load_manifest(
        ready_manifest(
            tmp_path / "manifest.json",
            digest=sha(payload),
            size=len(payload),
            url="http://example.invalid/plum.onnx",
        )
    )
    with pytest.raises(WeightManifestError, match="artifact_url_insecure"):
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="plum-ready-v1",
            output_dir=tmp_path / "weights",
            opener=lambda _url, timeout: None,
        )


def test_manifest_loading_rejects_malformed_documents(tmp_path: Path) -> None:
    with pytest.raises(WeightManifestError, match="manifest_missing"):
        load_manifest(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(WeightManifestError, match="manifest_not_json"):
        load_manifest(broken)
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(WeightManifestError, match="manifest_not_object"):
        load_manifest(array)
    wrong_version = tmp_path / "wrong.json"
    wrong_version.write_text(
        json.dumps({"manifest_version": "v0", "artifacts": []}), encoding="utf-8"
    )
    with pytest.raises(WeightManifestError, match="manifest_version_unsupported"):
        load_manifest(wrong_version)
    no_artifacts = tmp_path / "empty.json"
    no_artifacts.write_text(json.dumps({"manifest_version": "vision-weights-v1"}), encoding="utf-8")
    with pytest.raises(WeightManifestError, match="manifest_artifacts_required"):
        load_manifest(no_artifacts)


def test_cli_reports_blockers_and_refuses_blocked_installs(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import fetch_weights
    finally:
        sys.path.pop(0)

    assert fetch_weights.main(["status"]) == 0
    source = tmp_path / "source.onnx"
    source.write_bytes(b"bytes")
    assert (
        fetch_weights.main(
            [
                "install-local",
                "--artifact",
                "plum-yolov8n-int8-v1",
                "--source",
                str(source),
                "--sha256",
                sha(b"bytes"),
                "--provenance",
                "attempt",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == fetch_weights.EXIT_INVALID
    )
    assert (
        fetch_weights.main(
            [
                "fetch",
                "--artifact",
                "plum-yolov8n-int8-v1",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == fetch_weights.EXIT_BLOCKED
    )


# --------------------------------------------------------------------------- real runtime


@dataclass
class ConstantModel:
    output: object


@pytest.mark.skipif(
    not (ONNXRUNTIME_AVAILABLE and ONNX_AVAILABLE),
    reason="onnxruntime/onnx not installed",
)
def test_real_onnxruntime_session_runs_end_to_end(tmp_path: Path) -> None:
    """Runtime smoke test with a synthetic constant-output graph.

    The graph is not a trained detector: it returns fixed boxes so the preprocessing,
    session call, decoding, and masking path can be verified. Nothing here is a model-quality
    claim, and the artifact lives in ``tmp_path`` only.
    """

    import numpy
    import onnx
    from onnx import helper, numpy_helper

    boxes = numpy.array(
        [[[320.0], [320.0], [40.0], [40.0], [0.90]]], dtype=numpy.float32
    )  # (1, 5, 1): four box coordinates plus one score
    zero = numpy.array([0.0], dtype=numpy.float32)
    graph = helper.make_graph(
        [
            helper.make_node("ReduceSum", ["images"], ["scalar"], keepdims=0),
            helper.make_node("Mul", ["scalar", "zero"], ["scaled"]),
            helper.make_node("Add", ["boxes", "scaled"], ["output"]),
        ],
        "synthetic-constant-detector",
        inputs=[helper.make_tensor_value_info("images", onnx.TensorProto.FLOAT, [1, 3, 640, 640])],
        outputs=[helper.make_tensor_value_info("output", onnx.TensorProto.FLOAT, [1, 5, 1])],
        initializer=[
            numpy_helper.from_array(boxes, name="boxes"),
            numpy_helper.from_array(zero, name="zero"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 10
    onnx.checker.check_model(model)
    path = tmp_path / "synthetic-constant.onnx"
    onnx.save(model, path)

    descriptor = OnnxArtifact(
        path=path,
        sha256=file_sha256(path),
        model_version="synthetic-constant-v1",
        data_version="none_synthetic_untrained",
        training_provenance="none_synthetic_untrained",
    )
    detector = YoloOnnxDetector(artifact=descriptor)
    allowed = tuple(tuple(True for _ in range(8)) for _ in range(8))
    detections = detector.detect(rgb_frame(), allowed)

    assert len(detections) == 1
    assert detections[0].confidence == pytest.approx(0.9)
    assert 0.4 <= detections[0].center_x <= 0.6
    assert detector.provenance()["artifact_sha256"] == descriptor.sha256


def test_frame_rgb_plane_is_validated() -> None:
    cells = 8 * 8
    common = {
        "frame_id": "f",
        "captured_at": T0,
        "width": 8,
        "height": 8,
        "luminance": tuple(0.5 for _ in range(cells)),
        "saturation": tuple(0.2 for _ in range(cells)),
    }
    with pytest.raises(DetectorError, match="frame_rgb_cell_count_mismatch"):
        Frame(rgb=tuple(0.5 for _ in range(cells)), **common)
    with pytest.raises(DetectorError, match="frame_cells_must_be_normalized"):
        Frame(rgb=tuple(1.5 for _ in range(cells * 3)), **common)
    with pytest.raises(DetectorError, match="frame_cells_must_be_numeric"):
        Frame(rgb=tuple(True for _ in range(cells * 3)), **common)
    frame = Frame(rgb=tuple(0.5 for _ in range(cells * 3)), **common)
    assert frame.rgb is not None
    # Two frames that differ only in RGB must not share a provenance digest.
    assert frame.frame_sha256 != replace_rgb(frame).frame_sha256


def replace_rgb(frame: Frame) -> Frame:
    return Frame(
        frame_id=frame.frame_id,
        captured_at=frame.captured_at,
        width=frame.width,
        height=frame.height,
        luminance=frame.luminance,
        saturation=frame.saturation,
    )


@pytest.mark.parametrize(
    "override, match",
    [
        ({"manifest_version": "vision-weights-v9"}, "manifest_version_unsupported"),
        ({"artifacts": ["not-an-object"]}, "manifest_artifact_not_object"),
        (
            {"artifacts": [{"artifact_id": "x"}]},
            "manifest_artifact_incomplete",
        ),
    ],
)
def test_manifest_document_validation(
    tmp_path: Path, override: dict[str, object], match: str
) -> None:
    document: dict[str, object] = {"manifest_version": "vision-weights-v1", "artifacts": []}
    document.update(override)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(WeightManifestError, match=match):
        load_manifest(path)


@pytest.mark.parametrize(
    "override, code",
    [
        ({"artifact_id": " "}, "manifest_artifact_id_required"),
        ({"status": "maybe"}, "manifest_status_invalid"),
        ({"format": "pt"}, "manifest_format_invalid"),
        ({"input_size": 1024}, "manifest_input_size_invalid"),
        ({"class_names": []}, "manifest_class_names_required"),
        ({"expected_sha256": "short"}, "manifest_digest_invalid"),
        ({"expected_size_bytes": None}, "manifest_ready_artifact_incomplete"),
        ({"status": "blocked", "blocked_reasons": []}, "manifest_blocked_reason_required"),
    ],
)
def test_manifest_artifact_validation(
    tmp_path: Path, override: dict[str, object], code: str
) -> None:
    base = {
        "artifact_id": "plum-ready-v1",
        "role": "primary_fruit_detector",
        "status": "ready",
        "format": "onnx",
        "architecture": "yolov8n",
        "quantization": "int8",
        "input_size": 640,
        "class_names": ["plum_cluster"],
        "expected_sha256": "a" * 64,
        "expected_size_bytes": 12,
        "download_url": "https://example.invalid/plum.onnx",
        "training_provenance": "reviewed",
        "approved_for_operation_use_placeholder": None,
        "approved_for_operational_use": True,
        "blocked_reasons": [],
        "acceptance_requirements": [],
    }
    base.pop("approved_for_operation_use_placeholder")
    base.update(override)
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"manifest_version": "vision-weights-v1", "artifacts": [base]}),
        encoding="utf-8",
    )
    with pytest.raises(WeightManifestError) as error:
        load_manifest(path)
    assert error.value.code == code
