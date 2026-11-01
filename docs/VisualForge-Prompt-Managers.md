# Prompt managers: Video, Image and remaining audio drafts

Video already has the working example: `App/server/video/video_prompt_enhancer.py`. `GUIDES[engine][mode][variant]` holds a separate instruction for each supported pair;
`DEFAULT_VARIANT` chooses the version justified by the recorded comparisons. A single universal caption is not a substitute for these instructions. The manager passes
reference pictures in their assigned roles, cleans the answer, retries malformed output once and returns an editable prompt. The original request remains in history.

The system instructions and model-facing prompts are English. The exact words of speech, lyrics and visible lettering stay in the person's language, including Russian,
without a translated duplicate. `GemmaTask.ask` sends `think=False`: reasoning is disabled, as in the existing Video implementation, rather than exposing another reasoning
stream. `GemmaTask.session` claims `text` under the submission lock, releases the lock while answering, participates in Stop, and always clears its Text owner.

## Image: all four current models are connected

The separate Image manager now automatically prepares Z-Image Turbo requests in Prompt, Detail, Control, Inpaint and Tile. The backend
calls it from `/api/generate`, after input validation and before Image admission, so the page and direct API clients behave alike.
The page automatically offers request-specific numbered choices only when Gemma finds material ambiguity. Compatible choices immediately
continue; an explicit exclusive-group conflict permits one final clarification. Server code caps the flow at two questions, then resolves
remaining conflicts by last selection. The original request, choices, generated caption, Gemma model and guide version remain in history.
See [Z-Image-Prompting](Z-Image-Prompting.md) for the protocol, per-mode decisions and CPU/GPU evidence limits.

`App/server/engines/zimage_prompts.py` contains the Z-Image instructions. Since 2026-11-01, `klein_prompts.py` activates Klein's Prompt, Single,
Reference, Multi and Layout using the same lifecycle and bounded clarification protocol, with its own mode-specific decisions. All validated references reach
Gemma in order; Layout sends the fixed boxes and a matching positional brief. Output must cite each box ID once; code appends the original table once.
Klein's native Qwen3.5 upsampler is bypassed, its UI switch hidden and preflight checks the pruned graph, avoiding a second rewrite or a dependency on unused nodes/weights.
The stored native workflows remain available to direct tooling. [Klein's card](models/image/image-flux3-klein.md) records publisher/community sources and CPU/UI evidence;
real Gemma/image quality remains unverified. `ImagePromptEnhancement` records preparation separately from native `ImagePromptExpansion`.
`qwen21_prompts.py` activates Qwen Image 2.1's Prompt, Single, Reference and Multi with its own rules and clarification decisions.
Single uses no reference tags; multiple inputs require every real `<imageN>` tag. Original alpha travels to the image encoder;
Gemma receives a neutral-matte preview plus original alpha metadata. Code retains explicit RGBA cues after rewriting.
Its native T2I/I2I PE is bypassed and hidden, so legacy flags cannot schedule a second rewrite or block managed alpha requests.
[Qwen 2.1's card](models/image/image-qwen-image-21.md) distinguishes upstream/community guidance, old native-PE alpha failures and new CPU checks from pending real-Gemma quality.
`qwen2511_prompts.py` activates Qwen Image Edit 2511's four modes, with distinct Standard/Lightning profile rules.
It uses the Plus encoder's `Picture 1`…`Picture 3` references, not Qwen 2.1 tags. Multi accepts exactly three sources;
Prompt sends no user pictures to Gemma and later edits one generated neutral canvas. The original negative prompt and adapter/profile choices
remain unchanged and are bound to waiting clarifications. A later change invalidates the answer instead of reusing stale choices.
[Edit 2511's card](models/image/image-qwen-edit-2511.md) records source research and separates CPU/UI routing from pending real-Gemma quality.
Video's `clarification_status()` and `prepare_clarification()` are inactive placeholders: no Video route or control invokes them.

## Remaining drafts (not connected)

| Section | File | Draft scope and protected data |
|---|---|---|
| Music | `App/server/music/music_prompt_enhancer.py` | ACE-Step and MiniMax instrumental/song modes; Stable Audio's audio mode. The English output is a musical caption; lyrics and vocal language remain separate and verbatim. |
| Voiceover | `App/server/voiceover/voiceover_prompt_enhancer.py` | One `speech` draft per current engine. The proposed English output is separate delivery guidance; source text, sample transcript, language and pronunciation dictionary remain verbatim. |

The common scaffold is `App/server/prompt_template.py`. Each section has its own `PromptEnhancer`, Text `OWNER`, planned event name, model rules, mode rules, output field
and context contract. `GUIDE_TEMPLATE` and the language policy build reviewable system instructions; `draft()` prepares messages without inference or persistence.

Music and Voiceover report `available=False`, `state="template"`; `supported()` is false and `enhance()` raises `NotImplementedError` before any GPU claim or model call.
Their planned event names remain unregistered. All current Image pairs are active; unknown model/mode pairs are rejected before inference. Text and Agent have no new manager.
The remaining drafts are an implementation starting point, not verified prompt-quality improvements.

Example for inspecting prepared instructions without loading a model (this is not a Gemma answer):

```python
from engines.image_prompt_enhancer import PromptEnhancer

manager = PromptEnhancer(None, None)
draft = manager.draft(
    'Нарисуй кафе с надписью «КОФЕ У ПЕТРА»',
    'qwen-edit-2511', 'prompt',
    context={'lettering': 'КОФЕ У ПЕТРА', 'canvas': 'landscape'},
)
# draft['messages'] contains the English system instruction and the unchanged request/context.
```

## Existing features that the drafts do not replace

* Qwen Image 2.1's optional native T2I/I2I expansion and Klein's native upsampler remain available to direct tooling, recorded as `ImagePromptExpansion`.
  The studio's active Gemma paths bypass them. Each route must have exactly one final-prompt owner.
* ACE-Step's optional 0.6B planner is a music-specific generation stage, not Gemma rewriting the user's caption.
* Voiceover's normalization, pronunciation dictionaries and Whisper verification prepare/check speech. They are not prompt expansion. The installed Qwen Base and VoxCPM2
  cloning workers have no general style-prompt field: English guidance must never be concatenated with the spoken text. VoxCPM2 Voice Design is a separate built-in-voice workflow.

## Activation checklist

1. Research one model at a time: publisher guidance, then first-hand community experience. Match each mode to the installed adapter and record which rules are inferred.
   Record scoped real comparisons when run with the owner's GPU approval; do not present research or CPU mocks as measured image quality.
2. Implement the section's `enhance()` result, bounds, cancellation, bounded retry and exact-quote/verbatim checks using `GemmaTask.session` and `ask`; do not call Ollama from a route.
3. Register its planned technical operation (`ImagePromptEnhancement`, `MusicPromptEnhancement`, `VoiceoverPromptEnhancement`) with localized labels; assign `OPERATION` only then.
4. Use a bounded same-origin route with component-policy checks and readiness. Validate references and controls before claiming the GPU. No whole-answer global lock.
5. Keep preparation automatic where requested, without requiring an editable preview or approval. Preserve history provenance and the original request; an asynchronous editable
   preview, if used, must reject stale results after a model/mode/reference change. Add RU/EN strings. For Voiceover, first define
   a supported adapter contract for the separate guidance; no unsupported engine controls or automatic rewriting of the narration.

CPU checks in `tests/test_prompt_templates.py` cover the guide matrix against current registries, distinct guides, shared language rules, exact preparation of Russian data,
unknown-pair rejection and the guarantee that inactive managers never call Text/GPU. They do not establish the quality of a future Gemma rewrite.
