"""Gemma rewrites a short or rough video prompt into the form the chosen video model follows best.

Every model and mode has its own hidden instruction (`GUIDES`), built from what the publisher recommends and what independent users
found; the person never has to learn how to write a prompt for each network. The rewritten text is what the video model receives; the
person's own words stay in the history. `variant` exists to compare instructions (see docs): the application uses `DEFAULT_VARIANT`.
"""
from ui_strings import source as ui_text
import base64
import io
import os
import re
import threading
from pathlib import Path

from generation_identity import operation
from gpu_coordinator import Busy, GpuUnavailable

OWNER = 'video-prompt'
MAX_PROMPT = 1800

# ---- LTX 2.5 -----------------------------------------------------------------------------------------------------------
# Lightricks trained the model on audio-visual captions of one fixed style and ships a prompt enhancer for it (LTX-2 repository,
# `ltx_core/text_encoders/gemma/encoders/prompts`). The rules below restate that style in our own words (the repository's text is under the
# LTX license and is not copied): action first, observable description only, one shot type + camera motion + viewpoint woven into the
# prose, the whole soundscape, chronological flow, one paragraph of 150-220 words, and the result is judged against the ORIGINAL request.
LTX_CAPTION_RULES = (
    'You turn a user\'s short video request into ONE detailed audio-visual description for the video model LTX 2.5, written the way that '
    'model\'s training captions are written. The finished video is judged against the user\'s ORIGINAL request, so keep every element the '
    'user stated and never contradict or drop anything.\n'
    'Rules:\n'
    '1. Begin immediately with the action or a visual detail. Never start with "The scene opens", "We see" or "There is".\n'
    '2. Describe only what can be seen and heard. Replace feelings with visible cues (not "he is sad" but "his eyebrows angle downward and his '
    'lips are pressed together").\n'
    '3. Give the environment (materials, textures, light, colors), the characters (clothing, posture, face, hair, apparent age group, build) '
    'and where everything is. Never infer ethnicity, nationality, religion or culture.\n'
    '4. Weave these into the prose, never as tags: exactly one shot type (extreme wide shot, wide shot, medium shot, medium close-up, close-up '
    'or extreme close-up); the camera movement, always stated (say that the camera stays static when it does not move); the viewpoint '
    '(front-facing, back-facing, side view, over-the-shoulder, top-down, low-angle or high-angle). If the user named a camera, a lens or a '
    'move, keep it exactly.\n'
    '5. Put the whole soundscape into the same prose: dialogue quoted exactly in its original language with the tone of voice, music (kind and '
    'mood) and environmental sounds with their sources (footsteps, wind, traffic, animals). If the request implies sound, describe it plausibly.\n'
    '6. Tell it in strict chronological, real-time order ("Initially...", "A moment later...", "Simultaneously...") and keep every stated action '
    'in motion.\n'
    '7. Write ONE paragraph: no lists, headers or labels such as "Audio:". Output only the paragraph, in English even if the request is in another '
    'language, with no preamble.\n')

LTX_CAPTION_QUALITY = (
    'Give the scene strong production value inside the same prose: cinematic color and contrast, natural light, crisp texture and depth. Never '
    'add new objects or actions for that.')

LTX_I2V_GROUNDING = (
    'You are also given the REFERENCE IMAGE: it is the exact first frame of the video. The opening of your description must match it exactly '
    '(same subjects, identity, clothing, setting, light and composition), then continue as the user\'s requested action unfolds from it. Never '
    'contradict or replace anything shown; the shot type and viewpoint at the start must match how the image is framed. One continuous take, '
    'no cuts.\n')

LTX_FIRST_LAST_GROUNDING = (
    'You are given TWO pictures: the first is the exact first frame of the video and the second is its exact last frame. Describe one continuous '
    'take that starts from the first picture and ends on the second: say what changes in between (the subject\'s move, the camera, the light) '
    'as a single plausible motion, and let the last sentence describe what the second picture shows. Never invent objects absent from both pictures.\n')

