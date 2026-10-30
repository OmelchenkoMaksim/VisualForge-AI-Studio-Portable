"""One owned offline native-Comfy Wan S2V job: one 77-frame segment, or an edit of independent scenes for longer speech.

The speech (cloned from a voice sample or a ready recording) is prepared first in a separate process. The video model renders
77 frames (4.8125 s) per segment, so a longer clip is an edit of scenes: each starts again from the reference picture with its
own slice of the speech, cut at a pause where possible, and the slices are laid end to end, so picture cuts follow one continuous
voice. (A chain of segments continuing each other, `WanSoundImageToVideoExtend`, was built and measured on 2026-10-30 and dropped:
see docs/Wan-S2V.md.)
"""
import argparse
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import wave
from wan_worker import Exporter

DEFAULT_NEGATIVE='blurry, low quality, static image, watermark, subtitles, deformed face, extra limbs, flicker'
SEGMENT_FRAMES=77          # one native chunk: 20 latent frames decode to 77 picture frames
MAX_LONG_SPEECH=75.0       # seconds; longer speech would need more than 16 segments
RATE=16000

def event(kind,**values):
    print(json.dumps(dict(event=kind,**values),ensure_ascii=False),flush=True)

def confined(folder,value):
    path=Path(value).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError('Входной файл S2V вне папки задачи')
    return path

