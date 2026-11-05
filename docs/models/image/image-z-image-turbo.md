# Z-Image Turbo INT8 (`z-image-turbo-int8`, Image)

Status: **researched; all five modes exercised with real Gemma through the UI**. Source review and prompt research: **2026-11-01**; evidence reviewed **2026-11-05**. Small local sample, no raw-versus-enhanced quality comparison. [Evidence marks](../README.md).

## Our version [code]

Distilled Turbo 6B; `z_image_turbo_int8_convrot.safetensors`, FP8 mixed Qwen3-4B encoder, `ae.safetensors` VAE. Comfy-Org/Z-Image-Turbo package revision `6fc90a3b1b653e935a0d175e260736de25b84df5`; base component `image.model.z-image-turbo`, Apache-2.0 as recorded in the catalog. **Base, Edit and Omni are different checkpoints.**

Optional Union **2.1 Lite 2602 8steps** (`image.model.z-image-controlnet-union`) and Tile **2.1 Lite 2601 8steps** (`image.model.z-image-controlnet-tile`), Alibaba-PAI revision `5155fc56d17821007d6f62ac192c09e0f0e72016`. Each patch is about 2.02 GB; detail/Tile also need Real-ESRGAN x2. Exact bytes and SHA-256: `App/config/component-catalog.json`. Optional does not mean installed/enabled.

## Use and limits

- **Best fit:** quick text-to-image and structural guidance. Convincing metal, cloth, wood and light; Canny recolouring preserved the clock outlines, masked editing recoloured its selected bells. **[recorded, clock tests, 2026-10-29]**
- **Failure cases:** three gears became four; `CLOCK` acquired punctuation. Plain img2img failed a red-to-green instruction, so Single/Reference instruction editing is not exposed. Detail/Tile synthesize texture and can change small parts or lettering. **[recorded, same audit]**
- Photorealism and English/Chinese lettering are publisher claims **[sources]**; illustration is a valid requested medium. Faces and Chinese typography were not separately qualified here **[unknown]**.
- No negative prompt: our CFG 1 has no negative branch **[code]**. Do not promise exact identity/counts/spelling, instruction editing from base Turbo, alpha output, automatic depth/pose extraction or recovery of authentic missing detail.
- Warm tested jobs took seconds; an older registry comment records roughly 13.6 GB whole-card peak at 1024 square. Admission asks for 11,000 MiB free; that threshold is an estimate, not measured peak usage or a portable minimum **[recorded/code]**.

## Modes and prompt adaptation [code]

| Mode | What actually happens | Caption Gemma should provide |
|---|---|---|
| `prompt` | Text only; 8 steps, res_multistep/simple, CFG 1, shift 3; 1024 square / 1152x768 / 768x1152 | One visible scene: subjects, counts, action and relations first; then useful framing, light, palette, materials and requested medium. |
| `detail` | Source -> about 1 MP -> ESRGAN x2 -> 5-step low-denoise refinement (0.20 or 0.33 UI presets) | Grounded caption of the **same** picture; preserve crop, identity, count, colours and medium. Add compatible fine detail. |
| `control` | Union, about 1 MP, 8 steps; Canny auto / grayscale auto / prepared map | Desired finished scene, following the map's structure. Edges do not preserve colour/identity; gray supplies luminance; prepared maps get no automatic pose/depth extraction. |
| `inpaint` | Source + same-size opaque grayscale mask; white edits, black keeps; Union, 8 steps; final composite keeps unselected RGB pixels | Desired contents of the white region with source perspective/light. For removal, caption the replacement background, omitting the removed object. The mask is not a second reference. |
| `tile` | Source -> about 1 MP -> ESRGAN x2 -> Tile 8 steps, about 4 MP output | Detailed caption of the same scene and plausible finer textures; keep composition, identity, lettering and style. |

Control strength is bounded to 0.65–1.0 (default 0.85 Control, 1.0 Inpaint). Prompt can separately use ESRGAN 2x/4x; a larger file is not native extra detail. Optional modes depend on available nodes, weights and policy. The mode table is an implementation contract, not a five-mode quality guarantee.

## Requests and the hidden manager

**Chosen policy**, adapted from publisher guidance and community experience: coherent English natural language; keep the user's subjects, count, relations, colours and style before adding compatible detail. A simple idea usually fits **80–160 words**, without padding or dropping facts. Avoid booru/weighted tokens, generic "masterpiece/8K" filler and compulsory camera/sensor lists. Describe visible viewpoint and environment; preserve illustration styles. Requested Russian lettering stays verbatim in straight double quotes, without translation or extra text. **[code; external rationale in the research report]**

Mode rules above are **our adaptation to the installed graphs**; the publisher does not prescribe a separate syntax for every mode. Community reports favour clear relations and sufficient environment for wide shots, but disagree on prompt length/token priority. Camera terms alone may give weak viewpoint control. These are anecdotes, not measured universal rules. **[sources, 2026-11-01]**

