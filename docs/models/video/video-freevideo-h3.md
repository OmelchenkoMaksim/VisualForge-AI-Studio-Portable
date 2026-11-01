# FreeVideo MiniMax H3 INT8 (`freevideo-h3-int8`, Video): legacy

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Adapter v0.4.19, commit `0825d1ba649c433e7cd6474e5cdd4b638e74061e`; model revision `da37681f5a1f7015fdb63482142b865111b687d3` (`App/server/video/video_registry.py`). **[code]**

A supported **legacy** adapter (FreeVideo v0.4.19, prepared INT8 weights) driven through an isolated CLI. Its limits differ from the newer engines; the current task determines whether it needs work. Studio score **4/10**. Marks: [the rules](../README.md).

## What it is for
Short 512x288 clips with sound and cloned voices when nothing else is wanted. Modes: `t2v`, `i2v`, `first_last`, `characters`.

## Strong at / Weak at
* Strong: it makes sound and works with cloned voices; a one-minute two-character film was made once (summarised with its unverified limits in the user guide) **[recorded, scoped; the first qualification is dated 2026-10-24]**.
* Weak: **512x288 only**, a small and soft picture; no Gemma rewrite guide (H3 has no entry in the prompt-enhancer guides, so no per-engine rewrite or clarification) **[recorded]**.

## Modes and settings
| Item | Values |
|---|---|
| Size, fps | 512x288, 24 |
| Frames | requests 39, 243, 1440; scenes use the **H3 `17n+5` grid** (never apply LTX or Wan alignment); the final film length is a separate contract |
| Quality | T2V: preview, standard, high; other modes: preview only |
| Not offered | other precisions, arbitrary sizes or fps, upstream-only adapters |

## Measured here **[recorded]** (RTX 5070 Ti, 2026-10-24, historical)
T2V 39 frames 8 steps 105 s (whole-card samples up to 14.7 GB); I2V 75 s (15.1 GB); T2V 243 frames 123 s. Cold and warm loads differ and sampling every second can miss peaks: not benchmarks.

## Not verified yet
Current speed and quality of every combination (registry validation decides which are valid), prompt style for H3 (no guide exists). **[unknown]**

## Where in the code, deeper reports
`App/server/video/video_adapters.py`, `video_pipeline.py`, `video_registry.py`. Report: [FreeVideo-H3.md](../../FreeVideo-H3.md); catalogue `docs/freevideo-model-catalog.json`.

## Sources and refresh
The FreeVideo project page on GitHub and the Hugging Face page of the prepared weights. Refresh only if the engine is revived.
