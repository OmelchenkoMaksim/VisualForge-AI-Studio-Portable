# FLUX.3 Klein INT8: capabilities and scoped verification

Reviewed on 2026-10-29 using the installed distilled INT8 checkpoint, current source and real studio jobs on an RTX 5070 Ti 16 GB. This is a small functional audit, not a general quality benchmark. Existing weights were sufficient; this audit downloaded no additional models.

## Publisher capabilities and the installed profile

The [official ComfyUI model card](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy) identifies a 5.6B image transformer, a separate Qwen3-VL-4B text encoder and up to ten reference images. Distilled Klein uses 16 steps and no classifier-free guidance; the separate Base checkpoint's 50-step/CFG 3.5 profile does not apply to these weights. The studio uses Euler/simple, CFG 1 and RGB output. Negative prompts and transparent output are not controls of this profile.

The [official inference repository](https://github.com/black-forest-labs/flux-3-image-klein) and [BFL Klein guide](https://docs.bfl.ai/flux_3/flux3_image_klein) document generation, reference editing, layout and native resolutions up to 4K. They do not imply that increasing resolution always improves semantic accuracy. The [reference-edit ComfyUI template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_flux3_klein_image_edit.json) uses the first input's proportions and about 1 MP by default. This studio retains that editing budget for the installed 16 GB system.

The [official Qwen3.5 prompt upsampler](https://huggingface.co/black-forest-labs/flux-3-image-klein-pup) produces full English captions and supports separate text/edit/layout contracts. Short ideas benefit from expansion; a complete caption can be used directly. The upsampler sees the references too, with its own reduced vision inputs; generation receives the reference latents separately. A good expanded caption is not proof that the image obeyed it.

[BFL's bounding-box guide](https://docs.bfl.ai/flux_3/flux3_image_bounding_boxes) describes spatial guidance. The installed workflow appends a validated element table to a caption containing matching `<id>` markers. Boxes use `[top,left,bottom,right]` on a 0–1000 grid internally. They guide position and scale, without a hard mask or guaranteed pixel preservation. The publisher also documents source/target boxes for object relocation; that separate editing interface is not qualified or exposed by this text-layout preset.

Consult the model card's linked FLUX Kommunity License for access and use terms. The earlier guide's blanket characterization of commercial output restrictions has been replaced by a link to the actual terms.

## Available studio controls

| Control | Actual processing | Limits |
|---|---|---|
| Prompt | Full caption → 16-step native generation | Square 1024×1024, landscape 1152×768, portrait 768×1152 at the default profile. |
| Single | One source image and an edit instruction | First input normalized to about 1 MP. An instruction editor, without exact identity/pixel guarantees. |
| Reference | Source plus one reference | State each image's role in the instruction. The first image determines proportions. |
| Multiple references | Existing Image 1/Image 2 plus 1–8 additional files, in displayed order | 3–10 total inputs, 100 MiB combined payload limit. More inputs are not a quality guarantee. |
| Layout | General scene prompt and 1–8 object descriptions with percentage boxes | Measured from the canvas's top-left. Description/ID/geometry validated before GPU admission. |
| Native resolution | Prompt/Layout latent is resized before sampling | 1 MP, approximately 4 MP/2K, or a 4K profile capped at a 4096-pixel longest edge. Rectangular 4K contains fewer than 16 MP. |
| AI Upscale 2× / 4× | Installed Real-ESRGAN x2 once or twice after generation | Separate from native resolution. Sharpens and invents texture; not factual restoration. Disabled for native 4K to avoid accidental 8K/16K work. |
| Expand prompt | Official Qwen3.5 captioning before generation/editing | Switch available in every FLUX mode. Off removes the upsampler nodes completely. |
| Another variant | Last result becomes the edit source | A creative variation, not a measured 80/20 similarity guarantee. Hidden after native 2K/4K because the edit profile would silently reduce it to about 1 MP. |

Only one quality preset is exposed. The assessment block beside the model selector summarizes observed strengths and limits in Russian and English. The guide under the prompt changes with the selected mode.

## Real output evidence

All UI cases used disposable state/output paths under the C user-temp `vfr-tests/flux3-audit-20261029` fixture. The owner's selected output directory remains `%USERPROFILE%\Desktop`. Seed 424242 was reused for controlled comparisons. Durations below are rounded observed job intervals, not speed promises. HDD cold loading and other applications materially change them.

| Case | Observed result |
|---|---|
| Prompt, expansion On, 1024 square | Convincing red clock, brass/cloth/wood and window lighting; three gears and readable `CLOCK` in this sample. Cold run about 161 s. |
| Single, red → green body | Main recoloring succeeded and the scene remained recognizable. Small reflections/bell colors changed too. About 25 s. |
| Reference, clock + synthetic striped fabric | Blue/yellow stripe pattern transferred to the body, but it still looked metallic rather than convincingly fabric-covered. About 20 s. |
| Three references, clock + stripes + cactus | All requested roles appeared: striped clock and a similar cactus in a red pot. About 28 s. References 4–10 have routing/schema coverage, not a ten-image GPU quality qualification. |
| Layout A/B, expansion On | Same two descriptions and seed; only boxes swapped. One sphere and one cube followed the requested diagonals. Color-mask centroids A: red (0.251,0.246), blue (0.760,0.749); B: red (0.752,0.752), blue (0.253,0.243). Warm B about 11 s; A after restart was a 162 s cold load. |
| Layout, expansion Off | Swapped-box control also placed exactly two objects on the intended diagonal, about 6 s. |
| Prompt Off + AI Upscale 4× | 4096 square, about 13 s; two real upscaler nodes and two journal children. Compared with Lanczos enlargement of the same-seed 1 MP control, edges and cloth texture changed visibly. Mean absolute RGB difference 5.033/255 is an effect check, not a quality score. |
| Native 2K + AI Upscale 2× | Actual 2048-square latent followed by a separate 2× operation; 4096-square output, about 26 s. Receipt distinguishes native profile and enlargement. |
| Native 4K, no upsampler/upscale | 4096 square generated directly; 277.79 s, peak whole-card 15,924 MiB out of 16,303 MiB. Finer texture but incorrect counts/markings and changed composition. Not a universal improvement over 1 MP. |
| Another variant from 1 MP | Completed as Single with one source; scene retained with changed secondary details, about 16 s. No numeric similarity claim. |
| Cancel during expansion | Parent Image and ImagePromptExpansion both cancelled after about 8 s; shared busy state cleared and UI allowed the next job. |

The native 2K/4K probes were direct ComfyUI research jobs; those are not studio submissions and are not expected in the shared event journal. Native 2K was also exercised through the UI. The 4K GPU test was not repeated after another application occupied several GB of VRAM. Its measured profile requires 15,000 MiB available at admission, and the UI explains the cost.

The failure that led to the layout fix is retained in the fixture: the ordinary text-caption contract invented left/right positions despite reversed boxes, producing duplicate objects. A direct caption without that expansion worked. The fixed layout contract receives the actual IDs, table and frame-relative locations; the original validated table is appended separately after expansion. Both A/B outputs then followed the planned positions. Box guidance remains probabilistic on more complex scenes.

## Fixes and verification

- Added multi-reference routing to both the upsampler image batch and generation latents, native resolution controls, and the text-layout workflow. New settings are validated before admission; other engines reject FLUX-only options.
- Moved the expansion switch out of the text-only panel. Fixed literal double-escaped newlines/quotes in the Single/Reference system prompts. The final Single path was also exercised by the variant test.
- Added actual reference count, native profile and layout parameters to history; retained the expanded caption and graph provenance. High-resolution variants no longer silently downsample. Cancellation now adds a terminal message to the stage panel too.
- Focused CPU regression suite: 120 tests, verbose execution. Node checks passed for engine selection, event-history UI and localization; edited scripts parsed successfully. Source checks found no control bytes, Python syntax errors or diff whitespace errors. These checks include earlier Z-Image changes, which remain uncommitted.
- The fixture journal contained 26 events: 14 Image parents, nine expansion children and three upscale children; 24 done and two cancelled. All IDs were unique, parent links/states matched, durations were present, and no running rows remained. All 13 successful receipts matched their expected technical children. A malformed box returned HTTP 400 without an accepted event or GPU job. UI filters for expansion/cancellation, completed upscales and unfinished events worked; copying an identity produced exactly type, ID, start time and duration.

Faces, identity consistency across people, complex layouts, Chinese lettering, source/target box relocation, and large reference sets were not quality-tested. The UI conclusion explicitly distinguishes tested three-image composition from ten-image routing support. No commit or push was made.
