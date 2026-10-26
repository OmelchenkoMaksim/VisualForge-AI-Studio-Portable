"""Direct LTX 2.5 on native ComfyUI; one owned offline process per generation."""
import argparse
import gc
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import wave

from video.ltx_support import ASSETS, inference_plan


def event(name,**values):
    print(json.dumps(dict(event=name,**values),ensure_ascii=False),flush=True)


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


def run(root,models,folder,request,encode_only=False,conditioning_path=None,continuation_path=None,save_tail=False):
    started=time.monotonic()
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
        LTXVConcatAVLatent,LTXVSeparateAVLatent,LTXVImgToVideoInplace,LTXVPreprocess)
    from comfy_extras.nodes_lt_audio import LTXVEmptyLatentAudio,LTXVAudioVAEDecode
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
        memory.unload_all_models();gc.collect();memory.soft_empty_cache()
    with torch.inference_mode():
        if encode_only:
            event('encoder_start')
            clip=nodes.CLIPLoader().load_clip(plan['text_encoder'],'ltxv',device='default')[0]
            positive=nodes.CLIPTextEncode().encode(clip,request['prompt'])[0]
            negative=nodes.CLIPTextEncode().encode(clip,'blurry, low quality, watermark, subtitles, distorted anatomy')[0]
            def cpu(value):
                if isinstance(value,torch.Tensor):return value.detach().cpu().clone()
                if isinstance(value,dict):return {key:cpu(item) for key,item in value.items()}
                if isinstance(value,(list,tuple)):return [cpu(item) for item in value]
                return value
            torch.save(dict(positive=cpu(positive),negative=cpu(negative),
                peak_allocated_vram_mb=round(torch.cuda.max_memory_allocated()/1024**2)),folder/'ltx-conditioning.pt')
            event('encoder_complete')
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
        if request['mode']=='i2v':
            path=Path(json.loads((folder/'media.json').read_text('utf-8'))['first']).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError('Исходный кадр вне папки задачи LTX 2.5')
            with Image.open(path) as source:
                image=torch.from_numpy(np.asarray(source.convert('RGB')).copy()).float().unsqueeze(0)/255
            image=LTXVPreprocess.execute(image,18)[0]
        latent=EmptyLTXVLatentVideo.execute(plan['base_width'],plan['base_height'],request['frames'],1)[0]
        if image is not None:
            latent=LTXVImgToVideoInplace.execute(vae,image,latent,.7)[0]
        audio_latent=LTXVEmptyLatentAudio.execute(request['frames'],request['fps'],1,audio_vae)[0]
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
            upscaler=LatentUpscaleModelLoader.execute(plan['upsampler'])[0]
            video=LTXVLatentUpsampler.execute(video,upscaler,vae)[0]
            del upscaler,sampled
            if image is not None:
                video=LTXVImgToVideoInplace.execute(vae,image,video,1.0)[0]
            if context:
                video,audio_latent=apply_context(video,audio_latent,context)
            latent=LTXVConcatAVLatent.execute(video,audio_latent)[0]
            del video,audio_latent
            release()
            sampled=sample(latent,plan['refine_sigmas'],plan['refine_seed'],2)
            del latent
        del model,positive,negative
        release()
        event('decode_phase')
        video,audio_latent=LTXVSeparateAVLatent.execute(sampled)
        del sampled
        if save_tail:
            audio_length=audio_vae.first_stage_model.num_of_latents_from_frames(25,request['fps'])
            torch.save(dict(base_video=base_video,video=video['samples'][:,:,-4:].cpu().clone(),
                audio=audio_latent['samples'][:,:,-audio_length:].cpu().clone()),folder/'ltx-tail.pt')
        audio=LTXVAudioVAEDecode.execute(audio_latent,audio_vae)[0]
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
