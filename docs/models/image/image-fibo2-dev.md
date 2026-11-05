# FIBO 2.0 Dev INT8 (`fibo2-dev-int8`, Image)

Status: **installed; locally checked in all four Image modes**. Source/upstream review: **2026-11-06**. Evidence: [card rules](../README.md).

## Our version

BRIA's full, non-distilled 8B Dev, packaged by Comfy-Org/Fibo-2.0 at revision `4742d4bec410034142c8e65559e5f00c6f29eccb`. INT8 convrot transformer and Qwen3-VL 4B encoder, FLUX.2 VAE. Encoder/VAE hashes match Klein's installed files; they are shared, not copied. No Turbo checkpoint or Turbo LoRA.

Pins/sizes/hashes: `App/config/fibo2-assets.json`; reproducible catalog entries: `tools/build_fibo2_assets.py`. Components: `image.model.fibo2-dev`, `image.backend.fibo2` (requires the existing Image runtime) and shared `image.model.qwen3vl4b-flux2`, also required by Klein. BRIA transformer: **CC BY-NC 4.0**, separate BRIA agreement for commercial use; Qwen encoder/FLUX.2 autoencoder: Apache-2.0. New transformer download: 8.41 GB; all three model files together: 13.60 GB decimal. Installed H files were checked against every model SHA-256; no repeated encoder/VAE download.

Backend: official ComfyUI **0.44.0**, revision `2c75dc1b379a59e0880619d029607af5169a62ef`, plus comfy-kitchen 0.2.40 and comfy-aimdo 0.6.0. Reuses portable Python/Torch and the existing Comfy frontend/template/doc packages; their older-version recommendation in the backend log does not imply they were upgraded. Studio UI uses its own frontend and checked API workflows.

## Use and limits

- **[external, publisher]** Unified generation and editing, structured scene control, improved texture/anatomy/lettering; up to four references. Rankings are dated publisher claims, not a local 9/10 qualification.
- **[code]** Full Dev profile: **50 steps, CFG 5, Euler / simple**, default model sampling shift 1.15. Native RGB near **1 MP**, dimensions divisible by 16: 1024², 1248×832, 832×1248. Real-ESRGAN can enlarge afterwards; it does not establish native 2K capability.
- **[code]** No native alpha, layout boxes, mask route or Inspire mode in this integration. Publisher masked editing exists, but the built-in `TextEncodeFibo2` has no mask input. Do not label an ordinary reference as a mask. Background removal and local pixel-stable multi-turn adapters were announced, not integrated.
- **[GPU, six outputs]** Subjective **9.0/10** (picture/detail/freedom from distortion: 9/9/9), scoped to these trials and installed INT8 weights. Natural fur/skin/materials, correct three-apple count and Russian lettering, preserved recognisable face, fabric transfer, full-body four-reference assembly and watercolor worked. Recolouring also changed garment construction and mug shape slightly. Exact preservation, six-person identity and superiority on arbitrary scenes remain **[unknown]**; this is not a controlled comparison against other engines.
- **[code]** Single source is scaled/cropped to the encoder's trained size; several sources retain separate trained aspect sizes. Output proportions follow the first input. Composition's existing aspect preparation still applies.

## Modes in the studio

| Mode | Actual inputs | Contract |
|---|---|---|
| Prompt | No images | New scene from VGL; selected canvas sets native size |
| Single | One image | VGL of the desired result plus `edit_instruction`; preserve unrelated source properties |
| Reference | Two images | First is source/aspect anchor; second supplies the requested property; tags `<image_1>`, `<image_2>` |
| Multi | Three or four images | Every real source has an explicit ordered role and exact `<image_N>` tag |

All four modes use the studio's ordinary routes, GPU coordination, history and automatic local prompt manager. Composition can choose FIBO when it is ready and enabled; it cannot use it as the final stage of an alpha request. Its estimate accounts for the substantial reference cost: roughly 45 graph seconds plus 60 per source, separate from Gemma and enlargement; see measurements below.

## Requests and hidden manager

`fibo2_prompts.py`, guide **fibo2-dev-2026-11-06-v1**: ordinary Russian/English intent → checked English scene prose and optional bounded clarification → **local Gemma VGL conversion**. The publisher's cloud-backed `bria-vgl` package is researched, not installed/called. No Gemini request, second native enhancer or 8B enhancer dependency.

VGL describes the final picture in `short_description`, `objects`, `background_setting`, nested `lighting`, `aesthetics`, `photographic_characteristics`, `style_medium`, `artistic_style`, `context`, and `text_render`. Edits additionally carry `edit_instruction`. Every desired visible line belongs in `text_render[].text` verbatim in its original language; all other descriptions are English. No invented slogans, seed/settings keys or unknown source tags. Code rejects malformed structures and translated requested lettering before inference; conversion has one retry.

The ordinary clarification system remains in charge: one request-specific numbered question when necessary, a second only for conflicting selections, no third. A concrete fox/mushroom scene is ready; camera jargon is the manager's job. Refinement/choices are checked as prose before serializing VGL. The format pass is **text-only**, and code attaches that checked prose as `edit_instruction`: a real second look at the source re-described its old yellow jacket and omitted the requested blue edit. Final VGL and `prompt_format=vgl` survive history reload.

