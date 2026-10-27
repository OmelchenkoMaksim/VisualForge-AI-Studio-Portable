"""One owned offline LTX job, sequential video/audio latent continuation windows."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from video.ltx_continuation import assembly_command, windows
from video.ltx_support import inference_plan


def event(name, **values):
    print(json.dumps(dict(event=name, **values), ensure_ascii=False), flush=True)


def child(command, timeout):
    result = subprocess.run(command, stdout=sys.stdout, stderr=sys.stderr, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('LTX continuation worker exited with code '+str(result.returncode))


def run(args):
    started = time.monotonic()
    folder = args.folder.resolve()
    request = json.loads(args.spec.read_text(encoding='utf-8'))
    plan = windows(request['frames'])
    worker = Path(__file__).with_name('ltx_worker.py')
    base = [sys.executable, '-X', 'faulthandler', '-u', str(worker), '--root', str(args.root), '--models', str(args.models)]
    child(base+['--folder',str(folder),'--spec',str(args.spec),'--encode-only'], 1200)
    config = inference_plan(request)|dict(continuation='overlapping video and audio latents', windows=plan,
                                        generated_frames=request['frames']+1, output_frames=request['frames'])
    (folder/'ltx-inference-config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    event('sampling_plan', **config)
    outputs = []
    previous = None
    for index, window in enumerate(plan):
        event('continuation_start',part=index+1,parts=len(plan))
        part = folder/('part-%02d' % (index+1))
        part.mkdir(exist_ok=False)
        spec = request|dict(frames=window['frames'],_continuation_index=index,seed=str((int(request['seed'])+index)%2**64))
        # Reapplying the uploaded I2V image would pull later windows back to the first frame.
        # Continue from generated AV latents instead; only the first window uses the image.
        if index:
            spec['mode']='t2v'
        elif request['mode']=='i2v':
            media=json.loads((folder/'media.json').read_text(encoding='utf-8'))
            source=Path(media['first']).resolve()
            if not source.is_relative_to(folder):
                raise ValueError('Source image outside owned LTX job')
            target=part/'first.png'
            shutil.copy2(source,target)
            (part/'media.json').write_text(json.dumps(dict(first=str(target))),encoding='utf-8')
        spec_path=part/'ltx-request.json'
        spec_path.write_text(json.dumps(spec,ensure_ascii=False),encoding='utf-8')
        command=base+['--folder',str(part),'--spec',str(spec_path),'--conditioning',str(folder/'ltx-conditioning.pt'),'--save-tail']
        if previous:
            command+=['--continuation',str(previous/'ltx-tail.pt')]
        child(command, 7200)
        if not (part/'result.mp4').is_file() or not (part/'ltx-tail.pt').is_file():
            raise RuntimeError('LTX continuation window did not finish')
        outputs.append(part/'result.mp4')
        previous=part
    event('assembly_start')
    child(assembly_command(args.root/'ffmpeg/bin/ffmpeg.exe',folder,outputs,request['frames'],request['fps']),1800)
    event('ltx_metrics',inference_sec=round(time.monotonic()-started,2))


def main():
    parser=argparse.ArgumentParser()
    for key in ('root','models','folder','spec'):
        parser.add_argument('--'+key,type=Path,required=True)
    try:
        run(parser.parse_args())
    except Exception:
        traceback.print_exc(limit=12)
        raise SystemExit(1)


if __name__=='__main__':
    main()
