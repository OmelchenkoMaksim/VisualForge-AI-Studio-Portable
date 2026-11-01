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

    def image(self, step, sources, canvas, seed, cancel):
        payload = dict(image_options(step), canvas=canvas, seed=seed)
        if sources:
            payload['prompt'] += (' Complete any empty canvas padding coherently with the requested scene, preserving the actual '
                                  'source content. Keep genuinely transparent space only when the requested output needs transparency.')
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
        answer = self.request('/api/generate', payload, timeout=1800)
        ident, token = answer['prompt_id'], answer['cancel_token']
        row = self.wait(lambda: self.request('/api/status?id=' + ident, timeout=60), cancel,
                        lambda _: self.request('/api/cancel', dict(prompt_id=ident, cancel_token=token)), limit=8 * 3600)
        if cancel.is_set():
            raise DirectorCancelled()
        if row.get('state') != 'done':
            raise DirectorError(row.get('error') or ui_text('composition.panel_image_failed_text'))
        return self.request('/api/result?id=%s&index=0' % ident, binary=True), dict(job=ident, save_dir=row.get('save_dir', ''),
                    engine=step['engine'], seed=seed, prompt_enhancement=answer.get('prompt_enhancement'))
