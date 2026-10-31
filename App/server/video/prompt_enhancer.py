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
    'mood) and environmental sounds with their sources (footsteps, wind, traffic, animals). If the request implies sound, describe it plausibly. '
    'Whenever a person speaks, put the exact words in quotes: when the user gave none, write one short natural sentence of at most 12 words '
    '(a speaker described without quoted words comes out as an unintelligible murmur; measured 2026-10-31 on a 30 s clip).\n'
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
    'Use concrete words (never "dynamic", "dramatic", "epic"); show emotion through visible details. Whenever a person speaks put the exact words '
    'in quotes (one short natural sentence of at most 12 words when the user gave none: a speaker without quoted words comes out as a murmur). '
    'Output only the paragraph.')

LTX_HYBRID_EXTRA = (
    'Length: 110 to 170 words. If the user\'s request is already detailed (camera, lens, light, sound), keep each of their specifics word for '
    'word and add only what is missing (usually the sound and the viewpoint); never shorten or generalize what they wrote. If it is only a few '
    'words, invent plausible concrete details but keep the idea exactly.')

# ---- Wan Animate 2 and SCAIL-2 ---------------------------------------------------------------------------------------------
# Wan Animate 2 (model card, Wan-AI/Wan2.2-Animate-2-14B) takes two texts: the appearance of the person and the background, each
# objective and without any action ("Character appearance description" / "Background description"; the card writes them in Chinese, the
# ComfyUI template in English), and the camera view is steered by the text too. The ComfyUI template adds a second text for the pose
# branch that tells what the motion in the video is. SCAIL-2 (zai-org/SCAIL-2) takes one text that describes the finished video, not an
# instruction; long and detailed texts work better, and a replacement text should describe the video AFTER the replacement, with the
# clothing of the new person and the objects around (vehicles, chairs, instruments).
CHARACTER_PICTURES = (
    'You see the pictures in this order: first the PHOTO of the person, then (when listed) extra object pictures, then FRAMES of the reference '
    'video in time order. Describe only what you can see; never name or identify anyone and never infer ethnicity, nationality or religion. The person '
    'who will be in the finished video looks exactly as in the PHOTO (face, hair, glasses, clothing, bare skin): never copy the clothing, glasses or hair '
    'of the person in the video frames, they are replaced; take from the frames only the pose, the motion, the objects, the place, the light and the camera.\n')

ANIMATE2_RULES = (
    'You write the two text prompts for the video model Wan Animate 2, which makes the person of a PHOTO perform the motion of a REFERENCE VIDEO.\n'
    + CHARACTER_PICTURES +
    'Answer with exactly these two labelled parts and nothing else:\n'
    'CHARACTER AND BACKGROUND: "Character appearance description: ..." followed by "Background description: ...", 60 to 110 words together, English.\n'
    '- The character part describes the PHOTO only: apparent age group and build, face and hair (including beard and glasses), every garment with colour '
    'and material, accessories. No actions, poses or feelings, nothing the photo does not show unless the user asks for it (then follow the user).\n'
    '- The background part says where the person is. Take the place, ground, objects, time of day and light from the user\'s request, otherwise from the '
    'video frames. End it with the camera: shot size, viewpoint (front, side, low or high angle) and whether it is static or moves. Objective words only, '
    'no mood.\n'
    'MOTION: one sentence of 15 to 35 words that tells what the person in the video frames does, in the order it happens (body, arms and hands, head, '
    'expression, speech). Do not describe looks or place here.\n'
    'Keep every element the user stated.')

SCAIL_REPLACE_RULES = (
    'You write the text prompt for the video model SCAIL-2, which REPLACES the performer of a REFERENCE VIDEO with the person of a PHOTO and keeps the rest '
    'of the video.\n' + CHARACTER_PICTURES)
SCAIL_REPLACE_DETAIL = (
    'Write ONE paragraph of 70 to 130 words in English that describes the FINISHED video as if it already existed (never an instruction such as "replace" '
    'or "make"): who is in it (the photo person: apparent age group, hair, face hair, glasses, every garment with colour and material), what they do as the '
    'video frames show it, in the order it happens, the objects they sit on, hold or touch (chair, car, instrument, tool), what moves around them '
    '(vehicles, people) exactly as the frames show, then the place, the light and the camera (shot size, viewpoint, static or moving). Keep the scene of '
    'the video; change it only where the user asks. Observable facts only, no feelings. Output only the paragraph.')

