"""Gemma as the director who watches a finished clip: a few frames, measured facts and the prompt in, a verdict and a better prompt out.

A video model never tells what went wrong. The review gives it a voice: Gemma (with vision) looks at frames taken along the clip, is handed the numbers
that frames cannot show (how much moves, cuts, exposure, the loudness of the sound, what Whisper heard against the words the prompt asked for) and writes
a corrected prompt in the style the model reads best (the same hidden guide that the rewrite uses). The person reads the verdict and, if they agree,
generates again with the corrected prompt; nothing is started by itself.
"""
from ui_strings import source as ui_text
import base64
import json
import re
import subprocess

from video.gemma_task import GemmaTask

OWNER = 'video-review'
FRAMES = 6
FRAME_WIDTH = 768
MAX_PROMPT = 1800
NOTES = {
    'ltx25-distilled': 'LTX 2.5 draws picture and sound together. Levers: shot size and subject first in the sentence, one clear action, one camera move, light, '
                       'exact quoted speech (with the language named), named sound sources.',
    'wan22-ti2v-5b': 'Wan 2.2 TI2V-5B is silent. Levers: up to four cinematic tags first (time, light, colour tone, shot size, composition), a large clearly named '
                     'subject, the motion described as a process in time.',
    'wan22-s2v-14b': 'Wan S2V makes a talking person from a photo and a recording: the voice drives mouth, head and hands. The text only sets the look, the place, '
                     'the shot and how the person delivers the speech; it must never describe lips or words.',
    'wan-animate2': 'Wan Animate 2 repeats the motion of a reference video with the person of a photo; the text is only the appearance and the background.',
    'scail2': 'SCAIL-2 replaces or animates a person with the person of a photo; the text describes the finished video, never an instruction.',
}

SYSTEM = (
    'You are the director who reviews a clip that a video model made from a prompt. You see {count} frames of the clip in time order and the measured facts. '
    'Judge only what you can see in the frames or what the facts say; never invent details; when the clip is fine, say so and change nothing.\n'
    'Check: does the picture match the prompt (subject, action, place, light, camera, shot size)? Is the subject large and clear, or tiny, cropped, lost or '
    'overexposed? Does the same person or object stay the same through the frames? Visible defects: blur, melted faces or hands, extra limbs, flicker, '
    'burnt or crushed areas, garbled writing. Is there enough motion for what the prompt promises? Does the sound facts (loudness, silence, what was heard '
    'against the requested words) fit?\n'
    'Then write a FIXED prompt for the same video model: keep everything that worked and change only what fixes the problems you found (name a closer shot '
    'size, put the subject first and large, add one clear action, simplify a crowded scene, repeat the spoken words exactly). Write it in English, in the '
    'form the rules below ask for; the words that are spoken or written in the picture stay exactly as they are, in quotes.\n'
    'Answer with ONLY one JSON object: {{"verdict": "good" or "fixable" or "bad", "score": a whole number 1-10 for how well the clip does what the prompt asks (flawless 10, good with small flaws 7-8, clear problems 4-6, failed 1-3), "summary": "two short sentences for the person", '
    '"problems": ["what is wrong, with the frame numbers that show it"], "fixes": ["each change made to the prompt, in a few words"], "fixed_prompt": "the full '
    'new prompt"}}. The summary, problems and fixes are written in {language}. When the verdict is "good", fixed_prompt is the original prompt.\n'
    'THE MODEL: {note}\nRULES OF THE PROMPT FOR THIS MODEL:\n{rules}')


def extract_frames(ffmpeg, video, duration, count=FRAMES, width=FRAME_WIDTH):
    """PNG frames (bytes) taken along the clip, not at the very first and last frame."""
    frames = []
    for index in range(count):
        moment = max(0.0, duration * (index + 0.5) / count)
        result = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss', '%.3f' % moment, '-i', str(video), '-frames:v', '1',
                                 '-vf', 'scale=%d:-2' % width, '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 0 and result.stdout:
            frames.append(result.stdout)
    return frames