**[CPU, reproduced]** ComfyUI's Pillow default Aileron has no glyphs for `ЛЕСУТРО`: all tested letters equalled the missing-glyph rectangle. The pinned backend patch uses **DejaVu Sans 2.37 only when the default font lacks a non-ASCII glyph**, preserving ASCII conditioning. Font and licence come from the official DejaVu release. Source patch checks before/after SHA-256, is idempotent and refuses unknown vendor edits. This repairs conditioning input, not a guarantee of readable generated text.

The negative prompt stays the trained null caption. The publisher's photographic negative JSON can suppress intentionally requested haze, soft focus, flat backgrounds or illustration; it is not imposed on every scene. Separate photographic style presets are not advertised as installed.

## Evidence and next checks

| Date | Scope | Result |
|---|---|---|
| 2026-11-06 | Actual updated backend `/object_info` | All four FIBO graphs and all four previous engines ready; schema/availability evidence only |
| 2026-11-06 | Real local Gemma, first fox request | Prose decision valid; VGL incorrectly made lighting/aesthetics strings. Submission was refused before inference; nested-object examples added to the guide |
| 2026-11-06 | Prompt, 620061, 1024², 50/CFG 5 | Natural whole fox finding a mushroom in snow; fine fur/forest/snow; no invented writing. Graph 86.73 s including initial loads |
| 2026-11-06 | Prompt, 620062, 832×1248 | Photograph of a woman, plausible visible hands, blue mug, exactly three red apples and correct **ЛЕС**. Graph 44.39 s |
| 2026-11-06 | Single, 620063, same portrait | Yellow jacket became navy; recognisable face/scene/apples/ЛЕС retained. Jacket construction/mug geometry drifted slightly. Graph 101.44 s |
| 2026-11-06 | Reference, 620064, woman + stripe fabric | Blue/yellow fabric transferred to jacket; face, scene, three apples and ЛЕС retained. Graph 136.40 s |
| 2026-11-06 | Multi, 620065, four ordered sources | Woman from first image standing full-body in second image's snowy forest, third image's blue mug, fourth image's blue/yellow scarf; both feet visible; no unwanted fox/mushroom/table/apples/sign. Graph 277.12 s; total route 453.18 s |
| 2026-11-06 | Prompt, 620066, watercolor + x2 | One robot watering one red flower in one blue pot, watercolor/paper texture preserved; 2496×1664. Graph including Real-ESRGAN 37.84 s; route 67.77 s |
| 2026-11-06 | CPU | Ten focused tests passed: VGL shape/language/lettering/references, code-owned edit instruction, text-only format pass, GPU lifecycle/Stop, full-profile graphs, catalog/patch safety and history reload. Broader affected-suite totals are recorded in `fibo2-research/affected-tests.log` |

Final affected CPU suite: **298 tests passed with `-v`**; Ruff F/B/E9 (B905 excluded), changed JavaScript syntax checks, engine-state and clarification Node checks passed. Every new source basename is unique; edited text had no unexpected control bytes. Post-install regression also ran a real Klein → Qwen 2.1 Composition on the H backend, including Cyrillic; it completed successfully. This does not imply a rerun of every older engine/mode.

**Post-install smoke, outside the fixed six-output rating sample:** seed **620067**, FIBO Prompt 1248×832, 50/CFG 5, **reserve-vram 1** as in the launcher. The H-installed diffusion/encoder/VAE and patched font produced a natural copper coffee grinder, exactly one red apple and correct **УТРО**; graph **140.67 s** including cold SATA model loading, about 181 s with Gemma/UI preparation. The actual H asset paths were confirmed in the backend log. RU/EN model picker, score axes, capabilities and result metadata were checked in the browser; the displayed step count is no longer duplicated.

Artifacts/logs: C user Temp `vfr-tests/fibo2-audit-20261106` and `vfr-tests/fibo2-research`; only synthetic reference photos. Hardware: RTX 5070 Ti 16 GB / 64 GB RAM. These six checks used cache-none and **reserve-vram 6**; recorded whole-card peak **13143 MiB**, not a minimum requirement. Gemma used partial CPU offload (12 GPU layers; large planning contexts 10). Timings vary with HDD loading, available VRAM and offload; they are not universal latency promises. Browser drove Prompt/Single; ordinary API drove Reference/Multi and watercolor. Every saved output was visually inspected. Required next qualification for a new decision: six different people, adversarial counts/text, unusual scripts or larger workflows; CPU/fake UI passes cannot establish their quality.

## Code, research and refresh

Entry points: `engines/image_registry.py`, `fibo2_prompts.py`, `image_prompt_enhancer.py`, `workflow_builder.py`; `App/workflows/fibo2-dev-int8/fibo2-*.api.json`; `tests/test_fibo2_prompt_manager.py`. UI guides and requirements use Image's existing controls.

Primary research (2026-11-06): **Hugging Face — briaai/Fibo-2.0-dev model card; Comfy-Org/Fibo-2.0 packaging**. **GitHub — Bria-AI/vgl, Thales generation/edit field guides and output schema; Comfy-Org/ComfyUI v0.44.0, nodes_fibo2.py and text_encoders/fibo2.py; Comfy-Org/workflow_templates, image_fibo2_edit_dev_pe; dejavu-fonts/dejavu-fonts 2.37**. Secondary discovery: Reddit StableDiffusion release discussions distinguish Dev from Turbo; their speed/quality anecdotes do not establish this profile's results.

Refresh only affected facts when checkpoint, backend, schema, font handling or sampling changes. Use this card as the foundation, then search current primary sources and recheck the relevant CPU/UI/GPU boundary. Keep reported evidence scoped to the actual quantization and hardware.
