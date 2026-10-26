# Audio mixer (music under a finished clip)

`App/server/video/mixer.py` lays up to three music tracks under a finished Video clip and saves a **new** MP4 next to it.
Post-processing with the bundled FFmpeg: no generation, no GPU. The source file is never modified and the picture is
copied bit for bit (`-c:v copy`); only the audio is rebuilt as AAC stereo 48 kHz 192 kb/s. Status: **implemented and
installed in release `2026.10.25-mixer.1` (application files verified by SHA-256; the installed EXE was not launched for
this release).**

## What a mix contains

- **Clip's own sound**: `keep` (default, gain adjustable) or `mute`. A clip without sound simply gets the music only.
- **Tracks** (1-3, music results taken from the Music module): `start` (s), `gain_db` (-40..+12), `fade_in`, `fade_out`
  (s), `loop`. A track is cut at the end of the clip; if it is shorter and `loop` is off the rest is silence (the plan
  says so); with `loop` it repeats up to the end. Fades must fit inside the audible span.
- **Ducking** (optional, only while the clip's own sound is kept): music is lowered by roughly 10 dB (sidechain compressor,
  threshold 0.04, ratio 6, attack 15 ms, release 400 ms) while the clip's own sound, e.g. speech, plays. One fixed profile.
- **Safety**: tracks are summed without normalisation, then limited (`alimiter` 0.97) so the sum cannot clip; the audio is
  padded/limited to the video length (`apad` + `-shortest`).

## Plan, verification and receipt

`plan_mix()` is the single source of truth for both the preview and the mix: spans, how each track ends (`video_end`,
`file_end`, `loop`), notes in Russian, lane data for the timeline and a `blocked` reason (track starts after the end,
fades longer than the track). After encoding the result is verified with ffprobe/volumedetect: AAC 48 kHz stereo, same
picture size and codec, duration within 0.15 s, not silent, peak not above 0 dB. A `<name>.mix.json` receipt records the
tracks, plan notes, whether ducking applied, mean/max volume and "video stream copied, not re-encoded". Names are created
exclusively (`<source> - with music.mp4`, numbered if taken); the encode goes to a hidden partial file that is moved into
place after verification, and is removed on any failure or cancel.

## Integration

- **Video service** (`video/service.py`): `mix-preview` (POST, writes nothing) and `mix` (POST) run as a background job
  that **shares the one-at-a-time slot, `export-status` and `export-cancel` with the social export** (row has
  `kind: "mix"`). On success the mixed MP4 is registered as a new Video history entry (`derived_from`, `derived_kind:
  "mix"`, `mix_summary`, `has_audio: true`), so it can be opened, exported to Instagram presets or mixed again.
- **Bridge** (`server.py`): the browser names music by Music job id (`music_id`); only the bridge turns it into a file with
  `MUSIC.file()`. A client that sends a `path` is rejected, so a request can never point at an arbitrary file.
- **Panel** ("Музыка и звук" above the export block): own-sound mode and gain, up to three track cards (music selector filled
  from the Music history, start, gain, fades, repeat), ducking checkbox, a timeline drawn on a canvas from the server plan
  (video, own sound, tracks with fades and gain) with the plan's notes, progress, cancel, completion notification and
  automatic opening of the new history entry.

## Evidence

`tests/test_mixer.py` (15 tests: request validation, plan, filter graph and command, plus real FFmpeg mixes: video stream
MD5 equal to the source, music and own sound both present, mute, ducking measured in the 880 Hz music band, start offset,
loop and short track, two tracks on a silent clip, limiter, blocked/cancel/cleanup) and `tests/test_mixer_service.py`
(5 tests: preview, background mix and history entry, request errors, shared slot with export, cancel cleanup). End-to-end
in one session: the real bridge and Video service from a copy of the dev `App` with a fixture history (existing S2V clip
and an existing ACE-Step result), driven in the in-app browser: add track, ducking, mix with progress, notification, new
history entry opened; bridge rejections (client path, unknown music id, extra field, bad number, oversize body) checked.
After installation the installed `App` was copied to a throw-away root and the preview and a real mix were repeated through
the bridge API against that copy.

Not tested: loud real-world material and subjective balance by listening (levels were measured, not listened to), music
formats other than WAV, many consecutive mixes, launching the installed EXE with this release.

## Not done / next

Own audio files and Music results of other formats as tracks, uploaded speech tracks with timed placement, per-scene audio
for long projects, loudness normalisation to a target (LUFS), a second ducking profile.
