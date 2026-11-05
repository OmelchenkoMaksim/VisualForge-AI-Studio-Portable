# Qwen Image Edit 2511 (`qwen-edit-2511`, Image default)

Status: **researched; real Gemma and all four modes exercised, 2026-11-05**. Publisher/community review: **2026-11-01**. Current samples and remaining gaps are scoped below. [Evidence rules](../README.md).

## Our version [code]

**20B instruction editor, FP8 mixed**, Qwen2.5-VL 7B FP8 encoder, Qwen RGB VAE. Main package: Comfy-Org/Qwen-Image-Edit_ComfyUI, revision `f68ace85e60b4a02a323e394253731947657b7d2`. Exact encoder/adapter revisions, files/hashes and licences remain in `App/config/component-catalog.json`. Separate from Image 2.1, Layered and native Qwen text-to-image models.

Standard is **40 steps, CFG 4**, Euler/simple. Lightning uses the separate LightX2V **4-step V1.0 BF16 LoRA**, CFG 1, not an eight-step or fused checkpoint. Its pin is `107d238d68b8387e32843d0b4a2e24ceb0d3ad86`. The community adapter is an independent explicit switch; its benefits are unqualified. No weights/settings were changed by this prompt-manager work.

## Useful for / limits

- Object recolouring, text replacement and material/reference edits. Earlier tests replaced CLOCK with TIME and transferred a woven pattern; three sources contributed scene, pattern and cactus. **[recorded, 2026-10-29]**
- Edit boundaries leaked into bells and the dial, and the material was reinterpreted. Preservation wording does not lock pixels. Faces/identity, Chinese/Russian lettering quality, geometry and multi-person consistency remain unqualified locally despite publisher examples. **[recorded/unknown]**
- RGB only: no real alpha, layers or dedicated mask/pixel-lock mode. Inputs are normalized by area to about 1 MP, keeping the full frame on an eight-pixel grid. First source sets edit proportions; AI Upscale is separate x2/4x. More instructions or steps cannot guarantee fidelity. **[code]**

## Modes and hidden prompt rules

The person writes normally. Automatic Gemma guide **`qwen2511-edit-2026-11-01-v1`**, `qwen2511_prompts.py`, writes English while retaining exact requested lettering in its original language and case. A bounded edit receives a concise action/target/preservation instruction, not an invented photographic recaption. Length scales with complexity; there is no measured universal optimum.

| Mode | Actual inputs / preparation |
|---|---|
| Single | One source. Focus on the requested edit; briefly protect important neighbouring details. Distinguish local edit from a requested new scene, and old writing from final writing. |
| Reference | Two inputs. Picture 1 supplies the source, Picture 2 the requested material/object/style. State the transfer and scope; exclude unwanted donor scenery. |
| Multi | Exactly three inputs. All Picture 1/2/3 roles required; no invented fourth input or Image 2.1 tags. All three reach Gemma and both Plus conditioning branches in order. |
| Prompt | No selected uploads participate. Gemma builds the complete scene; code adds one replacement instruction and supplies a generated neutral canvas to the editor. Experimental, not native text-to-image. |
| Another variant | Last result becomes a Single source, including after Prompt. Noninteractive preparation retains the chosen profile, negative text and adapter state. |

Standard and Lightning have separate hidden instructions. Negative text remains verbatim in its own Standard branch; Lightning rejects a nonempty negative before Gemma because CFG 1 would ignore it. The manager cannot enable an adapter, change a profile or generate another negative field. **[code/CPU]**

A clear request proceeds immediately. An unclear source target/donor role can produce numbered, request-specific choices. A group conflict allows one final question; code forbids a third. Waiting choices are bound to sources, original request, profile, negative and adapter controls. Shared clarification v2 separates English decisions from UI localization, requires conflict groups and preserves chosen instructions at the start of the final caption. One real Russian combined-colour choice below was relevant and executed; this is not a universal question-quality guarantee. **[code/CPU/GPU]**

## Sources and adaptation [2026-11-01]

