## Application shell, themes and sounds

The page is a 56 px top bar plus a section rail on the left. The top bar shows the brand, the model and current mode, the readiness pill (full text as a tooltip), a hardware chip that opens the «Ваш компьютер» card as a popover, the light/dark switch and the gear menu. The rail holds Image, Video, Music and Text; one indicator slides to the selected section. Image, Video and Music place the form left and the result right; the Image result column stays in view while the form scrolls, and its start button stays at the bottom of the form. Long static explanations fold behind «Что это значит?». Below 1100 px the forms take one column; below 900 px the rail becomes a row of tabs.

All colors are variables in `App/frontend/theme.css`, defined for `data-theme="dark"` and `"light"` on `<html>`; the accent is Indigo. `theme-init.js` applies the saved theme before the first paint. The sun (dark theme) / moon (light theme) button switches it with a View Transition circle reveal, or instantly when the browser lacks it or reduced motion is requested. Motion uses only transform and opacity. The theme is saved in the browser profile's localStorage (`vf-theme`).

The gear menu's «Звуки» chooses the completion melody for success and for errors (or none) and the volume, with previews. `sounds.js` synthesizes every melody with Web Audio (no audio files); each lasts at most 7 s including the reverb tail. Choices are kept in localStorage (`vf-sounds`). Notifications sound only after the page has had a click or key press, as browsers require. `StudioSounds.render(kind, id)` renders a preset silently and reports its audible length and peak.

## Model-aware module headers

Image, Video, Music and Text share the same banner: the selected model above the studio name, the current mode below it, and a readiness line. Each module owns a cached header state; changing tabs restores that state immediately. Late responses from an inactive module update only its cache. Readiness reflects the existing model/runtime checks, not a completed GPU test or guaranteed generation. Text distinguishes text-only and vision builds, and does not load Ollama weights to render its header.

## Settings menu

The gear button at the top right opens Components and Logs. Components opens the existing installer workspace; Logs opens the existing log dialog. The four creative modules remain in the main navigation. The menu closes after choosing an action, clicking outside, moving keyboard focus outside, or pressing Escape. Arrow keys, Home and End navigate its actions; Escape returns focus to the gear. The menu stays available during generation, while the existing installation admission rules still apply.

# Folder selection and completion feedback

Image, Video and Music share the disk/directory browser. Each saves its own output setting. Selecting a folder commits it immediately; cancel leaves the setting unchanged. On first opening, a missing configured directory falls back to its nearest existing parent without creating folders. Manual navigation still reports missing paths.

Terminal jobs produce a persistent in-app notification and a short local synthesized chime: ascending for success, descending for errors. Cancelled jobs and opening historical results do not notify. Duplicate terminal responses are ignored. Browser audio is unlocked on the first pointer/key interaction; browser/Windows mute and autoplay restrictions still apply. Success opens the actual job output directory, independently of later setting changes. Errors open the module's bounded job log; pre-submission failures have no job log yet and show their message directly.

The `/api/open-output-dir` POST route opens an existing absolute directory through Windows `os.startfile`, with the bridge's existing same-origin checks and a 4096-byte request limit. It does not create directories or change settings.

## Model date provenance

Dates displayed beside model names distinguish model releases from publication of converted weights. The presentation catalog is `App/frontend/studio-ui.js`; model identifiers and inference settings are unchanged. Checked against author sources on 2026-10-24:

