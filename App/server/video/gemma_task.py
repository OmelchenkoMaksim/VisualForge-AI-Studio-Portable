"""What every Gemma helper of the Video section has in common: is the model there, take the video card for the text module, run one chat call.

The prompt rewrite (`prompt_enhancer.py`), the scenario planner (`scene_planner.py`) and the clip review (`director_review.py`) all follow one pattern:
check that Gemma is installed, claim the card for the Text module under the bridge's submission lock, tell the Text manager who is using the model
(so a chat message or a video job can ask it to stop), run the call(s), and always give the model back. A helper subclasses `GemmaTask`, names its
`OWNER` and `OPERATION`, and does its own work inside `with self.session() as cancel:`.
"""
from ui_strings import source as ui_text
import base64
from contextlib import contextmanager
import io
import threading

from generation_identity import operation


def small_image(data_url, side=1024):
    """Base64 PNG of a data-URL picture scaled down to `side` pixels for Gemma's vision input; None when it cannot be prepared."""
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
        return None            # the picture is an extra for the rewrite or the scenario; the text is still answered


class GemmaTask:
    OWNER = 'video-gemma'          # the name under which the Text module sees this use of the model
    OPERATION = ''                 # the generation_identity type of one model call (see event_history.TYPES)

    def __init__(self, text, gpu, submission_lock=None):
        self.text = text
        self.gpu = gpu
        self.lock = submission_lock or threading.RLock()

    def status(self):
        """-> dict(available, reason, model): whether the Text module can answer now and why not."""
        if self.text is None:
            return dict(available=False, reason=ui_text('video.plan_text_section_unavailable'), model='')
        try:
            info = self.text.availability()
        except Exception as error:
            return dict(available=False, reason=ui_text('video.plan_text_not_responding') % error, model='')
        if not info['available']:
            return dict(available=False, reason=info['reason'] or ui_text('video.plan_text_unavailable'), model='')
        return dict(available=True, reason='', model=self.text.gemma_model())

    @contextmanager
    def session(self):
        """Claim the card, start Gemma and yield the `cancel` event of this use; the model is released however the block ends.

        ValueError (for the person) when Gemma is not available; Busy / GpuUnavailable from the coordinator pass through; an OSError of the model
        server is turned into a ValueError with the first 160 characters of its message."""
        status = self.status()
        if not status['available']:
            raise ValueError(status['reason'])
        with self.lock:
            self.gpu.claim('text')
            cancel = self.text.begin(self.OWNER)
        try:
            self.text.start()
            yield cancel
        except OSError as error:
            raise ValueError(ui_text('video.plan_gemma_did_not_respond') % str(error)[:160]) from None
        finally:
            self.text.end(self.OWNER)

    def ask(self, messages, cancel, **options):
        """One streamed chat call without thinking: the text of the answer. `options` go to Ollama (temperature, num_ctx, num_predict...)."""
        pieces = []
        with operation(self.OPERATION, cancel=cancel):
            for row in self.text.stream_chat(self.text.gemma_model(), messages, False, cancel, options=options):
                pieces.append((row.get('message') or {}).get('content', ''))
        return ''.join(pieces)