LTX_A2V_GROUNDING = (
    'The sound of this video is a recording the user supplied: the picture will be fitted to it. So do NOT describe sound at all (no dialogue, '
    'music or ambience). Instead describe the speaker or performer, how they deliver it (face, mouth, head and hand movement, body language), '
    'the place and the camera, in one paragraph of 80-130 words. If a first-frame picture is attached, match it exactly.\n')

LTX_A2V = LTX_A2V_GROUNDING + (
    'Rules: begin with the action or a visual detail; describe only what can be seen (feelings as visible cues); state exactly one shot type, '
    'the camera movement (or that the camera stays static) and the viewpoint, keeping any camera or lens the user named; keep every element '
    'the user stated; write English, no preamble.')

LTX_CONCISE = (
    'Rewrite the user\'s video request as ONE flowing paragraph of 70 to 110 words for the video model LTX 2.5, in English. Keep everything the '
    'user stated. Order: shot type and camera move (one move only), who or what is in frame with concrete visible details, the single main '
    'action in present tense, the setting and the light (name the time of day and the light direction), and the sounds with visible sources. '
    'Use concrete words (never "dynamic", "dramatic", "epic"); show emotion through visible details. Output only the paragraph.')

LTX_HYBRID_EXTRA = (
    'Length: 110 to 170 words. If the user\'s request is already detailed (camera, lens, light, sound), keep each of their specifics word for '
    'word and add only what is missing (usually the sound and the viewpoint); never shorten or generalize what they wrote. If it is only a few '
    'words, invent plausible concrete details but keep the idea exactly.')

GUIDES = {
    'ltx25-distilled': {
        't2v': dict(caption=LTX_CAPTION_RULES + LTX_CAPTION_QUALITY + '\nAim for a paragraph of 150-220 words.',
                    concise=LTX_CONCISE,
                    hybrid=LTX_CAPTION_RULES + LTX_HYBRID_EXTRA + '\n' + LTX_CAPTION_QUALITY),
        'i2v': dict(caption=LTX_I2V_GROUNDING + LTX_CAPTION_RULES + LTX_CAPTION_QUALITY + '\nAim for a paragraph of 150-220 words.',
                    concise=LTX_I2V_GROUNDING + LTX_CONCISE,
                    hybrid=LTX_I2V_GROUNDING + LTX_CAPTION_RULES + LTX_HYBRID_EXTRA + '\n' + LTX_CAPTION_QUALITY),
        'first_last': dict(hybrid=LTX_FIRST_LAST_GROUNDING + LTX_CAPTION_RULES + LTX_HYBRID_EXTRA + '\n' + LTX_CAPTION_QUALITY),
        'a2v': dict(hybrid=LTX_A2V),
    },
}
# What the application uses; changed only by a measured comparison (docs/LTX-2.5-Video.md).
# Measured 2026-10-31 (preview, 768x512, 97 frames, seed 11, three request levels, one subject): for t2v `concise` had the most motion and the
# loudest sound at every level and kept a detailed request's framing, while `caption` and Lightricks' own text drifted to blurred close-ups and
# the latter was nearly silent on the medium request. For i2v `hybrid` moved more and kept audible sound (-46 dB against -63 dB), both followed
# the action; first/last frame and audio-to-video have only `hybrid` and were not compared.
DEFAULT_VARIANT = {('ltx25-distilled', 't2v'): 'concise', ('ltx25-distilled', 'i2v'): 'hybrid',
                   ('ltx25-distilled', 'first_last'): 'hybrid', ('ltx25-distilled', 'a2v'): 'hybrid'}


def supported(engine, mode):
    return mode in GUIDES.get(engine, {})


