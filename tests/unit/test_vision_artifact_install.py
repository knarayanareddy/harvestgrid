"""Tests for verified artifact installation and for real-weight inference behind the privacy ROI.

The installer tests are offline: they build a small tarball in ``tmp_path`` and inject a fake
opener, so the download, archive, digest, and license gates are all exercised without a network.

The detector test only runs when the reference checkpoint from the manifest has actually been
installed (``python tools/fetch_weights.py fetch|install-archive --artifact
fruit-quality-yolov8n-320-v1``). It is skipped otherwise, because the artifact is deliberately
not committed.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from harvestgrid_domain.roi import (
    CameraViewSnapshot,
    MaskKind,
    MaskRegion,
    Point,
    Polygon,
    ViewStatus,
    allowed_cells,
)
from harvestgrid_vision.detector import Frame
from harvestgrid_vision.onnx_detector import ArtifactError, YoloOnnxDetector
from harvestgrid_vision.weights import (
    WEIGHTS_DIR,
    InstalledArtifact,
    WeightManifestError,
    artifact_target,
    fetch_remote_artifact,
    install_archive_artifact,
    load_manifest,
    to_onnx_artifact,
)

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
REFERENCE = "fruit-quality-yolov8n-320-v1"
MODEL_BYTES = b"real-looking-onnx-bytes-for-the-installer"
ARCHIVE_MEMBER = "upstream-abc123/model/weights/best.onnx"


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def archive_bytes(*, member: str = ARCHIVE_MEMBER, payload: bytes = MODEL_BYTES) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
        decoy = tarfile.TarInfo("upstream-abc123/README.md")
        decoy_text = b"# upstream\n"
        decoy.size = len(decoy_text)
        handle.addfile(decoy, io.BytesIO(decoy_text))
    return buffer.getvalue()


def write_manifest(
    path: Path,
    *,
    archive: bytes | None = None,
    license: str = "AGPL-3.0-only",
    url: str = "https://example.invalid/source.tar.gz",
    digest: str | None = None,
    member: str | None = None,
    archive_digest: str | None = None,
    size: int | None = None,
) -> Path:
    entry: dict[str, Any] = {
        "artifact_id": "fruit-test-v1",
        "role": "fruit_detection_reference",
        "status": "ready",
        "format": "onnx",
        "architecture": "yolov8n",
        "quantization": "fp32",
        "input_size": 320,
        "class_names": ["fruit"],
        "expected_sha256": digest or sha(MODEL_BYTES),
        "expected_size_bytes": len(MODEL_BYTES) if size is None else size,
        "download_url": url,
        "training_provenance": "fixture provenance",
        "approved_for_operational_use": False,
        "blocked_reasons": [],
        "acceptance_requirements": ["license review"],
        "license": license,
        "license_notes": "test",
        "model_card": "docs/models/fixture.md",
    }
    if archive is not None or member is not None:
        entry["archive"] = {
            "sha256": archive_digest or (sha(archive) if archive is not None else "0" * 64),
            "member": member or ARCHIVE_MEMBER,
        }
    document = {
        "manifest_version": "vision-weights-v1",
        "updated": "2026-10-24",
        "artifacts": [entry],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def opener_for(payload: bytes) -> Any:
    def opener(url: str, timeout: int = 0) -> FakeResponse:
        del timeout
        assert url.startswith("https://")
        return FakeResponse(payload)

    return opener


# --------------------------------------------------------------------------- manifest


def test_reference_artifact_is_declared_with_a_real_digest_and_license() -> None:
    manifest = load_manifest()
    reference = manifest.artifact(REFERENCE)

    assert reference.status == "ready"
    assert reference.downloadable is True
    assert reference.architecture == "yolov8n"
    assert reference.input_size == 320
    assert len(reference.class_names) == 8
    assert "plum" not in " ".join(reference.class_names)
    assert reference.expected_sha256 is not None and len(reference.expected_sha256) == 64
    assert reference.expected_size_bytes == 12_144_721
    assert reference.approved_for_operational_use is False
    assert reference.license == "AGPL-3.0-only"
    assert reference.license_acceptance_required is True
    assert reference.model_card == "docs/models/fruit-quality-yolov8n-320-v1.md"
    assert (WEIGHTS_DIR.parent.parent.parent / reference.model_card).is_file()
    # The archive is pinned: both the tarball and the model member are digest-checked.
    assert reference.archive_member is not None
    assert reference.archive_sha256 is not None and len(reference.archive_sha256) == 64
    assert "2c9ab8de378b9a196c71bd42cef60649b434902f" in (reference.download_url or "")


def test_primary_plum_detector_stays_blocked() -> None:
    manifest = load_manifest()

    assert manifest.artifact("plum-yolov8n-int8-v1").status == "blocked"
    assert manifest.artifact(REFERENCE).approved_for_operational_use is False


def test_installed_binaries_are_git_ignored() -> None:
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:  # pragma: no cover - git is present in every checkout
        pytest.skip("git is not available")
    result = subprocess.run(  # noqa: S603
        [git, "check-ignore", "--quiet", "packages/vision/weights/probe.onnx"],
        cwd=WEIGHTS_DIR,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, "*.onnx must stay untracked"


# --------------------------------------------------------------------------- license gate


def test_copyleft_license_requires_explicit_acceptance(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(write_manifest(tmp_path / "manifest.json", archive=payload))

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
        )
    assert error.value.code == "artifact_license_acceptance_required"
    assert "AGPL-3.0-only" in (error.value.detail or "")
    assert not (tmp_path / "weights" / "fruit-test-v1.onnx").exists()


def test_wrong_license_acceptance_is_refused(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(write_manifest(tmp_path / "manifest.json", archive=payload))

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
            accepted_license="MIT",
        )
    assert error.value.code == "artifact_license_acceptance_required"
    assert "AGPL-3.0-only" in (error.value.detail or "")


def test_permissive_license_needs_no_acceptance(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(
        write_manifest(tmp_path / "manifest.json", archive=payload, license="Apache-2.0")
    )
    installed = fetch_remote_artifact(
        manifest=manifest,
        artifact_id="fruit-test-v1",
        output_dir=tmp_path / "weights",
        opener=opener_for(payload),
        fetched_at=T0,
    )

    assert installed.sha256 == sha(MODEL_BYTES)
    sidecar = json.loads(installed.provenance_path.read_text())
    assert sidecar["license"] == "Apache-2.0"
    assert sidecar["license_accepted"] is None


# --------------------------------------------------------------------------- archive install


def test_fetch_extracts_the_declared_member_and_verifies_both_digests(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(write_manifest(tmp_path / "manifest.json", archive=payload))

    installed = fetch_remote_artifact(
        manifest=manifest,
        artifact_id="fruit-test-v1",
        output_dir=tmp_path / "weights",
        opener=opener_for(payload),
        accepted_license="AGPL-3.0-only",
        fetched_at=T0,
    )

    assert installed.path.read_bytes() == MODEL_BYTES
    assert installed.size_bytes == len(MODEL_BYTES)
    sidecar = json.loads(installed.provenance_path.read_text())
    assert sidecar["archive_sha256"] == sha(payload)
    assert sidecar["archive_member"] == ARCHIVE_MEMBER
    assert sidecar["download_url"] == "https://example.invalid/source.tar.gz"
    assert sidecar["installed_at"] == T0.isoformat()
    assert sidecar["license"] == "AGPL-3.0-only"
    assert sidecar["license_accepted"] == "AGPL-3.0-only"
    assert sidecar["approved_for_operational_use"] is False
    assert sidecar["class_names"] == ["fruit"]


def test_changed_archive_is_rejected_before_extraction(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(
        write_manifest(tmp_path / "manifest.json", archive=payload, archive_digest="0" * 64)
    )

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
            accepted_license="AGPL-3.0-only",
        )
    assert error.value.code == "artifact_archive_digest_mismatch"
    assert not (tmp_path / "weights").exists()


def test_missing_member_is_rejected(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(
        write_manifest(
            tmp_path / "manifest.json", archive=payload, member="upstream-abc123/model/other.onnx"
        )
    )

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
            accepted_license="AGPL-3.0-only",
        )
    assert error.value.code == "artifact_archive_member_missing"


def test_wrong_model_digest_is_rejected(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(
        write_manifest(tmp_path / "manifest.json", archive=payload, digest="1" * 64)
    )

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
            accepted_license="AGPL-3.0-only",
        )
    assert error.value.code == "artifact_digest_mismatch"


def test_wrong_model_size_is_rejected(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(
        write_manifest(tmp_path / "manifest.json", archive=payload, size=len(MODEL_BYTES) - 1)
    )

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
            accepted_license="AGPL-3.0-only",
        )
    assert error.value.code == "artifact_size_mismatch"


def test_insecure_download_url_is_refused(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(
        write_manifest(
            tmp_path / "manifest.json", archive=payload, url="http://example.invalid/model.onnx"
        )
    )

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
            accepted_license="AGPL-3.0-only",
        )
    assert error.value.code == "artifact_url_insecure"


def test_existing_install_is_kept_unless_forced(tmp_path: Path) -> None:
    payload = archive_bytes()
    manifest = load_manifest(write_manifest(tmp_path / "manifest.json", archive=payload))
    first = fetch_remote_artifact(
        manifest=manifest,
        artifact_id="fruit-test-v1",
        output_dir=tmp_path / "weights",
        opener=opener_for(payload),
        accepted_license="AGPL-3.0-only",
        fetched_at=T0,
    )

    with pytest.raises(WeightManifestError) as error:
        fetch_remote_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            output_dir=tmp_path / "weights",
            opener=opener_for(payload),
            accepted_license="AGPL-3.0-only",
        )
    assert error.value.code == "install_target_exists"

    forced = fetch_remote_artifact(
        manifest=manifest,
        artifact_id="fruit-test-v1",
        output_dir=tmp_path / "weights",
        opener=opener_for(payload),
        accepted_license="AGPL-3.0-only",
        force=True,
        fetched_at=T0,
    )
    assert forced.sha256 == first.sha256
    assert len(forced.path.read_bytes()) == len(MODEL_BYTES)


def test_install_archive_verifies_a_local_mirror(tmp_path: Path) -> None:
    payload = archive_bytes()
    archive_path = tmp_path / "mirror.tar.gz"
    archive_path.write_bytes(payload)
    manifest = load_manifest(write_manifest(tmp_path / "manifest.json", archive=payload))

    installed = install_archive_artifact(
        manifest=manifest,
        artifact_id="fruit-test-v1",
        archive=archive_path,
        output_dir=tmp_path / "weights",
        accepted_license="AGPL-3.0-only",
        installed_at=T0,
    )

    assert installed.path.read_bytes() == MODEL_BYTES
    sidecar = json.loads(installed.provenance_path.read_text())
    assert sidecar["install_source"] == "archive:mirror.tar.gz"
    assert sidecar["archive_sha256"] == sha(payload)


def test_install_archive_tampering_is_rejected(tmp_path: Path) -> None:
    payload = bytearray(archive_bytes())
    payload[-8] ^= 0xFF  # corrupt the gzip trailer
    archive_path = tmp_path / "tampered.tar.gz"
    archive_path.write_bytes(bytes(payload))
    manifest = load_manifest(write_manifest(tmp_path / "manifest.json", archive=archive_bytes()))

    with pytest.raises(WeightManifestError) as error:
        install_archive_artifact(
            manifest=manifest,
            artifact_id="fruit-test-v1",
            archive=archive_path,
            output_dir=tmp_path / "weights",
            accepted_license="AGPL-3.0-only",
        )
    assert error.value.code in {
        "artifact_archive_digest_mismatch",
        "artifact_archive_invalid",
    }


def test_blocked_artifact_cannot_be_installed_from_an_archive(tmp_path: Path) -> None:
    manifest = load_manifest()
    archive_path = tmp_path / "mirror.tar.gz"
    archive_path.write_bytes(archive_bytes())

    with pytest.raises(WeightManifestError) as error:
        install_archive_artifact(
            manifest=manifest,
            artifact_id="plum-yolov8n-int8-v1",
            archive=archive_path,
            output_dir=tmp_path / "weights",
        )
    assert error.value.code == "artifact_not_installable"


# --------------------------------------------------------------------------- real weights


def _reference_installed() -> bool:
    manifest = load_manifest()
    return artifact_target(manifest.artifact(REFERENCE), WEIGHTS_DIR).is_file()


requires_reference = pytest.mark.skipif(
    not _reference_installed(),
    reason=(
        "reference checkpoint not installed; run tools/fetch_weights.py fetch --artifact "
        f"{REFERENCE} --accept-license AGPL-3.0-only"
    ),
)


def privacy_view(width: int, height: int) -> CameraViewSnapshot:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    polygon = Polygon((Point(0.0, 0.0), Point(0.5, 0.0), Point(0.5, 1.0), Point(0.0, 1.0)))
    return CameraViewSnapshot(
        view_id="view-1",
        tree_id="tree-plum-01",
        device_id="device-1",
        version=1,
        roi=polygon,
        masks=(
            MaskRegion(
                kind=MaskKind.NEIGHBOR_PROPERTY,
                polygon=Polygon(
                    (Point(0.0, 0.0), Point(0.5, 0.0), Point(0.5, 0.5), Point(0.0, 0.5))
                ),
            ),
        ),
        reference_crop_sha256="a" * 64,
        frame_width=width,
        frame_height=height,
        status=ViewStatus.APPROVED,
        created_by="owner-1",
        created_at=now,
        owner_approved_by="owner-1",
        owner_approved_at=now,
        coordinator_approved_by="coordinator-1",
        coordinator_approved_at=now,
        rejection_reason=None,
        invalidation_reason=None,
    )


@requires_reference
def test_reference_checkpoint_loads_through_the_detector() -> None:
    manifest = load_manifest()
    entry = manifest.artifact(REFERENCE)
    target = artifact_target(entry, WEIGHTS_DIR)
    installed = InstalledArtifact(
        artifact_id=REFERENCE,
        path=target,
        provenance_path=target.with_suffix(".provenance.json"),
        sha256=sha(target.read_bytes()),
        size_bytes=target.stat().st_size,
        install_source="test",
    )
    artifact = to_onnx_artifact(installed, manifest)

    detector = YoloOnnxDetector(artifact=artifact)
    assert detector.model_kind == "onnx_yolov8"
    assert artifact.input_size == 320
    assert artifact.class_names == entry.class_names

    # A flat frame proves the session, letterbox, and decode paths run against real weights.
    width, height = 64, 48
    frame = Frame(
        frame_id="flat",
        captured_at=T0,
        width=width,
        height=height,
        luminance=tuple(0.06 for _ in range(width * height)),
        saturation=tuple(0.02 for _ in range(width * height)),
        provenance="test",
        rgb=tuple(0.06 for _ in range(width * height * 3)),
    )
    allowed = allowed_cells(privacy_view(width, height), width=width, height=height)
    detections = detector.detect(frame, allowed)

    assert detections == ()  # nothing fruit-like here, and the mask forbids three quarters


@requires_reference
def test_reference_checkpoint_rejects_a_drifted_digest(tmp_path: Path) -> None:
    manifest = load_manifest()
    entry = manifest.artifact(REFERENCE)
    target = artifact_target(entry, WEIGHTS_DIR)
    copy = tmp_path / "tampered.onnx"
    copy.write_bytes(target.read_bytes() + b"\x00")
    installed = InstalledArtifact(
        artifact_id=REFERENCE,
        path=copy,
        provenance_path=tmp_path / "p.json",
        sha256=entry.expected_sha256 or "0" * 64,
        size_bytes=copy.stat().st_size,
        install_source="test",
    )
    detector = YoloOnnxDetector(artifact=to_onnx_artifact(installed, manifest))

    with pytest.raises(ArtifactError) as error:
        detector.detect(
            Frame(
                frame_id="flat",
                captured_at=T0,
                width=4,
                height=4,
                luminance=tuple(0.5 for _ in range(16)),
                saturation=tuple(0.5 for _ in range(16)),
                rgb=tuple(0.5 for _ in range(48)),
            ),
            tuple(tuple(True for _ in range(4)) for _ in range(4)),
        )
    assert error.value.code == "artifact_digest_mismatch"
