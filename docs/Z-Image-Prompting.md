# Z-Image Turbo: hidden prompt preparation

Research date: **2026-11-01**. Scope: the installed **Z-Image Turbo INT8**, Qwen3-4B FP8 encoder, low-denoise detail workflow,
Union Lite 2602 and Tile Lite 2601. Base, Edit and Omni are separate checkpoints. No model was downloaded or run for this research.

## Publisher evidence

* In the [official prompting discussion](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo/discussions/8), Tongyi-MAI recommends detailed
  prompts expanded by an LLM and explains that Turbo does not use negative prompts. The same discussion distinguishes the online demo's
  token budget from locally configured limits: long descriptions should remain purposeful, not grow without a bound.
* The team's [actual prompt-enhancer instruction](https://huggingface.co/spaces/Tongyi-MAI/Z-Image-Turbo/blob/9baf60ec91e2546b60200b2fdb44ee076a19d353/pe.py) prioritizes
  the original subject, number, action and specified properties; then composition, illumination, materials, color and depth. It handles
  intended lettering verbatim in quotes and rejects vague praise/quality tags. We retain that mechanism, with a shorter instruction
  and explicit safeguards against unwanted additions. Its dramatic persona is unnecessary for our manager.
* [ComfyUI's official refinement workflow](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/utility_z_image_turbo_2k_upscaler.app.json)
  recommends a detailed caption of the input to keep refinement coherent, particularly at higher denoise. This supports captioning the
  existing image rather than sending a vague request to improve quality. Our installed workflow and denoise limits remain unchanged.
* The [Union/Tile publisher card](https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1/blob/5155fc56d17821007d6f62ac192c09e0f0e72016/README.md)
  recommends detailed prompts for stability, distinguishes the distilled eight-step patches, documents inpainting, and assigns Tile
  to enlargement. Gray is supported by 2602. It does not prescribe a separate textual syntax for each control type or inpainting:
  the mode-specific caption rules below are our adaptation to the installed graphs, not a claimed publisher recipe.

## Independent experience

The [January prompting discussion](https://www.reddit.com/r/StableDiffusion/comments/1qa16te/zimage_turbo_prompting_questions/)
contains first-hand reports favoring coherent natural-language descriptions, explicit relations and a scene summary followed by details.
Participants disagree about token priority and practical length. This supports clear structure, not a universal minimum word count,
special weighting syntax or a guarantee that longer always works better.

In [the ComfyUI discussion](https://www.reddit.com/r/comfyui/comments/1q9cwr2/experiences_with_prompting_zimage_turbo_what/), users report
limited viewpoint control from camera vocabulary and better structural control from an actual picture/map. A separate
[prompting thread](https://www.reddit.com/r/StableDiffusion/comments/1qm3ko3/how_to_prompt_zimage_turbo_looking_for_advice/)
describes subject-heavy prompts drifting into close-ups and suggests explicitly describing the surrounding environment for wider views.
These are anecdotal observations, not controlled benchmarks. We use them to avoid unnecessary camera jargon and describe visible framing;
we do not add LoRAs, replacement encoders or supposed magic words.

## Instructions chosen for Gemma

`App/server/engines/zimage_prompts.py` holds a shared model instruction plus one short instruction per mode and a separate note for
each supported control type. Version: `zimage-turbo-2026-11-01-v1`.

| Mode | What Gemma receives | What it should write |
|---|---|---|
| Prompt | Original request and selected canvas; no selected uploads | One completed scene: subject/action/relations first, then useful framing, light, palette, texture and requested medium. |
| Detail | Request and source thumbnail | A grounded caption of the same picture and compatible finer detail; retain subject count, crop, colors and medium. |
| Control | Request, source/map thumbnail and actual `canny` / `gray` / `prepared` selection | The desired finished scene following structural guidance. Canny supplies outlines; gray supplies luminance; a prepared map is already prepared, with no promised depth/pose extraction. |
| Inpaint | Request, source and validated mask thumbnails, in that order | Desired contents of the editable region with surrounding context for perspective and light. For removal, describe the replacement background. The mask is not a second scene. |
| Tile | Request and source thumbnail | A detailed caption of the existing scene, with finer compatible materials and unchanged composition/identity/medium. |

For a simple idea the instruction suggests about 80–160 English words, without mandatory padding. Detailed requests retain their facts.
Gemma can choose photographic detail when appropriate, but does not add camera bodies, sensors or lenses to every request, force a photo
style on illustrations, or invent extra subjects and writing. It writes model-facing prose in English and preserves intended Russian
lettering in its original language. `think=False` follows the existing Video manager; no separate reasoning stream is requested.

For example, the owner's fox idea should become a visible moment: a fox lowering its nose toward a mushroom exposed in disturbed snow,
with the relationship readable in the frame and a conifer forest around it. Lighting and fur/snow texture can clarify that picture.
This is a design example, not a recorded Gemma answer or an evaluated image.

## Runtime and verification limits

The existing `/api/generate` runs the separate Image `PromptEnhancer` automatically for these five modes, after request, component and
image validation. It uses `GemmaTask` for Text/GPU admission and Stop; it never calls Ollama directly from a route. The global submission
lock is released during the answer and reacquired for Image claim/upload/queue submission. The page enables the clarification flow below
automatically, without a prompt-engineering switch. Existing API clients and the Director retain a noninteractive enhancement path.

The manager uses oriented thumbnails up to 1024 pixels, keeps source/mask order, bounds output to 5000 characters and retries malformed
answers once. Quoted request text and recognizable unquoted uppercase labels receive exact-text checks; Russian prose outside quoted
literals is rejected. Arbitrary unquoted lettering and lettering read from a source picture still depend on Gemma's interpretation:
the checks are not a complete semantic or OCR verifier. Failed/cancelled preparation does not enqueue an image or silently use a fallback.

History retains `original_prompt`, `enhanced_prompt` and `prompt_enhancement` (engine, mode, Gemma model and guide version). Native
FLUX/Qwen expansion remains distinct and unchanged. Music and Voiceover remain templates, and other Image engines await their own research.

## Optional clarification inside the request panel

The page sends `clarify: true` to `/api/generate` for Z-Image. Gemma receives the researched model/mode instruction plus a separate
`CLARIFICATION_RULES[mode]` and bounded JSON decision contract. If the scene is sufficiently specified it returns the finished caption
immediately: the fox/mushroom/snow/forest example needs no questionnaire. Missing camera hardware, lighting vocabulary or optional polish
alone is not a reason to stop. The decision to ask is model-dependent, not a locally verified measure of ambiguity.

If clarification is useful, the page shows one question with 2–8 numbered, request-specific options next to the original prompt. The question
and labels follow the UI language; selected values remain English instructions. Prompt choices concern the desired picture; Detail/Tile
normally have enough context in the source; Control never asks for an already selected preprocessor; Inpaint may clarify an unclear local
edit goal. These are separate mode instructions, not a universal questionnaire. The user answers `1, 3`, `13`, or `0` to delegate remaining
choices and proceed. Bad numbers are corrected in the same panel without another Gemma question.

`App/server/prompt_clarification.py` enforces **at most two displayed questions**. A nonempty option group declares mutually exclusive
alternatives for one subject/property; different groups and ungrouped additions can combine. Compatible first answers go straight to final
captioning. Only selecting several options within an exclusive group produces a second question, containing just the disputed options;
uncontested choices remain selected. After the second answer there is no question-producing branch. Any remaining conflict uses the last
selected number for its group, including repeated numbers such as `1,2,1`; omitted disputed groups use their previous last selection.
The page acknowledges the second answer with “Choices accepted. Starting generation!” before requesting the final generation.

The server does not accept a client's question count, choice instructions or group definitions. A random, expiring continuation token binds
the original prompt, exact model/mode, validated images/mask, controls and seed. Source inputs and the original prompt stay visible and locked
through the page flow. Old, changed, concurrent or replayed continuations cannot enqueue a second image. Waiting sessions keep no source
bytes or active Text owner, expire after 30 minutes, and are limited to 32 in memory; restarting the bridge invalidates them. Cancellation
uses same-origin `/api/image/clarification-cancel` with a bounded body and no GPU claim. While awaiting a number, other modules may use the GPU.

History adds the question/selected-label transcript, final English choices, question count and clarification version inside
`prompt_enhancement.clarification`; continuation tokens and image payloads are not persisted. Calls without `clarify: true` never suspend for
human input, preserving Director/automation behavior. Other models have no active clarification pass. Video exposes only an inactive
`clarification_status()` / `prepare_clarification()` scaffold; its existing enhancement and generation routes are unchanged.

CPU tests in `tests/test_image_prompt_manager.py` and `tests/test_prompt_clarification.py` exercise the five modes, control variants,
image preparation, retries, cancellation, literal preservation, actual graph input, admission locking, persisted provenance, multiple choices,
the hard question cap, stale/replayed tokens and the inactive Video scaffold. `tests/image_clarification_test.js` checks the browser flow,
number parsing, safe label rendering, Enter handling and the final acknowledgement. These tests use a fake Text model. **No real
Gemma rewrite or generated-image quality comparison has been run for this guide version**; publisher/community research is evidence for
the instruction choice, not a measured claim of better output. A future approved GPU comparison should hold seeds/settings constant
and check ordinary scenes, requested illustration styles, Russian lettering and source preservation separately for each mode.
