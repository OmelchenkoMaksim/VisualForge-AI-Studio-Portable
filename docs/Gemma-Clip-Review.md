# Gemma watches the finished clip and corrects the prompt

Added 2026-10-31. Every finished clip of the Video history that was made from **one prompt** has the button **"Gemma review: what is wrong and how to fix it"**. Gemma (with vision, the
studio's own model) looks at six frames taken along the clip, is given the numbers that frames cannot show, and answers with a verdict, the problems with the frame numbers, a list of what it
changed, and a **corrected prompt** written in the style the video model reads best (the same hidden guide the rewrite uses). The button "Put the corrected prompt into the field" copies it into the prompt
box and turns the Gemma rewrite off, so it is not rewritten again; nothing is generated until the person presses Generate.

## What Gemma is given (`App/server/video/director_review.py`, route `POST /api/video/review`)

* six PNG frames (768 px wide) at the middle of six equal parts of the clip;
* measured facts: length, size, fps, **motion** (mean frame difference, put into words: almost static / calm / lively / very strong), hard cuts, burnt-out and crushed pixels, brightness of the first and
  last tenth, sound level (mean, loudest second, near-silent seconds);
* for a prompt with spoken lines: what **Whisper heard** in the clip against the requested words and the share of them heard (the same listener as the speech check of LTX scenes);
* the prompt that was sent, the person's own idea (when it differs), the mode, the levers of this model and its hidden prompt rules.

The answer is a JSON object (`verdict` good / fixable / bad, `score` 1-10 for how well the clip does what the prompt asks, `summary`, `problems`, `fixes`, `fixed_prompt`); a malformed answer is asked for once more.
The summary, problems and fixes come in the interface language, the corrected prompt in English. Takes 20 s to two minutes (the first call loads Gemma from the HDD).

## What was measured (2026-10-31, RTX 5070 Ti, Wan 2.2 TI2V-5B, 832x480, 81 frames, seed 11)

| Case | Verdict | Result |
|---|---|---|
| "A red fox walks through a snowy pine forest at dawn. The camera slowly follows it." sent as it is: the fox is a dark speck far away | fixable, 5/10: "the fox is too small and distant in frames 1-6", "wavy distortion of the trunks" | corrected prompt: medium shot, a large red fox as the central subject, a tracking camera; generated again with the same seed: **the fox is large, orange, clearly walking, the trees are stable** (contact sheet before / after) |
| LTX clip with a Russian spoken line, Whisper heard it exactly | good, 10/10 | `fixed_prompt` equals the original; Whisper line shown |
| A Wan clip that was already good | fixable, 6/10 | invented "motion blur" and "oversaturation": a review of a good clip may be a false alarm; read the problems against the clip |
| A two-scene LTX film | misread the planned hard cut as a defect | films of scenes or windows (several prompts) are refused for now |

One loop on one subject is an anecdote, not a statistic. The fix worked because the diagnosis (subject too small) is something both Gemma and the model respond to; do not expect it to repair
faces, hands or a bad seed.

## Limits

* One prompt per clip: scenes and windows (`segment_prompts`) are refused. Silent models get no sound facts; an S2V clip is judged by its frames (its voice is the input).
* The review never changes the seed, the size or the mode; it only writes the prompt.
* It needs Gemma installed and the card free of another job (Gemma loads like a chat answer; the next video job frees it).
* Not tested: Animate 2 and SCAIL-2 clips, portrait sizes, English interface.

Tests: `tests/test_director_review.py` (answer parsing, facts, real ffmpeg measurement of a small clip, the messages sent to Gemma, retry).