SCAIL_ANIMATE_RULES = (
    'You write the text prompt for the video model SCAIL-2, which ANIMATES the person of a PHOTO with the motion of a REFERENCE VIDEO. The place comes from '
    'the photo unless the user names another.\n' + CHARACTER_PICTURES)
SCAIL_ANIMATE_DETAIL = (
    'Write ONE paragraph of 60 to 110 words in English that describes the FINISHED video as if it already existed (never an instruction): the person '
    '(apparent age group, hair, face hair, glasses, every garment with colour and material, as the PHOTO shows), what they do as the video frames show it, '
    'in order, the objects they touch, the place and the light (from the photo or the user), and the camera (shot size, viewpoint, static or moving). '
    'Observable facts only, no feelings. Output only the paragraph.')

CHARACTER_CONCISE = (
    'Write ONE paragraph of 40 to 70 words in English that describes the finished video for the video model: the person of the PHOTO (age group, hair, '
    'every garment with colour), what they do as the video frames show, the place and light, the camera. Observable facts only, never an instruction. '
    'Keep every element the user stated. Output only the paragraph.')

# ---- Wan 2.2 TI2V-5B and S2V-14B ---------------------------------------------------------------------------------------------
# The Wan 2.2 repository ships prompt-extension instructions for its A14B models (wan/utils/system_prompt.py); the TI2V-5B and S2V cards have no
# prompt guide of their own, so the TI2V-5B guides below restate the A14B ones, which describe the same Wan training captions: for text-to-video
# a "film director" who keeps the subject and the action, adds visible detail, picks up to four cinematic settings (time of day, light source and
# quality, colour tone, shot size, camera angle, composition), describes the motion as a process and writes 60-200 words in English; for
# image-to-video a rewriter who keeps only the dynamic content (the action and the camera), drops what the picture already shows and writes at
# most 100 words. For S2V the card only shows short style-plus-scene prompts ("Summer beach vacation style, a white cat wearing sunglasses sits on a
# surfboard", "a person is singing"): the speech sound drives the mouth, head and hands, so the text sets the look, the place and the camera.
WAN_T2V_DIRECTOR = (
    'You are a film director who turns a short video request into one complete English prompt for the video model Wan 2.2. Keep the subject and '
    'the action the user stated and never add a new subject.\n'
    'Rules:\n'
    '1. Put the technical settings first as short tags, then the scene. Choose at most four settings: time of day, light source (matching '
    'indoors or outdoors), light quality (soft or hard), light angle, colour tone, shot size, camera angle, composition. Defaults when the user '
    'gave none: daytime, medium or wide shot, centre composition. Leave out the camera angle when you describe a camera movement. Keep any '
    'camera, lens or style the user named.\n'
    '2. If the user named a style, say it first. For an illustration, cartoon or 2D style leave out the photographic and cinematic words.\n'
    '3. Describe the subject in detail: appearance, clothing, expression, how many there are, the pose; then the background and its objects.\n'
    '4. Describe the motion as a process in time: what moves, how fast, in which direction. If the user gave no action add a clear, simple, natural '
    'movement of the subject itself (walking, turning, reaching), never just standing or sitting still. Small background motion (drifting clouds, '
    'moving leaves) is welcome.\n'
    '5. Only what can be seen: no feelings, moods or literary phrases. Describe a sky as clear blue so it does not burn out.\n'
    '6. Write one English paragraph of {words} words, with no preamble and no list. Output only the prompt.')

WAN_T2V_CONCISE = (
    'Rewrite the user\'s video request as ONE English paragraph of 40 to 70 words for the video model Wan 2.2. Keep everything the user stated and '
    'add no new subject. Order: style (only if named), shot size and camera movement, the subject with concrete visible details, the action as it '
    'unfolds in time, the place and the light. Concrete visible words only, no feelings, no list. Output only the paragraph.')

WAN_I2V_GROUNDING = (
    'You are given the START PICTURE of the video: it is the first frame, and the video model already sees everything in it. Do NOT describe what '
    'the picture shows (the person, the clothes, the room); write only what moves.\n')

WAN_I2V_MOTION = WAN_I2V_GROUNDING + (
    'You turn the user\'s request into the motion prompt for the video model Wan 2.2 (image to video), in English, at most 100 words.\n'
    'Rules: keep the user\'s actions and any camera instruction exactly; name the subject in a few words from the picture if the user only gave an '
    'action; if the user wrote a long text keep only the sequence of actions; if the request is only a few words add plausible natural motion of the '
    'things in the picture (the subject, the cloth, the hair, the background) and a calm camera; describe the motion as a process in time, in '
    'the order it happens. Never change the identity, the clothing or the place of the picture. Only what can be seen, no feelings. Output only the prompt.')

