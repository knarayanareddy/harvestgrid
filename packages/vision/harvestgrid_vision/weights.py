"""Weight-manifest loading and verified local installation.

No ONNX binary is committed to this repository. This module reads
``packages/vision/weights/manifest.json``, refuses to fetch or install an artifact that is
not fully specified, and installs a locally produced model only after its SHA-256 matches a
value supplied out of band. Every install writes a provenance sidecar so a later run can
prove which bytes were used.
"""

from __future__ import annotations

import json
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .onnx_detector import OnnxArtifact, file_sha256

WEIGHTS_DIR = Path(__file__).resolve().parent.parent / "weights"
MANIFEST_PATH = WEIGHTS_DIR / "manifest.json"
MANIFEST_VERSION = "vision-weights-v1"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
ALLOWED_ARTIFACT_STATUS = ("ready", "blocked", "test_only")
REQUIRED_KEYS = (
    "artifact_id",
    "role",
    "status",
    "format",
    "architecture",
    "quantization",
    "input_size",
    "class_names",
    "expected_sha256",
    "expected_size_bytes",
    "download_url",
    "training_provenance",
    "approved_for_operational_use",
    "blocked_reasons",
    "acceptance_requirements",
)


class WeightManifestError(RuntimeError):
    """Manifest or install request is invalid; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ManifestArtifact:
    artifact_id: str
    role: str
    status: str
    format: str
    architecture: str
    quantization: str
    input_size: int
    class_names: tuple[str, ...]
    expected_sha256: str | None
    expected_size_bytes: int | None
    download_url: str | None
    training_provenance: str | None
    approved_for_operational_use: bool
    blocked_reasons: tuple[str, ...]
    acceptance_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise WeightManifestError("manifest_artifact_id_required")
        if self.status not in ALLOWED_ARTIFACT_STATUS:
            raise WeightManifestError("manifest_status_invalid", self.status)
        if self.format != "onnx":
            raise WeightManifestError("manifest_format_invalid", self.format)
        if self.input_size not in (320, 416, 512, 640):
            raise WeightManifestError("manifest_input_size_invalid", str(self.input_size))
        if not self.class_names:
            raise WeightManifestError("manifest_class_names_required")
        if self.expected_sha256 is not None and (
            len(self.expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.expected_sha256)
        ):
            raise WeightManifestError("manifest_digest_invalid", self.artifact_id)
        if self.status == "ready" and (
            self.expected_sha256 is None
            or self.expected_size_bytes is None
            or self.training_provenance is None
        ):
            raise WeightManifestError("manifest_ready_artifact_incomplete", self.artifact_id)
        if self.status == "blocked" and not self.blocked_reasons:
            raise WeightManifestError("manifest_blocked_reason_required", self.artifact_id)

    @property
    def downloadable(self) -> bool:
        return self.status == "ready" and self.download_url is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "role": self.role,
            "status": self.status,
            "format": self.format,
            "architecture": self.architecture,
            "quantization": self.quantization,
            "input_size": self.input_size,
            "class_names": list(self.class_names),
            "expected_sha256": self.expected_sha256,
            "expected_size_bytes": self.expected_size_bytes,
            "download_url": self.download_url,
            "training_provenance": self.training_provenance,
            "approved_for_operational_use": self.approved_for_operational_use,
            "blocked_reasons": list(self.blocked_reasons),
            "acceptance_requirements": list(self.acceptance_requirements),
        }


@dataclass(frozen=True)
class WeightManifest:
    manifest_version: str
    updated: str
    artifacts: tuple[ManifestArtifact, ...]

    def artifact(self, artifact_id: str) -> ManifestArtifact:
        for candidate in self.artifacts:
            if candidate.artifact_id == artifact_id:
                return candidate
        raise WeightManifestError("manifest_artifact_unknown", artifact_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "updated": self.updated,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
        }


def load_manifest(path: Path = MANIFEST_PATH) -> WeightManifest:
    if not path.is_file():
        raise WeightManifestError("manifest_missing", str(path.name))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise WeightManifestError("manifest_not_json", str(path.name)) from None
    if not isinstance(document, dict):
        raise WeightManifestError("manifest_not_object")
    version = document.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise WeightManifestError("manifest_version_unsupported", str(version))
    raw_artifacts = document.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise WeightManifestError("manifest_artifacts_required")
    artifacts: list[ManifestArtifact] = []
    for entry in raw_artifacts:
        if not isinstance(entry, dict):
            raise WeightManifestError("manifest_artifact_not_object")
        missing = [key for key in REQUIRED_KEYS if key not in entry]
        if missing:
            raise WeightManifestError("manifest_artifact_incomplete", ",".join(missing))
        artifacts.append(
            ManifestArtifact(
                artifact_id=str(entry["artifact_id"]),
                role=str(entry["role"]),
                status=str(entry["status"]),
                format=str(entry["format"]),
                architecture=str(entry["architecture"]),
                quantization=str(entry["quantization"]),
                input_size=int(entry["input_size"]),
                class_names=tuple(str(name) for name in entry["class_names"]),
                expected_sha256=entry["expected_sha256"],
                expected_size_bytes=entry["expected_size_bytes"],
                download_url=entry["download_url"],
                training_provenance=entry["training_provenance"],
                approved_for_operational_use=bool(entry["approved_for_operational_use"]),
                blocked_reasons=tuple(str(reason) for reason in entry["blocked_reasons"]),
                acceptance_requirements=tuple(
                    str(item) for item in entry["acceptance_requirements"]
                ),
            )
        )
    return WeightManifest(
        manifest_version=MANIFEST_VERSION,
        updated=str(document.get("updated", "")),
        artifacts=tuple(artifacts),
    )


@dataclass(frozen=True)
class InstalledArtifact:
    artifact_id: str
    path: Path
    provenance_path: Path
    sha256: str
    size_bytes: int
    install_source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "path": self.path.name,
            "provenance_path": self.provenance_path.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "install_source": self.install_source,
        }


def artifact_target(artifact: ManifestArtifact, output_dir: Path = WEIGHTS_DIR) -> Path:
    return output_dir / f"{artifact.artifact_id}.onnx"


def install_local_artifact(
    *,
    manifest: WeightManifest,
    artifact_id: str,
    source: Path,
    sha256: str,
    provenance: str,
    output_dir: Path = WEIGHTS_DIR,
    force: bool = False,
    installed_at: datetime | None = None,
) -> InstalledArtifact:
    """Install a locally trained artifact after digest verification.

    Refuses blocked or test-only manifest entries: those exist to describe *why* no
    artifact is present, and must never be satisfied by an arbitrary file.
    """

    artifact = manifest.artifact(artifact_id)
    if artifact.status != "ready":
        raise WeightManifestError("artifact_not_installable", artifact.status)
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise WeightManifestError("install_digest_invalid")
    if artifact.expected_sha256 is not None and artifact.expected_sha256 != sha256:
        raise WeightManifestError("install_digest_conflicts_with_manifest")
    if not source.is_file():
        raise WeightManifestError("install_source_missing", source.name)
    size = source.stat().st_size
    if size <= 0:
        raise WeightManifestError("install_source_empty", source.name)
    if size > MAX_ARTIFACT_BYTES:
        raise WeightManifestError("install_source_too_large", str(size))
    actual = file_sha256(source)
    if actual != sha256:
        raise WeightManifestError("install_digest_mismatch", f"actual={actual[:12]}")
    if not provenance.strip():
        raise WeightManifestError("install_provenance_required")
    target = artifact_target(artifact, output_dir)
    if target.exists() and not force:
        raise WeightManifestError("install_target_exists", target.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    installed = InstalledArtifact(
        artifact_id=artifact.artifact_id,
        path=target,
        provenance_path=output_dir / f"{artifact.artifact_id}.provenance.json",
        sha256=actual,
        size_bytes=size,
        install_source=provenance,
    )
    stamp = (installed_at or datetime.now(UTC)).astimezone(UTC)
    installed.provenance_path.write_text(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "manifest_version": manifest.manifest_version,
                "sha256": actual,
                "size_bytes": size,
                "installed_at": stamp.isoformat(),
                "install_source": provenance,
                "training_provenance": artifact.training_provenance,
                "approved_for_operational_use": artifact.approved_for_operational_use,
                "note": "Digest-verified local install. Binaries are never committed.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return installed


def to_onnx_artifact(installed: InstalledArtifact, manifest: WeightManifest) -> OnnxArtifact:
    """Build the runtime artifact descriptor from an installed file and its manifest."""

    entry = manifest.artifact(installed.artifact_id)
    return OnnxArtifact(
        path=installed.path,
        sha256=installed.sha256,
        model_version=entry.artifact_id,
        data_version=manifest.manifest_version,
        class_names=entry.class_names,
        input_size=entry.input_size,
        quantization=entry.quantization,
        training_provenance=entry.training_provenance or "unavailable",
        approved_for_operational_use=entry.approved_for_operational_use,
    )


def fetch_remote_artifact(
    *,
    manifest: WeightManifest,
    artifact_id: str,
    output_dir: Path = WEIGHTS_DIR,
    opener: Any = None,
) -> InstalledArtifact:
    """Download a ``ready`` artifact and verify it.

    ``opener`` is injectable so the download path is testable without a network. Blocked
    entries fail here with the documented reasons instead of attempting a request.
    """

    artifact = manifest.artifact(artifact_id)
    if not artifact.downloadable:
        raise WeightManifestError(
            "artifact_not_downloadable", f"{artifact.status}:{'|'.join(artifact.blocked_reasons)}"
        )
    url = artifact.download_url
    if url is None:  # pragma: no cover - guarded by the downloadable property
        raise WeightManifestError("artifact_not_downloadable", "url_missing")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise WeightManifestError("artifact_url_insecure", parsed.scheme or "missing")
    open_function = opener or urllib.request.urlopen
    with open_function(url, timeout=60) as response:  # noqa: S310
        payload = response.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise WeightManifestError("artifact_too_large")
    if artifact.expected_size_bytes is not None and len(payload) != artifact.expected_size_bytes:
        raise WeightManifestError("artifact_size_mismatch")
    actual = file_sha256_bytes(payload)
    if artifact.expected_sha256 is not None and actual != artifact.expected_sha256:
        raise WeightManifestError("artifact_digest_mismatch")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_target(artifact, output_dir)
    target.write_bytes(payload)
    return InstalledArtifact(
        artifact_id=artifact.artifact_id,
        path=target,
        provenance_path=output_dir / f"{artifact.artifact_id}.provenance.json",
        sha256=actual,
        size_bytes=len(payload),
        install_source=f"download:{url}",
    )


def file_sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
