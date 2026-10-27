# Third-party components

VisualForge AI Studio does not contain these components. The setup program downloads them from their original publishers into
`Runtime/` and `Models/`, after checking the SHA-256 recorded in [App/config/components.json](App/config/components.json). Each keeps
its own license; the texts that ship with this repository are in [Support/Licenses](Support/Licenses). Read the license of every model
before using it: the descriptions below are a summary for orientation, not legal advice.

## Programs (always installed)

| Component | License | Source |
|---|---|---|
| ComfyUI 0.42.1 portable (Image engine); ComfyUI at a pinned commit (Video/Wan nodes) | GPL-3.0 | github.com/Comfy-Org/ComfyUI |
| Embedded Python (ComfyUI portable), CPython 3.12 builds (python-build-standalone, python.org) | PSF-2.0 | python.org, github.com/astral-sh/python-build-standalone |
| PyTorch, torchvision, torchaudio | BSD-3-Clause | download.pytorch.org |
| SageAttention (Windows build) | Apache-2.0 | github.com/woct0rdho/SageAttention |
| FreeVideo 0.4.19 | Apache-2.0 | github.com/FlashML-org/FreeVideo |
| VDN / MiniMax H3 sources and the patched `diffusers` | Apache-2.0 | github.com/OpenVDN/vdn-minimax-h3, github.com/huggingface/diffusers |
| ACE-Step 1.5 sources | MIT | github.com/ace-step/ACE-Step-1.5 |
| ffmpeg 9.0.2 essentials build | GPL-3.0 build (gyan.dev) | github.com/GyanD/codexffmpeg |
| Ollama 0.33.3 (private copy, ROCm libraries omitted) | MIT, plus bundled libraries | github.com/ollama/ollama |
| OpenCode 1.19.4 (agent mode of the Text tab) | MIT | github.com/anomalyco/opencode |
| Aider 0.86.2 (agent mode of the Text tab) and its Python packages (108, exact versions and hashes in `App/config/aider-assets.json`) | Apache-2.0 (Aider); each package its own license | github.com/Aider-AI/aider, PyPI |
| DeepSeek Harness 0.2.1-rc.2 (agent mode of the Text tab) and its npm packages (609, exact versions and hashes in `App/config/dsh-assets.json`) | MIT (DeepSeek Harness); each package its own license | github.com/deepseek-ai/deepseek-harness, npmjs.com |
| Node.js 24.21.0 (`node.exe`, runtime of DeepSeek Harness) | MIT, plus bundled third-party licences (`LICENSE`) | nodejs.org |
| 7-Zip console unpacker `7zr.exe` | LGPL | github.com/ip7z/7zip |
| Microsoft C++ runtime DLLs (Music runtime) | Microsoft redistributable terms (`MSVC-LICENSE.rtf`) | taken from the signed `vc_redist.x64.exe` without running it |
| Python packages (about 140 in the Video and Music runtimes) | each package's own license | PyPI (exact versions and hashes in the catalog) |

## Models (optional, downloaded on request)

| Model | License | Notes |
|---|---|---|
| Qwen Image Edit 2511 FP8 | Apache-2.0 | Comfy-Org repackaging |
| Qwen Image Edit NSFW LoRA | OpenRAIL++ | community LoRA |
| Qwen Image 2.1 Uncensored FP8 | Qwen Research License, **non-commercial** | community quantization; unfiltered output |
| Z-Image Turbo INT8 and Qwen3-4B FP8 text encoder | Apache-2.0 | Comfy-Org repackaging of Tongyi-MAI/Z-Image-Turbo; text-to-image only |
| FLUX.3 Klein distilled INT8 | FLUX Kommunity License v1.0, **non-commercial** unless you are a Qualifying User (gross annualized revenue under US$5M, with content filtering or output review and AI disclosure as the license requires) | Black Forest Labs; gated repository, the terms must be accepted on Hugging Face; the license text is downloaded to `Models/Image/FLUX3-Klein/LICENSE.md`; outputs must not be used to improve another AI model |
| Qwen3-VL-4B INT8 text encoder and Qwen3.5-9B INT8 FLUX.3 prompt upsampler | Apache-2.0 | packaged with FLUX.3 Klein |
| `ae.safetensors` (VAE shipped with the Z-Image Turbo package) | not stated | the Comfy-Org and Tongyi-MAI cards name neither its origin nor its license; check the terms before commercial use |
| Real-ESRGAN x2plus | BSD-3-Clause | |
| FreeVideo H3 / MiniMax H3 INT8 | MiniMax H3 Community License (its LICENSE file is downloaded with the model) | the license text limits its "Applicable Territory" and excludes the European Union, the United Kingdom, the Republic of Korea and the United States; read it before use |
| Qwen3-TTS 0.6B | Apache-2.0 | |
| Wan 2.2 TI2V-5B, Wan 2.2 S2V-14B, UMT5-XXL encoder | Apache-2.0 | Comfy-Org repackaging |
| LTX 2.5 Distilled INT8 ConvRot, dedicated Gemma 4 encoder, video/audio VAEs and upsampler | LTX 2.x Community License | Official Lightricks/LTX-2.5 assets; model access terms must be accepted by the user. See docs/LTX25.md. |
| ACE-Step 1.5 Turbo, 0.6B planner | MIT | |
| Stable Audio 3 Medium | Stability AI Community License (non-commercial and limited commercial use, read `Models/Music/StableAudio3/LICENSE.md` and stability.ai/license) | package of Comfy-Org (huggingface.co/Comfy-Org/stable-audio-3); model by Stability AI |
| T5Gemma text encoder of Stable Audio 3 | Gemma Terms of Use (ai.google.dev/gemma/terms), including its use restrictions | redistributed by Stability AI and Comfy-Org; its license file sits behind the gate and is not downloaded |
| MiniMax Music3 INT8 | MiniMax Music3 Community License | its LICENSE file is downloaded with the model; read its conditions |
| Whisper large-v3-turbo (voice input) | MIT | OpenAI, github.com/openai/whisper; downloaded from huggingface.co/openai |
| Whisper small, Whisper large-v3 (voice input) | Apache-2.0 | OpenAI, huggingface.co/openai |
| Gemma 4 and other Ollama models | the model's own license, shown by Ollama | |

The workflows in `App/workflows` are modified adaptations of templates from github.com/Comfy-Org/workflow_templates (MIT); see
[Support/Licenses/Workflow-Templates.txt](Support/Licenses/Workflow-Templates.txt).
