#!/usr/bin/env python3
"""Manage vision model weights without committing binaries.

Usage::

    python tools/fetch_weights.py status
    python tools/fetch_weights.py install-local \
        --artifact plum-yolov8n-int8-v1 --source /secure/plum-int8.onnx \
        --sha256 <64-hex> --provenance "self-trained on reviewed dataset dsm-2026-10"
    python tools/fetch_weights.py fetch --artifact plum-yolov8n-int8-v1

The ``install-local`` path is the supported route for a self-trained model: no public fruit
checkpoint exists, and generic YOLOv8 weights would not detect fruit. Binaries stay
Git-ignored; the provenance sidecar records the digest that was verified.
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
            f"sha256={artifact.expected_sha256 or 'unrecorded'}"
        )
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
    install = subcommands.add_parser("install-local", help="install a verified local artifact")
    install.add_argument("--artifact", required=True)
    install.add_argument("--output-dir", type=Path, default=WEIGHTS_DIR)
    install.add_argument("--source", type=Path, required=True)
    install.add_argument("--sha256", required=True)
    install.add_argument("--provenance", required=True)
    install.add_argument("--force", action="store_true")

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
    print(f"installed {installed.artifact_id} -> {installed.path} (sha256 {installed.sha256})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
