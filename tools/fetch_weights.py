#!/usr/bin/env python3
"""Manage vision model weights without committing binaries.

Usage::

    python tools/fetch_weights.py status
    python tools/fetch_weights.py install-local \
        --artifact plum-yolov8n-int8-v1 --source /secure/plum-int8.onnx \
        --sha256 <64-hex> --provenance "self-trained on reviewed dataset dsm-2026-10"
    python tools/fetch_weights.py fetch --artifact plum-yolov8n-int8-v1
    python tools/fetch_weights.py fetch --artifact fruit-quality-yolov8n-320-v1 \
        --accept-license AGPL-3.0-only

The ``install-local`` path is the supported route for a self-trained model. ``fetch`` installs a
manifest-declared remote artifact: the download may be a source archive, in which case both the
archive and the extracted ``.onnx`` are digest-verified. Artifacts whose license is not
permissive require an explicit ``--accept-license`` with the manifest's SPDX identifier, so
copyleft or unlicensed weights cannot be installed by accident. Binaries stay Git-ignored; the
provenance sidecar records the digest, license, and source that were verified.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "vision"))

from harvestgrid_vision.weights import (  # noqa: E402
    MANIFEST_PATH,
    WEIGHTS_DIR,
    WeightManifestError,
    fetch_remote_artifact,
    install_archive_artifact,
    install_local_artifact,
    load_manifest,
)

EXIT_INVALID = 2
EXIT_BLOCKED = 3


def _print_manifest() -> int:
    manifest = load_manifest()
    print(f"manifest {manifest.manifest_version} (updated {manifest.updated})")
    for artifact in manifest.artifacts:
        print(
            f"- {artifact.artifact_id}: status={artifact.status} "
            f"arch={artifact.architecture} quantization={artifact.quantization} "
            f"input={artifact.input_size} classes={len(artifact.class_names)} "
            f"sha256={artifact.expected_sha256 or 'unrecorded'} "
            f"license={artifact.license or 'unrecorded'}"
        )
        if artifact.archive_member is not None:
            print(f"    archive member: {artifact.archive_member}")
        if artifact.license_acceptance_required:
            print(f"    fetch requires: --accept-license {artifact.license}")
        if artifact.model_card:
            print(f"    model card: {artifact.model_card}")
        for reason in artifact.blocked_reasons:
            print(f"    blocked: {reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HarvestGrid vision weight manager")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status", help="print the manifest and its blockers")
    fetch = subcommands.add_parser("fetch", help="download a ready artifact")
    fetch.add_argument("--artifact", required=True)
    fetch.add_argument("--output-dir", type=Path, default=WEIGHTS_DIR)
    fetch.add_argument(
        "--accept-license",
        default=None,
        help="SPDX identifier of a non-permissive artifact license, acknowledged explicitly",
    )
    fetch.add_argument("--force", action="store_true", help="replace an existing local artifact")
    install = subcommands.add_parser("install-local", help="install a verified local artifact")
    install.add_argument("--artifact", required=True)
    install.add_argument("--output-dir", type=Path, default=WEIGHTS_DIR)
    install.add_argument("--source", type=Path, required=True)
    install.add_argument("--sha256", required=True)
    install.add_argument("--provenance", required=True)
    install.add_argument("--force", action="store_true")
    archive = subcommands.add_parser(
        "install-archive",
        help="install a ready artifact from a downloaded source archive (mirror / air-gapped)",
    )
    archive.add_argument("--artifact", required=True)
    archive.add_argument("--archive", type=Path, required=True)
    archive.add_argument("--output-dir", type=Path, default=WEIGHTS_DIR)
    archive.add_argument(
        "--accept-license",
        default=None,
        help="SPDX identifier of a non-permissive artifact license, acknowledged explicitly",
    )
    archive.add_argument("--force", action="store_true")

    arguments = parser.parse_args(argv)
    if arguments.command == "status":
        return _print_manifest()

    try:
        manifest = load_manifest(arguments.manifest)
        if arguments.command == "fetch":
            installed = fetch_remote_artifact(
                manifest=manifest,
                artifact_id=arguments.artifact,
                output_dir=arguments.output_dir,
                accepted_license=arguments.accept_license,
                force=arguments.force,
            )
        elif arguments.command == "install-archive":
            installed = install_archive_artifact(
                manifest=manifest,
                artifact_id=arguments.artifact,
                archive=arguments.archive,
                output_dir=arguments.output_dir,
                accepted_license=arguments.accept_license,
                force=arguments.force,
            )
        else:
            installed = install_local_artifact(
                manifest=manifest,
                artifact_id=arguments.artifact,
                source=arguments.source,
                sha256=arguments.sha256,
                provenance=arguments.provenance,
                output_dir=arguments.output_dir,
                force=arguments.force,
            )
    except WeightManifestError as error:
        print(f"refused: {error}", file=sys.stderr)
        if error.code == "artifact_not_downloadable":
            return EXIT_BLOCKED
        return EXIT_INVALID
    artifact = manifest.artifact(installed.artifact_id)
    print(f"installed {installed.artifact_id} -> {installed.path} (sha256 {installed.sha256})")
    print(f"provenance sidecar: {installed.provenance_path}")
    approved = artifact.approved_for_operational_use
    print(f"license: {artifact.license or 'unrecorded'} (operational use approved: {approved})")
    if not artifact.approved_for_operational_use:
        print("not cleared for operational use: see the artifact's acceptance requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
