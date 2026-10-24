# Vision weights

No ONNX binary is committed to this repository. `manifest.json` records each artifact, where it
may come from, which license governs it, and what must be true before it may exist.

## Artifacts

| Artifact | Status | What it is |
| --- | --- | --- |
| `plum-yolov8n-int8-v1` | `blocked` | The detector HarvestGrid actually needs. No consented plum dataset exists, so it cannot be trained, reviewed, or approved. |
| `fruit-quality-yolov8n-320-v1` | `ready` | A real, trained YOLOv8n fruit-quality detector from a pinned upstream commit. Reference use only: it has no plum class and is not approved for operational use. |
| `synthetic-shape-smoke-v1` | `test_only` | Built inside tests to exercise letterboxing, decoding, NMS, and masking. Detects nothing. |

## The blocked primary detector

`plum-yolov8n-int8-v1` is `blocked` for four reasons, all recorded in the manifest:

1. No owner-consented European-plum detection dataset has been released, so no checkpoint can
   be trained.
2. No reviewed model card, held-out site/season evaluation, or approval record exists.
3. Generic COCO/ImageNet YOLOv8n weights contain no fruit class; shipping one as a plum
   detector would fabricate model evidence.
4. GitHub release assets (`objects.githubusercontent.com`) are unreachable from the validation
   sandbox, so third-party weights can only arrive through a pinned source archive or a
   mirrored artifact store.

`synthetic-shape-smoke-v1` is `test_only`: tests build it in `tmp_path` to exercise
preprocessing, decoding, NMS, and privacy masking against a real `onnxruntime` session. It has
no trained weights, detects nothing, and is never referenced by the walking path, API, or
forecast input.

## Installing the reference artifact

`fruit-quality-yolov8n-320-v1` is downloaded from a pinned source archive, so a quietly changed
upstream tarball cannot smuggle in different weights: the archive digest and the extracted
model digest are both checked, and the license must be accepted explicitly.

```bash
python tools/fetch_weights.py status

# direct download (needs outbound HTTPS to codeload.github.com)
python tools/fetch_weights.py fetch \
  --artifact fruit-quality-yolov8n-320-v1 \
  --accept-license AGPL-3.0-only

# air-gapped / mirror install of the same pinned archive
python tools/fetch_weights.py install-archive \
  --artifact fruit-quality-yolov8n-320-v1 \
  --archive /mirror/upstream-2c9ab8de.tar.gz \
  --accept-license AGPL-3.0-only

# verify the installed weights on a real photograph, including ROI masking
python tools/verify_vision.py --image /path/to/fruit.jpg \
  --report evidence/vision-artifact-verification.json
```

The full model card, including why the license is AGPL-3.0-only and what the checkpoint cannot
do, is `docs/models/fruit-quality-yolov8n-320-v1.md`. The license gate is deliberate: an
artifact whose license is not permissive (MIT, Apache-2.0, BSD, ISC, CC0) cannot be installed
without passing its SPDX identifier to `--accept-license`.

## Installing a self-trained artifact

Only a `ready` entry can be installed, and only after its SHA-256 is verified:

```bash
python tools/fetch_weights.py install-local \
  --artifact plum-yolov8n-int8-v1 \
  --source /secure/plum-yolov8n-int8.onnx \
  --sha256 <64-hex-digest> \
  --provenance "self-trained on reviewed dataset dsm-2026-10"
```

Every install writes `<artifact-id>.onnx` plus a `.provenance.json` sidecar recording the
digest, size, install source, archive digest and member, license, class names, input size,
timestamp, and `approved_for_operational_use`. Blocked and test-only entries are refused with
exit code 3 and code `artifact_not_installable`.

## Runtime contract

`YoloOnnxDetector` verifies the digest before creating a session, requires an RGB frame,
letterboxes to `input_size`, decodes both `(C, N)` and `(N, C)` YOLOv8 layouts, applies
class-agnostic NMS, and drops any detection whose box is mostly outside the privacy-masked
allowed region. Without `onnxruntime`, or without the artifact, it raises instead of falling
back to the heuristic fixture.

## Git policy

`*.onnx`, `models/artifacts/`, and `datasets/private/` are Git-ignored. Binaries belong in an
immutable artifact store with a model card and dataset manifest; this directory holds only the
manifest, this policy note, and (after an explicit install) untracked local files.
