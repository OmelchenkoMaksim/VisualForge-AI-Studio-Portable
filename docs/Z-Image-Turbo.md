# Z-Image Turbo INT8: capabilities and scoped verification

Reviewed on 2026-10-29 against the current source and real studio jobs on an RTX 5070 Ti 16 GB. This is a small capability audit, not a broad image-quality benchmark. No new peak-VRAM measurement was made.

## What the checkpoints support

The [official Turbo model card](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) describes a distilled 6B text-to-image model, 8 effective model evaluations, photorealism and English/Chinese lettering. Turbo has no classifier-free guidance; ComfyUI's CFG 1 disables the negative branch. This differs from the separate Base/Edit/Omni checkpoints: their capabilities must not be attributed to the installed Turbo weights.

The [official ComfyUI INT8 package](https://huggingface.co/Comfy-Org/Z-Image-Turbo) supplies the installed INT8 transformer, FP8 Qwen3-4B encoder and VAE. Its [INT8 template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_z_image_turbo_int8.json) uses 8 steps, `res_multistep / simple`, CFG 1 and shift 3. Its [2K upscaler example](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/utility_z_image_turbo_2k_upscaler.app.json) also demonstrates low-denoise refinement. Our detail workflow uses the installed Real-ESRGAN x2 directly, rather than the example's x4 followed by downsampling.

[Alibaba-PAI Union/Tile 2.1](https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1) are additional checkpoints. The installed optional variants are Union Lite 2602 (edges, prepared control maps, grayscale and masked inpainting) and Tile Lite 2601 (image-conditioned enlargement), both distilled for 8 steps. Each file is 2,016,627,488 bytes; repository revision `5155fc56d17821007d6f62ac192c09e0f0e72016`. Sizes, SHA-256 and immutable download URLs are pinned by `build_zimage_addons()` in `tools/build_catalog.py` and the component catalog. They are not downloaded with the base Turbo component automatically.

## Modes in the studio

| Mode | Actual processing | Limits |
|---|---|---|
| Prompt | 8-step text generation; square 1024×1024, landscape 1152×768, portrait 768×1152 | No negative prompt or image input. One quality preset. |
| Prompt + 2× / 4× | Real-ESRGAN x2, once or twice, after generation | Changes edges/textures, not just pixel dimensions. 4× is not a promise of additional authentic detail. |
| 2K detail | Source → about 1 MP → Real-ESRGAN x2 → 5-step Z-Image, DPM++ 2M SDE / beta; denoise 0.20 or 0.33 | Redraws texture. Can alter text, faces and small features. Very large inputs are reduced first. |
| ControlNet | Union Lite; Canny extracted automatically, grayscale converted automatically, or a prepared control map supplied by the user | About 1 MP, 8 steps. Structure guidance is not pixel/color preservation. No automatic pose/depth estimation. |
| Masked edit | Union Lite, source and same-size opaque grayscale mask; white edits, black preserves; 8 steps | RGB output keeps source dimensions. Final composition preserves unselected RGB pixels. Source alpha is not preserved. Prompt adherence and seams still require inspection. |
| Tile 2K | Source → about 1 MP → Real-ESRGAN x2 → Tile-guided 8-step generation | About 4 MP; new textures may be invented. Not forensic restoration. |

Optional modes are exposed only when their actual nodes/checkpoints are available and enabled. Missing/disabled Union or Tile does not disable Prompt or independent detail refinement. The ordinary Single/Reference editing modes remain unavailable. The permanently disabled variant button is hidden for Turbo.

## Observed strengths and weaknesses

- The clock scene rendered convincing metal, cloth, wood and lighting; a separate robot illustration exercised another style. Warm jobs in the tested profiles take seconds; the first HDD load was much slower. This is not a speed guarantee for other machines.
- Counts and precise lettering were imperfect: the request for three gears produced four, and `CLOCK` acquired punctuation. Chinese lettering, faces and pose maps were not separately evaluated here.
- Plain image-to-image at denoise 0.30/0.65 changed details but failed the requested red-to-green recoloring; it was not exposed as a reliable instruction-edit mode.
- Union Canny did recolor the clock green while preserving its main outlines. Union masked editing recolored the selected bells. These are scoped successes, not guarantees for arbitrary edits.
- Real-ESRGAN 2× visibly sharpened edges and introduced some outlining. The second 2× pass produced a larger 4× file with a similar character. Both retained; their labels explain the tradeoff.
- Low-denoise Z-Image and Tile visibly redraw textures. Compare their output to the source, especially lettering and small parts.

The model selector's **Strengths and limitations** block presents this conclusion in Russian/English. Other models are populated as their audits are completed.

## Implementation and checks

Templates live in `App/workflows/z-image-turbo-int8/`. Workflow admission rejects unsupported settings rather than ignoring them. Mask validation rejects empty, colored, transparent or mismatched masks before GPU admission. Mask feathering stays inside the selected region, avoiding a hard composite seam while keeping black keep pixels untouched.

CPU regression checks cover graph schemas/dependency cycles, mode selection/fallback, independent optional dependencies and policy switches, strength bounds, mask validation, source preservation, localization and the bridge. Real checks cover all Prompt formats, Off/2×/4×, both detail strengths, Canny/grayscale/prepared synthetic-depth maps, masked editing, Tile, seed reuse, cancellation and output saving. Prepared pose/HED/MLSD/scribble maps remain author-supported inputs, not separately qualified presets.

The focused CPU suite passed 111 tests; the separate installer/catalog suite passed 46. UI checks also covered Russian/English assessment and guides, retained source/prompt/strength values during language changes, mask paste-target selection and the restored mask-mode default. Fixes address untranslated composed capability/release labels, stale preset/guide translations and Image 2 paste routing in mask mode.

The event journal records each accepted studio job as Image. Optional ImageRefinement/ImageControlNet children and one or two ImageUpscale children describe work in the shared ComfyUI graph. Their durations overlap; they are not per-node GPU timing measurements. Direct ComfyUI research probes bypass the studio and are not expected in its journal. Rejected masks create no accepted generation.

Temporary inputs, outputs and bounded verification logs are kept in the C user-temp `vfr-tests/zimage-audit` fixture. The owner's selected Image output directory is unchanged. Model weights are intentional installed components; download cache and test data remain outside the source tree.
