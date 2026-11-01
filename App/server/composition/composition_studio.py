"""Image-only use of the Director's authenticated loopback transport and cancellation protocol."""
import base64

from director.studio import Studio, DirectorCancelled, DirectorError
from ui_strings import source as ui_text
from .composition_plan import capabilities, image_options


class CompositionStudio(Studio):
    def __init__(self, port, token, pause=None):
        super().__init__(port, token, None, **({'pause':pause} if pause else {}))

    def capabilities(self):
        return capabilities(self.request('/api/preflight', timeout=60))

    def image(self, step, sources, canvas, seed, cancel, note=''):
        payload = dict(image_options(step), canvas=canvas, seed=seed)
        if note:                                  # the first picture was put on a canvas (`composition_media.prepare`): the model is told what the margins are
            payload['prompt'] += ' ' + note
        images = [dict(base64=base64.b64encode(raw).decode('ascii')) for raw in sources]
        if images:
            payload['image1'] = images[0]
        if len(images) > 1:
            payload['image2'] = images[1]
        if len(images) > 2:
            payload['extra_images'] = images[2:]
        if cancel.is_set():
            raise DirectorCancelled()
        # The request may include a slow Gemma pass. Cancel its preparation owner as well as an already queued image.
        # `start` waits out a 409 (the card is held for a moment by another module) instead of ending the whole composition at the first refusal.
        answer = self.start('/api/generate', payload, timeout=1800)
        ident, token = answer['prompt_id'], answer['cancel_token']
        row = self.wait(lambda: self.request('/api/status?id=' + ident, timeout=60), cancel,
                        lambda _: self.request('/api/cancel', dict(prompt_id=ident, cancel_token=token)), limit=8 * 3600)
        if cancel.is_set():
            raise DirectorCancelled()
        if row.get('state') != 'done':
            raise DirectorError(row.get('error') or ui_text('composition.panel_image_failed_text'))
        return self.request('/api/result?id=%s&index=0' % ident, binary=True), dict(job=ident, save_dir=row.get('save_dir', ''),
                    engine=step['engine'], seed=seed, prompt_enhancement=answer.get('prompt_enhancement'))