def prepare_speech(root,voice_model,folder,request):
    """Finish TTS in a separate process before importing/loading the video model. -> (16 kHz mono wav, duration in seconds)."""
    ffmpeg=str(root/'ffmpeg/bin/ffmpeg.exe')
    long_clip=request.get('structure','single')!='single'
    source=confined(folder,request['speech_input_path'])
    if request['speech_mode']=='clone':
        event('voice_start',name='Wan S2V')
        source=folder/'cloned-speech.wav'
        spec=folder/'voice-request.json'
        spec.write_text(json.dumps(dict(model=str(voice_model),voice_path=request['speech_input_path'],
            speech_text=request['speech_text'],language=request['language'],output=str(source)),ensure_ascii=False),encoding='utf-8')
        try:
            with (folder/'voice.log').open('wb') as log:
                subprocess.run([str(root/'python/python.exe'),'-u',str(Path(__file__).with_name('voice_worker.py')),
                    '--packages',str(root/'voice-packages'),'--request',str(spec),'--ffmpeg',ffmpeg],
                    stdout=log,stderr=subprocess.STDOUT,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.CalledProcessError:
            with (folder/'voice.log').open('rb') as log:
                log.seek(max(0,(folder/'voice.log').stat().st_size-4096))
                print(log.read().decode('utf-8',errors='replace'),file=sys.stderr,flush=True)
            raise
    probe=subprocess.run([str(root/'ffmpeg/bin/ffprobe.exe'),'-v','error','-show_format','-of','json',str(source)],
        capture_output=True,text=True,timeout=20,check=True)
    duration=float(json.loads(probe.stdout)['format']['duration'])
    target=folder/'conditioned-speech.wav'
    if long_clip:
        if not .1<=duration<=MAX_LONG_SPEECH:
            raise ValueError('Речь занимает %.1f с; длинные профили S2V принимают от 0,1 до %d с. Сократите реплику или запись. Автоматическая обрезка отключена.' % (duration,MAX_LONG_SPEECH))
        # No padding or cutting here: the plan below decides how the speech is laid out on the picture timeline.
        subprocess.run([ffmpeg,'-nostdin','-v','error','-n','-i',str(source),'-vn','-ac','1','-ar',str(RATE),'-c:a','pcm_s16le',str(target)],check=True,timeout=120)
        return target,duration
    length=SEGMENT_FRAMES/request['fps']
    if not .1<=duration<=length:
        raise ValueError('Речь занимает %.2f с; короткий профиль S2V вмещает %.4f с. Сократите реплику или запись. Автоматическая обрезка отключена.' % (duration,length))
    subprocess.run([ffmpeg,'-nostdin','-v','error','-n','-i',str(source),'-vn','-ac','1','-ar',str(RATE),
        '-af','apad','-t',str(length),'-c:a','pcm_s16le',str(target)],check=True,timeout=30)
    return target,duration

def read_wave(path):
    import numpy as np
    with wave.open(str(path),'rb') as audio:
        if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=RATE:
            raise ValueError('Ожидалась подготовленная моно PCM16 речь')
        return np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(np.float32)/32768.0

def write_wave(path,samples):
    import numpy as np
    with wave.open(str(path),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(RATE)
        audio.writeframes((np.clip(samples,-1,1)*32767).astype('<i2').tobytes())

def pause_near(samples,low,high):
    """Sample index of the quietest 0.2 s inside [low, high] (the window centre), or None when nothing there is quiet enough."""
    import numpy as np
    window=RATE//5
    if high-low<window:return None
    energy=np.convolve(samples[low:high]**2,np.ones(window,dtype=np.float32)/window,mode='valid')
    loudness=float(np.mean(samples**2))+1e-12
    index=int(len(energy)-1-np.argmin(energy[::-1]))   # the later one of equal minima
    return low+index+window//2 if energy[index]<0.05*loudness else None

def plan_segments(samples,structure,fps):
    """-> dict(segments, frames, slices): picture frames in total, and for scenes the (start, end) sample range of each scene's speech."""
    import numpy as np
    if structure=='single':
        return dict(segments=1,frames=SEGMENT_FRAMES,slices=[(0,len(samples))])
    limit=int(SEGMENT_FRAMES/fps*RATE)
    slices,start=[],0
    while start<len(samples):
        end=min(len(samples),start+limit)
        if end<len(samples):
            end=pause_near(samples,start+int(2.5*RATE),end) or end    # a scene shorter than 2.5 s would waste a whole segment
        slices.append((start,end));start=end
    # Every shot keeps only as many pictures as its slice of the speech lasts (the model renders 77 anyway), so the cuts add no
    # silence; the last shot gets half a second more.
    counts=[min(SEGMENT_FRAMES,math.ceil((b-a)/RATE*fps)) for a,b in slices]
    counts[-1]=min(SEGMENT_FRAMES,counts[-1]+8)
    return dict(segments=len(slices),frames=sum(counts),slices=slices,counts=counts)

def run(args):
    root,models,encoder_root,folder,voice_model=map(lambda p:Path(p).resolve(),
        (args.root,args.models,args.encoder_root,args.folder,args.voice_model))
    request=json.loads(Path(args.spec).read_text(encoding='utf-8'))
    structure=request.get('structure','single')
    started=time.monotonic()
    speech,speech_duration=prepare_speech(root,voice_model,folder,request)
    sys.path.insert(0,str(root/'vendor/ComfyUI'))
    # SageAttention ships with the Video runtime; Wan TI2V-5B sampling got about 19% faster with it at the same seed and layout,
    # and S2V 512x512 went from 349 s to 242 s. PyTorch attention stays the fallback and can be forced for comparison.
    import importlib.util
    sage=os.environ.get('VISUALFORGE_WAN_ATTENTION')!='pytorch' and importlib.util.find_spec('sageattention') is not None
    sys.argv=['visualforge-s2v','--disable-dynamic-vram','--disable-pinned-memory',
        '--disable-smart-memory','--use-sage-attention' if sage else '--use-pytorch-cross-attention','--reserve-vram','2.0']
    logging.basicConfig(level=logging.WARNING)
    import comfy.options
    comfy.options.enable_args_parsing()
    import torch
    import numpy as np
    from PIL import Image
    import folder_paths,nodes,comfy.utils
    import comfy.model_management as memory
    from comfy_extras.nodes_wan import WanSoundImageToVideo
    from comfy_extras.nodes_audio_encoder import AudioEncoderLoader,AudioEncoderEncode
    from comfy_extras.nodes_model_advanced import ModelSamplingSD3
    if not torch.cuda.is_available():raise RuntimeError('S2V требует CUDA GPU')
    torch.set_num_threads(min(8,os.cpu_count() or 4))
    for name in ('diffusion_models','audio_encoders','vae'):
        folder_paths.add_model_folder_path(name,str(models/name),is_default=True)
    folder_paths.add_model_folder_path('text_encoders',str(encoder_root/'text_encoders'),is_default=True)
    comfy.utils.PROGRESS_BAR_ENABLED=False
    width,height,fps=request['width'],request['height'],request['fps']
    samples=read_wave(speech)
    plan=plan_segments(samples,structure,fps)
    count,frames=plan['segments'],plan['frames']
    prompts=request.get('segment_prompts') or [request['prompt']]
    # The client may have estimated the number of segments from the text before the speech existed: map its prompts proportionally.
    prompt_for=lambda index:prompts[min(len(prompts)-1,index*len(prompts)//count)]
    profile=dict(steps=20,cfg=6.0,shift=8.0,sampler='uni_pc',scheduler='simple',
        width=width,height=height,frames=frames,fps=fps,structure=structure,segments=count,
        seed=request['seed'],native_comfy_version='0.34.0',offload='native estimate-based, reserve 2 GB',attention='sage' if sage else 'pytorch',
        speech_mode=request['speech_mode'],speech_duration=speech_duration,lightning_lora=False)
    (folder/'s2v-inference-config.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    event('sampling_plan',**profile)
    with torch.inference_mode():
        event('encoder_start')
        clip=nodes.CLIPLoader().load_clip('umt5_xxl_fp8_e4m3fn_scaled.safetensors','wan',device='default')[0]
        positives={text:nodes.CLIPTextEncode().encode(clip,text)[0] for text in {prompt_for(i) for i in range(count)}}
        negative=nodes.CLIPTextEncode().encode(clip,request.get('negative_prompt') or DEFAULT_NEGATIVE)[0]
        del clip
        memory.unload_all_models();memory.soft_empty_cache()
        encoder=AudioEncoderLoader.execute('wav2vec2_large_english_fp16.safetensors')[0]
        encode=lambda wave_:AudioEncoderEncode.execute(encoder,dict(waveform=torch.from_numpy(wave_).reshape(1,1,-1),sample_rate=RATE))[0]
        if structure=='scenes':
            segment=int(SEGMENT_FRAMES/fps*RATE)
            padded=lambda a,b:np.concatenate([samples[a:b],np.zeros(max(0,segment-(b-a)),dtype=np.float32)])
            encoded=[encode(padded(a,b)) for a,b in plan['slices']]
        else:
            encoded=[encode(samples)]
        del encoder
        memory.unload_all_models();memory.soft_empty_cache()
        vae=nodes.VAELoader().load_vae('wan_2.1_vae.safetensors')[0]
        media=json.loads((folder/'media.json').read_text(encoding='utf-8'))
        with Image.open(confined(folder,media['first'])) as image:
            reference=torch.from_numpy(np.asarray(image.convert('RGB')).copy()).float().unsqueeze(0)/255
        event('model_load_phase',phase='diffusion')
        original=nodes.UNETLoader().load_unet('wan2.2_s2v_14B_fp8_scaled.safetensors','default')[0]
        model=ModelSamplingSD3().patch(original,8.0)[0]
        state=dict(segment=0)
        def progress(value,total,preview=None,**kwargs):
            event('step',completed_steps=int(value),total=int(total),segment=state['segment'],segments=count,elapsed=round(time.monotonic()-started,1))
        comfy.utils.set_progress_bar_global_hook(progress)
        event('sampling_start',elapsed=round(time.monotonic()-started,1))
        latents=[]
        for index in range(count):
            state['segment']=index
            begun=time.monotonic()
            positive=positives[prompt_for(index)]
            conditioned=WanSoundImageToVideo.execute(positive,negative,vae,width,height,SEGMENT_FRAMES,1,ref_image=reference,
                audio_encoder_output=encoded[index if structure=='scenes' else 0])
            pos,neg,latent=conditioned
            # Every scene shares the job seed: the same face and light from shot to shot.
            seed=int(request['seed'])%2**64
            sampled=nodes.KSampler().sample(model,seed,20,6.0,'uni_pc','simple',pos,neg,latent,denoise=1.0)[0]
            latents.append(sampled['samples'].cpu())
            event('segment_timing',index=index,segments=count,seconds=round(time.monotonic()-begun,1))
            del pos,neg,latent,sampled,conditioned
        comfy.utils.set_progress_bar_global_hook(None)
        del model,original,positives,negative,reference
        memory.unload_all_models();memory.soft_empty_cache()
        event('decode_phase',elapsed=round(time.monotonic()-started,1))
        silent=folder/'silent-video.mp4'
        exporter=Exporter(silent,root/'ffmpeg/bin/ffmpeg.exe',width,height,fps)
        written=0
        try:
            decode=lambda latent:nodes.VAEDecodeTiled().decode(vae,dict(samples=latent),tile_size=512,overlap=64,temporal_size=32,temporal_overlap=8)[0]
            keep=plan['counts'] if structure=='scenes' else [frames]
            for group,limit in zip(latents,keep):
                images=decode(group)
                if images.ndim!=4 or not torch.isfinite(images).all() or float(images.max()-images.min())<.001:
                    raise RuntimeError('S2V вернул повреждённые или однотонные кадры')
                images=images[:limit].cpu()
                exporter.write(images);written+=len(images)
                del images;memory.soft_empty_cache()
            exporter.close()
        except BaseException:
            exporter.abort();raise
        if written!=frames:raise RuntimeError('S2V вернул %d кадров вместо %d' % (written,frames))
        # The speech laid on the picture timeline: for scenes every slice is padded to a whole number of pictures, so cuts follow the voice.
        if structure=='scenes':
            parts=[np.concatenate([samples[a:b],np.zeros(max(0,int(n/fps*RATE)-(b-a)),dtype=np.float32)])[:int(n/fps*RATE)] for (a,b),n in zip(plan['slices'],plan['counts'])]
            timeline=np.concatenate(parts)
        else:
            timeline=samples
        total=int(frames/fps*RATE)
        timeline=np.concatenate([timeline,np.zeros(max(0,total-len(timeline)),dtype=np.float32)])[:total]
        write_wave(folder/'timeline-speech.wav',timeline)
        subprocess.run([str(root/'ffmpeg/bin/ffmpeg.exe'),'-nostdin','-v','error','-n','-i',str(silent),'-i',str(folder/'timeline-speech.wav'),
            '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','160k','-t',str(frames/fps),
            '-movflags','+faststart',str(folder/'result.mp4')],check=True,timeout=300)
        (folder/'s2v-result.json').write_text(json.dumps(dict(frames=frames,segments=count,structure=structure,speech_duration=speech_duration)),encoding='utf-8')
        event('s2v_metrics',inference_sec=round(time.monotonic()-started,2),segments=count,frames=frames,
            peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated()/1024**2),torch=torch.__version__,python=sys.version.split()[0])

def main():
    parser=argparse.ArgumentParser()
    for name in ('root','models','encoder-root','voice-model','folder','spec'):parser.add_argument('--'+name,required=True)
    try:run(parser.parse_args())
    except Exception:
        traceback.print_exc(limit=12);raise SystemExit(1)

if __name__=='__main__':main()