def measure(ffmpeg, video):
    """Numbers a few frames cannot show: length, motion, cuts, exposure, sound level. Needs numpy (the bridge Python has it)."""
    import numpy as np
    probe = subprocess.run([str(ffmpeg).replace('ffmpeg.exe', 'ffprobe.exe'), '-v', 'error', '-show_entries', 'stream=codec_type,width,height,r_frame_rate:format=duration',
                            '-of', 'json', str(video)], capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    info = json.loads(probe.stdout)
    streams = info.get('streams', [])
    picture = next(stream for stream in streams if stream['codec_type'] == 'video')
    fps = eval(picture['r_frame_rate'], {'__builtins__': {}}, {}) if re.fullmatch(r'\d+/\d+', picture['r_frame_rate']) else 24.0
    duration = float(info['format']['duration'])
    width, height = 192, max(2, round(192 * picture['height'] / picture['width']) // 2 * 2)
    raw = subprocess.run([str(ffmpeg), '-v', 'error', '-i', str(video), '-vf', 'scale=%d:%d' % (width, height), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True, timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)
    facts = dict(seconds=round(duration, 2), fps=round(fps, 2), width=picture['width'], height=picture['height'], frames=len(frames))
    if len(frames) > 2:
        diff = np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2, 3))
        median = float(np.median(diff))
        facts['motion'] = round(float(diff.mean()), 2)
        facts['cuts'] = int(sum(1 for value in diff if value > max(6 * median, 12)))
        grey = frames.mean(axis=3)
        facts['burnt_share'] = round(float((grey >= 250).mean()), 3)
        facts['crushed_share'] = round(float((grey <= 6).mean()), 3)
        facts['brightness_first_last'] = [int(grey[:max(1, len(grey) // 10)].mean()), int(grey[-max(1, len(grey) // 10):].mean())]
    if any(stream['codec_type'] == 'audio' for stream in streams):
        pcm = subprocess.run([str(ffmpeg), '-v', 'error', '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True, timeout=120,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        samples = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
        levels = [20 * np.log10(max(1e-6, float(np.sqrt((samples[i:i + 16000] ** 2).mean())))) for i in range(0, len(samples), 16000)] or [-120.0]
        facts['sound'] = dict(mean_db=round(float(np.mean(levels)), 1), loudest_db=round(float(max(levels)), 1), silent_seconds=int(sum(1 for level in levels if level < -50)))
    return facts


def describe(facts, heard=None):
    """The facts as short lines for the prompt of the review."""
    lines = ['%.1f s, %dx%d, %.0f fps' % (facts['seconds'], facts['width'], facts['height'], facts['fps'])]
    if 'motion' in facts:
        level = 'almost static' if facts['motion'] < 1.0 else 'calm' if facts['motion'] < 3.0 else 'lively' if facts['motion'] < 8.0 else 'very strong'
        lines.append('motion: %s (mean frame difference %.1f); hard cuts: %d' % (level, facts['motion'], facts['cuts']))
        lines.append('burnt-out pixels %.0f%%, crushed black pixels %.0f%%; brightness first/last tenth %s' % (100 * facts['burnt_share'], 100 * facts['crushed_share'], facts['brightness_first_last']))
    sound = facts.get('sound')
    lines.append('sound: none' if not sound else 'sound: mean %.0f dBFS, loudest second %.0f dBFS, %d seconds near-silent (below -50 dBFS)' % (sound['mean_db'], sound['loudest_db'], sound['silent_seconds']))
    if heard:
        lines.append('speech asked for: "%s"; speech heard by Whisper: "%s"; share heard: %.0f%%' % (heard['expected'], heard['heard'], 100 * heard['share']))
    return '\n'.join('- ' + line for line in lines)


def parse(answer):
    """The model's answer -> the review dict, or None when it is not the JSON that was asked for."""
    text = re.sub(r'^```[a-z]*\s*|\s*```$', '', answer.strip(), flags=re.I)
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get('verdict') not in ('good', 'fixable', 'bad') or not isinstance(value.get('fixed_prompt'), str):
        return None
    fixed = ' '.join(value['fixed_prompt'].split())
    if not 20 <= len(fixed) <= MAX_PROMPT:
        return None
    score = value.get('score')
    clean = lambda items: [' '.join(str(item).split())[:300] for item in items][:8] if isinstance(items, list) else []
    return dict(verdict=value['verdict'], score=score if isinstance(score, int) and 1 <= score <= 10 else None, summary=' '.join(str(value.get('summary', '')).split())[:600],
                problems=clean(value.get('problems')), fixes=clean(value.get('fixes')), fixed_prompt=fixed)



class DirectorReview(GemmaTask):
    OWNER = OWNER
    OPERATION = 'VideoReview'

    def review(self, frames, facts, prompt, engine, mode, rules, language='Russian', heard=None, idea=None):
        """-> dict(verdict, score, summary, problems, fixes, fixed_prompt, model). Raises Busy / GpuUnavailable / ValueError with a message for the person."""
        if not frames:
            raise ValueError(ui_text('video.review_no_frames'))
        with self.session() as cancel:
            system = SYSTEM.format(count=len(frames), language=language, note=NOTES.get(engine, 'Write one English paragraph with the subject, the action, the place, the light and the camera.'), rules=rules)
            content = 'The prompt that made the clip:\n%s\n' % prompt.strip()
            if idea and idea.strip() and idea.strip() != prompt.strip():
                content += '\nThe person\'s own idea, from which that prompt was written:\n%s\n' % idea.strip()
            content += '\nMode: %s.\n\nMeasured facts:\n%s\n\nThe frames follow in time order (pictures 1 to %d).' % (mode, describe(facts, heard), len(frames))
            # said once more next to the data: a long English system text pulled the first answer into English although Russian was asked for
            content += '\n\nWrite "summary", "problems" and "fixes" in %s (the first answer came in English when this was only in the instructions); "fixed_prompt" stays English.' % language
            messages = [dict(role='system', content=system), dict(role='user', content=content, images=[base64.b64encode(frame).decode('ascii') for frame in frames])]
            for _ in range(2):
                answer = self.ask(messages, cancel, temperature=0.3, top_p=0.9, top_k=40, num_ctx=14336, num_predict=1400)
                if cancel.is_set():
                    raise ValueError(ui_text('video.plan_building_stopped_message'))
                review = parse(answer)
                if review:
                    review['model'] = self.text.gemma_model()
                    return review
                messages += [dict(role='assistant', content=answer[:3000]), dict(role='user', content='Answer again with ONLY the JSON object described, with fixed_prompt of at most 1700 characters.')]
            raise ValueError(ui_text('video.review_gemma_failed'))
