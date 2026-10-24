# Vision weights

No ONNX binary is committed to this repository, and none can be downloaded from this
environment. `manifest.json` records the artifact the runtime expects, why it is absent, and
what must be true before it may exist.

## Current status: blocked

`plum-yolov8n-int8-v1` is `blocked` for four reasons, all recorded in the manifest:

1. No owner-consented European-plum detection dataset has been released, so no checkpoint can
   be trained.
2. No reviewed model card, held-out site/season evaluation, or approval record exists.
3. Generic COCO/ImageNet YOLOv8n weights contain no fruit class; shipping one as a plum
   detector would fabricate model evidence.
4. This sandbox cannot download GitHub release assets (`objects.githubusercontent.com` is
   unreachable), so no third-party binary could be retrieved even if it were appropriate.

`synthetic-shape-smoke-v1` is `test_only`: tests build it in `tmp_path` to exercise
preprocessing, decoding, NMS, and privacy masking against a real `onnxruntime` session. It has
no trained weights, detects nothing, and is never referenced by the walking path, API, or
forecast input.

## Installing a real artifact

Only a `ready` entry can be installed, and only after its SHA-256 is verified:

```bash
python tools/fetch_weights.py status
python tools/fetch_weights.py install-local \
  --artifact plum-yolov8n-int8-v1 \
  --source /secure/plum-yolov8n-int8.onnx \
  --sha256 <64-hex-digest> \
  --provenance "self-trained on reviewed dataset dsm-2026-10"
```

The command writes `plum-yolov8n-int8-v1.onnx` plus a `.provenance.json` sidecar recording the
digest, size, install source, timestamp, and `approved_for_operational_use`. Blocked and
test-only entries are refused with exit code 3 and code `artifact_not_installable`. A download
path (`fetch`) exists for `ready` entries with an `https` URL and re-verifies size and digest
before writing.

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
