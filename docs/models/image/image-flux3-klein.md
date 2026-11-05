# FLUX.3 Klein INT8 (`flux3-klein-int8`, Image)

Status: **researched; all five modes exercised with real Gemma through the UI on 2026-11-05**. Publisher/community research: **2026-11-01**, external-VLM instructions refreshed **2026-11-05**. Small local sample; no claim that Gemma matches native PUP. [Evidence rules](../README.md).

## Our version [code]

Distilled 5.6B image transformer, **INT8 ConvRot**, Qwen3-VL-4B encoder, FLUX.2 VAE; Euler/simple, **16 steps, CFG 1**, RGB. Not FLUX.2 Klein or the 50-step/CFG-3.5 Klein Base. Package: black-forest-labs/flux-3-image-klein-comfy, revision `feef60b0aaf63a4773ace96636ebbcf43b881312`; exact files/hashes and recorded licence (FLUX Kommunity License) remain in `App/config/component-catalog.json`.

The original Qwen3.5 9B upsampler remains in the package/stored templates. Studio requests now use **one automatic Gemma pass**; native upsampler nodes are removed before submission, and preflight checks that pruned graph. An unused upsampler file/node cannot disable this path. Its expansion switch is hidden; legacy `expand` flags cannot schedule a second rewrite. No weights were downloaded or removed.

## Use and limits

- Best fit: text-to-image, editing/composition from ordered references, layout, Director keyframes. Convincing materials/light; two/three-source compositions and swapped-box positions worked in the earlier clock tests. Recognisable heroes appeared in Director keyframes. **[recorded, 2026-10-29 and 11-01]**
- Failures: incorrect counts/spelling, recolour spill to adjacent parts, composition changes at native 4K. Ten-reference routing does not establish ten-reference image quality. Faces across several people, Chinese lettering and complex layouts remain unqualified. **[recorded/unknown]**
- Expect a candidate to inspect. Do not promise pixel locks, exact identity/counts/text, alpha, hard masks or higher resolution fixing semantics. Expansion cannot guarantee good anatomy.

## Modes and requests

The person writes ordinary language. Hidden instructions in `klein_prompts.py`, guide **`klein-distilled-2026-11-01-v1`**, produce English, preserving requested Russian lettering verbatim in quotes. No compulsory camera list, quality-tag filler, new logos or inferred hidden identity/ethnicity.

| Mode | Actual studio inputs/settings [code] | Gemma instruction selected from research |
|---|---|---|
| Prompt | No pictures; square/landscape/portrait, native 1 MP / about 2K / up to 4096-pixel longest edge | Full scene caption, usually 100–350 words: medium, setting/light/framing, subjects and spatial relations. Honour an explicit illustration/style; never pad or discard requested facts. |
| Single | One source, normalised to about 1 MP; first input sets proportions | For bounded changes, usually 50–150 words: identify target, specify the edit and consequences, then briefly what stays. A global transformation names its scope; removal names the fill. Avoid redesigning the whole source. |
| Reference | Source + donor; first image sets proportions | Local transfer uses edit prose; a new composition uses a generation request assigning source/donor roles and what is newly generated. Match scale/light and preserve requested identity/accessories. |
| Multi | 3–10 ordered images, 100 MiB total; first sets proportions | Same task-shape choice, every reference's role by ordinal, no renumbering or identity merging. Use enough detail for all sources; do not drop inputs to meet a word target. |
| Layout | Text + 1–8 rows, `[top,left,bottom,right]` on 0–1000 grid; native resolution as Prompt | Global caption with each existing `<id>` once and positions consistent with boxes. No generated JSON/coordinates/new IDs. Code appends the validated original table once. Source/target box editing is not exposed. |

AI Upscale x2/x4 uses Real-ESRGAN after generation, separate from native size; native 4K disables it. Another variant is a Single edit of the previous result, not a numeric similarity guarantee.

The model card stresses full captions; BFL's **actual I2I system instruction is task-specific**: focused edit prose for a source retained with bounded changes, generation prose for newly composed references. Our guide preserves this distinction. References anchor content, not immutable structure; boxes guide placement without clipping objects. The immutable-table safeguard and avoiding guessed identity are studio adaptations. **[sources/code]**

**Illustrative, not a tested Gemma answer:** “make the jacket green” becomes a jacket edit with a preservation clause; “the woman from picture 1 at the harbour from picture 2” becomes a composed scene assigning both roles. The Director already renumbers its draft picture numbers once, when requesting Klein; the enhancer must not renumber again.

Clarification is mode-specific: ask only about a material missing edit target/source role/visual intention. Source appearance, selected boxes and technical settings do not need a questionnaire. One question with 2–8 numbered options, multiple selections or 0; a second only for explicit conflicts, never a third. Existing API/Director calls without `clarify: true` stay noninteractive. `GemmaTask` supplies Stop, Text ownership and `think=False`. Klein uses temperature 0 for both steps, adapting the publisher's greedy-upsample recommendation to Gemma; equivalence to its trained Qwen upsampler remains unproven. **[code/sources]**

## Director reference findings [recorded]

**Giving the pictures (Director findings, 2026-11-01, [recorded]):** output proportions follow the first picture. With a landscape place photo first the result was 1248x832 and a true wide shot (the person small); with a blank landscape canvas first and the hero second, a new landscape image 1376x752, composition good, **clothes not kept** (a coat for a denim jacket); with the portrait hero **padded onto a landscape canvas with blurred sides** and the instruction "extend to the left and right, keep the woman exactly as she is" a landscape close-up 1376x752 with the same hat, scarf and jacket. Prefer: place first; hero alone: padded canvas.

