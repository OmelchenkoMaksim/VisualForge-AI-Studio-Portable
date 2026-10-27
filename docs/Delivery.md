# Delivery export (Instagram / social presets)

`App/server/video/delivery.py` re-encodes an **existing** clip into a delivery preset with the bundled FFmpeg. It does not
generate, enhance or mix anything and needs no GPU. The source file is never modified. Status: **implemented in the dev
checkout and wired into the Video service, bridge and Video panel; installed in release `2026.10.25-delivery.1`
(application files verified by SHA-256; the installed EXE was not launched for this release).**

## Integration

- **Video service** (`video/service.py`): exports run as a background job on the finished clip of a history entry, in the
  Video autosave folder. One export at a time; it never claims the GPU, so it can run beside a generation. Results are kept
  in memory (last 20) plus the MP4 and its `.delivery.json` on disk. `shutdown()` stops a running export.
- **Routes** (bridge `/api/video/...`, forwarded to the service): `export-preview` (GET `id`, `preset`, `audio`: the same
  plan the export uses, from a real probe, writes nothing), `export` (POST `id`, `preset`, `audio`, returns the job with a
  `cancel_token`), `export-status` (GET `id`, or no id for the running export), `export-cancel` (POST `id`, `cancel_token`).
  `availability` carries `delivery.presets` and `delivery.audio_modes`. Unknown parameters, unfinished jobs, an unknown
  preset and "sound required but missing" are rejected before anything starts.
- **Panel** ("Экспорт для соцсетей" under the result): preset and sound selectors, a canvas preview of how the frame lies in
  the target (a frame taken from the clip, black fields, purple outline) with a list of facts from the server (scale and
  whether it is an enlargement, black-field sizes, frame-rate conversion, sound origin), progress, cancel, and the usual
  completion notification with "open folder". A running export is picked up again after the page is reloaded. The last
  preset and sound choice are remembered in the browser.

## Presets

| ID | Delivered | Aspect |
|---|---|---|
| `reels-1080x1920` | 1080×1920 | 9:16 |
| `vertical-720x1280` | 720×1280 | 9:16 |
| `feed-1080x1350` | 1080×1350 | 4:5 |
| `landscape-1920x1080` | 1920×1080 | 16:9 |

Common profile (a design choice to be validated, not a quotation of a platform specification): MP4, H.264 High,
`yuv420p`, square pixels, 30 fps CFR, CRF 18 / preset `slow` by default, AAC stereo 48 kHz 192 kb/s, `+faststart`.

## Behaviour

- **Fit is always pad.** The whole picture stays visible, scaled without distortion to the largest even size that fits
  (Lanczos) and centred with even offsets; the remainder is black. Rotation metadata and non-square source pixels are
  honoured. Cropping is not offered: it needs a visible focus choice first.
- **Scaling up is not enhancement.** A 832×480 source in a 1080×1920 preset is enlarged about 1.3×. The receipt records
  `source` and `delivered` sizes, `picture.scale_factor`, `picture.scaled_up` and `enhancement: "none"`.
- **Frame rate.** Anything that is not 30 fps is converted by duplicating or dropping frames (`frame_rate_conversion` in
  the receipt); no new motion is created.
- **Audio** (`audio` argument): `keep` (default) keeps the source track, or no track when there is none; `require` rejects a
  source without sound; `fill` keeps the source track or adds a silent stereo track. Audio is padded/limited to the video
  length (`apad` + `-shortest`), never the other way round.
- **Safe output.** `<source stem> - <preset>.mp4` and `.delivery.json` are created exclusively (a numeric suffix is added
  if the name exists). FFmpeg writes to a hidden partial file that is verified and then moved into place; on any failure
  the partial, the reserved names and the receipt are removed.
- **Verification before publication** (ffprobe): exact size, square pixels, 30 fps, h264/yuv420p, audio presence as
  expected, duration within 0.15 s of the source, frame count within ±2 of `duration × 30`.

## Use

```text
python App/server/video/delivery.py --runtime-root <Runtime/Video> --output-dir <folder> clip.mp4 reels-1080x1920 [--audio keep|require|fill]
```

`--runtime-root` may also come from `VISUALFORGE_VIDEO_RUNTIME`. In code: `export_clip(source, directory, preset_id, ffmpeg,
ffprobe, audio='keep', x264_preset='slow', crf=18)` returns the receipt dictionary; invalid input raises `ValueError`,
FFmpeg or verification failures raise `DeliveryError`. Calls are blocking and have a timeout (default one hour); they are not
yet integrated with owned-process cancellation.

## Evidence

`tests/test_delivery.py`: 11 CPU tests, 7 of them real short FFmpeg exports from synthetic clips (all four presets, audio
modes, padding colour, name uniqueness, failure cleanup). `tests/test_delivery_service.py`: 6 tests of the service layer
(preview, background export, request rejection, one-at-a-time, cancel cleanup, shutdown); the cancel test uses a 60 s
1080p synthetic clip and skips itself if the export finishes first. Both use the bundled FFmpeg from
`VISUALFORGE_VIDEO_RUNTIME` (default: the installed Runtime/Video on H, read-only).

End-to-end check (one session): the real bridge and Video service from a copy of the dev `App` in a throw-away root with a
fixture history of two existing clips, driven in the in-app browser. Preview drew the real frame with correct fields;
a 1080×1920 export of the S2V clip finished with progress and a notification, the file is h264 1080×1920 30 fps plus AAC
48 kHz stereo; HTTP errors (bad preset, extra field, unknown job, wrong cancel token, wrong content type) behaved as
documented. After installation the installed `App` was copied to a throw-away root and the same preview/export API calls
were repeated against it (landscape export of a silent clip with a silent track added). Not tested: the export of a long
clip through the UI, rotated/anamorphic real sources, other encoders, launching the installed EXE with this release, any
platform upload.

## Not done / next

Exporting clips that are no longer in the 100-entry Video history, receipt listing in the history panel, optional crop with
a focus control, loudness handling, and the separate enhancement stage.
