"""Long LTX clips use bounded shared AV context and frame-exact CPU assembly."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'App/server'))
from video.ltx_continuation import windows, assembly_command, LONG_FRAMES
from video.registry import validate
from video.adapters import build_command
from video import ltx_long

RUNTIME=Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME',ROOT/'Runtime/Video'))


class ContinuationTests(unittest.TestCase):
    def test_image_is_used_only_for_initial_window_and_prompt_encoder_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source=folder/'first.png';source.write_bytes(b'owned-image')
            (folder/'media.json').write_text(json.dumps(dict(first=str(source))))
            spec=folder/'ltx-request.json'
            spec.write_text(json.dumps(dict(engine='ltx25-distilled',mode='i2v',prompt='A bell',frames=360,
                fps=24,width=768,height=512,quality='standard',seed='42')))
            calls=[]
            def child(command,timeout):
                calls.append(command)
                if '--encode-only' in command:
                    (folder/'ltx-conditioning.pt').write_bytes(b'private-conditioning')
                elif '--folder' in command:
                    part=Path(command[command.index('--folder')+1])
                    (part/'result.mp4').touch();(part/'ltx-tail.pt').touch()
            args=SimpleNamespace(folder=folder,spec=spec,root=folder/'Runtime',models=folder/'Models')
            with patch.object(ltx_long,'child',side_effect=child),patch.object(ltx_long,'event'):
                ltx_long.run(args)
            self.assertEqual(sum('--encode-only' in c for c in calls),1)
            self.assertEqual((folder/'part-01/first.png').read_bytes(),source.read_bytes())
            for index,part in enumerate(sorted(folder.glob('part-*'))):
                row=json.loads((part/'ltx-request.json').read_text())
                self.assertEqual(row['mode'],'i2v' if index==0 else 't2v')
                command=calls[index+1]
                self.assertIn(str(folder/'ltx-conditioning.pt'),command)
                self.assertEqual('--continuation' in command,index>0)

    @unittest.skipUnless((RUNTIME/'python/python.exe').is_file(),'Optional Video runtime')
    def test_shared_av_prefix_is_frozen_and_future_remains_generatable(self):
        script='''
import torch
from video.ltx_worker import apply_context
video={'samples':torch.zeros(1,2,16,2,2)}
audio={'samples':torch.zeros(1,8,126,16)}
context={'base_video':torch.ones(1,2,4,2,2),'video':torch.full((1,2,4,4,4),3.),'audio':torch.full((1,8,26,16),2.)}
v,a=apply_context(video,audio,context,base=True)
assert torch.all(v['samples'][:,:,:4]==1) and torch.all(v['samples'][:,:,4:]==0)
assert torch.all(a['samples'][:,:,:26]==2) and torch.all(a['samples'][:,:,26:]==0)
assert torch.all(v['noise_mask'][:,:,:4]==0) and torch.all(v['noise_mask'][:,:,4:]==1)
assert torch.all(a['noise_mask'][:,:,:26]==0) and torch.all(a['noise_mask'][:,:,26:]==1)
v,a=apply_context({'samples':torch.zeros(1,2,16,4,4)},audio,context)
assert torch.all(v['samples'][:,:,:4]==3)
assert torch.count_nonzero(video['samples'])==0
try:apply_context(video,audio,context)
except ValueError:pass
else:raise AssertionError('Mismatched stage context must be rejected')
'''
        env=dict(os.environ,PYTHONPATH=str(ROOT/'App/server'),CUDA_VISIBLE_DEVICES='-1',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([str(RUNTIME/'python/python.exe'),'-B','-c',script],env=env,capture_output=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace')[-1500:])

    def test_duration_plan_covers_timeline_with_bounded_overlap(self):
        for count in LONG_FRAMES:
            plan=windows(count)
            self.assertEqual(plan[-1]['start']+plan[-1]['frames'],count+1)
            self.assertTrue(all(w['frames']<=121 and w['frames']%8==1 for w in plan))
            for previous,current in zip(plan,plan[1:]):
                self.assertEqual(previous['start']+previous['frames']-current['start'],25)
            self.assertEqual(len(plan),{360:4,720:8,1440:15}[count])
        with self.assertRaises(ValueError):windows(1439)

    def test_long_requests_are_routed_only_to_owned_continuation_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            for frames in LONG_FRAMES:
                row=validate(dict(engine='ltx25-distilled',prompt='A bell swings',frames=frames))
                command=build_command(folder,folder/'Models/Video/H3',folder,row)
                self.assertIn('ltx_long.py',' '.join(command))
            row=validate(dict(engine='ltx25-distilled',prompt='A bell swings',frames=49))
            self.assertIn('ltx_worker.py',' '.join(build_command(folder,folder/'Models/Video/H3',folder,row)))

    @unittest.skipUnless((RUNTIME/'ffmpeg/bin/ffmpeg.exe').is_file(),'Optional Video runtime')
    def test_real_15_30_60_second_assembly_has_exact_frames_and_audio(self):
        ffmpeg=RUNTIME/'ffmpeg/bin/ffmpeg.exe'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/'source.mp4'
            subprocess.run([str(ffmpeg),'-v','error','-nostdin','-n','-f','lavfi','-i','testsrc2=size=64x64:rate=24',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t',str(121/24),
                '-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-ac','2',str(source)],check=True,timeout=60)
            for frames in LONG_FRAMES:
                folder=root/str(frames);folder.mkdir()
                parts=[source]*len(windows(frames))
                # Owned inputs may reside anywhere under this isolated job root.
                command=assembly_command(ffmpeg,root,parts,frames)
                command[-1]=str(folder/'result.mp4')
                result=subprocess.run(command,capture_output=True,timeout=90)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace')[-1800:])
                raw=subprocess.check_output([str(ffmpeg.with_name('ffprobe.exe')),'-v','error','-count_frames','-show_streams','-of','json',command[-1]])
                streams=json.loads(raw)['streams']
                video=next(s for s in streams if s['codec_type']=='video')
                audio=next(s for s in streams if s['codec_type']=='audio')
                self.assertEqual((int(video['nb_read_frames']),video['r_frame_rate']),(frames,'24/1'))
                self.assertAlmostEqual(float(video['duration']),frames/24,delta=.001)
                self.assertAlmostEqual(float(audio['duration']),frames/24,delta=.03)
                self.assertEqual((audio['sample_rate'],audio['channels']),('48000',2))


if __name__=='__main__':unittest.main()
