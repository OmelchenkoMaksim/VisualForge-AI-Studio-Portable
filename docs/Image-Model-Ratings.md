# Image model quality ratings

The model picker and its three meters use `Engine.rating` in `App/server/engines/image_registry.py`, exposed by `image_preflight.py` and rendered by `image-studio.js`. A model without reviewed evidence has no numeric score. The UI shows the review date and sample size in Russian and English.

The scale is **0–10; 10 means a clean, convincing photograph from a quality DSLR or mirrorless camera**. These are subjective ratings of our installed quantizations and local samples, not published benchmarks or ratings of every checkpoint in a model family.

| Axis | What to inspect |
|---|---|
| Picture quality | Convincing light, materials, depth and photographic appearance. Keep an explicitly requested illustration in its own medium. |
| Fine detail | Useful, coherent texture and legible details; synthesized sharpness alone does not earn a higher score. |
| Freedom from distortion | Anatomy, geometry, counts, unwanted additions, spelling and consistency. Inspect occluded parts honestly rather than claiming they were checked. |

`overall` is the mean of `picture`, `detail` and `consistency`, rounded to one decimal. A tenth is a display convention, not measurement precision. Small differences do not establish superiority on another task. Editing, reference capacity, alpha, modes and resource cost are assessed separately in the model's strengths/limits and [card](models/image/README.md).

The [2026-11-05 real UI audit](VisualForge-Image-UI-Audit-2026-11-05.md) contains the dated scores, settings, successful and failed images, Gemma defects and unqualified cases. The main timed run is complete: all 18 ordinary modes, with Composition results recorded separately from the rating sample. CPU tests and fake backends establish software contracts; they cannot establish image quality.

**2026-11-06 addition:** [FIBO 2.0 Dev INT8](models/image/image-fibo2-dev.md): **9.0 overall** (picture/detail/freedom from distortion: **9/9/9**), six ordinary outputs covering all four exposed modes. The small sample includes a natural fox photograph, a portrait with correct hands/three apples/"ЛЕС", recolouring, reference fabric transfer, a full-body four-source new scene and watercolor with x2 enlargement. Bounded edits altered some garment/mug geometry; this rating does not promise exact preservation, six-person identity or universal 9/10 performance. It is not a controlled same-seed comparison with the earlier models.

For a refresh, start with the model card. Keep the original request, exact weights/profile/seed, prepared English prompt, selected choices, ordered sources and actual saved image. Exercise the exposed modes and representative photographic, lettering, counting and illustration requests. For alpha, inspect the PNG channel and browser compositing; for masked edits, compare the unselected pixels. Include failures and distinguish a bad Gemma caption from the image model ignoring a faithful one. Compare settings/seeds before making causal claims, and retain the date/scope of older measurements. Obtain the required approval before a new GPU run.

Update the registry scores/date/sample count, relevant assessment strings, model card and dated evidence together. Rebuild string catalogs after editing them, check the actual RU/EN picker/meters, and run the affected CPU/JavaScript checks. Do not assign a score to an unexecuted model or treat more references/resolution as proof of better quality.
