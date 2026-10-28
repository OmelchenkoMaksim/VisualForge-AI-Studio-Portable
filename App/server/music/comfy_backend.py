"""Stable Audio 3 on the Image ComfyUI: one job is one ComfyUI prompt, held under the Music GPU slot until the WAV is published.

The worker engines (ACE-Step, MiniMax) run in their own Python subprocess; this backend instead talks HTTP to the ComfyUI that the
Image section already runs. ComfyUI writes a FLAC preview into its temp folder; the bridge fetches it and turns it into the WAV that
Music stores, reading the real sample rate, channel count and length from that file.
"""
from ui_strings import source as ui_text
import hashlib
import json
from pathlib import Path
from typing import Callable
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

WORKFLOW = 'stable-audio-3-medium/text.api.json'
RESULT_NODE = '90'
CHECKPOINT = ('checkpoints', 'stable_audio_3_medium.safetensors')
TEXT_ENCODER = ('text_encoders', 't5gemma_b_b_ul2.safetensors')
MAX_FLAC_BYTES = 512 * 1024 * 1024
JOB_TIMEOUT_SEC = 3600


class ComfyError(RuntimeError):
    """ComfyUI answered with an HTTP error; its own explanation is kept because it names the node or input at fault."""


class Cancelled(Exception):
    """The user stopped the job; ComfyUI confirmed it or the wait for confirmation ran out."""


class ComfyClient:
    """Minimal loopback client for one ComfyUI; it never uses a proxy and never follows a path the caller did not build."""

    def __init__(self, base, client_id=None):
        self.base = base.rstrip('/')
        self.client_id = client_id or uuid.uuid4().hex
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _open(self, path, data=None, timeout=30, limit=None):
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.base + path, data=body, headers={'Content-Type': 'application/json'} if body is not None else {})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.read() if limit is None else response.read(limit + 1)
        except urllib.error.HTTPError as error:
            raise ComfyError('ComfyUI HTTP %d: %s' % (error.code, error.read(3000).decode('utf-8', 'replace'))) from None

    def get(self, path, timeout=30):
        return json.loads(self._open(path, timeout=timeout))

    def post(self, path, data, timeout=30):
        raw = self._open(path, data, timeout=timeout)
        return json.loads(raw) if raw else {}

    def fetch(self, path, limit=MAX_FLAC_BYTES, timeout=120):
        data = self._open(path, timeout=timeout, limit=limit)
        if len(data) > limit:
            raise RuntimeError(ui_text('music.fetch_comfy_ui_returned_audio_file_too'))
        return data


def flac_to_wav(source, target):
    """Decode a FLAC into a PCM WAV without changing a sample: 16-bit stays 16-bit, 32-bit stays 32-bit, float is quantized to 16-bit."""
    import av
    with av.open(str(source)) as container:
        stream = container.streams.audio[0]
        fmt = (stream.codec_context.format.name or '').rstrip('p')
        out_format, width = ('s32', 4) if fmt == 's32' else ('s16', 2)
        layout = stream.layout.name
        resampler = av.AudioResampler(format=out_format, layout=layout, rate=stream.rate)
        channels = len(stream.layout.channels)
        with wave.open(str(target), 'wb') as output:
            output.setnchannels(channels)
            output.setsampwidth(width)
            output.setframerate(stream.rate)
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    output.writeframes(converted.to_ndarray().tobytes())
            for converted in resampler.resample(None) or []:
                output.writeframes(converted.to_ndarray().tobytes())


def inspect_wav(path):
    """Facts about the finished file itself, never about what was requested."""
    with wave.open(str(path), 'rb') as stream:
        frames, rate = stream.getnframes(), stream.getframerate()
        info = dict(sample_rate=rate, channels=stream.getnchannels(), bits=stream.getsampwidth() * 8, frames=frames)
    if not frames or not rate:
        raise RuntimeError(ui_text('music.inspect_wav_stable_audio_created_empty'))
    info['duration'] = round(frames / rate, 2)
    return info


