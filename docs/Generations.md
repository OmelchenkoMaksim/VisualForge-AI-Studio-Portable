# Generation identity

`App/server/generation.py` supplies one `generation` object per accepted operation, including technical operations recorded in the [unified event history](Event-History.md). It contains exactly four fields:

```json
{
  "type": "Image",
  "id": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "started_at": "2026-10-28T09:00:00.000Z",
  "duration_sec": 12.345
}
```

- `type`: a unique operation category from `event_history.TYPES`. Main types include `Image`, `Video`, `Music`, `Text`, `Agent`, `Voiceover`, `VideoExport` and `VideoMix`; recognition, warmups, model calls and other technical work have distinct types listed in [Event-History.md](Event-History.md).
- `id`: 32 cryptographically random bytes encoded as 64 lowercase hexadecimal characters. At one million operations the birthday-bound collision probability is approximately 4.3 × 10⁻⁶⁶, not a mathematical guarantee of uniqueness. Active IDs are also checked in-process before issuance.
- `started_at`: UTC ISO 8601 with milliseconds (`Z`). Requests rejected at route admission do not create a generation. Module-specific validation and preparation may run before or within an operation.
- `duration_sec`: seconds rounded to milliseconds, `null` while running. A monotonic timer measures the accepted operation, including cold model loading and subsequent GPU waits. Completion freezes the value once, including cancellation/error; a system clock adjustment does not alter it. Media duration (e.g. a 30-second song) is a separate field. Final file metadata/log bookkeeping may happen after this measurement is frozen.

Existing API IDs (`prompt_id`, Video/Music job ID, chat/message/session IDs, cancellation tokens) keep their original meaning. The new generation ID is public correlation metadata and grants no permission to cancel or delete work. Downloading components and warming a model without a generation are not generation operations.

## Scope and storage

Image receipts and status responses retain the object. Video/Music retain it in their job rows, history and output JSON sidecars. Video exports and mixes receive fresh VideoExport/VideoMix identities; a registered mix result shares the mixing operation's identity rather than inheriting its source generation.

Each Text answer/regeneration receives a new identity. The request and answer retain the same object; a failure before the first token keeps the finished metadata on the request and error event without adding an empty answer. Regenerating replaces the previous answer under the existing chat semantics. An Agent task has one identity across all fresh sessions/loop iterations, handoff creation and checks; its stored messages receive the same final total duration. Handoff notes include its ID. The internal per-iteration agent callback guard remains separate from the public task ID.

Speech recognition responses include a SpeechRecognition or SpeechRecognitionPreview identity, covering optional Gemma polishing. Model loads, silent warmups, punctuation and Gemma processing also have their own centrally recorded technical identities. Partial recognition requests are separate operations; skipped previews perform no inference and keep their original small response without an ID. Speech has no new persisted transcript archive. Main operation identities retain their existing diagnostic routing. Technical operation identities go exclusively to the new central event store and do not enter module logs or histories. Shared logs still retain mixed data and are not individually purged by history deletion.

History cards and Text/Agent answers show an expandable «Данные генерации» card with Russian labels, the full ID and «Скопировать объект». Dates display in the browser's local timezone; copied JSON keeps UTC. Historical receipts without the object get a stable SHA-256 ID derived from their module and legacy ID on read, without a bulk rewrite of user files. Available old timestamps/durations are retained; unknown values stay `null`. Old Text assistant messages lack an exact start timestamp, so it is not inferred from the answer creation time. Previously generated sidecars/logs are not rewritten automatically.
