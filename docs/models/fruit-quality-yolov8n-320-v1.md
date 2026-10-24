# Model card: `fruit-quality-yolov8n-320-v1`

A **reference** fruit detector that lets the vision runtime be exercised with real, trained
weights. It is not a HarvestGrid product model, it has never seen a HarvestGrid site, and
`approved_for_operational_use` is `false` in `packages/vision/weights/manifest.json`.

## What it is

| Property | Value |
| --- | --- |
| Artifact id | `fruit-quality-yolov8n-320-v1` |
| Architecture | YOLOv8n (`task: detect`), Ultralytics 8.4.37 export |
| Format / quantization | ONNX, fp32, opset 20, IR 9 |
| Input | `images`, float32 `[1, 3, 320, 320]`, RGB, 0–1, letterboxed |
| Output | `output0`, `[1, 12, 2100]` (4 box + 8 class channels, 2100 anchors) |
| Size | 12 144 721 bytes |
| SHA-256 | `c76ccd5e40b06c45e30c6d5249214993cbef4ccbd4be153f1e6295473e8070b6` |
| Classes | `bad apple`, `bad banana`, `bad orange`, `bad pomegranate`, `good apple`, `good banana`, `good orange`, `good pomegranate` |
| License | **AGPL-3.0-only** (see below) |
| Operational use | Not approved |

## Provenance

- Upstream repository: `AKASH-ALAM/Fruit_Quality_Detection_Using_Deep_Learning`
  (repository license: MIT), pinned at commit `2c9ab8de378b9a196c71bd42cef60649b434902f`.
- File in that commit: `model/fruit_quality/weights/best.onnx`
  (source archive SHA-256 `66b7cb2167b2d15ebb57e90a9871fb702092c46b4ff6acfc258c2f3e9e4ab0ec`).
- Training reported upstream: YOLOv8n, 50 epochs, `imgsz=320`, Rotten Fruit Detector Dataset v3
  (Mendeley `xkbjx8959c/2`), 8 balanced fresh/rotten classes; upstream README claims mAP@50
  98.97 %, precision 98.17 %, recall 97.79 % on its own validation split.
- Those metrics are **upstream claims on an upstream split**. HarvestGrid has not reproduced
  them, and no HarvestGrid site, season, cultivar, or plum appears in any evaluation.

## License

The upstream repository is MIT, but the ONNX metadata records
`license = AGPL-3.0 License (https://ultralytics.com/license)` because the artifact is a
YOLOv8 export, and Ultralytics licenses its models under AGPL-3.0. The stricter reading
governs: the manifest declares `AGPL-3.0-only`, `tools/fetch_weights.py` refuses to install the
artifact without `--accept-license AGPL-3.0-only`, and no weight bytes are committed to this
MIT-licensed repository. A license review is required before anything derived from this
checkpoint is distributed or used operationally.

## How it is used here

Only to prove the inference path works end to end with real weights:

```bash
python tools/fetch_weights.py install-archive \
  --artifact fruit-quality-yolov8n-320-v1 \
  --archive /path/to/upstream-2c9ab8de.tar.gz \
  --accept-license AGPL-3.0-only

python tools/verify_vision.py \
  --image /path/to/fruit-photo.jpg \
  --grid 480x360 \
  --report evidence/vision-artifact-verification.json
```

`verify_vision.py` loads the artifact through `YoloOnnxDetector`, which verifies the digest,
letterboxes to 320, decodes the `(C, N)` YOLOv8 layout, applies class-agnostic NMS, and then
drops any box whose area is mostly outside the privacy-allowed cells produced by
`harvestgrid_domain.roi.allowed_cells` (ROI polygon minus exclusion masks). The committed
evidence in `evidence/vision-artifact-verification.json` records one run: 7 boxes on the whole
frame, 4 after the ROI polygon, 3 after the exclusion mask.

## Limitations

1. **Wrong crop.** The classes are apple, banana, orange and pomegranate. There is no plum
   class, so this model cannot detect the fruit HarvestGrid forecasts. `plum-yolov8n-int8-v1`
   remains `blocked` in the manifest for exactly this reason.
2. **Quality, not quantity.** The labels are fresh/rotten pairs. The runtime collapses class
   scores to one class-agnostic cluster box, so the fresh/rotten split is not surfaced; doing so
   would need a reviewed mapping to HarvestGrid feature semantics.
3. **Domain gap.** Studio-style photographs of single fruit on plain backgrounds are not
   canopy photographs with leaves, occlusion, scale variation, and sky.
4. **No privacy evaluation.** The checkpoint was not evaluated for detecting people, plates, or
   neighbouring property; the ROI/mask raster, not the model, is what enforces privacy.
