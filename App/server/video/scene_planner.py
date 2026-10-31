"""Gemma (the Text section's private Ollama) writes one prompt per segment of a long Wan clip.

A long clip is a chain of about 5-second segments, each continuing from the last frame of the previous one. Reusing the same
prompt for every segment would repeat the same action, so the model splits the user's idea into consecutive beats while
repeating the character and setting description in every prompt (the video model has no memory between segments).
The model is a normal Text model: it takes the video card like a chat answer does, and the next Video job frees it again.
"""
from ui_strings import source as ui_text
import base64
import io
import json
import re
import threading

from generation_identity import operation
from gpu_coordinator import Busy, GpuUnavailable

OWNER = 'video-plan'
MAX_PROMPT = 1500
SYSTEM = (
    'You are an assistant film director writing prompts for an AI video model (Wan 2.2). The final video is made of {count} consecutive '
    'clips of about 5 seconds each, generated one after another: every clip starts from the last frame of the previous one.\n'
    'Split the user\'s idea into exactly {count} prompts, one per clip, that tell one continuous story from beginning to end.\n'
    'Rules:\n'
    '- Write in English, even when the idea is in another language.\n'
    '- The video model remembers nothing between clips. Start EVERY prompt with the same short description of the main character(s), '
    'the place, the lighting and the visual style, using the same words each time, so the look stays consistent.\n'
    '- After that, describe ONE simple continuous action for the clip. It must continue exactly where the previous clip ended and move the '
    'story forward; never repeat an earlier action.\n'
    '- The video model tends to freeze in the pose of the first frame it is given. So from clip 2 on, begin the action with a clear change '
    'of pose or position (for example "turns away and starts walking", "gets up and runs"), and avoid long static actions such as '
    'yawning, staring or waiting.\n'
    '- No cuts, no time jumps, no new characters or objects unless the idea needs them. Use calm camera moves: static, slow pan, '
    'slow dolly or tracking.\n'
    '- Do not write text, subtitles, logos or sounds.\n'
    '- Each prompt is 40 to 80 words.\n'
    'Answer with a JSON array of {count} strings and nothing else.')


# Independent shots joined with hard cuts: nothing carries over between them except what each prompt repeats.
SCENES_SYSTEM = (
    'You are an assistant film director writing prompts for an AI video model (Wan 2.2). The final video is a film edited from {count} '
    'separate shots of about 5 seconds each. Every shot is generated independently and the shots are joined with hard cuts.\n'
    'Split the user\'s idea into exactly {count} prompts, one per shot, that tell one story from beginning to end, so that the shots '
    'in order show what the idea describes.\n'
    'Rules:\n'
    '- Write in English, even when the idea is in another language.\n'
    '- The video model knows nothing about the other shots. Every prompt must be fully self-contained: name the main character(s) '
    'with the same fixed description in EVERY prompt (species, age, build, hair or fur, clothes, colors), and repeat the visual '
    'style and the time of day, using the same words each time, so the cast looks the same from shot to shot. The place may change '
    'when the story needs it; then describe the new place fully.\n'
    '- Choose ONE style phrase for the whole film that fits the idea (for a realistic story: "photorealistic cinematic footage, natural '
    'colors") and start every prompt with exactly that phrase. Avoid words that pull the picture toward cartoons or illustration '
    '(dreamy, magical, fairy, whimsical, glowing, storybook) unless the idea asks for a cartoon.\n'
    '- Each shot shows ONE simple action that moves the story forward. Never repeat an earlier shot.\n'
    '- Vary the framing like a film editor: an establishing wide shot, medium shots, close-ups, a view from another side. '
    'State the camera in each prompt (static, slow pan, slow dolly or tracking).\n'
    '- Do not write text, subtitles, logos or sounds.\n'
    '- Each prompt is 40 to 80 words.\n'
    'Answer with a JSON array of {count} strings and nothing else.')


# Wan S2V animates a given photo of a person with their voice: the voice already drives lips, face, head and body, so the prompt
# only has to steer the camera, the setting and the person's bearing.
TALKING_COMMON = (
    '- Write in English, even when the idea or the speech is in another language.\n'
    '- Never describe the words, the lips or the mouth: the voice drives them. Describe the camera, the setting, the light and the '
    'person\'s posture, gestures and emotion that fit what is being said in that part.\n'
    '- Start EVERY prompt with the same short description of the person (age, build, hair, clothes), the place, the light and the style, '
    'using the same words each time.\n'
    '- Use calm camera moves: static, slow push-in, slow pan or slight handheld. Do not write text, subtitles, logos or sounds.\n'
    '- Each prompt is 30 to 60 words.\n'
    'Answer with a JSON array of {count} strings and nothing else.')

