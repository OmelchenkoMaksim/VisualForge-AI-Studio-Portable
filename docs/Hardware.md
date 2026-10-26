# Local PC summary and model compatibility

The header's **Ваш компьютер** side panel shows the CPU name, graphics cards, dedicated VRAM and physically installed RAM. It stays available in Image, Video, Music and Text. On narrower windows it moves into the header flow. Selecting a model opens one dismissible compatibility card; the panel's status button reopens it. Changing modules dismisses the card, and Escape closes it without taking focus from the model controls.

The bridge exposes `GET /api/hardware` behind its existing loopback/origin checks. CPU comes from the Windows processor registry key, RAM from `GetPhysicallyInstalledSystemMemory`, and NVIDIA names/VRAM from `nvidia-smi`. If NVIDIA detection fails, Windows CIM supplies adapter names only. WMI's `AdapterRAM` is deliberately ignored because it can wrap on modern GPUs; missing VRAM remains unknown. No model is loaded, GPU benchmark performed, hardware identifiers collected, network request sent or machine report persisted. Inventory is cached for 60 seconds. **Обновить** requests a fresh inventory with a five-second scan cooldown. Model inventories are read afresh without inference.

## Meaning of colours

- **Green**: OS/architecture, detected CUDA accelerator and memory meet the starting profile. It is not a promise about generation time, arbitrary resolutions or driver/kernel compatibility on every NVIDIA generation.
- **Yellow**: RAM/VRAM below the recommendation, CPU/RAM offload for Text, or incomplete detection. This does not claim a lower-memory configuration was GPU-tested.
- **Red**: a known mandatory requirement is unmet: this portable package requires Windows x64, native Image/Video/Music pipelines require NVIDIA CUDA, total VRAM is below the existing launch gate, or ACE-Step is below its stated 16 GiB RAM minimum. The reason is shown explicitly.

These are advisory cards. Existing runtime readiness and free-memory checks still decide actual launches. Installed VRAM is capacity, not currently free memory. Closing another application may free memory but cannot increase capacity. Multiple GPUs are shown separately; capacity is never summed, and the assessment uses the first detected NVIDIA adapter as the default native device. Custom external CUDA device overrides are not resolved by this advisory check.

## Starting profiles

These are studio recommendations for the shipped quantizations/offload paths, not universal upstream minimum specifications. Memory units are GiB, shown as ГБ consistently with the rest of the interface. VRAM recommendations allow a 0.25 GiB driver-reporting margin; the mandatory launch gate uses the reported capacity without that margin. Display rounds capacities within 0.15 GiB of an integer for familiar board sizes.

| Profile | Recommended VRAM | Recommended RAM | Source of recommendation / mandatory launch gate |
|---|---:|---:|---|
| Qwen Image Edit 2511 / Qwen Image 2.1 Uncensored FP8 | 16 | 64 | Studio verified-machine memory budget; lower-memory minimum unqualified. |
| FreeVideo H3 | 16 | 64 | Studio tested profile in User Guide; bridge launch gate 12000 MiB free VRAM. |
| Wan 2.2 TI2V | 16 | 32 | Video registry; launch gate 6500 MiB free VRAM. |
| Wan 2.2 S2V | 16 | 64 | Tested native offload profile; launch gate 12000 MiB free VRAM. |
| LTX 2.5 Distilled | 16 | 64 | Video registry and LTX acceptance; launch gate 8000 MiB free VRAM. |
| ACE-Step 1.5 Turbo | 16 | 32 | Music registry, RAM minimum 16; Music launch gate 8000 MiB free VRAM. |
| MiniMax Music3 INT8 | 16 | 64 | Music registry; Music launch gate 8000 MiB free VRAM. |
| Installed Text builds | Estimated per build | Estimated per build | GPU budget: weight/projector bytes plus 1 GiB, rounded up to a common capacity tier (8/12/16/24/32/48/64, then multiples of 8). RAM budget: weights × 1.2 + 4 GiB, rounded up to a power of two, minimum 16. Explicitly labelled an estimate; context/KV cache varies by architecture. CPU/RAM fallback is yellow, never an unsupported-GPU red verdict. |

Requirements live in `App/server/hardware.py`; numeric launch gates are taken from the shipped Video registry and Music's existing bridge gate. Unknown text builds are assessed from their installed weight size. Missing metadata gets an unknown/yellow result, not a guessed green verdict. The comparison table includes recommended and detected VRAM/RAM on all three card colours.

## Verification

`tests/test_hardware.py` covers three severity levels, all shipped model profiles, missing/unsupported GPU, unknown RAM, multi-GPU isolation, runtime architecture, reporting tolerance, malformed NVIDIA output, cache copying and refresh limits. The real bridge smoke test checks the API and new frontend assets. UI checks use actual hardware for green/large-text yellow, and isolated fixture hardware for the 8 GiB yellow/red cases. These checks do not run model inference on smaller GPUs.

## Model requirements in every module

Each Image/Video/Music/Text selector has a collapsible **Требования модели** block. Its compact summary always shows recommended VRAM and model footprint; opening it shows the runtime launch gate separately from recommended memory, CPU/OS requirements, supported modes, output formats, resolutions/durations or text quantization/parameter count/context. Facts come from the shipped engine registries and bounded Ollama config/parameter layers, without loading weights. Music's old fixed MIT/48 kHz paragraph is replaced by per-model license/sample-rate facts; existing verification evidence remains separately available. Header hardware styling is scoped to the top-level header so Text's chat heading stays compact.

Storage comes from `model_requirements.py` using the component catalog and the installer's dependency closure/status/space-budget rules. The block distinguishes full model download, complete model footprint, a clean download including required software, estimated free space including unpacking/download cache and the installer's 8% reserve, and remaining download/additional free space on this installation. Shared encoders are included once in the model dependency closure. Optional image upscale / speech-cloning weights are listed separately and excluded from the base total. Results, generation scratch and the OS pagefile are not included. Availability is a presence/size check, not a weight hash verification. No download or installation starts from reading the block.

LTX's six pinned assets total 41,162,142,158 bytes (38.3 GiB). Wan totals include the shared text encoder, not the other Wan model. Installed Ollama builds use unique blob sizes plus the manifest for footprint, and show that a public download source is unspecified; they do not invent a registry download for custom local names. The separate Ollama runtime disk estimate is displayed. Partial/cache reuse and shared blobs may reduce transfer, but the full-copy budget remains visible.

`tests/test_model_requirements.py` checks pinned LTX totals and parity with the installer, shared Wan encoder inclusion, cold/partial/installed budgets, local Ollama source honesty and engine-specific capabilities. Text metadata tests check quantization, parameter count and full manifest/blob footprint without inference. HTTP smoke checks the enriched payload and frontend asset. UI checks cover all four module blocks and selection updates.
