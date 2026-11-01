# Composition: automatic Image production

The **Композиция / Composition** button in Image opens an automatic creative workspace. The ordinary model/mode form remains available through **Обычный режим / Standard mode**. Composition is an orchestrator, not another checkpoint or an entry in `image_registry.ENGINES`.

The person describes the desired picture, optionally adds materials, chooses **1–5 variations** and optionally square/landscape/portrait format. There is no Ultra switch: supported quality profiles always use the maximum-quality policy. Gemma chooses models and an ordered chain for each variation, using current readiness and the researched Image model guides. It may use one network or several; more processing is not inherently better.

## Materials and variations

| Group | Limit | Meaning |
|---|---:|---|
| Heroes | 6 | People/characters to carry into each result |
| Places | 4 | Location, landscape or interior |
| Objects | 8 | Vehicles, devices and other supplied things |
| References | 4 | Scene/composition base or style/content donor, as described by the person |

All groups are optional. Each shows its existing photos plus **one empty slot** until full. Labels explain who/what a photo represents. Intake uses the shared Image converter; one upload is at most 100 MiB, all stored draft materials at most 200 MiB. Drafts, labels and descriptions persist across reloads; a draft nobody touched for seven days (a started composition touches its draft) and run folders beyond the newest 30 are removed when the workspace opens, since every finished image also lives in the ordinary Image output folder and history. A start must name an existing draft: without one it used to make an empty draft and compose without the person's photos. No voices/video/models are uploaded here.

Every variation must use every supplied material somewhere in its chain; missing material IDs invalidate the plan. Later stages take the previous result first, so they cannot accidentally discard the preceding stage by starting from unrelated photos. Model limits still apply: Klein/Qwen 2.1 up to ten inputs, Edit 2511 three, Z-Image text generation without references or one-source Detail/Tile. Large material sets require stages, with **at most 12 per variation**. Identical variation plans are rejected. This validates routing, not whether a network actually drew every person/object correctly.

The reference's role is free: the request can make it the receiving scene or take only its style/content. The planner selects its position in each source list. First-image aspect controls edit outputs (Klein and Qwen 2.1 draw on the shape of the first picture and normalise it to about one megapixel), so the first picture of a step is brought to the canvas by `composition_media.prepare`: a picture whose shape is within 6 % of the canvas's is stretched to it, any other is fitted whole and put over a blurred copy of itself (a picture with transparency over a transparent frame, so that its alpha survives), and the step's prompt gets one sentence saying what the margins are (only then). The first version only ever shrank a picture onto a flat grey frame: a 1152x768 photo on a landscape canvas was left with 69 % grey around it (measured). The blurred margin is the technique that Klein turned into a real scene in the Director's keyframe tests of 2026-11-01 ([model card](models/image/image-flux3-klein.md)); flat grey was never tried. Original uploaded files remain unchanged. Prepared PNG inputs are bounded for the ordinary Image route.

## Quality and execution

- Qwen 2.1 uses 40 steps; Edit 2511 uses Standard 40/CFG 4, without automatically enabling its community LoRA; Klein retains its 16-step distilled preset; Z-Image retains its trained preset. Gemma cannot select a speed profile.
- Native high-resolution generation and x2/x4 AI Upscale are available where supported; the finished picture may not exceed 16 megapixels (native megapixels times the upscale squared: 4K is the largest that was ever run), and a Qwen 2.1 stage with more than three inputs stays at one megapixel because that model gives every picture the selected pixel budget. The planner chooses them when useful; extra resampling can harm writing, identity and alpha edges, so it is not forced onto every stage. Detail/Tile retain their actual required x2 path.
- A plan that fails validation is sent back to Gemma once with the exact reason (which variant and step, what is allowed), not a generic sentence. Once the plan exists the panel shows "stage n of N; about M minutes in all" (`estimate_minutes`: a rough sum from the model audits of 2026-10-29, never compared with a real composition).
- Each stage calls **`/api/generate`** (a busy answer, 409, is waited out for up to ten minutes as in the Director), then status/result/cancellation routes. Existing per-model prompt preparation, component policy, GPU admission, saving, Image history and event identities all apply. No direct worker/model calls or automatic model downloads.
- The coordinator's `composition` reservation lasts across planning and all stages. Authenticated loopback requests use the same `X-VisualForge-Run` mechanism as the Director, with a separate private token. Other modules cannot enter the gaps. Child operations inherit the composition event scope.
- Local Stop and global Stop stop planning/preparation and the active image. A late Gemma answer cannot enqueue an image after cancellation. Existing completed variations remain visible after a later failure or Stop.

