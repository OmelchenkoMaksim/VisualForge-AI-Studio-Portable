# Qwen Image 2.1: capabilities and scoped verification

Audit date: 2026-10-29. Installed profile: `qwen-image-21-uc-fp8`, RTX 5070 Ti 16 GB / 64 GB RAM. This is a small functional audit, not a broad quality or identity benchmark. Test media, state and logs are in C user-temp `vfr-tests/qwen21-audit-20261029`; the owner's output folder remains `%USERPROFILE%\Desktop`. Heavy inference is sequential and uses the installed ComfyUI runtime with `--cache-none`. Timings include preparation, HDD loading and graph execution; warm and cold results are not interchangeable.

## Sources and checkpoint identity

The [official Qwen card](https://huggingface.co/Qwen/Qwen-Image-2.1) and [publisher repository](https://github.com/QwenLM/Qwen-Image-2.1) describe a unified 7B generation/editing model with native RGBA, multiple references and 2K output. Turbo is a separate distilled checkpoint with its own eight-step schedule; the installed FP8 model is the regular checkpoint. The upstream pipeline examples use 40 steps.

The [ComfyUI guide](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1) starts its base workflow at 25 steps, Euler/simple, CFG 1. At CFG 1 the negative branch is skipped. The installed `TextEncodeQwenImage21` accepts 16 slots; the studio exposes the publisher's ten-image scope. It returns a latent on the first reference's resized aspect ratio. Resolution is a pixel budget, not an instruction to crop to a square. A 2048 target gives roughly 4 MP.

The [installed FP8 distributor](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized) identifies the original Qwen weights as its source and packages quantized formats. The UC label does not establish better prompt following or edit accuracy. No adult-content quality testing was performed. The component retains its Qwen Research license metadata.

Two optional official prompt enhancers were downloaded with the owner's explicit approval. Both are INT8 ConvRot Qwen3.5 9B files from [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1), pinned to `0217efd1d9c9452e84f6448903d6e21bfa171ffd`:

| Task | Bytes | SHA-256 |
|---|---:|---|
| T2I | 9,471,072,252 | `9182abae56fe05459840a86d22abd21f972061c92fce032630af680c8c5178d3` |
| I2I | 9,471,072,252 | `32707d01b427e488af252b95c551989aad59f9fec611a694f5db6bde7f0f1f6c` |

Downloads were staged on C, verified, copied to the canonical H model directory, and verified again before removing their temporary partial files. Base weights were not redownloaded. The two optional Components entries are `image.model.qwen21-pe-t2i` and `image.model.qwen21-pe-i2i`; neither is a base-engine dependency. The separate system prompts are imported from the official ComfyUI text/image templates, with source URLs and snapshot hashes in `App/config/qwen21-prompt-enhancement.json`.

## Exposed controls

- Prompt: native generation, no uploaded references. Square/landscape/portrait at about 1 MP or native 2K.
- Single: one source, instruction-based editing and transparent-background extraction. The background-removal button prepares an editable instruction; it does not run inference automatically.
- Reference: source plus one reference; address them as `<image1>` and `<image2>`.
- Multiple references: three to ten ordered images, at most 100 MiB combined. The first image sets output proportions. Each image uses the selected reference pixel budget, so many 2K inputs are expensive.
- Steps: explicit 25 and 40. Former Good/High quality promises and the unqualified 35/50 presets are removed. Historical receipts still display their recorded step counts. More steps are not a guarantee of better composition.
- Optional enhancement: T2I for Prompt, I2I with images for edits. Off by default for this model. Missing/disabled enhancer components do not disable the base model. Explicit alpha/transparency requests bypass enhancement in the UI; an API request forcing both is rejected before inference. Actual checkpoint and expanded caption are recorded in history; the caption is shown as inert user text.
- AI Upscale: one or two Real-ESRGAN x2 passes. This is separate from native resolution. The installed operator processes RGB with the model and resizes the generated alpha channel separately; this does not restore authentic missing detail.
- Negative prompting stays unavailable because the published CFG-1 workflow cannot use it. Turbo, hard pixel locks, ControlNet and layer decomposition are not falsely advertised for these installed weights.

## Confirmed input-alpha bug

`LoadImage` returns RGB pixels and an inverted alpha mask separately. The previous graph wired only RGB into Qwen's reference encoder, silently dropping input transparency before the RGBA VAE. A minimal CPU probe using the installed `InputImpl.VideoFromFile` read a synthetic transparent PNG: image shape `[1,64,64,3]`, separate alpha `[1,64,64,1]`, extrema 0 and 1. This establishes the reader contract independently of probabilistic generation.

Single, Reference and every additional Multi slot now use `JoinImageWithAlpha` before `TextEncodeQwenImage21`. Its input expects the same inverted-mask convention, so a second inversion must not be added. RGB sources become opaque RGBA. The optional I2I enhancer receives the publisher template's RGB vision inputs separately. Input alpha reaching the model does not guarantee that the generated alpha or pixels remain identical.

## Observed UI results

The control scene is a clock, three gears and a CLOCK card generated during the FLUX audit. Additional references are synthetic stripes and a cactus. Seed 424242 was reused for the following checks.

| Test | Observed result | Total time |
|---|---|---:|
| Original Prompt, 25 steps, request RGBA clock/gears/label | Genuine transparent PNG, legible CLOCK and clock numerals. The model placed gears on the clock and omitted the separate paper card instead of following the requested composition. | 144 s |
| Single, recolor only the red body | Green body; recognizable scene, gears and CLOCK card retained. No pixel-lock guarantee. | 31 s |
| Fixed RGBA input, recolor red body/bells blue, 2× | Requested blue clock; generated alpha spans 0–255 in the 2048-square output. One ImageUpscale event. | 23 s |
| Reference, native 2K, no AI Upscale | Convincing woven blue/yellow material on the clock body, face/card readable; 2048-square output from the model, with no upscale child. | 221 s |
| Three inputs, 1 MP, 4× | Scene, pattern and cactus all contribute; cactus placed on the right. Pattern reinterpreted. 4096-square output, two ImageUpscale children. | 56 s |
| Same Prompt/seed at 40 steps | Clock details change, but the gears/card placement error persists. This is not evidence of a universal quality improvement. | 23 s |
| Background-removal shortcut, 25 steps | Clock extracted into an RGBA PNG with alpha spanning 0–255; surrounding cloth/card/gears removed. Fine edges and shadows remain generated, rather than pixel-exact segmentation. | 20 s |
| T2I expansion of the original transparent clock request | 784-word caption, separate gears/card and readable CLOCK, but the caption invented a checkerboard backdrop. PNG alpha 235–255: no genuine transparent region. | 201 s |
| Same T2I request with an added output-format system warning | 531-word caption still demanded the checkerboard. PNG alpha 205–255, again no transparent region. The failed prompt-only workaround was removed. | 37 s |
| RGB poster, T2I enhancement, native 2K landscape, seed 777 | 2304×1536, readable CLOCK STUDIO and ОТКРЫТО, clock/cactus positioned left/right. Clock color/style drifted from the requested red flat illustration. One expansion child, no upscale child. | 93 s |
| Another variant of that poster, I2I enhancement | Source layout and both headings preserved; aged-bronze finish and paper texture changed. Actual mode Single, selected mode Prompt, I2I checkpoint, one input and native 4 MP recorded even after the form was changed to 1 MP / enhancer Off. Output 2496×1664: edits normalize the first source to the selected pixel budget, rather than locking its exact dimensions. | 294 s |
| First cancellation attempt, RGB Prompt + PE + scheduled 2× | The UI automation did not dispatch a cancel request; the job completed and is honestly recorded as done, not cancelled. This is not evidence of an application cancellation failure. | 107 s |
| Confirmed cancellation, same workflow | The button was activated by keyboard; `/api/cancel` returned 200. Image, ImagePromptExpansion and scheduled ImageUpscale all ended cancelled; shared busy state cleared. The child durations are graph scopes, not evidence that upscale had executed. | 15 s |

The two failed alpha probes establish a specific enhancer limitation. The UI now disables and unchecks enhancement for explicit RGBA, alpha-channel and transparent-background wording, with a reason. The API independently rejects forcing it. Ordinary transparent-glass descriptions and a literal ALPHA title remain allowed. Direct generation and background removal already produced real alpha, so the working native route is retained. This does not claim that every language or paraphrase can be recognized automatically; users should review the expanded caption for intent drift.

The source-code regression suite covers input alpha wiring, ordered references through slot ten, native resolution on both generation and editing, optional-enhancer independence, the transparency restriction, history and localization.

## Final verification

Thirteen real UI submissions produced twelve completed receipts and one cancelled job. The completed set includes the two explicitly documented alpha-quality failures; a successful ComfyUI status does not certify visual correctness. Native generation, editing, three-image composition, 25/40 steps, real alpha, both upscale factors and both prompt enhancers were exercised. Ten-image wiring/limits are CPU-tested against the encoder contract; ten-image quality and multi-image I2I enhancement were not GPU-qualified. Faces, identity fidelity and complex typography remain unevaluated.

The focused CPU suite passed **135 tests**, with verbose test names confirming execution. Installer/catalog checks passed **46 tests**. Node engine-state, localization and event-history UI tests passed. Source checks found no control bytes, Python parse errors or whitespace errors.

The disposable shared journal contains exactly **24 events**: 13 Image, six ImagePromptExpansion and five ImageUpscale. Twenty-one records are done and three cancelled. Identities are unique, each child has the expected parent and terminal state, and actual input counts/checkpoints match the receipts. Six invalid-option requests returned HTTP 400 without adding events. Disabling only I2I left the base engine and T2I ready; a forced I2I request returned 400 before inference. The temporary policy was then removed and all four enhancement modes became available again.

UI checks covered RU/EN assessment/control labels, language switching with three uploaded filenames and Russian prompt text intact, restored enhancer draft choice, the cancelled-expansion journal filter and its copied four-field object, and a correctly worded empty running filter. `journal-verification.json`, `png-verification.json`, original UI receipts/PNGs and screenshots are retained in the C fixture. The production source remains H and the owner's Desktop save destination is preserved. No commit or push was made.
