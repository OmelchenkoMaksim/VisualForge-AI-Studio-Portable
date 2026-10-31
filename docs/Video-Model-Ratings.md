# Quality and power ratings of the video models

Added 2026-10-31. Every engine of the Video section carries a score from 0 to 10 in the engine list ("... · score 7/10") and a block with five axes under the
model description. **10 is the real world filmed by a good camera.** The numbers live in `ENGINES[...]['rating']` in `App/server/video/video_registry.py`
(`rating()` computes `overall` as the mean of the axes that exist, in steps of 0.5), the panel only draws them.

| Score | Meaning |
|---|---|
| 10 | the real world filmed by a good camera |
| 8 | cannot be told apart at a glance |
| 6 | looks like video, but distortions show on a careful look |
| 4 | plainly generated |
| 2 | a draft |

**What the numbers are.** The developer's judgement from the studio's own tests on an RTX 5070 Ti (contact sheets, motion measured as the mean frame
difference, listening to the sound), not an independent benchmark. A score is changed only by a new comparison, with the evidence written here. No local model
reaches 8: `tests/test_video_ratings.py` keeps that honest.

## The five axes

* **Picture**: detail, texture, light, faces, resolution.
* **Motion**: physics and naturalness of what moves.
* **Consistency**: the same face, object and place over time, across segments and scenes.
* **Sound**: speech and noise the engine makes itself; empty when it makes none (then it is not counted).
* **Following the request**: how closely the result does what the text, the picture or the reference asked.

## Scores (2026-10-31)

| Engine | Picture | Motion | Consistency | Sound | Request | **Overall** | Why |
|---|---|---|---|---|---|---|---|
| FreeVideo MiniMax H3 int8 | 3 | 4 | 4 | 4 | 4 | **4** | 512x288 only; sound and cloned voices work, but a small, soft picture |
| Wan 2.2 TI2V-5B | 5 | 5 | 5 | none | 5 | **5** | 704p at 24 fps, a clean scene when the request is simple; small subjects blur, hair and background drift, silent |
| Wan 2.2 S2V-14B | 6 | 5 | 6 | 5 | 4 | **5** | a believable talking face with the speech's sync, 512x512 at 16 fps, little control over gesture and place |
| LTX 2.5 Distilled | 7 | 7 | 6.5 | 6.5 | 7 | **7** | the best picture and light of the local engines, sound made with the picture, up to 1280x704; faces and hands at a distance and long clips still drift |
| Wan Animate 2 | 6.5 | 7.5 | 6.5 | none | 5 | **6.5** | the person of your photo with the motion and the sound of a real video; the place comes from the text, the face is good, details soften |
| SCAIL-2 | 7 | 7.5 | 6.5 | none | 6 | **7** | replacement inside a real scene: the scene, the objects and the sound stay real; the new person is the weak part (body below the photo, identity over a long clip) |

For the two character engines the motion (and, in SCAIL-2 `replace`, the whole scene) is taken from your own reference video, so they score high on motion and the
result is only as good as the reference.

## Where it shows

* `video-panel.js`: the engine list label, the `#videoRating` block (`showRating()`, `<meter>` rows), strings `video.rating_*`.
* `studio-theme.css`: `.model-rating`.
* `tests/test_video_ratings.py`: every engine has all axes in half steps, `overall` is the mean, the maximum stays below 8.
