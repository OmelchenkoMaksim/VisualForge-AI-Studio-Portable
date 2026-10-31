"""The Gemma-facing routes of the Video section: scenario (`/api/video/plan`), prompt rewrite (`/api/video/enhance`) and clip review (`/api/video/review`).

They used to be methods of the bridge handler. Like the Music, Voiceover and Speech routes they now live with their module; the bridge only hands over
its request handler (`response_json`) and the objects it built. None of them holds the GPU lock while Gemma works (the call takes tens of seconds):
each one asks the coordinator for the card for the Text module itself (`GemmaTask.session`).
"""
from ui_strings import source as ui_text
from pathlib import Path
import re

from gpu_coordinator import Busy, GpuUnavailable
from video.prompt_enhancer import character_note, guide, supported

CLIP_ID = re.compile(r'[0-9a-f-]{36}')
GENERIC_RULES = 'One English paragraph: the subject, the action, the place, the light and the camera.'


def reply(handler, work):
    """Run `work()` and answer 200 with its result; Busy -> 409, no GPU -> 503, a message for the person (ValueError) -> 400."""
    try:
        return handler.response_json(200, work())
    except Busy as error:
        return handler.response_json(409, {'error': str(error)})
    except GpuUnavailable as error:
        return handler.response_json(503, {'error': str(error)})
    except ValueError as error:
        return handler.response_json(400, {'error': str(error)[:800]})


def plan(handler, planner, data):
    """Gemma splits one idea into a prompt per scene, segment or window of a long clip."""
    if planner is None:
        return handler.response_json(503, {'error': ui_text('video.plan_text_section_unavailable')})
    return reply(handler, lambda: planner.plan(data.get('prompt'), data.get('segments'), data.get('source_image'), data.get('join', 'continue'),
                                               talking=data.get('talking') is True, speech=data.get('speech_text'), sound=data.get('sound') is True))


def enhance(handler, enhancer, data):
    """Gemma rewrites one prompt for the chosen video model and mode (the hidden per-model instruction of prompt_enhancer.py)."""
    if enhancer is None:
        return handler.response_json(503, {'error': ui_text('video.plan_text_section_unavailable')})

    def work():
        pictures = [data.get('source_image')] + ([data.get('end_image')] if data.get('end_image') else [])
        note = ''
        if data.get('mode') in ('animate', 'replace'):
            # Character animation: the photo, the extra object pictures and a few frames of the reference video, told apart by a note.
            extras, frames = (data.get(key) if isinstance(data.get(key), list) else [] for key in ('subject_images', 'driving_frames'))
            extras, frames = [p for p in extras[:2] if isinstance(p, str)], [p for p in frames[:4] if isinstance(p, str)]
            pictures += extras + frames
            note = character_note(data.get('objects'), len(extras), len(frames))
        return enhancer.enhance(data.get('prompt'), data.get('engine'), data.get('mode'), [p for p in pictures if p], data.get('variant'), note)
    return reply(handler, work)


def finished_clip(video, ident):
    """The history entry and the file of a finished clip of this installation, or ValueError: only what the studio itself made can be reviewed."""
    if not isinstance(ident, str) or not CLIP_ID.fullmatch(ident):
        raise ValueError(ui_text('video.review_unknown_clip'))
    entry = next((row for row in video.call('/history').get('entries', []) if row.get('id') == ident), None)
    path = Path(entry['saved_path']) if entry and entry.get('state') == 'done' and entry.get('saved_path') else None
    if path is None or not path.is_file():
        raise ValueError(ui_text('video.review_unknown_clip'))
    if entry.get('segment_prompts'):
        # a film of scenes or windows has many prompts and planned cuts: the review reads one prompt
        raise ValueError(ui_text('video.review_scenes_unsupported'))
    return entry, path


def review(handler, reviewer, video, gpu, paths, data):
    """Gemma watches a finished clip of the Video history (frames, measured facts, what Whisper heard) and answers with a verdict and a corrected prompt.
    Only reads: nothing is generated."""
    if reviewer is None or video is None:
        return handler.response_json(503, {'error': ui_text('video.plan_text_section_unavailable')})

    def work():
        from video import director_review
        from video.speech_check import Listener, heard_share, spoken_lines
        entry, path = finished_clip(video, data.get('id'))
        ffmpeg = paths.root / 'Runtime/Video/ffmpeg/bin/ffmpeg.exe'
        facts = director_review.measure(ffmpeg, path)
        frames = director_review.extract_frames(ffmpeg, path, facts['seconds'])
        sent = entry.get('prompt') or entry.get('original_prompt') or ''
        heard = None
        lines = spoken_lines(sent) if 'sound' in facts else []
        if lines and not gpu.busy_modules():           # Whisper takes the card for a moment: only when no other module works
            listener = Listener(paths.root, ffmpeg, paths.cache)
            if listener.available():
                try:
                    listener.start()
                    text = ' '.join(line for line, _ in lines)
                    said = listener.hear(path, lines[0][1], facts['seconds'])
                    heard = dict(expected=text, heard=said, share=heard_share(text, said))
                except Exception:                      # the check is an extra: the review goes on without it
                    heard = None
                finally:
                    listener.stop()
        engine, mode = entry.get('engine'), entry.get('mode')
        rules = guide(engine, mode)[1] if supported(engine, mode) else GENERIC_RULES
        answer = reviewer.review(frames, facts, sent, engine, mode, rules, 'English' if data.get('language') == 'English' else 'Russian', heard, entry.get('original_prompt'))
        answer.update(id=entry['id'], facts=facts, heard=heard)
        return answer
    return reply(handler, work)
