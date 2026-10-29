# Qwen Image Edit 2511: capabilities and scoped verification

Audited on 2026-10-29 using the installed FP8 mixed editor on an RTX 5070 Ti 16 GB / 64 GB RAM system. This is a small functional audit with real studio submissions, not a broad quality benchmark. No new weights were downloaded. Test state, inputs, outputs and logs live under the C user-temp `vfr-tests/qwen2511-audit-20261029` fixture; the owner's Image output folder remains `%USERPROFILE%\Desktop`.

## Research and the installed model

The [official 2511 card](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) describes a 20B instruction editor with improved consistency, integrated community capabilities, material editing and geometric reasoning. It is a separate checkpoint from Qwen's native text-generation, Layered and Image 2.1 models. Claims about those checkpoints must not be transferred to this profile. The publisher's standard example uses 40 steps and true CFG 4.

The [official ComfyUI 2511 guide](https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511) demonstrates reference-based material replacement and an optional Lightning adapter. The installed `TextEncodeQwenImageEditPlus` node accepts exactly three optional image slots. Each image reaches the visual encoder at reduced resolution and the reference VAE at approximately 1 MP; both semantic and appearance conditioning matter. The studio now exposes the third slot and keeps input order in both conditioning branches.

[LightX2V's model card](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) supplies the separate four-step adapter. The already installed BF16 file has 849,608,296 bytes and SHA-256 `22226e8d05d354bb356627d428809f5afd7819399b077238a2b70a82883a904f`, matching the publisher's metadata and a local checksum. The catalog pins revision `107d238d68b8387e32843d0b4a2e24ceb0d3ad86`. It is not a replacement for the main transformer.

The installation also contains a [community NSFW adapter](https://huggingface.co/ScottzillaSystems/qwen-image-edit-plus-nsfw-lora). It previously ran unconditionally in every 2511 graph. It is now an explicit switch and independent component. Its subject-specific effects were not evaluated here; benign recoloring worked with it enabled, while text/reference/composition tests also worked without it. This does not establish that either setting is universally better. Model and adapter licenses remain separate in Components.

## Studio modes and controls

| Control | Actual behavior | Limits |
|---|---|---|
| Single | Edit Image 1 by instruction | About 1 MP; areas outside the requested object may change too. |
| Reference | Image 1 is the source, Image 2 supplies attributes or subjects | Describe the role of each image. Material/pattern transfer is not exact copying. |
| Three images | Source, second reference and exactly one additional Image 3 | All three are passed in order to positive/negative conditioning. At most 100 MiB combined. |
| Prompt | Automatically created neutral canvas plus a full-scene instruction | Experimental canvas editing, not a separate native text-to-image checkpoint. Uploaded user references are not sent in this mode. |
| Standard | 40 steps, Euler/simple, CFG 4 | Allows an optional negative prompt up to 2000 characters. Exclusions do not guarantee compliance. |
| Lightning | Optional acceleration LoRA, four steps, CFG 1 | Negative prompts cannot affect CFG-1 sampling: the field is disabled and its text is retained for Standard. API requests with a nonempty negative prompt are rejected. |
| Optional NSFW LoRA | Explicit on/off switch, independent of Lightning | Missing/disabled adapters do not disable base editing. Existing installations initially retain the former enabled choice when the adapter is available. |
| AI Upscale | Real-ESRGAN x2 once or twice after decoding | 2×/4× dimensions, separate from the model's approximately 1 MP generation. New texture is not authentic restoration. |
| Another variant | Last result becomes the new Single source for a Prompt result | Retains the previous profile, adapter and negative settings; no measured similarity percentage is promised. |

The unsupported quality implication of the old Good/High labels has been replaced by explicit Standard/Lightning profiles. This audit did not establish that 50/60 steps have zero effect; they are no longer advertised as automatically better presets. Old receipts keep their actual step budgets and historical profile labels.

Optional adapters are separate Components entries: `image.model.qwen-edit-lightning` and `image.model.qwen-edit-uncensored`. Their absence or policy switch removes only the relevant option. Older encoders lacking `image3` retain the original modes without advertising three-image support.

Qwen 2511 outputs RGB. This profile provides no alpha output, layer decomposition or hard mask/pixel lock. Baked-in lighting/viewpoint capabilities can be requested through Single instructions; there is no separately installed camera/pose estimator in this workflow. Human identity, Chinese lettering, geometric reasoning and multi-person consistency were not quality-qualified in this audit.

## Reproduced cropping bug

The old `FluxKontextImageScale` chose a fixed aspect bucket and center-cropped the image before Qwen received it. A CPU probe ran the installed vendor methods on a 1200×100 synthetic panorama with red and blue edge markers. The old route produced 1568×672 and lost both markers completely. `ImageScaleToTotalPixels` produced 3544×296, preserving 78,440 red and 78,440 blue pixels.

Single, Reference, Three images and the Prompt canvas now normalize by area with Lanczos and an eight-pixel grid. This preserves the full frame with only rounding of dimensions. Both appearance conditioning and the sampling latent use that normalized source. The model can still redraw or omit content; preserving the input frame is not a pixel-preservation guarantee. Square and landscape image jobs exercised the updated graph.

## Real UI results

Seed 424242 was reused. The product input was a previously generated clock photograph; additional references were synthetic stripes and a cactus. All heavy jobs ran sequentially. Several GB of GPU memory were occupied by other applications; none of their processes were stopped. These are observed intervals with `--cache-none`, HDD loading and changing LoRA combinations, not general speed predictions or controlled hardware benchmarks.

| Test | Result | Observed time |
|---|---|---|
| Original Single, 40 steps, community adapter On | Clock body became green, but green also reached brass bells that should have stayed unchanged. | 453 s |
| Same recoloring/seed, Lightning, same adapter | Similar successful recoloring and similar boundary errors. Pixel outputs differ; neither sample proves a general quality advantage. Four sampling steps took about 11 s; preparation dominated total time. | 200 s |
| Standard, community adapter Off, `CLOCK` → `TIME`, negative text supplied | Correct `TIME`, recognizable scene and three gears. Receipt records the exact negative string and CFG 4. This was not an isolated A/B test of negative-prompt quality. | 392 s |
| Reference + Lightning, adapter Off, 2× | Blue/yellow woven pattern transferred convincingly; bells were covered too despite the preservation instruction. 2048-square output and one upscale child. | 192 s |
| Three images + Lightning, adapter Off, 4× | Scene, pattern and cactus/red pot all contributed. Pattern was reinterpreted and leaked onto the clock face. 4096-square output and two upscale children. | 197 s |
| Prompt + Lightning, landscape, no enlargement | New watercolor robot/cactus illustration with readable `GROW`; 1256×840. Prompt used the neutral canvas, not the still-selected source/reference uploads. | 117 s |
| Another variant of the illustration | Same scene/subjects/text with changed contrast, outlines and paper texture. Receipt records selected Prompt but actual Single, one source and the retained adapter/profile settings. | 107 s |
| Cancel a Prompt with scheduled 2× | UI cancellation completed; Image and ImageUpscale both ended cancelled, with no retained busy state. The upscale duration is a graph scope, not evidence that its node had already run. | 53 s total |

The approximately 4.296/255 mean absolute pixel difference between the recoloring profiles is only an effect check, not a semantic quality score. The comparison is saved as `profile-comparison.png`. Real-ESRGAN is the same operator inspected in the [FLUX audit](FLUX-3-Klein.md); the 2511 UI tests additionally verified output dimensions and one/two actual graph passes.

## Implementation and verification

The base templates are `App/workflows/qwen2511-single.api.json`, `qwen2511-reference.api.json` and `qwen2511-multi.api.json`; Prompt derives from Single using the generated canvas. `qwen_inputs.py` validates negative/adapter controls and `reference_inputs.py` validates each encoder's additional-image limit. FLUX retains its own box/resolution validation. History records actual reference count, profile/sampler parameters, adapter choice and negative text, with the existing graph/template provenance. Negative text is displayed as inert user text. Draft persistence includes the new negative field and adapter choice.

The focused Image/bridge/localization/history suite passed 128 tests after the final dependency fallback check; installer/catalog tests passed 46. Node checks cover selection/guides, localization and the shared event-history UI. The installed Lightning checksum matches the pinned digest. The full-frame resize failure and fix were reproduced on CPU using the installed vendor code, independently of the image model's probabilistic output.

The model selector's Russian/English assessment separates observed strengths, unstable edit boundaries and implementation limits. Language switching preserves uploaded references and user-entered text; reloading preserves the new negative-prompt draft and adapter choice. Disabling both optional components in the disposable fixture hides Lightning and disables the community checkbox without disabling Standard. A direct request for the disabled profile returns HTTP 400 before inference; restoring the policy restores both controls.

The global journal contains exactly 12 events for these tests: eight Image parents and four ImageUpscale children, with ten completed and two cancelled records. Identities are unique, children match their parents, seven successful receipts exist, and the shared busy state is clear. Four invalid-option requests returned HTTP 400 without creating generation events. The visual text encoder is not a separately generated prompt-expansion operation. Technical durations include the shared ComfyUI graph and may overlap their parent. UI filters and copied event objects were checked. An empty filtered result previously claimed that the journal had no records; it now correctly says that no events match the selected filters. The running filter was verified empty after the fix. Detailed fixture journal and UI evidence is retained in C user-temp.

No commit or push was made. Existing Z-Image/FLUX work remains in the same uncommitted H working tree. The main studio continues to use the owner's chosen output folder.
