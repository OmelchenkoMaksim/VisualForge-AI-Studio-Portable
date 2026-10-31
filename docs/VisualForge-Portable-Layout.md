# Portable filesystem contract

The root of an installation holds `VisualForge AI Studio.exe`, `App`, `Runtime`, `Models`, `UserData` and `Support`. Only `App`, `docs`, `tests`, `tools`, `Support/Licenses` and a few root files
are source (`.gitignore` is an allow-list); everything the setup downloads or the studio produces stays out of Git.

| Directory | Responsibility |
|---|---|
| `App/server` | the Python bridge and the engine adapters |
| `App/frontend` | HTML, JavaScript, CSS and optional branding |
| `App/workflows` | ComfyUI API workflows of the Image models |
| `App/config` | the shared layout contract, the component catalog, external model paths, pinned asset lists |
| `Runtime/ComfyUI_windows_portable` | the unsplit ComfyUI and its embedded Python (Image) |
| `Runtime/Video`, `Runtime/Music`, `Runtime/Text`, `Runtime/Agents`, `Runtime/Tools` | the isolated runtimes of the other sections and small helper programs |
| `Models` | all weights, including auxiliary VAEs and LoRAs |
| `UserData/Results` | default outputs |
| `UserData/Settings` | application settings, ports, ComfyUI settings, job histories, verification summaries |
| `UserData/Prompts` | the prompt archive |
| `UserData/BrowserProfiles` | the application window's browser profiles |
| `UserData/Logs` | supervisor, bridge, backend and job logs |
| `UserData/Cache` | uploads, temporary files, process markers, library caches |
| `UserData/Backups` | original settings, source backups, the migration journal |
| `Support` | licenses, the recovery entry point (`Recovery.cmd`), migration utilities, diagnostics, local documentation |

* `App/config/portable-layout.json` is the single path contract. The native EXE reads its own directory, resolves its supervisor from that contract and passes
  `VISUALFORGE_PORTABLE_ROOT`; PowerShell resolves the same contract and Python finds it from that variable or from its module ancestors. No service depends on the working directory.
  Paths stay inside the portable root; only an output folder the person chose may be outside it.
* The ComfyUI bundle stays whole because its import paths rely on its internal layout; the weights live in `Models`, reached through `App/config/extra_model_paths.yaml`
  (its `../../Models` base is resolved against the YAML's directory). Command-line flags redirect ComfyUI's input, output, temp and user directories into `UserData`. The legacy
  result folder maps to `UserData/Results/LegacyAutosaves`; existing image names are kept.
* A missing external output path produces an actionable warning, never a silent redirect. No writable links are used.
* Files the layout migration did not recognise are kept under `Support/Diagnostics/PreservedUnknown` with their relative path; the original launchers are historical rollback artifacts under
  `Support/LegacyLaunchers`. The recovery entry point of this layout is `Support/Recovery.cmd`.
* Caches that need an ASCII path (CUDA/Triton compilers) live in the host's Windows Temp, not under the portable root: a Cyrillic folder name breaks the compiler's file lookup.

ComfyUI's external model configuration: <https://docs.comfy.org/basic-concepts/models>.
