"""GPU smoke checks of the Video section through the studio's own HTTP API (the bridge, http://127.0.0.1:8189 by default), the way the panel calls it.

Run it after a change to a GPU worker (ltx_worker, s2v_worker, character_worker, wan_worker). Project rule: give the owner a time estimate and get an
explicit yes before any GPU inference; `run` therefore refuses to start without --yes-gpu. Start the studio first, then:

    python tools/video_smoke.py availability
    python tools/video_smoke.py prepare                  # the Video save folder is switched to <out>/results; the old one is remembered
    python tools/video_smoke.py run ltx --yes-gpu        # ltx | ltx2 | s2v | animate, one job each
    python tools/video_smoke.py cleanup                  # deletes the test jobs through the studio's own delete route
    python tools/video_smoke.py restore                  # puts the save folder back

The jobs and what they cover (RTX 5070 Ti 16 GB, 2026-11-01, wall time with a cold hard drive for the first one):
    ltx      LTX 2.5 fast, 768x512, 49 frames                    about 6 min, peak 12.5 GB   sampler, audio mux
    ltx2     LTX 2.5 two-pass, the same size                     about 4.5 min, 12.4 GB      the refinement call of the sampler
    s2v      Wan 2.2 S2V, one segment with a cloned voice        about 8.5 min, 11.5 GB      voice child, audio encoder
    animate  Wan Animate 2, 81 frames with a motion prompt       about 6 min, 12.1 GB       text encoder and CLIP vision helpers
Inputs come from the environment, so no personal file lives in the repository: VISUALFORGE_SMOKE_PHOTO (a portrait), VISUALFORGE_SMOKE_DRIVING (a short
video of a person, at least 17 frames at 24 fps), VISUALFORGE_SMOKE_VOICE (a voice sample of 2-30 s), VISUALFORGE_SMOKE_OUT (default: a folder in the system
temp), VISUALFORGE_SMOKE_URL (default http://127.0.0.1:8189). Only the job you run needs its inputs.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('VISUALFORGE_SMOKE_OUT') or Path(tempfile.gettempdir()) / 'visualforge-smoke')
BASE = (os.environ.get('VISUALFORGE_SMOKE_URL') or 'http://127.0.0.1:8189').rstrip('/') + '/api/video/'
FFPROBE = ROOT / 'Runtime/Video/ffmpeg/bin/ffprobe.exe'
FOX = 'A red fox walks through a snowy pine forest at dawn. The camera slowly follows it. Snow crunches under its paws and a distant bird calls.'
LIMITS = {'ltx': 900, 'ltx2': 1200, 's2v': 1500, 'animate': 1200}      # seconds before the job is cancelled


def data_url(variable, mime):
    path = os.environ.get(variable)
    if not path or not Path(path).is_file():
        raise SystemExit('Set %s to an existing file for this job.' % variable)
    return 'data:%s;base64,%s' % (mime, base64.b64encode(Path(path).read_bytes()).decode('ascii'))


JOBS = {
    'ltx': lambda: dict(engine='ltx25-distilled', mode='t2v', quality='preview', width=768, height=512, frames=49, fps=24, seed='11', prompt=FOX),
    'ltx2': lambda: dict(engine='ltx25-distilled', mode='t2v', quality='standard', width=768, height=512, frames=49, fps=24, seed='12', prompt=FOX),
    's2v': lambda: dict(engine='wan22-s2v-14b', mode='s2v', quality='standard', width=512, height=512, frames=77, fps=16, seed='11',
                        prompt='A person looks into the camera and speaks calmly in a bright room, soft daylight, static camera.',
                        source_image=data_url('VISUALFORGE_SMOKE_PHOTO', 'image/jpeg'), speech_mode='clone', voice_audio=data_url('VISUALFORGE_SMOKE_VOICE', 'audio/wav'),
                        speech_text='Hello, this is a short test of the voice.', language='English'),
    'animate': lambda: dict(engine='wan-animate2', mode='animate', quality='standard', width=512, height=896, frames=81, fps=24, seed='11',
                            prompt='A person in a bright room, soft daylight.', source_image=data_url('VISUALFORGE_SMOKE_PHOTO', 'image/jpeg'),
                            driving_video=data_url('VISUALFORGE_SMOKE_DRIVING', 'video/mp4'), motion_prompt='the person nods and smiles',
                            fit_reference=False, keep_audio=False),
}


def call(route, data=None, timeout=300):
    request = urllib.request.Request(BASE + route, data=None if data is None else json.dumps(data).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit('HTTP %d on %s: %s' % (error.code, route, error.read()[:400].decode('utf-8', 'replace')))
    except urllib.error.URLError as error:
        raise SystemExit('The studio does not answer at %s (%s). Start it first.' % (BASE, error.reason))


def probe(path):
    """What ffprobe counts in the finished file: the proof that frames and sound are really there."""
    result = subprocess.run([str(FFPROBE), '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=120)
    info = json.loads(result.stdout)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    return dict(width=video['width'], height=video['height'], frames=int(video['nb_read_frames']), fps=video['r_frame_rate'], duration=round(float(info['format']['duration']), 3),
                audio=[(s['codec_name'], s.get('channels'), s.get('sample_rate')) for s in info['streams'] if s['codec_type'] == 'audio'])


def run(name):
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.time()
    row = call('generate', JOBS[name](), timeout=600)
    ident = row['id']
    ids = json.loads((OUT / 'job-ids.json').read_text()) if (OUT / 'job-ids.json').exists() else {}
    (OUT / 'job-ids.json').write_text(json.dumps(dict(ids, **{name: ident})))
    print('submitted %s: %s' % (name, ident), flush=True)
    last = None
    while True:
        row = call('status?id=' + ident, timeout=60)
        line = '%s | %s' % (row['state'], row.get('phase'))
        if line != last:
            print('[%5ds] %s' % (time.time() - started, line), flush=True)
            last = line
        if row['state'] in ('done', 'error', 'cancelled'):
            break
        if time.time() - started > LIMITS[name]:
            print('time limit: cancelling the job', flush=True)
            call('cancel', dict(id=ident, cancel_token=row['cancel_token']))
            time.sleep(15)
            break
        time.sleep(5)
    summary = dict(name=name, state=row['state'], wall_sec=round(time.time() - started), error=(row.get('error') or '')[-1500:],
                   metrics=row.get('runtime_metrics'), saved_path=row.get('saved_path'))
    if row['state'] == 'done' and row.get('saved_path'):
        summary['probe'] = probe(row['saved_path'])
    (OUT / ('result-%s.json' % name)).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RESULT ' + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if row['state'] == 'done' else 1


def main(argv):
    command = argv[1] if len(argv) > 1 else ''
    original = OUT / 'original-settings.json'
    if command == 'availability':
        for engine in call('availability')['engines']:
            if engine['id'] in ('ltx25-distilled', 'wan22-s2v-14b', 'wan-animate2'):
                print(engine['id'], 'available=%s' % engine['available'], engine.get('missing') or '')
        return 0
    if command == 'prepare':
        (OUT / 'results').mkdir(parents=True, exist_ok=True)
        if not original.exists():                 # a second `prepare` must not remember the temporary folder as the original
            original.write_text(json.dumps(call('settings')), encoding='utf-8')
        print('save folder now:', call('settings', dict(save_dir=str(OUT / 'results'))))
        return 0
    if command == 'run' and len(argv) > 2 and argv[2] in JOBS:
        if '--yes-gpu' not in argv:
            raise SystemExit('GPU inference needs the owner\'s explicit yes and a time estimate first; then add --yes-gpu.')
        return run(argv[2])
    if command == 'cleanup':
        for name, ident in json.loads((OUT / 'job-ids.json').read_text()).items():
            try:
                print(name, ident, call('delete', dict(id=ident)))
            except SystemExit as error:
                print(name, ident, 'not deleted:', error)
        return 0
    if command == 'restore':
        print('save folder restored:', call('settings', dict(save_dir=json.loads(original.read_text(encoding='utf-8'))['save_dir'])))
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