TALKING_SCENES_SYSTEM = (
    'You are an assistant film director writing prompts for an AI speech-to-video model (Wan 2.2 S2V). A photo of a person is animated '
    'by a voice recording. The final video is edited from {count} separate shots of up to 5 seconds, joined with hard cuts while the voice '
    'goes on. Every shot is generated on its own from the same photo.\n'
    'Write exactly {count} prompts, one per shot, in order.\n'
    'Rules:\n'
    + TALKING_COMMON.replace('Answer with', '- The photo fixes the person and the place, so keep both unchanged in every prompt. Vary only what an '
                             'editor varies: the camera distance and angle (medium shot, close-up, slightly from the side), the camera move '
                             'and the gesture or expression of each shot.\nAnswer with'))


# LTX 2.5 makes the picture and the sound in one pass, so its prompts must name the sound too (whatever is left out the model invents).
LTX_COMMON = (
    '- Write in English, even when the idea is in another language.\n'
    '- Choose ONE style phrase for the whole film that fits the idea (for a real animal or place: "photorealistic nature documentary footage, '
    'natural colors, long lens") and begin every prompt with exactly that phrase. Avoid words that pull the picture toward animation or '
    'illustration (cute, adorable, whimsical, magical, fairy, storybook, glowing) unless the idea asks for a cartoon.\n'
    '- Each prompt is ONE flowing paragraph of 60 to 110 words in this order: shot type and camera, who or what is in frame with a fixed '
    'description (species or age, build, hair or fur, clothes, colors), the single action of this shot, the setting and light, and the SOUND.\n'
    '- Name every sound with a source that can be seen or placed in the frame ("paws crunch in the snow", "a kettle whistles on the stove"), '
    'never a mood word like "tense atmosphere". Show emotion with physical details a lens can see.\n'
    '- If the idea contains speech, put the exact words in quotation marks, name the language, and let the shot begin with a short beat of silence. '
    'When a person is shown talking and the idea gives no words, write one short natural sentence (at most 12 words) for each shot where they '
    'speak, so that the sentences of consecutive shots follow each other as one speech; a speaker without quoted words comes out as an '
    'unintelligible murmur.\n'
    '- Use calm camera moves (static, slow push-in, slow pan, tracking). No on-screen text, subtitles or logos.\n'
    'Answer with a JSON array of {count} strings and nothing else.')

LTX_CHAIN_SYSTEM = (
    'You are an assistant film director writing prompts for an AI video model with sound (LTX 2.5). The final video is ONE continuous '
    'scene made of {count} consecutive parts of about 5 seconds; each part continues from the end of the previous one (the last second '
    'overlaps), so the place, the character and the light stay the same.\n'
    'Write exactly {count} prompts, one per part, that move one story forward from beginning to end; never repeat an earlier action.\n'
    'Rules:\n'
    + LTX_COMMON.replace('Answer with', '- No cuts and no change of place; a part may only continue what the previous one was doing, with a clearly new '
                         'movement or event so the picture does not freeze.\nAnswer with'))

LTX_SCENES_SYSTEM = (
    'You are an assistant film director writing prompts for an AI video model with sound (LTX 2.5). The final video is a film edited from '
    '{count} separate shots of about 5 seconds joined with hard cuts. Every shot is generated on its own, knowing nothing about the others.\n'
    'Write exactly {count} prompts, one per shot, that tell one story from beginning to end.\n'
    'Rules:\n'
    + LTX_COMMON.replace('Answer with', '- Every prompt is fully self-contained: describe the recurring characters with the same fixed words in EVERY prompt (never "he" or '
                         '"the same man"), and start with the shot type: an establishing wide shot, medium shots, close-ups, another side. '
                         'State the cut: what the sound does at this point (the same ambience goes on, a new place sounds different).\nAnswer with'))


def _extract(answer, count):
    """-> list of prompts from the model's answer; raises ValueError when it cannot be read as `count` prompts."""
    text = re.sub(r'^```[a-z]*\s*|\s*```$', '', answer.strip(), flags=re.I)
    start, end = text.find('['), text.rfind(']')
    items = None
    if start != -1 and end > start:
        try:
            items = json.loads(text[start:end + 1])
        except ValueError:
            items = None
    if items is None:
        # A numbered list is the most common deviation from the requested format.
        items = [re.sub(r'^\s*(?:\d+[.):]|[-*])\s*', '', line).strip(' "') for line in text.splitlines() if re.match(r'^\s*(?:\d+[.):]|[-*])\s+\S', line)]
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ValueError(ui_text('video.plan_answer_unreadable'))
    items = [' '.join(item.split()) for item in items if item.strip()]
    if len(items) != count:
        raise ValueError(ui_text('video.plan_wrong_count') % (len(items), count))
    if any(len(item) > MAX_PROMPT for item in items):
        raise ValueError(ui_text('video.plan_prompt_too_long'))
    return items


