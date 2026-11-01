# FreeVideo H3 INT8: legacy engine reference

This engine remains in `App/server/video/video_registry.py` as `freevideo-h3-int8`. It is a supported legacy adapter, not the
recommended starting point for new Video integration. Use [the Video architecture](VisualForge-Video-Architecture.md) for shared
job ownership, GPU admission, cancellation, saving and scene assembly.

## Current source contract

* `video_adapters.py` selects `freevideo_cli`; `video_pipeline.py` drives the isolated CLI and assembles the resulting scenes.
* The registry pins FreeVideo v0.4.19, commit `0825d1ba649c433e7cd6474e5cdd4b638e74061e`, and prepared INT8 model revision
  `da37681f5a1f7015fdb63482142b865111b687d3`. Download/dependency pins belong in the catalogs, not a copied installation plan.
* Modes: `t2v`, `i2v`, `first_last`, `characters`; 512×288 at 24 FPS. The exposed frame requests are 39, 243 and 1440;
  this is not a promise that every mode/quality/duration combination is valid. Consult registry validation for combinations.
* T2V offers Preview, Standard and High; the other modes use Preview. Individual diffusion scenes use the H3 `17n+5` frame grid;
  an assembled film's final duration is a separate contract. Never apply LTX/Wan frame alignment to this adapter.
* H3 has no entry in the active Gemma prompt-enhancer guides. Do not imply it has the per-engine rewrite or clarification paths
  of the newer engines. Alternative precision profiles, arbitrary resolutions/FPS and upstream-only adapters are not exposed.

## Retained evidence and limits

The initial **2026-10-24** qualification used RTX 5070 Ti 16 GB, synthetic inputs, 512×288, 24 FPS and seed `2026102402`.
Nine jobs covered short T2V, I2V, first/last frames, different quality budgets, a Cyrillic path and 10.125-second T2V.
Representative whole-device VRAM samples and wall times were:

| Job | Seconds | Sampled device memory, MiB |
|---|---:|---:|
| T2V, 39 frames, 8 steps | 105.2 | 14722 |
| I2V, 39 frames, 8 steps | 75.2 | 15098 |
| T2V, 243 frames, 8 steps | 123.0 | 14606 |

These are historical observations, not current benchmarks: loading/cache states differ, and approximately one-second device samples
include desktop use and may miss peaks. MP4 decoding, range serving, cancellation and Image/Video exclusion were checked in that audit.
Later one-minute/two-character evidence and its unverified limits are summarized in the [user guide](VisualForge-User-Guide.md#status-and-limitations).

The former preparation, cache-download and staging reports were retired from the working tree. Their full tables and deployment diary
remain in Git (`git log --all -- docs/video-validation-ru.md`); they do not describe the current installation. No new GPU run was made
for this documentation cleanup.
