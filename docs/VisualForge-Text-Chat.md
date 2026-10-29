# Text (local chat with an Ollama-format model)

The **Text** tab is a chat with history on a model that runs entirely inside the portable folder. Status: **implemented and
installed in release `2026.10.25-text.2` (text chat in `text.1`, pictures in messages in `text.2`; application files, runtime and
model verified by SHA-256; the installed EXE was not launched for these releases).**

> **In a fresh clone** the model is whatever you choose in the setup or in the Components tab (Gemma 4 E2B/E4B/26B from the Ollama registry,
> any other registry tag with `--text-model`, or a model copied from your own Ollama with `--import-ollama`). The model named below is the
> author's own fine-tune that this section was first built and tested with; it is not downloadable from the registry.

## Imported models and tuning (text.3)

The model selector is visible above the conversation, with concise names and weight sizes; the full Ollama tag remains in its tooltip.
Every complete manifest in the private store is discovered automatically. Text-only models disable image attachments.
Large models can require CPU/RAM offload on a 16 GB card; no generation-speed claim is made for imported models.

**Настройки из Ollama** is enabled by default. Chat requests send no sampling overrides: the model's original parameter,
system, template, renderer/parser and other layers remain authoritative. This preserves top-k, penalties, batch settings and
answer limits, including parameters not exposed in the UI. An explicit per-chat system instruction still replaces the built-in one.
Disable the switch and save to use the manual context, sampling and answer-limit controls. Keep-alive remains controlled by the studio.
Without a model-specific context value, the private server uses the studio's configured context (32 K by default).
Voice polishing retains its own explicit request options. Existing chats and the previously selected default model are preserved.

Import copies manifests and all referenced blobs from a local Ollama store, validates SHA-256 and leaves the source untouched.
Weights and private tuning remain excluded from Git. No registry download or modification of the user's Ollama is required.

## Why Ollama, and why a private copy

The installed model (`150ts-Gemma4A4B-9.41-AndroidKotlin-Logic-Math-Algorithms-C32K`, architecture `gemma4`, 25.2 B
parameters with about 4 B active, Q4_0, 14.4 GB weights + 1.2 GB vision projector, thinking and tools capable) was built for
Ollama: its Modelfile uses `RENDERER gemma4` / `PARSER gemma4`, which are Ollama features, not plain llama.cpp ones, and the
Ollama engine is what the owner tuned it on. ComfyUI has no chat engine for it. So the studio runs **its own Ollama
process** instead of reusing the owner's:

| | Owner's Ollama | Studio's private Ollama |
|---|---|---|
| Binary | `%LOCALAPPDATA%\Programs\Ollama` (untouched) | `Runtime/Text/ollama/` (a copy of `ollama.exe` + `lib/ollama`, without the AMD ROCm folder) |
| Models | `~/.ollama/models` (untouched) | `Models/Text/ollama/` (a copy of manifest + blobs of this model, digests verified) |
| Port | 11434 | 8292 on 127.0.0.1 (`TextPort` in `runtime.settings.json` or `VISUALFORGE_TEXT_PORT`) |
| Home / keys / logs | the user's profile | `UserData/Cache/Text/home`, log `UserData/Logs/text-backend.log` |
| Life cycle | tray app, keep-alive forever | started on first use, killed with the application (Windows job object) |

Private environment: `OLLAMA_NOPRUNE=1`, `OLLAMA_NO_CLOUD=1`, `OLLAMA_NOHISTORY=1`, flash attention on, KV cache f16, one
parallel request, one loaded model, `OLLAMA_KEEP_ALIVE` from the settings (default 10 minutes, so the GPU is not held for ever).
Nothing is pulled from the internet; other `OLLAMA_*` variables of the user are not inherited.

## GPU sharing

The model needs almost the whole 16 GB card (about 15.9 GB with a 32 K context, of which about 14.4 GB on the GPU). Therefore:

- A chat answer is **refused** (409) while Image, Video or Music is working; idle Image models are freed first.
- Starting Image, Video or Music **unloads the chat model automatically** (`release_gpu`); while an answer is being written these
  routes answer 409 "GPU занят Text" and the chat must be stopped first.
- A chat cannot be deleted while it answers; only one chat answers at a time.
- "Выгрузить модель из памяти" frees the card by hand.

## Chats