## Measured here **[recorded]**
| What | Result | Date |
|---|---|---|
| Prompt, expansion on, 1024 | about 161 s cold | 2026-10-29 |
| Single recolour / Reference / three references | 25 s / 20 s / 28 s | 2026-10-29 |
| Layout A/B | about 11 s warm, 162 s after a restart | 2026-10-29 |
| Native 4K | 278 s, whole-card peak 15,924 MiB of 16,303 | 2026-10-29 |
| Keyframes of the Director | first 160-190 s (cold), then 16-35 s each (about 1.2 MP) | 2026-11-01 |
GPU claim: 12,000 MB (15,000 for 4K); the card must be free of other apps.

## Verification and gaps

**2026-11-05 [GPU/UI]:** all five modes completed with the installed INT8 profile and real Gemma. Prompt produced convincing winter fur/snow but two mushrooms for one. Single removal and a two-source mug replacement retained the mushrooms despite faithful prepared text; submitted graph/inputs/text were checked, and the cause is unresolved. Three-source Multi successfully composed a summer terrace, the fox, a blue mug and a scarf with the third source's stripes. Native 2048-square Layout placed a red teapot left and blue mug right on one table; boxes were soft size anchors. A separate portrait kept the full head, three apples and readable Russian "УТРО", with plausible visible fingers and skin. This qualifies those samples, not arbitrary faces, exact identity or all edits. Timings/settings and further tests: [ongoing real audit](../../VisualForge-Image-UI-Audit-2026-11-05.md).

**2026-11-05 [GPU/UI, Composition]:** two hero/place variants kept the full figure after passing the original intent through the final enhancer, with approximate facial likeness. Harder four-source receiving-scene tests kept the woman, lake, mug and teapot but sometimes cropped feet/legs, copied a seated pose or lost veranda geometry. A real blurred-band defect came from prepared aspect padding and disappeared after choosing an already fitting first input. A text-only fidelity pass restored the standing instruction and the next image stood; legs were still cropped. Five style-only donor variants then kept watercolor paper/washes, a whole fox, one mushroom and readable "ЛЕС" without the donor's robot/cactus/old lettering. Composition/framing differed, though planned top-down was only moderately elevated. Keep caption/adapter defects separate from the network ignoring faithful instructions. These are additional Composition samples, outside the seven-image ordinary-mode rating sample.

**2026-11-05 [sources]:** BFL GitHub `src/flux3/pup_prompts_external_vlm.json` confirms external-VLM preparation, task-specific edit/generation voice and reference ordinals. It also recommends plain photograph openings instead of synthetic-realism praise. These are publisher recommendations, not an established cause or cure for the failed small edits above.

**2026-11-01 [CPU]:** `tests/test_klein_prompt_manager.py` covers five modes, ten ordered thumbnails/latent inputs, context budget, lettering, missing/duplicate/invented IDs, immutable boxes, no second native rewrite, provenance and clarification conflicts/stale bindings. Shared Image/clarification regressions also ran. Fake-model checks establish routing/safeguards, not semantic adherence or improved pictures.

**2026-11-01 [UI, fake backend]:** actual Image page and bridge/manager with fake Gemma/GPU/ComfyUI: Klein selection, hidden expansion switch, locked original request, conflict-only second question, Enter submission and ready-without-question path. Refreshed request guidance was checked in RU/EN. No real inference. Disposable fixture/screenshot: C user Temp `vfr-tests/klein-browser-preview.py`, `klein-clarification-ui.png`.

**[unknown]:** Gemma versus native-Qwen3.5 quality, general clarification reliability, exact faces/multi-person identity, Chinese text, complex layouts, ten-reference quality and padded outpainting on other subjects. Investigate the two failed local edits with controlled prompts/settings; compare swapped Layout boxes and repeated seeds before generalising. The 2026-11-05 real samples qualify the listed scenarios only.

## Code and sources

Code: `App/server/engines/klein_prompts.py`, `image_prompt_enhancer.py`, `image_registry.py`, `image_preflight.py`, `workflow_builder.py`, `flux_inputs.py`; workflows `App/workflows/flux3-klein-int8/`. Reports: [earlier generation audit](../../FLUX-3-Klein.md), [prompt-manager lifecycle](../../VisualForge-Prompt-Managers.md).

**Publisher research, 2026-11-01:** Hugging Face — black-forest-labs/flux-3-image-klein and `-pup` cards, `system_prompts.json` T2I/I2I/box contracts (reviewed at mutable main); BFL documentation — “FLUX 3 Image Klein”, “Bounding boxes with FLUX 3 Image”. An alternative LLM is supported for prompt preparation; this does not prove Gemma matches the trained upsampler. New w4a8 announcements do not change our pinned INT8 package.

**Independent experience, same date:** Reddit r/StableDiffusion “Flux 3 Klein as edit model” (1x6u9vt); comments under r/comfyui “our launch-day example workflows were off” (1x8qn3d; Comfy author's announcement is integrator evidence, replies are independent); r/TopologyAI “FLUX.3-Klein for prop extractor” (1x7e1ul). Mixed anatomy/detail/reference outcomes remain even with expansion. The extractor author distinguishes detection/crop failures from model failures. Use these anecdotes to guide inspection, not invent universal prompt rules.

**Independent measured report:** strata→signal research, “FLUX 3 Image Klein, run again with its maker's prompt upsampler” (published 2026-10-27): one blind judge preferred expansion for 5/6 game-art subjects, BF16/RTX 3090. Small sample, different quantisation/style; no proof for our Gemma/INT8 path.

Use these findings as the foundation. Refresh the affected fact on checkpoint/encoder/adapter/guide changes or a concrete failure, using site plus titles/IDs above. Compare with our exposed modes and update the date/scope after appropriate CPU/UI and approved real tests. Keep old measurements dated.
