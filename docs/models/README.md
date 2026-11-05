# Model reference cards

Start here before researching a studio model again. Keep the accumulated findings as the foundation; refresh only the facts needed for the next decision. Cards describe **our checkpoint, quantisation, adapters and exposed modes**, not everything advertised for the model family.

| Section | Cards |
|---|---|
| [Image](image/README.md) | Z-Image Turbo, FLUX.3 Klein, Qwen Image 2.1, Qwen Image Edit 2511 |
| [Video](video/README.md) | LTX 2.5, Wan TI2V, Wan S2V, Wan Animate 2, SCAIL-2, FreeVideo H3 |
| [Music](music/README.md) | ACE-Step 1.5 Turbo, MiniMax Music3, Stable Audio 3 Medium |
| [Voiceover](voiceover/README.md) | VoxCPM2, Qwen3-TTS 0.6B Base |
| [Text](text/README.md) | Gemma 4 family, shared by chat and planners |
| [Speech](speech/README.md) | Whisper variants and Russian punctuation |

The Director is an orchestrator, not another model. Auxiliary encoders, VAEs, upsamplers and ControlNets belong with their parent engine; exact files, sizes and SHA-256 remain in `App/config/component-catalog.json`. A registry entry or card does **not** mean the component is installed or enabled.

Historical local timings usually refer to Windows, RTX 5070 Ti 16 GB, roughly 62 GB RAM and models on a SATA HDD. Keep cold/warm load conditions with each number; if the report does not say, the condition is unknown. GPU admission thresholds are estimates, not peak measurements.

## Reading evidence

| Mark | What it establishes |
|---|---|
| **[code]** | Current source/config implements this contract; no claim about generated quality. Include review date and source file. |
| **[recorded]** | A dated internal report records a local observation. Follow its evidence link; it is not a new run or a guarantee across seeds. |
| **[CPU]** | A named test checked a rule, graph, numerical result or fake-model protocol. |
| **[UI]** | A browser test exercised the actual controls. State whether the backend was fake or real. |
| **[GPU]** | A real model job ran with the stated checkpoint/settings. File validity alone does not establish visual or listening quality. |
| **[sources]** | Publisher documentation or independent experience; identify which. Community anecdotes remain hypotheses. |
| **[unknown]** | Not checked, missing evidence or a proposed capability. |

Use a date, scope and evidence pointer with a result, for example: `[UI, fake backend, 2026-11-01]` or `[recorded, three-reference scene, 2026-10-29]`. These marks are **not interchangeable**: unit tests cannot prove good faces, Whisper cannot prove natural speech or lip sync, and a successful job cannot prove prompt adherence. Unmarked prose in the other draft cards is a summary awaiting an evidence audit; do not promote it to a confirmed finding.

**Current coverage (2026-11-06):** all five Image models (Z-Image, FLUX.3 Klein, Qwen Image 2.1, Qwen Edit 2511 and FIBO 2.0 Dev) have researched per-mode prompt guides and evidence checklists. The first four have scoped real-generation observations; FIBO's new integration records qualification separately in its card. The other sections' cards are development drafts seeded from project reports, not newly researched or requalified models. Gemma and Whisper cards cover families: record the exact variant for every comparison.

## Refresh and verify

1. Read the card and relevant source first. Follow a deeper report only for the claim needed. Do not re-google unchanged basics or copy a long report into a card.
2. If a version, licence, upstream feature or recommendation matters now, search afresh using the **site, publisher/repository, page title and topic** named in the card. Prefer the publisher for capabilities; label independent experience separately. Website names alone are too broad, so retain these search handles and the research date. Short cards need no full external URLs; preserve precise citations already present in deeper research reports.
3. Check applicability to our quantisation, checkpoint, adapter and mode. An upstream capability may be absent from our graph or UI. Update the card's review date and affected claim; preserve older scoped measurements unless evidence supersedes them.
4. Verify the change at the right level: focused CPU tests for validators/graphs; the actual UI for controls, source retention, language and cancellation; real generated files for quality. Compare fixed seeds/settings, inspect images or video frames, measure audio, and use listening judgement where needed. Give a time estimate and obtain the owner's approval **before GPU inference**, as required by AGENTS.md. Use C user Temp for fixtures and keep heavy inference sequential.
5. Record exactly what ran, what passed or failed, and what remains unchecked. A plan or suggested test is never PASS. Failed cases belong in the card too. Do not generalise one subject/seed or CPU test to all modes.

## Add or maintain a card

Copy [model-card-template.md](model-card-template.md) to `<section>/<section>-<model>.md`; check case-insensitive basename uniqueness first. Keep the useful summary short: identity, strengths/weaknesses, expected limits, a mode table, prompt rules, evidence and next checks. Examples must say **tested** or **illustrative**. Put long logs and comparisons in a linked report.

When adding/removing an engine or changing its checkpoint, update its card, section index and registry/catalog together. Never fill a placeholder by extrapolating from another member of the model family. Source/config remains authoritative when prose drifts.
