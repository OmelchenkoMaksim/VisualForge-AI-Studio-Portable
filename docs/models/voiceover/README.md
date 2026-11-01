# Voiceover models: which one for what

Evidence: [card rules](../README.md). Section comparisons are draft summaries of the linked cards/reports; **[recorded]** means historical evidence, not a new test. Source/config is authoritative; availability still depends on installation and policy.

| Engine (card) | Id | Rate | Cloning | Memory | Speed (work per second of audio) | Choose it when |
|---|---|---|---|---|---|---|
| [VoxCPM2](voiceover-voxcpm2.md) | `voxcpm2` | **48 kHz** | reference sample alone (default) or with its transcript | 5.1 GB model, about 6.1 GB at work | 0.35 s with `torch.compile`, 0.79 s without; one fragment at a time | the first choice for books and for the Director's lines |
| [Qwen3-TTS 0.6B Base](voiceover-qwen3-tts.md) | `qwen3-tts-12hz-0.6b-base` | 24 kHz | speaker embedding of a 3-30 s sample | 2.3 GB, 7 GB with batches of 8 | 0.28-0.31 s (batches of 8) | the fallback; far less memory; batches |

## Rules that hold for the Voiceover section

* **Eight built-in voices** (four female, four male) are made on the user's machine once by VoxCPM2 **Voice Design** from English descriptions and chosen by measurements (heard back word for word, median pitch fits the gender, 9-24 s, not clipped); no real person's voice is copied. The repository keeps no audio.
* **After synthesis Whisper listens to every fragment** and repeats one that came out wrong; doubtful fragments are counted and flagged. The assembled audio is normalised with `loudnorm` I=-18, TP=-1.5.
* **Text preparation** happens before the engines: numbers, abbreviations and a built-in pronunciation dictionary (Russian); engines are given text that is already normalised.
* **Chapters** are the unit of the Director's speech jobs: lines are chapters of one text so that a job loads the model once; the finished file says where each begins.
* The Windows install path contains Cyrillic: VoxCPM2's `torch.compile` died on it until the cache folders were moved to the ASCII Temp **[recorded 2026-11-01]**; the worker also falls back to no compilation when the warm-up fails.
* Voices in the Director are heard over the picture with no lip sync.
* The separate Voiceover prompt manager is an inactive scaffold. Spoken text remains literal content in its language; do not translate it to English under a generic prompt rule.
* Candidates, **not integrated**: Qwen3-TTS 1.7B, MOSS-TTS v1.5, Chatterbox V3 **[sources]**.

## When you need to check or extend this section
Use the Voiceover tab through the UI with a short text and the built-in voices, then Whisper and your own ears (the owner's judgement is the only one that counts for naturalness). `tests/test_voiceover.py`. Full measurements: [VisualForge-Voiceover-Guide.md](../../VisualForge-Voiceover-Guide.md).
