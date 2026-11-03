# Visualization video export

Open a video and choose **Export → Export visualization video**. Select existing
scalar results, annotation/transcript timelines, or spatial results, choose a time range, then
preview its first five seconds or export the complete range as an MP4. Rendering
continues after the dialog closes. Reopen it to see progress, cancel jobs, play or
download completed videos, and reuse their settings.

Exports support up to six layers (at most five metric/label rows, one caption
timeline, and twelve selected clusters). They offer colors, text size, panel opacity, maximum long-edge
resolution (720, 1280 or 1920 pixels), and optional original audio. They preserve
aspect ratio and do not upscale. Export reuses completed analysis. Run analysis
plugins first, then select their results in the export dialog.

## Spatial visualizations

| Result | Visualization | Required analysis |
| --- | --- | --- |
| Face clusters | Named, colored cards; face boxes; optional five-point landmarks | Face clustering; boxes require retained original geometry |
| Place clusters | Thumbnail cards highlighted while the cluster is present | Place clustering |
| Active speaker | Green speaking / gray non-speaking face boxes, optional cluster name | Active speaker detection and a shot timeline; model runs at 25 FPS |
| OCR | Recognized text over its bounding box | A new OCR run; Nano OCR remains a caption/label source |
| Facial expressions | Highest model score beside each individual face | A new Deepface emotion run |
| Body pose | Person boxes, local track numbers, and COCO 17-point skeletons | New Body pose and people plugin and installed model |

Face/place cards use the **current database memberships and names**, including
manual moves and deletions. Colors are stable per cluster and can be overridden.
Snapshots freeze memberships, labels, geometry, and small embedded thumbnails.
Old exports remain unchanged when clusters are edited later.

Sampled boxes are the default. Optional interpolation connects nearby,
unambiguous geometry matches and requires a shot timeline. It stops at cuts,
gaps over 0.75 seconds, and ambiguous matches; it cannot recover missed detections.
Use denser analysis for fast motion. Person numbers are local tracks, with no
cross-shot person identification. Face clusters and continuous tracks are separate.
An active-speaker box receives a cluster name only when one selected face box
overlaps strongly and unambiguously at that timestamp; otherwise it stays unnamed.

Expression labels describe model predictions of visible expressions. Speaker
scores use the model's zero decision boundary, not calibrated probabilities.
OCR merged boxes have no confidence score. Pose keypoints use a model heatmap
score threshold of 2 for visibility; person detection confidence is configurable.
Crowded scenes can produce overlapping labels; disable labels/cards or select
fewer clusters to keep the output readable.

Older face runs can always supply cards. To recover boxes without changing any
curated clusters, an operator who knows the original cached analyser output IDs can run:

```sh
docker compose exec backend uv run --package backend python3 backend/src/backend/manage.py recover_face_geometry RUN_UUID --bbox-data-id ORIGINAL_BOXES_UUID --keypoints-data-id ORIGINAL_KEYPOINTS_UUID
```

Landmarks are optional. Recovery checks that the geometry contains the original
face IDs from that run's embeddings and refuses to overwrite existing geometry.
If those cached outputs are gone, rerun Face clustering to create a separate run;
new detection IDs are not silently substituted into curated old clusters.

## Installation

Apply migrations through `0031_spatial_result_types`, build the frontend, and restart
the Celery worker so it registers `backend.tasks.video_visualization.render_visualization_export`.
Restart/redeploy the analyser and Ray Serve applications to load updated output
schemas and plugin versions for OCR, face expressions, tracking, and speakers.
The API and worker must share `DATA_CACHE_ROOT`, `MEDIA_ROOT`, and `DATA_OUTPUT_PATH`.

The default backend image and ARM/CUDA images install FFmpeg and DejaVu fonts.
Rebuild the image used by the worker. Custom images need `ffmpeg`, `ffprobe`,
the `libx264` encoder, the `ass`/`drawbox` filters, and a Unicode font such as
DejaVu Sans. `VISUALIZATION_FFMPEG` and `VISUALIZATION_FFPROBE` Django settings
can override executable paths.
Spatial rendering also uses Pillow, installed through the existing image dependencies.

Install the optional body-pose model from the repository root:

```sh
python scripts/download-body-pose-model.py
```

