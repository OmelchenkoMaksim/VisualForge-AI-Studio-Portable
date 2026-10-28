# Unified local event history

Settings → «История событий» opens the independent event journal. New operations are recorded after the updated studio starts; existing text logs and generation receipts are not scanned or rewritten to invent past technical events. The journal survives restarts, shows newest start dates first, filters main/technical operations, type and state, loads older pages and copies a generation's four-field object.

The object defined in [Generations.md](Generations.md) remains exactly `{type, id, started_at, duration_sec}`. Its surrounding event row adds `state`, `parent_id`, `category` and the derived technical flag. No prompts, transcripts, pictures, audio, file contents, credentials or cancellation tokens are stored in this journal. It is not a new transcript archive.

## Operations

`App/server/event_history.py` is the authoritative type/label registry. Main operations are Image, Video, Music, Text, Agent, Voiceover, VideoExport and VideoMix. Technical operations have their own types and IDs:

| Area | Types and boundary |
|---|---|
| Microphone | `MicrophoneSession`: browser permission/recording session, including cancellation, failure and a best-effort page-close notification. |
| Recognition | `SpeechModelLoad`, `SpeechModelWarmup`, `SpeechRecognition`, `SpeechRecognitionPreview`, `SpeechPunctuation`. A skipped live preview performs no inference and creates no event. |
| Gemma voice processing | `SpeechTextPolish`, `SpeechTextPolishWarmup`. These are distinct from an ordinary Text chat and do not create Text chat messages. |
| Pictures | `ImageRecognition`: a vision chat model processing image history; `ImagePromptExpansion` and `ImageUpscale`: operations submitted within an Image ComfyUI graph. A 4× upscale schedules two 2× passes with separate IDs. |
| Agents | `AgentModelCall` for gateway requests; `AgentHandoff` when the manager explicitly marks the request as a handoff. They reference the task's Agent identity across loop iterations. Multimodal gateway requests also have an `ImageRecognition` child. |
| Media workers | `VideoModelGeneration`, `MusicModelGeneration`: owned backend execution; `SpeechSynthesis`: the Qwen voice generation inside a video pipeline. `MusicPlanning` wraps ACE-Step's public language-model entry point when its planner is used, without changing the vendor files. Worker environment/request metadata carries the appropriate parent ID. |

IDs describe operations, including their setup/waiting scope. Tokens and diffusion steps belong to the enclosing model operation. Parent and child durations overlap, so they must not simply be summed for analytics. Image graph child durations span submission through completion of the shared graph, including queued/cached execution; they are not measurements of individual ComfyUI node kernels. The event card explicitly explains this boundary.

## Persistence and routing

`UserData/Settings/event-history.sqlite3` is intentional user history, shared by the bridge, Video backend and owned Python workers. SQLite WAL and short transactions coordinate writers across processes. Start inserts one row; completion updates it instead of duplicating the event. Indexes support date/type/state ordering and parent lookup. The API uses bounded pages (default 100, maximum 200) and a date/ID cursor so new arrivals do not shift older pages.

`generation.configure()` is called at bridge/Video startup. Owned Python workers inherit only the database path and a parent identity; third-party coding agents retain their existing allow-listed environment and report model operations through the gateway. A failed history write does not cancel inference. Corrupt databases are preserved and read failures are reported in the history dialog. A missing final event retains `duration_sec: null`; the UI says that no completion was recorded rather than claiming a crashed process is still running.

Technical metadata goes only to this central store. The existing module histories and logs keep their purpose and existing contents; technical identities are neither printed into those streams nor appended to module log files. The new journal endpoints are excluded from ordinary HTTP access logging; Speech passes its microphone parent in `X-VisualForge-Session`, not the logged request URL. Native runtime diagnostics continue under their existing behavior. Media history deletion also removes the central generation row and its technical descendants, including descendants whose parent row failed to persist.

## API and future interactions

- `GET /api/event-history`: optional `scope=main|technical`, `type`, `state`, `limit`, `before`; returns entries, type labels, the next cursor and storage warnings. Host/Origin protection is inherited from the bridge.
- `POST /api/event-history/session`: only `{action: "start"}` and `{action: "finish", id, state}` for a MicrophoneSession. Clients cannot create arbitrary types or finish another type of operation. It records microphone activity, not arbitrary button clicks.
- The existing `/api/events` WebSocket remains the Image progress channel.

The schema reserves a category and a small data field for future interaction events. Click analytics is not collected in this implementation. Opening the journal, filtering and copying objects create no analytics records.

CPU tests cover cross-process writers, persistence, chronological cursor pagination, filters, failure/cancel states, unchanged module log bytes, technical stdout suppression, parent deletion, HTTP validation, the settings UI and microphone lifecycle. Inference bodies remain unchanged; live GPU checks require a separate approval.