class ComfyBackend:
    def __init__(self, paths, client=None, convert=flac_to_wav, clock=time.monotonic, sleep=time.sleep):
        self.paths = paths
        self.client = client
        self.convert = convert
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._prompt_id = None

    def attach(self, client):
        """The bridge connects the Image ComfyUI after it knows the port."""
        self.client = client

    # ---- files and templates -----------------------------------------------------------------------------------------
    def workflow_path(self):
        return self.paths.engine_workflows / WORKFLOW

    def template(self):
        return json.loads(self.workflow_path().read_text(encoding='utf-8'))

    def required_files(self):
        return [self.paths.models / folder / name for folder, name in (CHECKPOINT, TEXT_ENCODER)]

    def availability(self):
        """Cheap local facts only: the files exist. Whether ComfyUI accepts the graph is checked when a job is submitted."""
        missing = [str(path) for path in self.required_files() if not path.is_file()]
        if not self.workflow_path().is_file():
            missing.append(str(self.workflow_path()))
        runtime = self.paths.root / 'Runtime/ComfyUI_windows_portable/ComfyUI/main.py'
        if not runtime.is_file():
            missing.append(str(runtime))
        return dict(available=not missing, missing=missing,
                    reason=ui_text('music.availability_stable_audio_3_medium_not_installed') if missing else '')

    # ---- graph -------------------------------------------------------------------------------------------------------
    def build(self, request):
        graph = self.template()
        graph['40']['inputs']['text'] = request['caption']
        graph['50']['inputs']['seconds'] = float(request['duration'])
        graph['60']['inputs']['seed'] = request['seed']
        sampler = graph['60']['inputs']
        metadata = dict(
            workflow_version=self._version(), workflow_sha256=hashlib.sha256(self.workflow_path().read_bytes()).hexdigest(),
            graph_sha256=hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
            sampler_parameters={key: sampler[key] for key in ('steps', 'cfg', 'sampler_name', 'scheduler', 'denoise')},
            requested_duration=request['duration'])
        return graph, metadata

    @staticmethod
    def _version():
        from .registry import STABLE_AUDIO
        return STABLE_AUDIO['workflow_version']

    def preflight(self, request):
        """Refuse before any GPU work when ComfyUI cannot run this graph; the reasons are the same ones Image reports."""
        from engines.preflight import combo_options, graph_errors, input_specs
        if self.client is None:
            raise ValueError(ui_text('music.preflight_image_comfy_ui_not_connected_restart'))
        try:
            info = self.client.get('/object_info', timeout=20)
        except (urllib.error.URLError, TimeoutError, OSError, ComfyError):
            raise ValueError(ui_text('music.preflight_image_comfy_ui_not_responding_wait')) from None
        graph, _ = self.build(request)
        missing = sorted({node['class_type'] for node in graph.values() if node['class_type'] not in info})
        if missing:
            raise ValueError(ui_text('music.preflight_missing_comfy_ui_nodes') + ', '.join(missing))
        absent = []
        for node in graph.values():
            for key, filename in (('ckpt_name', CHECKPOINT[1]), ('clip_name', TEXT_ENCODER[1])):
                if node['inputs'].get(key) == filename:
                    options = combo_options(input_specs(info[node['class_type']]).get(key))
                    if options is None or filename not in options:
                        absent.append(filename)
        if absent:
            raise ValueError(ui_text('music.preflight_comfy_ui_cannot_find_models') + ', '.join(absent) + ui_text('music.preflight_check_components'))
        # Only the two model names were answered above (with the Components hint); any other unavailable value, such as a
        # sampler, scheduler or CLIP type that a ComfyUI update dropped, must stop the job here and not fail it on the GPU.
        handled = tuple(': unavailable %s=' % key for key in ('ckpt_name', 'clip_name'))
        errors = [error for error in graph_errors(graph, info) if not any(mark in error for mark in handled)]
        if errors:
            raise ValueError(ui_text('music.preflight_stable_audio_graph_incompatible_comfy_ui') + '; '.join(errors[:6]))

    # ---- one job -----------------------------------------------------------------------------------------------------
    def owned_prompt_ids(self):
        """ComfyUI ids that belong to Music, so Image's busy check and Stop button leave them alone."""
        with self._lock:
            return {self._prompt_id} if self._prompt_id else set()

    def run(self, request, directory, progress=lambda phase: None, log=lambda line: None, cancelled=lambda: False):
        """Run one job on this thread. `cancelled()` is polled, so a Stop request never makes a network call under the manager lock."""
        directory = Path(directory)
        scratch = directory / 'audio'
        scratch.mkdir(parents=True, exist_ok=True)
        graph, metadata = self.build(request)
        started = self.clock()
        prompt_id = None
        try:
            try:
                answer = self.client.post('/prompt', {'prompt': graph, 'client_id': self.client.client_id}, timeout=30)
            except ComfyError as error:
                raise RuntimeError(ui_text('music.run_comfy_ui_rejected_graph') + str(error)[:700]) from None
            prompt_id = answer.get('prompt_id')
            if not isinstance(prompt_id, str) or not prompt_id:
                raise RuntimeError(ui_text('music.run_comfy_ui_rejected_graph') + str(answer)[:600])
            with self._lock:
                self._prompt_id = prompt_id
            from .storage import write_json
            write_json(directory / 'comfy-artifacts.json', {'prompt_id': prompt_id})
            log(ui_text('music.run_graph_submitted_comfy_ui_prompt_id') + prompt_id)
            progress(ui_text('music.run_waiting_comfy_ui_queue'))
            item = self._wait(prompt_id, started, progress, log, cancelled)
            write_json(directory / 'comfy-artifacts.json', {'prompt_id': prompt_id, 'outputs': [item]})
            flac = scratch / 'result.flac'
            query = urllib.parse.urlencode({'filename': item['filename'], 'subfolder': item.get('subfolder', ''), 'type': item.get('type', 'temp')})
            flac.write_bytes(self.client.fetch('/view?' + query))
            log(ui_text('music.run_flac_received_bytes') % flac.stat().st_size)
            progress(ui_text('music.run_converting_wav'))
            wav = scratch / 'result.wav'
            self.convert(flac, wav)
            facts = inspect_wav(wav)
            log(ui_text('music.run_wav_hz_channels_bit_s') % (facts['sample_rate'], facts['channels'], facts['bits'], facts['duration']))
            metadata.update(facts, generation_sec=round(self.clock() - started, 1), backend='comfy', source_format='flac', comfy_prompt_id=prompt_id)
            return dict(path=str(wav), metadata=metadata)
        except Cancelled:
            raise
        except BaseException:
            # A failed job (timeout, unreadable history, bad or oversized result) must not leave its prompt running: Music
            # stops owning it below, and ComfyUI would keep the GPU busy for a job nobody waits for. Cancelling a prompt that
            # already finished is harmless, so this runs while the id is still Music's, before the finally releases it.
            if prompt_id:
                self._remote_cancel(prompt_id, log)
            raise
        finally:
            with self._lock:
                self._prompt_id = None
            self._free(log)

    def _wait(self, prompt_id, started, progress, log, cancelled):
        phase, cancel_sent = '', False
        while True:
            if cancelled() and not cancel_sent:
                cancel_sent = True
                progress(ui_text('music.wait_cancelling_music'))
                self._remote_cancel(prompt_id, log)
            if self.clock() - started > JOB_TIMEOUT_SEC:
                raise RuntimeError(ui_text('music.wait_stable_audio_did_not_finish_within'))
            entry = (self.client.get('/history/' + prompt_id, timeout=30) or {}).get(prompt_id)
            if entry:
                status = entry.get('status', {})
                if status.get('status_str') == 'error' or status.get('completed') is False:
                    if cancelled():
                        raise Cancelled()
                    errors = [m[1] for m in status.get('messages', []) if m and m[0] == 'execution_error' and len(m) > 1 and isinstance(m[1], dict)]
                    detail = errors[-1].get('exception_message') if errors else None
                    raise RuntimeError('ComfyUI: ' + str(detail or ui_text('music.wait_task_failed'))[:800])
                audio = (entry.get('outputs', {}).get(RESULT_NODE) or {}).get('audio') or []
                if audio:
                    item = audio[0]
                    if not isinstance(item, dict) or not isinstance(item.get('filename'), str) or '/' in item['filename'] or '\\' in item['filename'] or '..' in str(item.get('subfolder', '')):
                        raise RuntimeError(ui_text('music.wait_comfy_ui_returned_unrecognized_audio_path'))
                    return item
                if status.get('completed') is True:
                    if cancelled():
                        raise Cancelled()
                    raise RuntimeError(ui_text('music.wait_comfy_ui_finished_without_audio'))
            elif cancelled() and not self._in_queue(prompt_id):
                raise Cancelled()
            now = ui_text('music.wait_generating_audio') if self._running(prompt_id) else ui_text('music.run_waiting_comfy_ui_queue')
            if now != phase:
                phase = now
                progress(now)
            self.sleep(1.0)

    def _queue(self):
        try:
            return self.client.get('/queue', timeout=10) or {}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, ComfyError):
            return {}

    def _running(self, prompt_id):
        return any(len(entry) > 1 and entry[1] == prompt_id for entry in self._queue().get('queue_running', []))

    def _in_queue(self, prompt_id):
        queue = self._queue()
        return any(len(entry) > 1 and entry[1] == prompt_id for key in ('queue_running', 'queue_pending') for entry in queue.get(key, []))

    def _remote_cancel(self, prompt_id, log: Callable[[str], None]):
        """Cancel only this prompt: ComfyUI interrupts it if it runs and dequeues it if it waits; other jobs are untouched."""
        try:
            answer = self.client.post('/api/jobs/' + prompt_id + '/cancel', {}, timeout=15)
            log(ui_text('music.remote_cancel_comfy_ui_cancellation') + json.dumps(answer, ensure_ascii=False)[:200])
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, ComfyError) as error:
            log(ui_text('music.remote_cancel_comfy_ui_task_failed') + str(error)[:200])

    def _free(self, log):
        """Ask ComfyUI to drop its models; the job is already finished or failed, so a refusal only gets logged."""
        try:
            self.client.post('/free', {'unload_models': True, 'free_memory': True}, timeout=10)
            log(ui_text('music.free_comfy_ui_models_unloaded'))
        except Exception as error:
            log(ui_text('music.free_comfy_ui_did_not_unload_models') + str(error)[:200])