def guide(engine, mode, variant=None):
    """-> (variant name, instruction). `official:<name>` loads a text file from VISUALFORGE_PROMPT_GUIDE_DIR (diagnostics only)."""
    if not supported(engine, mode):
        raise ValueError(ui_text('video.enhance_not_available_for_mode'))
    choices = GUIDES[engine][mode]
    variant = variant or DEFAULT_VARIANT[(engine, mode)]
    if variant.startswith('file:'):
        folder = os.environ.get('VISUALFORGE_PROMPT_GUIDE_DIR')
        name = variant[5:]
        if not folder or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', name) or not (Path(folder) / name).is_file():
            raise ValueError(ui_text('video.enhance_unknown_variant'))
        return variant, (Path(folder) / name).read_text(encoding='utf-8')
    if variant not in choices:
        raise ValueError(ui_text('video.enhance_unknown_variant'))
    return variant, choices[variant]


def clean(answer):
    """The model's answer -> one paragraph without fences, labels, quotes around the whole text or a preamble."""
    text = re.sub(r'^```[a-z]*\s*|\s*```$', '', answer.strip(), flags=re.I)
    text = re.sub(r'^(?:here is|here\'s|caption|prompt|description|enhanced prompt)[^:\n]{0,40}:\s*', '', text, flags=re.I)
    text = ' '.join(text.split())
    if len(text) > 2 and text[0] == text[-1] and text[0] in '"“\'' and text.count(text[0]) == 2:
        text = text[1:-1].strip()
    return text


def small_image(data_url, side=1024):
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


class PromptEnhancer:
    def __init__(self, text, gpu, submission_lock=None):
        self.text = text
        self.gpu = gpu
        self.lock = submission_lock or threading.RLock()

    def status(self):
        if self.text is None:
            return dict(available=False, reason=ui_text('video.plan_text_section_unavailable'))
        try:
            info = self.text.availability()
        except Exception as error:
            return dict(available=False, reason=ui_text('video.plan_text_not_responding') % error)
        return dict(available=bool(info['available']), reason='' if info['available'] else info['reason'] or ui_text('video.plan_text_unavailable'))

    def _ask(self, messages, cancel):
        pieces = []
        options = dict(temperature=0.5, top_p=0.9, top_k=40, num_ctx=6144, num_predict=700)
        with operation('VideoPromptEnhancement', cancel=cancel):
            for row in self.text.stream_chat(self.text.gemma_model(), messages, False, cancel, options=options):
                pieces.append((row.get('message') or {}).get('content', ''))
        return ''.join(pieces)

    def enhance(self, prompt, engine, mode, images=(), variant=None):
        """-> dict(prompt=<rewritten>, variant=..., model=...). Raises Busy / GpuUnavailable / ValueError with a message for the person."""
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
            raise ValueError(ui_text('video.validation_enter_video_description_up_5000_characters'))
        variant, system = guide(engine, mode, variant)
        status = self.status()
        if not status['available']:
            raise ValueError(status['reason'])
        with self.lock:
            self.gpu.claim('text')
            cancel = self.text.begin(OWNER)
        try:
            self.text.start()
            user = dict(role='user', content='User request:\n' + prompt.strip())
            pictures = [small_image(item) for item in images if isinstance(item, str) and ',' in item]
            pictures = [item for item in pictures if item]
            if pictures:
                user['images'] = pictures
            messages = [dict(role='system', content=system), user]
            answer = ''
            for _ in range(2):
                answer = clean(self._ask(messages, cancel))
                if cancel.is_set():
                    raise ValueError(ui_text('video.plan_building_stopped_message'))
                if 20 <= len(answer) <= MAX_PROMPT:
                    return dict(prompt=answer, variant=variant, model=self.text.gemma_model())
                messages += [dict(role='assistant', content=answer), dict(role='user', content='Answer again with only the paragraph, at most 1700 characters.')]
            raise ValueError(ui_text('video.enhance_gemma_failed'))
        except OSError as error:
            raise ValueError(ui_text('video.plan_gemma_did_not_respond') % str(error)[:160]) from None
        finally:
            self.text.end(OWNER)