def _small_image(data_url, side=1024):
    """Base64 PNG of the source frame scaled down for Gemma's vision input; None when the picture cannot be prepared."""
    try:
        from PIL import Image
        raw = base64.b64decode(data_url.split(',', 1)[1], validate=False)
        with Image.open(io.BytesIO(raw)) as image:
            image = image.convert('RGB')
            image.thumbnail((side, side))
            buffer = io.BytesIO()
            image.save(buffer, 'PNG')
        return base64.b64encode(buffer.getvalue()).decode('ascii')
    except Exception:
        return None


class ScenePlanner:
    def __init__(self, text, gpu, submission_lock=None):
        self.text = text
        self.gpu = gpu
        self.lock = submission_lock or threading.RLock()

    def status(self):
        if self.text is None:
            return dict(available=False, reason=ui_text('video.plan_text_section_unavailable'), model='')
        try:
            info = self.text.availability()
        except Exception as error:
            return dict(available=False, reason=ui_text('video.plan_text_not_responding') % error, model='')
        if not info['available']:
            return dict(available=False, reason=info['reason'] or ui_text('video.plan_text_unavailable'), model='')
        return dict(available=True, reason='', model=self.text.gemma_model())

    def _ask(self, messages, cancel, limit):
        pieces = []
        options = dict(temperature=0.7, top_p=0.95, top_k=64, num_ctx=8192, num_predict=limit)
        with operation('VideoPromptPlanning', cancel=cancel):
            for row in self.text.stream_chat(self.text.gemma_model(), messages, False, cancel, options=options):
                pieces.append((row.get('message') or {}).get('content', ''))
        return ''.join(pieces)

    def plan(self, idea, count, image=None, join='continue', talking=False, speech=None, sound=False):
        """-> dict(prompts=[...], model=...). Raises Busy / GpuUnavailable / ValueError with a message for the person."""
        if not isinstance(idea, str) or not idea.strip() or len(idea) > 5000:
            raise ValueError(ui_text('video.validation_enter_video_description_up_5000_characters'))
        if join not in ('continue', 'cut'):
            raise ValueError(ui_text('video.plan_invalid_segment_count'))
        if type(count) is not int or not 2 <= count <= (16 if talking else 30 if join == 'cut' else 15 if sound else 12):
            raise ValueError(ui_text('video.plan_invalid_segment_count'))
        status = self.status()
        if not status['available']:
            raise ValueError(status['reason'])
        with self.lock:
            self.gpu.claim('text')
            cancel = self.text.begin(OWNER)
        try:
            self.text.start()
            user = dict(role='user', content='Idea for the whole video:\n' + idea.strip())
            picture = _small_image(image) if isinstance(image, str) and ',' in image else None
            if picture:
                user['images'] = [picture]
                unit = 'shot' if join == 'cut' else 'clip'
                user['content'] += ('\n\nThe attached picture is the first frame of %s 1: describe the character and place exactly as '
                                    'pictured in every prompt, and make %s 1 move on from it.' % (unit, unit))
            if talking and isinstance(speech, str) and speech.strip():
                user['content'] += ('\n\nWhat the person says in the whole video (it is spread over the %d parts in order, about equal in length):\n%s'
                                    % (count, speech.strip()[:1500]))
            if sound:      # LTX: picture and sound in one pass
                system = LTX_SCENES_SYSTEM if join == 'cut' else LTX_CHAIN_SYSTEM
            else:
                system = TALKING_SCENES_SYSTEM if talking else (SCENES_SYSTEM if join == 'cut' else SYSTEM)
            messages = [dict(role='system', content=system.format(count=count)), user]
            limit = min(6144, 260 * count + 300)
            error = None
            for _ in range(2):
                answer = self._ask(messages, cancel, limit)
                if cancel.is_set():
                    raise ValueError(ui_text('video.plan_building_stopped_message'))
                try:
                    return dict(prompts=_extract(answer, count), model=self.text.gemma_model())
                except ValueError as problem:
                    error = problem
                    messages += [dict(role='assistant', content=answer),
                                 dict(role='user', content='Wrong format: %s. Answer again with only a JSON array of exactly %d strings.' % (problem, count))]
            raise ValueError(ui_text('video.plan_gemma_failed') % error)
        except OSError as error:
            raise ValueError(ui_text('video.plan_gemma_did_not_respond') % str(error)[:160]) from None
        finally:
            self.text.end(OWNER)