This downloads the official torchvision Keypoint R-CNN ResNet-50 FPN COCO weights
(about 226 MB) into `data/models/body_pose/` and verifies the published filename's
SHA-256 prefix. Use `--model-root` for other layouts. Both deployment manifests
include the plugin: CPU in the base configuration, CUDA in the CUDA configuration.
Inference never downloads the model implicitly and reports a missing model with
the installation command. Its Ray environment installs torch and torchvision.
Architecture and output conventions: [torchvision documentation](https://docs.pytorch.org/vision/stable/models/keypoint_rcnn.html).

For the base Compose stack:

```sh
docker compose build backend celery
docker compose exec backend uv run --package backend python3 backend/src/backend/manage.py migrate
docker compose up -d backend celery
cd frontend
npm run build
```

Use the same Compose file/overrides as the running installation. On newer Node
versions, the existing Vue CLI 4 / Webpack 4 build needs
`NODE_OPTIONS=--openssl-legacy-provider` (PowerShell:
`$env:NODE_OPTIONS='--openssl-legacy-provider'`).

## Data and timing

Submission validates ownership, completed scalar runs, supported visualization
types, clip bounds, and a maximum of two active exports per user. Annotation
timelines can be rendered as panel labels or captions. Unlabelled shot segments
show their source start time. Empty parent timelines are omitted.

Each submission writes an immutable `snapshot.json` containing configuration,
source file identity, selected result/timeline IDs, and clipped overlay intervals.
The **Data and settings** download exposes that snapshot. Later annotation edits
or result deletion do not alter a queued render. **Reuse settings** creates a new
snapshot from the currently available results; it is not a byte-for-byte replay
of an older snapshot. Reproduction of an old snapshot also requires its original
source video and a compatible FFmpeg/font environment.

Scalar values are held for their recorded `delta_time`, bounded by the next
sample. When absent, the median positive sample gap is used (0.5 seconds for a
single sample). Missing/non-finite samples display `—`; values are not
interpolated or extrapolated across long gaps. Labels use their explicit time
intervals, overlapping labels share a row, and caption gaps remain blank.
Long text is wrapped and ellipsized: two lines per panel row, three per caption.

FFmpeg seeks/trims the original video, handles rotation and pixel aspect ratio,
burns a generated ASS overlay and streamed RGBA spatial graphics, and produces H.264/yuv420p MP4 with AAC audio when
present and requested. It retains variable frame timing rather than rebuilding
timestamps from an average frame rate. Overlay event timing has ASS's 10 ms
resolution. Spatial graphics update at 25 FPS while the base video's frame timing
is retained. Normalized decoded-frame geometry is transformed for display rotation
and sample aspect ratio. Existing analysis timestamps are used as stored; historical decoder
timing inaccuracies cannot be repaired by this export layer.

Preview and export use the same renderer and output settings. Preview limits
the interval to five seconds. Cancellation stops the encoder and removes its
partial MP4. Download and playback endpoints require the video's owner; playback
supports HTTP byte ranges. No output is published into the public media folder.

## Operations and retention

Artifacts live in `DATA_CACHE_ROOT/visualization_exports/<job UUID>/`. Jobs and
their settings are stored in the database. Errors are logged by the worker;
`ffmpeg.log` inside the job directory contains encoder errors. A worker must have
write access to the shared cache directory. Large exports require corresponding
disk space and worker capacity.

Run cleanup periodically through the installation's scheduler:

```sh
docker compose exec backend uv run --package backend python3 backend/src/backend/manage.py visualization_export_cleanup --days 7
```

This removes terminal jobs older than seven days and their files; active jobs
are untouched. Deleting a video also removes its exports. Cleanup is not
automatically scheduled. Abrupt worker termination can leave a job marked
running; operators should mark such jobs failed after confirming that no encoder
is running, then retry from the saved settings.

## Extension points and verification

`utils/visualization_export.py` validates and freezes selections;
`visualization_spatial.py` adapts stored geometry and current clusters;
`visualization_compositor.py` draws transparent frames, and
`visualization_render.py` streams those frames into FFmpeg alongside ASS text.
`utils.spatial_tracking` provides conservative geometry matching. Snapshot version
2 adds spatial records/cards; version 1 scalar and caption snapshots still render.
The active-speaker model currently buffers decoded video in memory, so long/high
resolution source videos can require substantial inference-worker RAM.

```sh
docker compose exec backend uv run --package backend python3 backend/src/backend/manage.py test backend.test_visualization backend.test_visualization_spatial
docker compose exec inference_ray .venv/bin/python -m unittest discover -s inference_ray/tests -p test_spatial_plugins.py
```

Tests cover ownership, clip validation, immutable snapshots, cancellation,
duplicate task delivery, retention on deletion, byte-range downloads, sampled
data gaps, annotation overlaps, and real FFmpeg output with audio and silent
portrait input. Spatial tests cover edited clusters, geometry recovery, clip offsets,
cuts/gaps/ambiguous matches, per-face results, speaker alignment, thumbnails,
rotation, actual FFmpeg spatial output, and cancellation. Inference tests cover
empty frames, track duration in seconds, normalized pose output and missing models.
