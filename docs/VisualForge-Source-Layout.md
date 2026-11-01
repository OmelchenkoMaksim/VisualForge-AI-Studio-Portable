# VisualForge source names and navigation

The publishable source set is selected by `.gitignore`: application code, configuration,
workflows, documentation, tests and tools. Installed models, runtimes and user data stay
outside that set. `Support/Docs` and the installation's `AGENTS.md` are local navigation
material; only `Support/Licenses` is published from `Support`.

Use short names that identify the feature when a file is viewed outside its directory:

Every new project-owned filename must be unique across source directories, compared case-insensitively. Check the Git source file list
before creating or renaming a file. Add the section, model or feature instead of duplicating a generic basename: use
`image_prompt_enhancer.py`, `video_prompt_enhancer.py`, `music_prompt_enhancer.py` and `voiceover_prompt_enhancer.py`.
The conventional discovery names below are exceptions; vendor/runtime files retain their upstream names.

- Python modules use underscores: `image_registry.py`, `speech_worker.py`,
  `gpu_coordinator.py`, `studio_server.py`.
- Browser assets use hyphens: `image-studio.js`, `video-panel.css`,
  `interface-strings.js`, `studio-index.html`.
- Translation catalogs use `<section>-strings.json`. The section in resource keys stays
  unchanged, for example `image.*` comes from `image-strings.json`.
- API workflows use a model prefix: `qwen2511-single.api.json`,
  `qwen21-reference.api.json`, `flux3-layout.api.json`, `zimage-tile.api.json`,
  `stableaudio3-text.api.json`. Mode IDs and model IDs remain independent of filenames.
- Documentation uses a product/model name and a topic, such as
  `VisualForge-Voice-Input.md` or `Qwen-Image-2.1.md`.
  Short model references live in `docs/models/<section>/<section>-<model>.md`
  ([index and evidence rules](models/README.md)); longer capability/research reports stay in `docs/`.

Keep conventional discovery filenames when another tool depends on them: `AGENTS.md`, `README.md`,
`__init__.py`, `__main__.py`, `sitecustomize.py`, Git configuration/hooks and license notices.
Do not add forwarding copies under former generic names: update callers and references
together, so the published tree has one source for each implementation.

Start the bridge through `App/Start-VisualForge.ps1`; its Python entry is
`App/server/studio_server.py`. Image logic is in `App/server/engines`, video in
`App/server/video`, audio in `music`, `speech` and `voiceover`, chat in `text`,
coding agents in `agents`, and component installation in `provision`.
The automatic creative workspaces use `App/server/director` for Video
([Director](VisualForge-Director.md)) and `App/server/composition` for Image
([Composition](VisualForge-Composition.md)); their stages call the studio's regular APIs.

After a rename, check Python imports, subprocess entry paths, the bridge's `STATIC`
table, HTML asset URLs, dynamic catalog/workflow filename construction, tests and
documentation links. Regenerate the browser strings with `tools/build_strings.py`.