| Installed choice | Displayed date | Meaning and author source |
|---|---|---|
| Qwen Image Edit 2511 | 2025-12-23 | Model/weights release, [Qwen news](https://github.com/QwenLM/Qwen-Image). |
| Qwen Image 2.1 Uncensored FP8 | 2026-09-23 | FP8 upload `3100d25aa1249cd6d22a384fe504f6fde8fbdd6a`; corrected ComfyUI metadata `40319fb15542f0ad22921e0124a191a8a935a60a` same day, [author history](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized/commits/main). Base Qwen Image 2.1 was released [2026-09-20](https://qwen.ai/blog?id=qwen-image-2.1); this is a different date. |
| ACE-Step 1.5 Turbo | 2026-01-23 | First Turbo weights upload `571cee88618ae5ef3d3fcdf79c46068ce4e2d727`, [author history](https://huggingface.co/ACE-Step/Ace-Step1.5/commits/main). The historical tree contains `acestep-v15-turbo/model.safetensors` (4,787,825,604 bytes). This is a weights publication date, not the February software tags or a claim about the public announcement date. |
| FreeVideo / MiniMax H3 | 2026-09-13 | VDN-H3 Edge prepared-weight publication `4f16eb1066d96592b84be0de532069f5d69fcfa9`, [author history](https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/commits/main). Not the proprietary MiniMax base model release or FreeVideo software release. Installed revision `da37681f5a1f7015fdb63482142b865111b687d3` was uploaded 2026-10-05. |
| Wan 2.2 TI2V-5B | 2025-07-28 | Model release, [Wan news](https://github.com/Wan-Video/Wan2.2). |
| Wan 2.2 S2V-14B | 2025-08-26 | Model release, [Wan news](https://github.com/Wan-Video/Wan2.2). |

Additional dates checked on 2026-10-26:

| Installed choice | Displayed date | Meaning and author source |
|---|---|---|
| LTX 2.5 Distilled | 2026-08-11 | LTX 2.5 release; [author release v1.2.0](https://github.com/Lightricks/LTX-2/releases/tag/v1.2.0) adds LTX 2.5 support. |
| Q38Light | 2026-08-14 | Base Qwen3.8-27B release, [Qwen news](https://github.com/QwenLM/Qwen3.8#news). Local GGUF metadata names Qwen3.8-27B and links to Qwen/Qwen3.8-27B. |
| Hauhau Q6 | 2026-04-16 | Base Qwen3.6-35B-A3B release, [Qwen news](https://github.com/QwenLM/Qwen3.8#news). Local GGUF names Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive. |
| Gemma 4 A4B | 2026-03-31 | Base Gemma 4 26B A4B release, [Google release log](https://ai.google.dev/gemma/docs/releases#march_31_2026). Local Ollama config identifies Gemma 4 and the installed MoE weights identify the 26B build. Google announcement posts are dated April 2; this label follows its explicit model release log. |

Text labels explicitly say вЂњРІС‹РїСѓСЃРє Р±Р°Р·С‹вЂќ. Publication dates of the user's tuned Ollama builds are unknown; import times and file timestamps are not release dates. Model selectors and the availability summary use the shared presentation catalog, with the provenance URL in the tooltip. Unknown text builds receive no inferred date.

No GPU inference changes, new model downloads or runtime dependency changes are required for this interface update.

## Launch splash

From the moment the launcher runs `App/Start-VisualForge.ps1` a 100x100 spinner with the studio icon stays above all windows (WPF,
own STA runspace, `Topmost`, never takes focus). The supervisor opens the page with `?launch=<nonce>`; the page, once its ComfyUI check
(`/api/preflight`) has finished and the Image controls are rendered, posts `/api/launch-ready` (the page then accepts a generation, or shows
why it cannot). The supervisor polls `GET /api/launch-ready?launch=<nonce>` every 250 ms and closes the splash; it also closes it when the
window was already open, on any startup error (before the error box), when the window closes, and after 3 minutes at most. The nonce
keeps a reused bridge from closing a new launch's splash early. A failure to show the splash never stops the launch.

## Windows notification area

The launcher owns one notification icon with the normal VisualForge logo while the window is open. Its two context menu commands restore/maximize/focus the isolated studio window, or exit the studio completely. Double-click also restores the window. Closing the normal application window still exits as before; closing to tray is not enabled.

A dedicated STA WinForms loop animates 24 pre-rendered logo rotations every 90 ms while `/api/busy` reports generation. The supervisor polls once per loop (about one second); a temporary failed request keeps the last known state. Idle returns to the unrotated logo. This covers Image, Video, Music and Text, including voice prompt polishing. CPU-only exports and downloads do not rotate the generation icon. Animation and native icon/GDI handles are disposed on shutdown.

Full exit first matches the exact isolated browser executable and complete profile argument, rechecks process identity, then terminates that process tree. The existing bridge/backend ownership checks remain mandatory; owned service trees are stopped including their workers, and workers also use kill-on-close job objects. Independently started Ollama, browsers and ComfyUI are preserved. Active studio work is interrupted when choosing full exit.