The ordinary Image output folder is respected. Individual Image steps retain their normal history/saved outputs. Composition additionally keeps final PNGs and provenance in `UserData/Composition/runs/<id>/`, with immutable material snapshots, original request, checked plan, steps, child job IDs and final result metadata. Its panel provides previews, PNG downloads and the latest 30 runs. Reopening after a studio restart marks unfinished work interrupted rather than pretending it resumed.

## Implementation and verification

`App/server/composition/`:

- `composition_plan.py`: request limits, ready-model capabilities, plan validation and fixed quality policy.
- `composition_media.py`: grouped materials, labels, snapshot paths and full-frame input fitting.
- `composition_brain.py`: bounded batches of material observations, then Gemma planning with one retry; `GemmaTask`, English model instructions and original-language lettering.
- `composition_studio.py`: Image calls through the Director's existing loopback transport.
- `composition_manager.py`: sequential run, reservation, durable status/history and cancellation.
- `composition_routes.py`: same-origin bounded endpoints for profile/draft/materials/start/status/result/history/cancel.

The bridge initializes the manager lazily when the workspace is opened, so importing it for tests does not create composition data in the installed studio. UI: `App/frontend/composition-panel.js` / `.css`; strings: `App/strings/composition-strings.json`.

**CPU evidence (2026-11-01):** 21 focused plan/media/manager/bridge tests passed with fake Gemma/ComfyUI, including the real route chain Klein → Edit 2511, maximum profile dispatch, grouped intake, distinct variations, material coverage, source snapshots, aspect fitting, partial results, interrupted runs, authenticated reservation and late-answer cancellation. The related Image, prompt-manager, Gemma, GPU admission, event/generation identity, bridge, localization and Director regression checks also passed after a dynamic localization-key fix (328 distinct tests overall, with affected checks rerun). Ruff F/B/E9 for Composition and its tests, F/E9 for bridge/GPU/history integration, JavaScript syntax, generated string validation, source guard and whitespace checks passed. These establish software behaviour, not generated quality.

**Audit of 2026-11-02 (CPU and a browser check, no GPU):** found and fixed the grey frame above, the silent start without a draft, the generic validation message, a refused start on a momentary 409, no limit on the size of an upscaled picture, a GET for a material that made an empty draft, a run file that could not be read stopping the workspace from opening, no clean-up of drafts and runs, and no sign of progress or duration (panel: a fake running status showed "stage 3 of 4; about 9 min"). 10 tests were added (31 in all). Not changed on purpose: a variation that fails ends the composition (the finished ones stay), because a failure of the card would repeat for the rest. Not run: any real Gemma plan or image of a composition, so the real effect of the blurred margin on Qwen models, of the Gemma plans on the 16-megapixel and one-megapixel rules, and of the estimate is **[unknown]**.

**UI evidence / remaining qualification:** a browser CPU fixture with synthetic images and fake model responses verified two hero uploads with exactly one next slot, three results from six sequential Klein/2511 stages, planning cancellation without another queued image, RU/EN changes, reload persistence, history previews and return to ordinary Image. No browser console errors were observed. The six-hero limit was exercised in CPU tests; six browser uploads were not run. Real GPU planning, likeness, complex multi-person placement, lettering, alpha, refinement/upscale choices and output quality still require a separately approved timed sequential run. Do not treat successful queueing or these CPU checks as proof of artistic quality.
