# Text models (Gemma) and the planners built on them

Evidence: [card rules](../README.md). Section comparisons are draft summaries of the linked cards/reports; **[recorded]** means historical evidence, not a new test. Source/config is authoritative; availability still depends on installation and policy.

| Card | What |
|---|---|
| [Gemma 4](text-gemma-4.md) | the local chat model and the "brain" of every planner: prompt rewrites for Image and Video, the scene planner, the Director's plan, clip review, screenshot reading, dictation editing |

## Rules that hold for the Text section

* **Gemma is required** (the studio needs one for rewrites and plans). A fresh install gets E4B by default; other sizes by choice (E2B, E4B, 26B A4B from the Ollama registry or its mirrors, a local GGUF, or an import from the user's Ollama). The model that is installed decides what the numbers below mean.
* **Every planner is a `GemmaTask`** (`App/server/video/gemma_task.py`): it claims `text` on the GPU coordinator for the call and gives it back, asks with `think=False`, and never holds the global GPU lock for a chat answer; a route never calls Ollama directly.
* **Gemma writes English for the networks**; quoted speech and lettering stay in the person's language.
* **Gemma never decides the numbers of a job**: it proposes JSON and code (`plan.normalize`, the validators of the routes) disposes. Its answers vary from run to run and ignore instructions in ways that are cheap to repair by code and expensive to repair by prompt (see the Gemma card).
* **Gemma cannot hear**; it sees pictures (a few frames of a video) and reads numbers.

## When you need to check or extend this section
Run the planner through the UI and read what it wrote: the Director keeps Gemma's raw answers in `UserData/Logs/Director/plans.log`. Unit tests use a fake Gemma (`tests/test_scene_planner.py`, `test_director_brain.py`, `test_prompt_enhancer.py`). Report: [VisualForge-Text-Chat.md](../../VisualForge-Text-Chat.md).