**Illustrative, not a tested Gemma answer:** "нарисуй лису которая нашла грибочек под снегом в хвойном лесу" -> "A fox lowers its nose toward a small mushroom exposed in disturbed snow in a conifer forest. Both the fox and mushroom are clearly visible, surrounded by snow-covered trunks and evergreen branches, with soft winter light and detailed fur and snow textures." The full manager may add compatible detail; this example is not a mandatory style.

`image_prompt_enhancer.py` is active for all five modes; instructions live in `zimage_prompts.py`, guide `zimage-turbo-2026-11-01-v3`. It sees source/mask thumbnails in the correct order, uses `GemmaTask` with `think=False`, and retains original/enhanced prompts and provenance. Literal checks cover quotes and recognisable uppercase labels; they are not a complete OCR/semantic verifier. V2 tells refinement to preserve uncertain small attributes by reference after Gemma misnamed a mushroom colour; a real repeat caption named it correctly. V3 stresses exact counts and separate placement after three requested apples became four; its image effect is unverified. **[code/GPU/unknown]**

The page may ask one request/mode-specific question with 2–8 numbered choices; multiple numbers or `0` are accepted. A second appears **only for explicit conflicting choices**, never a third. Missing camera jargon alone is no reason to ask; the fox idea is already sufficient. This protocol is enforced by code, while the decision that an intent needs clarification is still model-dependent. Director/API calls without `clarify: true` remain noninteractive. **[code]**

## Evidence and next checks

| Date | Scope | Established / remaining limit |
|---|---|---|
| 2026-10-29 | **[recorded]** real jobs and UI, before this Gemma manager | All Prompt formats; Off/2x/4x; both detail presets; Canny/gray/prepared synthetic depth map; masked edit; Tile; seed reuse, cancel, save and RU/EN UI. Scoped results and failures: [generation audit](../../Z-Image-Turbo.md). |
| 2026-11-01 | **[recorded, CPU]** fake Gemma and graph/bridge tests | Per-mode instructions, source/mask order, malformed retry, literal checks, cancellation, provenance, clarification conflicts/cap and token handling. No inference quality established. |
| 2026-11-01 | **[recorded, UI with fake replies]** clarification panel | Number selection, second conflict question and final acknowledgement in the browser; no real Gemma decision or image. |
| 2026-11-01, reviewed 11-05 | **[GPU/UI]** real Gemma and all five modes | Fox photo, Detail 0.20, Canny coat change, masked mushroom replacement and Tile completed; illustration medium and Cyrillic writing also checked. Inpaint kept every outside-mask RGB pixel unchanged. Count failed (four apples for three); Control/Tile looked harder than Prompt/Detail. See the [ongoing audit](../../VisualForge-Image-UI-Audit-2026-11-05.md) for settings, timings and limits. |
| Pending | **[unknown]** real manager comparison | Hold seeds/settings fixed, compare raw and enhanced requests in each mode; inspect adherence, style, counts, Cyrillic text, edit boundaries and source preservation. Repeat with several seeds before generalising. |

Next CPU checks: `tests/test_image_prompt_manager.py`, `test_prompt_clarification.py`, `test_image_engines.py`, `test_image_reliability.py`, `tests/image_clarification_test.js`. UI: check selected mode, source/mask retention, RU/EN, ready/clarify paths, conflict and Stop. GPU: approved short jobs sequentially, inspect outputs rather than only successful status. Faces, Chinese lettering, pose/HED/MLSD/scribble maps and authentic-detail recovery remain unqualified.

## Sources and refresh

Publisher research **2026-11-01**: **Hugging Face** — Tongyi-MAI/Z-Image-Turbo prompting discussion #8 and official demo `pe.py`; Comfy-Org/Z-Image-Turbo INT8 package; Alibaba-PAI Union/Tile 2.1 card. **GitHub** — Comfy-Org/workflow_templates, INT8 and 2K refinement examples.

Independent experience **2026-11-01**: **Reddit**, r/StableDiffusion "Zimage turbo prompting questions" (1qa16te), "How to prompt ZImage Turbo" (1qm3ko3); r/comfyui "Experiences with prompting ZImage Turbo" (1q9cwr2). Treat anecdotal advice separately from publisher capabilities.

Use these findings as the foundation. Refresh the affected fact after a checkpoint/encoder/ControlNet change, a new prompt-guide version or a concrete unresolved failure. Search afresh using the handles above; retain the tested scope and recheck relevant UI/CPU/GPU behaviour before marking new results confirmed. Exact external citations and instruction rationale: [prompt research](../../Z-Image-Prompting.md). Code: `App/server/engines/image_registry.py`, `workflow_builder.py`, `image_prompt_enhancer.py`, `zimage_prompts.py`; graphs: `App/workflows/z-image-turbo-int8/`.
