"""Direct LTX 2.5 on native ComfyUI; one owned offline process per generation."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import wave

from video.comfy_memory import release_models
from video.ltx_support import ASSETS, inference_plan


STARTED=time.monotonic()


def event(name,**values):
    # `elapsed` (seconds since this process started) shows where a job spends its time: model reads from a slow disk
    # dominate cold runs, so phases must be told apart from sampling steps.
    print(json.dumps(dict(event=name,elapsed=round(time.monotonic()-STARTED,1),**values),ensure_ascii=False),flush=True)


def apply_context(video,audio,context,base=False):
    """Freeze the shared generated AV prefix; denoise only the new continuation."""
    import torch
    result=[]
    for latent,key in ((video,'base_video' if base else 'video'),(audio,'audio')):
        samples=latent['samples'].clone()
        previous=context[key]
        if previous.ndim!=samples.ndim or any(previous.shape[i]!=samples.shape[i] for i in range(samples.ndim) if i!=2) or previous.shape[2]>=samples.shape[2]:
            raise ValueError('LTX continuation latent shape mismatch')
        length=previous.shape[2]
        samples[:,:,:length]=previous.to(samples)
        mask=torch.ones_like(samples)
        mask[:,:,:length]=0
        result.append(dict(latent,samples=samples,noise_mask=mask))
    return tuple(result)


def read_recording(path):
    """The prepared 48 kHz stereo PCM16 recording of audio-to-video -> dict(waveform [1, 2, samples], sample_rate)."""
    import numpy as np
    import torch
    with wave.open(str(path),'rb') as stream:
        if stream.getnchannels()!=2 or stream.getsampwidth()!=2:
            raise ValueError('Ожидалась подготовленная стерео PCM16 запись')
        rate=stream.getframerate()
        samples=np.frombuffer(stream.readframes(stream.getnframes()),dtype='<i2').astype(np.float32).reshape(-1,2)/32768.0
    return dict(waveform=torch.from_numpy(samples.T.copy()).unsqueeze(0),sample_rate=rate)


def freeze_recording(empty,encoded):
    """Audio latent of the recording with a zero noise mask: the sampler may not change it, so the picture is made to fit the sound."""
    import torch
    samples=torch.zeros_like(empty['samples'])
    length=min(samples.shape[2],encoded.shape[2])
    if samples.shape[2]-length>2 or samples.shape[:2]!=encoded.shape[:2] or samples.shape[3]!=encoded.shape[3]:
        raise ValueError('LTX audio-to-video: форма звукового латента не совпала с клипом')
    samples[:,:,:length]=encoded[:,:,:length].to(samples)
    return dict(empty,samples=samples,noise_mask=torch.zeros_like(samples))


def export_media(images,audio,folder,root,request):
    """Stream RGB frames, then mux real model audio; no generated image sequence on disk."""
    import torch
    from video.wan_worker import export_video
    waveform=audio['waveform'].detach().float().cpu()
    rate=int(audio['sample_rate'])
    if waveform.ndim!=3 or waveform.shape[0]!=1 or waveform.shape[1] not in (1,2) or rate<=0 or not torch.isfinite(waveform).all():
        raise RuntimeError('LTX 2.5 вернул повреждённый звук')
    if abs(waveform.shape[-1]/rate-request['frames']/request['fps'])>.25:
        raise RuntimeError('Длительность сгенерированного звука LTX 2.5 не соответствует видео')
    pcm=(waveform[0].T.clamp(-1,1)*32767).round().to(torch.int16).numpy().astype('<i2')
    with wave.open(str(folder/'ltx-audio.wav'),'wb') as stream:
        stream.setnchannels(waveform.shape[1]);stream.setsampwidth(2);stream.setframerate(rate);stream.writeframes(pcm.tobytes())
    ffmpeg=root/'ffmpeg/bin/ffmpeg.exe'
    export_video(images,folder/'ltx-picture.mp4',ffmpeg,request['width'],request['height'],request['fps'])
    command=[str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-n','-i',str(folder/'ltx-picture.mp4'),
        '-i',str(folder/'ltx-audio.wav'),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k',
        '-ar','48000','-ac','2','-af','apad','-t',str(request['frames']/request['fps']),'-movflags','+faststart',str(folder/'result.mp4')]
    result=subprocess.run(command,capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError('Не удалось сохранить звук LTX 2.5: '+result.stderr.decode('utf-8',errors='replace')[-1500:])


NEGATIVE_PROMPT='blurry, low quality, watermark, subtitles, distorted anatomy'
CACHE_NAME='.conditioning-cache'
CACHE_LIMIT=40


def conditioning_cache(folder,prompt):
    """-> path of the cached text conditioning for this prompt.

    Encoding a prompt means reading the 15 GB LTX-tuned Gemma encoder from disk (about 90 s from a hard drive, three quarters of a
    short clip's time), and the result depends only on the prompt text and the encoder file. A new seed, size or duration of the
    same prompt (the usual next try) therefore skips it. The cache sits beside the job folders, which the confinement check allows."""
    parts=(prompt,NEGATIVE_PROMPT,*(a['sha256'] for a in ASSETS if a['path'].startswith('text_encoders/')))
    key=hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()
    return folder.parent/CACHE_NAME/(key+'.pt')


def prune_cache(directory):
    files=sorted(directory.glob('*.pt'),key=lambda path:path.stat().st_mtime,reverse=True)
    for path in files[CACHE_LIMIT:]:
        path.unlink(missing_ok=True)


def run(root,models,folder,request,encode_only=False,conditioning_path=None,continuation_path=None,save_tail=False):
    started=time.monotonic()
    missing=[]
    if encode_only:
        # One prompt, or one per scene / window of a long clip (`segment_prompts`): the encoder is read from disk once for all of
        # them, and every prompt already in the cache is only copied.
        prompts=request.get('segment_prompts')
        targets=[(folder/('ltx-conditioning-%02d.pt'%index),text) for index,text in enumerate(prompts)] if prompts else [(folder/'ltx-conditioning.pt',request['prompt'])]
        for target,text in targets:
            cached=conditioning_cache(folder,text)
            if cached.is_file():
                shutil.copyfile(cached,target)
                cached.touch()
            else:
                missing.append((target,cached,text))
        if not missing:
            event('encoder_cached')
            return
    for asset in ASSETS:
        path=models/asset['path']
        if not path.is_file() or path.stat().st_size!=asset['bytes']:
            raise ValueError('LTX 2.5: файл отсутствует или имеет неверный размер: '+asset['path'])
    # Loading LTX after unloading its Gemma encoder crashed on Windows in one process.
    # Let the owned encoder child exit before importing/loading the diffusion runtime.
    if not encode_only and conditioning_path is None:
        command=[sys.executable,'-X','faulthandler','-u',str(Path(__file__).resolve()),
            '--root',str(root),'--models',str(models),'--folder',str(folder),
            '--spec',str(folder/'ltx-request.json'),'--encode-only']
        result=subprocess.run(command,timeout=600,stdout=sys.stdout,stderr=sys.stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            raise RuntimeError('LTX 2.5: процесс текстового энкодера завершился с кодом '+str(result.returncode))
    sys.path.insert(0,str(root/'vendor/ComfyUI'))
    # SageAttention was measured on 2026-10-30 (1024x576, 97 frames, two passes, same seed): the 8 sampling steps took 7.3 s with it and
    # 7.3 s without, so unlike Wan it brings nothing here; the time goes to reading the text encoder and the model from disk.
    sys.argv=['visualforge-ltx25','--disable-dynamic-vram','--disable-pinned-memory','--disable-smart-memory',
        '--use-pytorch-cross-attention','--reserve-vram','2','--disable-all-custom-nodes']
    logging.basicConfig(level=logging.INFO)
    import comfy.options
    comfy.options.enable_args_parsing()
    import torch
    import numpy as np
    from PIL import Image
    import folder_paths
    import nodes
    import comfy.model_management as memory
    import comfy.utils
    from comfy_extras.nodes_lt import (EmptyLTXVLatentVideo,LTXVConditioning,LTXVDualCFGGuider,
        LTXVConcatAVLatent,LTXVSeparateAVLatent,LTXVImgToVideoInplace,LTXVPreprocess,LTXVAddGuide,LTXVCropGuides)
    from comfy_extras.nodes_lt_audio import LTXVEmptyLatentAudio,LTXVAudioVAEDecode,LTXVAudioVAEEncode
    from comfy_extras.nodes_custom_sampler import ManualSigmas,RandomNoise,KSamplerSelect,SamplerCustomAdvanced
    from comfy_extras.nodes_hunyuan import LatentUpscaleModelLoader
    from comfy_extras.nodes_lt_upsampler import LTXVLatentUpsampler
    if not torch.cuda.is_available():
        raise RuntimeError('Для LTX 2.5 требуется NVIDIA CUDA GPU')
    torch.set_num_threads(min(8,os.cpu_count() or 4))
    for name in ('diffusion_models','text_encoders','vae','latent_upscale_models'):
        folder_paths.add_model_folder_path(name,str(models/name),is_default=True)
    comfy.utils.PROGRESS_BAR_ENABLED=False
    plan=inference_plan(request)
    (folder/'ltx-inference-config.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    if not encode_only:event('sampling_plan',**(plan|({'scope':'window'} if save_tail else {})))
    def release():
        release_models(memory,torch)
    with torch.inference_mode():
        if encode_only:
            event('encoder_start')
            clip=nodes.CLIPLoader().load_clip(plan['text_encoder'],'ltxv',device='default')[0]
            negative=nodes.CLIPTextEncode().encode(clip,NEGATIVE_PROMPT)[0]
            def cpu(value):
                if isinstance(value,torch.Tensor):return value.detach().cpu().clone()
                if isinstance(value,dict):return {key:cpu(item) for key,item in value.items()}
                if isinstance(value,(list,tuple)):return [cpu(item) for item in value]
                return value
            for target,cached,text in missing:
                positive=nodes.CLIPTextEncode().encode(clip,text)[0]
                torch.save(dict(positive=cpu(positive),negative=cpu(negative),
                    peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated()/1024**2)),target)
                cached.parent.mkdir(parents=True,exist_ok=True)
                part=cached.with_name(cached.name+'.part')
                shutil.copyfile(target,part)
                os.replace(part,cached)
            prune_cache(missing[0][1].parent)
            event('encoder_complete',prompts=len(missing))
            return
        conditioning_path=conditioning_path or folder/'ltx-conditioning.pt'
        if not conditioning_path.resolve().is_relative_to(folder.parent.resolve()):
            raise ValueError('Conditioning outside owned LTX job')
        conditioning=torch.load(conditioning_path,map_location='cpu',weights_only=True)
        context=None
        if continuation_path:
            if not continuation_path.resolve().is_relative_to(folder.parent.resolve()):
                raise ValueError('Continuation outside owned LTX job')
            context=torch.load(continuation_path,map_location='cpu',weights_only=True)
        positive,negative=conditioning['positive'],conditioning['negative']
        positive,negative=LTXVConditioning.execute(positive,negative,request['fps'])
        event('model_load_phase',phase='vae')
        vae=nodes.VAELoader().load_vae(plan['video_vae'])[0]
        audio_vae=nodes.VAELoader().load_vae(plan['audio_vae'])[0]
        image=None
        media=json.loads((folder/'media.json').read_text('utf-8')) if (folder/'media.json').is_file() else {}
        def load_frame(key):
            path=Path(media[key]).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError('Исходный кадр вне папки задачи LTX 2.5')
            with Image.open(path) as source:
                picture=torch.from_numpy(np.asarray(source.convert('RGB')).copy()).float().unsqueeze(0)/255
            return LTXVPreprocess.execute(picture,18)[0]
        last=None
        if request['mode'] in ('i2v','first_last') or request['mode']=='a2v' and 'first' in media:
            image=load_frame('first')
        if request['mode']=='first_last':
            last=load_frame('last')
        latent=EmptyLTXVLatentVideo.execute(plan['base_width'],plan['base_height'],request['frames'],1)[0]
        if image is not None:
            latent=LTXVImgToVideoInplace.execute(vae,image,latent,.7)[0]
        if last is not None:
            # The ending picture is a keyframe guide: its tokens ride behind the real frames and are cut off after sampling (LTXVCropGuides).
            # Writing it into the last latent frame instead would be wrong: one encoded picture stands for a single pixel frame, the
            # last latent frame for the eight before the end.
            positive,negative,latent=LTXVAddGuide.execute(positive,negative,vae,latent,last,-1,1.0)
        audio_latent=LTXVEmptyLatentAudio.execute(request['frames'],request['fps'],1,audio_vae)[0]
        recording=frozen=None
        if request['mode']=='a2v':
            audio_path=Path(media['audio']).resolve()
            if not audio_path.is_relative_to(folder.resolve()):
                raise ValueError('Запись LTX 2.5 вне папки задачи')
            recording=read_recording(audio_path)
            frozen=freeze_recording(audio_latent,LTXVAudioVAEEncode.execute(recording,audio_vae)[0]['samples'])
            audio_latent=frozen
        if context:
            latent,audio_latent=apply_context(latent,audio_latent,context,base=True)
        latent=LTXVConcatAVLatent.execute(latent,audio_latent)[0]
        release()
        event('model_load_phase',phase='diffusion')
        model=nodes.UNETLoader().load_unet(plan['transformer'],'default')[0]
        sampler=KSamplerSelect.execute(plan['sampler'])[0]
        def sample(latent,values,seed,stage):
            last=[-1]
            def progress(value,total,preview=None,**kwargs):
                if value!=last[0]:
                    last[0]=value;event('step',completed_steps=int(value),total=int(total),stage=stage)
            comfy.utils.set_progress_bar_global_hook(progress)
            try:
                guider=LTXVDualCFGGuider.execute(model,positive,negative,1.0,1.0)[0]
                noise=RandomNoise.execute(seed)[0]
                sigmas=ManualSigmas.execute(','.join(map(str,values)))[0]
                return SamplerCustomAdvanced.execute(noise,guider,sampler,sigmas,latent)[0]
            finally:
                comfy.utils.set_progress_bar_global_hook(None)
        event('sampling_start',stage=1)
        sampled=sample(latent,plan['sigmas'],int(request['seed']),1)
        del latent
        base_video=LTXVSeparateAVLatent.execute(sampled)[0]['samples'][:,:,-4:].cpu().clone() if save_tail else None
        release()
        if plan['refine_sigmas']:
            event('sampling_prepare',stage=2)
            video,audio_latent=LTXVSeparateAVLatent.execute(sampled)
            if last is not None:
                positive,negative,video=LTXVCropGuides.execute(positive,negative,video)
            upscaler=LatentUpscaleModelLoader.execute(plan['upsampler'])[0]
            video=LTXVLatentUpsampler.execute(video,upscaler,vae)[0]
            del upscaler,sampled
            if image is not None:
                video=LTXVImgToVideoInplace.execute(vae,image,video,1.0)[0]
            if last is not None:
                positive,negative,video=LTXVAddGuide.execute(positive,negative,vae,video,last,-1,1.0)
            if context:
                video,audio_latent=apply_context(video,audio_latent,context)
            if frozen is not None:
                audio_latent=frozen      # the second pass must not let the sampler touch the recording either
            latent=LTXVConcatAVLatent.execute(video,audio_latent)[0]
            del video,audio_latent
            release()
            sampled=sample(latent,plan['refine_sigmas'],plan['refine_seed'],2)
            del latent
        video,audio_latent=LTXVSeparateAVLatent.execute(sampled)
        if last is not None:
            positive,negative,video=LTXVCropGuides.execute(positive,negative,video)    # needs the conditioning that carries the guide positions
        del model,positive,negative,sampled
        release()
        event('decode_phase')
        if save_tail:
            audio_length=audio_vae.first_stage_model.num_of_latents_from_frames(25,request['fps'])
            torch.save(dict(base_video=base_video,video=video['samples'][:,:,-4:].cpu().clone(),
                audio=audio_latent['samples'][:,:,-audio_length:].cpu().clone()),folder/'ltx-tail.pt')
        # Audio-to-video returns the person's own recording untouched; the vocoder would only re-synthesize it.
        audio=recording if recording is not None else LTXVAudioVAEDecode.execute(audio_latent,audio_vae)[0]
        del audio_latent,audio_vae
        release()
        images=nodes.VAEDecodeTiled().decode(vae,video,tile_size=512,overlap=64,temporal_size=64,temporal_overlap=16)[0]
        del video,vae
        if images.ndim!=4 or len(images)!=request['frames'] or tuple(images.shape[1:3])!=(request['height'],request['width']):
            raise RuntimeError('LTX 2.5 вернул неверный размер или число кадров')
        if not torch.isfinite(images).all() or float(images.max()-images.min())<.001:
            raise RuntimeError('LTX 2.5 вернул повреждённые или однотонные кадры')
        images=images.cpu()
        release()
        export_media(images,audio,folder,root,request)
        event('ltx_metrics',inference_sec=round(time.monotonic()-started,2),
            peak_allocated_vram_mb=max(conditioning['peak_allocated_vram_mb'],round(torch.cuda.max_memory_allocated()/1024**2)),torch=torch.__version__,python=sys.version.split()[0])


def main():
    parser=argparse.ArgumentParser()
    for name in ('root','models','folder','spec'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--encode-only',action='store_true')
    parser.add_argument('--conditioning',type=Path)
    parser.add_argument('--continuation',type=Path)
    parser.add_argument('--save-tail',action='store_true')
    args=parser.parse_args()
    try:
        run(args.root.resolve(),args.models.resolve(),args.folder.resolve(),json.loads(args.spec.read_text('utf-8')),args.encode_only,args.conditioning,args.continuation,args.save_tail)
    except Exception:
        traceback.print_exc(limit=12)
        raise SystemExit(1)


if __name__=='__main__':main()