WAN_I2V_BRIEF = WAN_I2V_GROUNDING + (
    'Write ONE English sentence or two, at most 45 words, for the video model Wan 2.2: the actions in the order they happen, the camera movement if the '
    'user named one (otherwise a steady camera). Keep what the user stated, add nothing new to the picture. Output only the prompt.')

WAN_S2V_GROUNDING = (
    'The video model Wan 2.2 S2V makes a talking or singing person from a PHOTO and a SPEECH recording the user supplies: the recording alone drives '
    'the mouth, the head and the hands, and the PHOTO is the first frame. So do NOT write what is said, the sound, the lips or the lip sync. Describe '
    'the look, the place and the camera, in English. Keep everything the user stated. Output only the prompt.\n')

WAN_S2V_SCENE = WAN_S2V_GROUNDING + (
    'Write one paragraph of 30 to 60 words: the style or the occasion if the user named one; the person as the PHOTO shows (age group, hair, clothes, '
    'face); how they deliver the speech (looks into the camera, natural small head movement and hand gestures, an expression that fits the user\'s '
    'idea); the place and the light as in the PHOTO; the shot size and a steady camera. Never change the person, the clothes or the place of the PHOTO.')

WAN_S2V_BRIEF = WAN_S2V_GROUNDING + (
    'Write ONE sentence of at most 20 words in the form of the model card\'s examples: the style or the occasion, the person from the PHOTO in a few '
    'words, what they do (speaks or sings to the camera) and the place.')

GUIDES = {
    'wan22-ti2v-5b': {
        't2v': dict(director=WAN_T2V_DIRECTOR.format(words='80 to 140'), concise=WAN_T2V_CONCISE),
        'i2v': dict(motion=WAN_I2V_MOTION, brief=WAN_I2V_BRIEF),
    },
    'wan22-s2v-14b': {'s2v': dict(scene=WAN_S2V_SCENE, brief=WAN_S2V_BRIEF)},
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
    'wan-animate2': {'animate': dict(structured=ANIMATE2_RULES)},
    'scail2': {'replace': dict(detailed=SCAIL_REPLACE_RULES + SCAIL_REPLACE_DETAIL, concise=SCAIL_REPLACE_RULES + CHARACTER_CONCISE),
               'animate': dict(detailed=SCAIL_ANIMATE_RULES + SCAIL_ANIMATE_DETAIL, concise=SCAIL_ANIMATE_RULES + CHARACTER_CONCISE)},
}
# Modes whose answer has two labelled texts (the picture-and-background text and the motion text of the pose branch).
TWO_PART = {('wan-animate2', 'animate')}
# What the application uses; changed only by a measured comparison (docs/LTX-2.5-Video.md).
# Measured 2026-10-31 (preview, 768x512, 97 frames, seed 11, three request levels, one subject): for t2v `concise` had the most motion and the
# loudest sound at every level and kept a detailed request's framing, while `caption` and Lightricks' own text drifted to blurred close-ups and
# the latter was nearly silent on the medium request. For i2v `hybrid` moved more and kept audible sound (-46 dB against -63 dB), both followed
# the action; first/last frame and audio-to-video have only `hybrid` and were not compared.
# Wan 2.2 (2026-10-31, 832x480, 81 frames, seed 11; docs/Wan-2.2-Video.md, docs/Wan-S2V.md): text-to-video `director` kept the subject large and in the frame at all
# three request levels (the unrewritten request gave a speck in the forest), `concise` moved more on a one-line request; image-to-video `motion` kept face and room
# steady while `brief` threw the hand over the face; S2V `scene` added head movement and gestures (motion 3.2-4.1 against 1.8-2.5) with the same face and sync.
DEFAULT_VARIANT = {('ltx25-distilled', 't2v'): 'concise', ('ltx25-distilled', 'i2v'): 'hybrid',
                   ('ltx25-distilled', 'first_last'): 'hybrid', ('ltx25-distilled', 'a2v'): 'hybrid',
                   ('wan22-ti2v-5b', 't2v'): 'director', ('wan22-ti2v-5b', 'i2v'): 'motion', ('wan22-s2v-14b', 's2v'): 'scene',
                   ('wan-animate2', 'animate'): 'structured', ('scail2', 'replace'): 'detailed', ('scail2', 'animate'): 'detailed'}


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