One JSON file per chat in `UserData/Text/Chats/` (atomic writes, a small `index.json` that is rebuilt if it disappears or
disagrees with the folder). Limits: 1000 chats, 2000 messages, 20 MB per chat, 100 000 characters per message, 8 000 for the
system instruction. Ids are 32 hex characters, so a request can never name a path. Per chat: title (from the first question,
renamable), system instruction (empty = the model's built-in one; a non-empty one replaces it), "think before answering" switch,
model. Global parameters (`UserData/Settings/text.settings.json`): context length (default 32 768), max answer length (8 192),
temperature (1), top-p (0.95), top-k (64), unload delay (2/10/30/60 min). These defaults are the model's own.

Answers stream as NDJSON (`status`, `delta`, `done`, `error`) through `/api/text/send`; the model's reasoning arrives in
`thinking` and is shown in a collapsed block. The answer (or the part received before Stop, an error or a closed tab) is saved by
the server, with tokens, speed, load time and context use. Text is only ever put into the page with `textContent`; code blocks
get a copy button.

## Pictures in messages

A model whose manifest contains a projector layer (this one: `vision`) accepts up to 4 PNG/JPEG pictures per message (the
**Прикрепить изображение** button, Ctrl+V from the clipboard, or dragging files onto the input). The browser shrinks them to a long
side of 1536 px (PNG kept as is when small, otherwise JPEG 0.92/0.8/0.65, at most about 5 MiB each); the server accepts only PNG and
JPEG recognised by their own headers (not by the declared type), at most 6 MiB and 8192 px per side, 4 per message, 60 per chat.
Files are stored in `UserData/Text/Chats/<chat id>.images/<image id>.png|jpg` and referenced from the chat file; they are removed
with the chat. They are shown as thumbnails through `GET /api/text/image?chat=&id=` (ids are looked up in the chat file, so no path can
be named; `nosniff`, `default-src 'none'`). Every request sends the whole history **including the earlier pictures**, so a follow-up
such as "what colour is the guitar?" works; each picture costs about 250 tokens of context. A picture-only message is allowed. A model
without a projector refuses pictures with a clear message and the attach button is disabled.

## Routes

GET `availability` (each model has `vision`), `status`, `chats`, `chat?id=`, `image?chat=&id=`; POST `chats`, `chat-rename`,
`chat-update`, `chat-delete`, `settings`, `stop`, `unload`, and the streaming POST `send` (`chat_id`, `content` and optional `images:
[{name, data: "data:image/png|jpeg;base64,..."}]`, or `regenerate: true`; up to 40 MiB). The streaming route only holds the GPU lock
for admission, never while it streams.

## Evidence

`tests/test_text.py`: 27 CPU tests (19 for chats and streaming, 8 for pictures: header sniffing and size limits, data URLs, files next to
the chat and their removal, 4/60 limits, no orphan file after a failed write, pictures and history reaching the model, regeneration,
picture-only messages, refusal for a text-only model, the image route) against `tests/fake_ollama.py` (same endpoints and NDJSON shape, no model): store, limits and
id safety, index rebuild, installed-model scan with a broken blob, settings validation, private environment, streamed answer with
thinking and stats, history and think switch, regenerate, error before and in the middle of an answer, Stop, browser disconnect,
GPU admission and release, refusal before streaming, route behaviour.

Real model on the staging copy (RTX 5070 Ti 16 GB, model on NVMe): first load 40 s; speed 26 tokens/s on the first answer
(with warm-up and thinking), 90-170 tokens/s afterwards; VRAM 15.9 GB with the model loaded and 0.3 GB after unloading; a system
instruction replaces the model's default one; thinking is separated from the answer; Russian text and a Kotlin code block were
produced. Driven in the in-app browser through the real bridge: new chat, streaming, collapsed reasoning, code block with copy,
Stop with the partial answer saved, regenerate, rename, delete, settings, 409 for Image/Video/Music/second send/delete/unload while
answering, automatic unload when a Video request arrives. The owner's own Ollama kept running and was not touched.

Pictures on the real model (staging copy, through the real bridge and the in-app browser): a photo dropped onto the input was shrunk
in the page, sent, stored and shown; the model described it correctly in Russian (a man playing an acoustic guitar), a follow-up
question about the earlier picture was answered from it (colour of the guitar), a picture-only message got an answer, paste and the
remove button worked, a fifth picture was refused, and the server rejected GIF, broken base64, five pictures, regeneration with
pictures, path-like ids, another chat's picture and a 41 MiB body. Deleting the chat removed its pictures.

After installation the installed bridge was started from H itself and one short Russian question was answered by the model stored
on the H hard disk (cold start): Ollama start 2 s, first token after 72 s (model load reported as 40 s; the rest is reading 16 GB
from the disk), 26 tokens, then unload to 0.35 GB VRAM and deletion of the test chat. Later answers do not pay the load again
while the model stays in memory (10 minutes by default).

Not tested: a 64 K context on this card, long chats near the context limit, many large pictures in one chat, picture quality for small
text (screenshots of code), tools, other models, launching the installed EXE with this release, several users.

## Not done / next

Chat export, search inside messages, a chat-title summary by the model, WebP/GIF input (the browser converts only what it can open;
the server takes PNG/JPEG), a cap on how many old pictures are resent, GPU acceptance runs for each additional imported model (discovery accepts any complete Ollama manifest).
