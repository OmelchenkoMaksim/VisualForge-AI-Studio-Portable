"""One owned offline native-Comfy Wan S2V chunk, with optional cloned speech first."""
import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import wave
from wan_worker import export_video

def event(kind,**values):
    print(json.dumps(dict(event=kind,**values),ensure_ascii=False),flush=True)

def confined(folder,value):
    path=Path(value).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError('Входной файл S2V вне папки задачи')
    return path

def prepare_speech(root,voice_model,folder,request):
    """Finish TTS in a separate process before importing/loading the video model."""
    ffmpeg=str(root/'ffmpeg/bin/ffmpeg.exe')
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
    length=request['frames']/request['fps']
    if not .1<=duration<=length:
        raise ValueError('Речь занимает %.2f с; короткий профиль S2V вмещает %.4f с. Сократите реплику или запись. Автоматическая обрезка отключена.' % (duration,length))
    target=folder/'conditioned-speech.wav'
    subprocess.run([ffmpeg,'-nostdin','-v','error','-n','-i',str(source),'-vn','-ac','1','-ar','16000',
        '-af','apad','-t',str(length),'-c:a','pcm_s16le',str(target)],check=True,timeout=30)
    return target,duration

def run(args):
    root,models,encoder_root,folder,voice_model=map(lambda p:Path(p).resolve(),
        (args.root,args.models,args.encoder_root,args.folder,args.voice_model))
    request=json.loads(Path(args.spec).read_text(encoding='utf-8'))
    started=time.monotonic()
    speech,speech_duration=prepare_speech(root,voice_model,folder,request)
    sys.path.insert(0,str(root/'vendor/ComfyUI'))
    sys.argv=['visualforge-s2v','--disable-dynamic-vram','--disable-pinned-memory',
        '--disable-smart-memory','--use-pytorch-cross-attention','--reserve-vram','2.0']
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
    profile=dict(steps=20,cfg=6.0,shift=8.0,sampler='uni_pc',scheduler='simple',
        width=request['width'],height=request['height'],frames=request['frames'],fps=request['fps'],
        seed=request['seed'],native_comfy_version='0.34.0',offload='native estimate-based, reserve 2 GB',
        speech_mode=request['speech_mode'],speech_duration=speech_duration,lightning_lora=False)
    (folder/'s2v-inference-config.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    event('sampling_plan',**profile)
    with torch.inference_mode():
        event('encoder_start')
        clip=nodes.CLIPLoader().load_clip('umt5_xxl_fp8_e4m3fn_scaled.safetensors','wan',device='default')[0]
        positive=nodes.CLIPTextEncode().encode(clip,request['prompt'])[0]
        negative=nodes.CLIPTextEncode().encode(clip,'blurry, low quality, static image, watermark, subtitles, deformed face, extra limbs, flicker')[0]
        del clip
        memory.unload_all_models();memory.soft_empty_cache()
        encoder=AudioEncoderLoader.execute('wav2vec2_large_english_fp16.safetensors')[0]
        # FFmpeg above already produced mono PCM16 WAV; no extra audio package is needed.
        with wave.open(str(speech),'rb') as audio:
            rate=audio.getframerate()
            if audio.getnchannels()!=1 or audio.getsampwidth()!=2:
                raise ValueError('Ожидалась подготовленная моно PCM16 речь')
            waveform=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(np.float32)/32768.0
        encoded=AudioEncoderEncode.execute(encoder,dict(waveform=torch.from_numpy(waveform).reshape(1,1,-1),sample_rate=rate))[0]
        del encoder,waveform
        memory.unload_all_models();memory.soft_empty_cache()
        vae=nodes.VAELoader().load_vae('wan_2.1_vae.safetensors')[0]
        media=json.loads((folder/'media.json').read_text(encoding='utf-8'))
        with Image.open(confined(folder,media['first'])) as image:
            reference=torch.from_numpy(np.asarray(image.convert('RGB')).copy()).float().unsqueeze(0)/255
        positive,negative,latent=WanSoundImageToVideo.execute(positive,negative,vae,request['width'],request['height'],
            request['frames'],1,ref_image=reference,audio_encoder_output=encoded)
        del reference,encoded
        memory.unload_all_models();memory.soft_empty_cache()
        event('model_load_phase',phase='diffusion')
        original=nodes.UNETLoader().load_unet('wan2.2_s2v_14B_fp8_scaled.safetensors','default')[0]
        model=ModelSamplingSD3().patch(original,8.0)[0]
        def progress(value,total,preview=None,**kwargs):event('step',completed_steps=int(value),total=int(total))
        comfy.utils.set_progress_bar_global_hook(progress)
        event('sampling_start')
        sampled=nodes.KSampler().sample(model,int(request['seed']),20,6.0,'uni_pc','simple',positive,negative,latent,denoise=1.0)[0]
        comfy.utils.set_progress_bar_global_hook(None)
        del model,original,latent,positive,negative
        memory.unload_all_models();memory.soft_empty_cache()
        event('decode_phase')
        images=nodes.VAEDecodeTiled().decode(vae,sampled,tile_size=512,overlap=64,temporal_size=32,temporal_overlap=8)[0]
        if images.ndim!=4 or len(images)!=request['frames'] or not torch.isfinite(images).all() or float(images.max()-images.min())<.001:
            raise RuntimeError('S2V вернул повреждённые или однотонные кадры')
        images=images.cpu();memory.unload_all_models();memory.soft_empty_cache()
        silent=folder/'silent-video.mp4'
        export_video(images,silent,root/'ffmpeg/bin/ffmpeg.exe',request['width'],request['height'],request['fps'])
        subprocess.run([str(root/'ffmpeg/bin/ffmpeg.exe'),'-nostdin','-v','error','-n','-i',str(silent),'-i',str(speech),
            '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','160k','-t',str(request['frames']/request['fps']),
            '-movflags','+faststart',str(folder/'result.mp4')],check=True,timeout=60)
        event('s2v_metrics',inference_sec=round(time.monotonic()-started,2),
            peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated()/1024**2),torch=torch.__version__,python=sys.version.split()[0])

def main():
    parser=argparse.ArgumentParser()
    for name in ('root','models','encoder-root','voice-model','folder','spec'):parser.add_argument('--'+name,required=True)
    try:run(parser.parse_args())
    except Exception:
        traceback.print_exc(limit=12);raise SystemExit(1)

if __name__=='__main__':main()