def split_parts(answer):
    """The answer of a two-text guide -> (character and background text, motion text), or None when the `MOTION:` part is missing."""
    text = re.sub(r'^```[a-z]*\s*|\s*```$', '', answer.strip(), flags=re.I)
    found = [match for match in re.finditer(r'(?im)^\W*MOTION\s*:', text)]
    if not found:
        return None
    first, second = clean(re.sub(r'(?i)^\W*CHARACTER[^:\n]*:\s*', '', text[:found[-1].start()].strip())), clean(text[found[-1].end():])
    return (first, second) if len(first) >= 40 and len(second) >= 10 else None


def character_note(objects, extras, frames):
    """What each picture sent with a character request is, so the model does not have to guess: photo, extra objects, video frames."""
    objects = objects if isinstance(objects, dict) else {}
    word = lambda value, default: value.strip()[:40] if isinstance(value, str) and value.strip() else default
    lines = ['Picture 1: the PHOTO of the person (found in the photo by the word "%s").' % word(objects.get('reference'), 'human')]
    subjects = objects.get('subjects') if isinstance(objects.get('subjects'), list) else []
    for index in range(extras):
        item = subjects[index] if index < len(subjects) and isinstance(subjects[index], dict) else {}
        lines.append('Picture %d: an extra object, "%s", which takes the place of "%s" in the video.' % (index + 2, word(item.get('reference'), 'car'), word(item.get('driving'), 'truck')))
    if frames:
        first = extras + 2
        lines.append('Picture%s %s: FRAMES of the reference video in time order; the performer there is found by the word "%s".'
                     % ('s' if frames > 1 else '', first if frames == 1 else '%d-%d' % (first, first + frames - 1), word(objects.get('driving'), 'human')))
    return '\n'.join(lines)


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

    def _ask(self, messages, cancel, pictures=0):
        pieces = []
        # every picture costs the model a few hundred tokens of context; a photo plus frames of a video need more room than a single frame
        options = dict(temperature=0.5, top_p=0.9, top_k=40, num_ctx=6144 if pictures < 3 else 10240, num_predict=700)
        with operation('VideoPromptEnhancement', cancel=cancel):
            for row in self.text.stream_chat(self.text.gemma_model(), messages, False, cancel, options=options):
                pieces.append((row.get('message') or {}).get('content', ''))
        return ''.join(pieces)

    def enhance(self, prompt, engine, mode, images=(), variant=None, note=''):
        """-> dict(prompt=<rewritten>, variant=..., model=...), plus `motion_prompt` for two-text guides. `note` tells what each picture is.

        Raises Busy / GpuUnavailable / ValueError with a message for the person."""
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
            user = dict(role='user', content='User request:\n' + prompt.strip() + (('\n\n' + note.strip()[:1500]) if isinstance(note, str) and note.strip() else ''))
            pictures = [small_image(item) for item in images if isinstance(item, str) and ',' in item]
            pictures = [item for item in pictures if item]
            if pictures:
                user['images'] = pictures
            messages = [dict(role='system', content=system), user]
            answer = ''
            two_texts = (engine, mode) in TWO_PART
            for _ in range(2):
                raw = self._ask(messages, cancel, len(pictures))
                if cancel.is_set():
                    raise ValueError(ui_text('video.plan_building_stopped_message'))
                if two_texts:
                    parts = split_parts(raw)
                    if parts and len(parts[0]) <= MAX_PROMPT:
                        return dict(prompt=parts[0], motion_prompt=parts[1], variant=variant, model=self.text.gemma_model())
                    messages += [dict(role='assistant', content=raw[:3000]), dict(role='user', content='Answer again with exactly the two labelled parts CHARACTER AND BACKGROUND: and MOTION:.')]
                    continue
                answer = clean(raw)
                if 20 <= len(answer) <= MAX_PROMPT:
                    return dict(prompt=answer, variant=variant, model=self.text.gemma_model())
                messages += [dict(role='assistant', content=answer), dict(role='user', content='Answer again with only the paragraph, at most 1700 characters.')]
            raise ValueError(ui_text('video.enhance_gemma_failed'))
        except OSError as error:
            raise ValueError(ui_text('video.plan_gemma_did_not_respond') % str(error)[:160]) from None
        finally:
            self.text.end(OWNER)