- **Publisher:** Hugging Face Qwen/Qwen-Image-Edit-2511 model card and official Space `app.py` edit-rewrite instructions; Qwen release blog. Keep clear edits direct, minimal grounded additions and exact quoted writing. We do not import its mask/outpainting templates into unsupported studio modes, copy an ambiguous example that changes the background owner, or infer ethnicity. The demo rewriter sees the first image; our pass receives every real source.
- **Adapter publisher:** Hugging Face lightx2v/Qwen-Image-Edit-2511-Lightning and ComfyUI's 2511 material-replacement tutorial. The installed `comfy_extras/nodes_qwen.py`, `TextEncodeQwenImageEditPlus`, already prefixes visual slots as Picture 1/2/3. Gemma cites those roles without duplicating vision tokens or a long source inventory.
- **Independent:** Reddit r/comfyui, “Qwen Edit 2511 not following prompts., How do I solve the issue?” (`1vh3gl4`): the reported fix was the Plus encoder, not merely more elaborate prose. “Cracked the case on high res + quality Qwen Edit 2511 outputs…” (`1tqm4pb`) favours simple edits and explicit preservation; its custom reference/negative wiring and high-resolution claims are not transplanted into our graph. Its claims that a word or inventory format is mandatory are not established universal rules.
- **Independent:** Reddit r/StableDiffusion, “Maintaining likeness to input images in Qwen Image Edit 2511?” (`1puwckf`): mixed identity results and explicit donor-role suggestions; some replies concern 2509. Treat as test hypotheses, not a fidelity guarantee for our FP8 pair.

Use these site/repository/title handles as the foundation; refresh changed facts and verify applicability to our checkpoint/mode. Detailed prior citations: [Qwen-Image-Edit-2511.md](../../Qwen-Image-Edit-2511.md).

## Evidence / next checks

**[GPU/UI, 2026-11-05]** Installed weights, real Gemma, all four modes, community adapter off. Standard Single changed the left apple to green and the jacket to blue after one Russian clarification; face/hands, two other red apples and "УТРО" remained recognizable. Lightning carried out both colours too, but recoloured zipper trim more broadly. Standard's negative field was retained exactly; Lightning sent none. Reference + Lightning/x2 replaced a mushroom with the donor mug. Standard three-input Multi composed the fox, mug and third-source fabric into a new summer terrace, excluding winter scenery. Prompt through the neutral canvas fulfilled the winter fox scene but looked more rendered than the native text generators. Timings, settings and failure limits: [real audit](../../VisualForge-Image-UI-Audit-2026-11-05.md). These samples do not establish exact identity or pixel preservation.

A separate Standard portrait kept the full head, three apples and readable "УТРО" with plausible visible fingers, but added a small duplicate word to the jacket. Lightning/x2 watercolor kept the robot, red scarf, one cactus/blue pot, paper and readable "РАСТИ"; its outlines were cleaner/harder than loose watercolor. Seven reviewed outputs underpin the installed profile's 8.0/10 subjective score; this is not a universal edit/identity guarantee.

**[CPU, 2026-11-01]** `tests/test_qwen2511_prompt_manager.py`: real route/graphs with fake Gemma/GPU/ComfyUI; all modes/profiles, three-source order, exact negatives, Russian final writing, reference retry, canvas isolation, stale controls, two-question cap, variations and history. Related regression: **210 tests passed**, plus Ruff, JS syntax, clarification, selection and i18n JS checks. No inference.

**[UI, 2026-11-01, fake backend]** Actual page/route with fake Gemma/GPU/ComfyUI: Standard retains exact negative and adapter state; Lightning sends an empty negative, keeps the draft for return to Standard; numbered conflict resolves without a third question; cancellation unlocks inputs without queuing. Four mode guides and RU/EN switching preserve Russian input. Two fake queues/three fake Gemma calls before a separate cancellation check. Screenshot: C user Temp `vfr-tests/qwen2511-clarification-ui.png`. This is routing evidence, not a real generation.

**[recorded, 2026-10-29]** Standard recolour 453 s; Lightning recolour 200 s (sampling about 11 s, preparation dominated); Standard text edit 392 s; Reference + 2x 192 s; three inputs + 4x 197 s; Prompt landscape 117 s. Mixed warm/cold HDD loading with `--cache-none`; not general speed predictions.

**[unknown]** General instruction/question reliability beyond the samples, exact edit boundaries, complex lettering, exact identity and multi-person composition. Standard/Lightning examples differ slightly in their prepared captions, so they do not isolate the effect of step count. Repeated seeds and controlled raw/enhanced comparisons remain useful; never mark them PASS from CPU or fake UI.

Code: `image_prompt_enhancer.py`, `qwen2511_prompts.py`, `qwen_inputs.py`, `reference_inputs.py`, `workflow_builder.py`; templates `App/workflows/qwen2511-*.api.json`.
