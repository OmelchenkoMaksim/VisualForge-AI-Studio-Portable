"""Real CPU-only LTX media publication with the bundled runtime and FFmpeg."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path(os.environ.get('VISUALFORGE_VIDEO_RUNTIME',ROOT/'Runtime/Video'))


@unittest.skipUnless((RUNTIME/'python/python.exe').is_file() and (RUNTIME/'ffmpeg/bin/ffprobe.exe').is_file(),'Video runtime is optional')
class MediaTests(unittest.TestCase):
    def test_real_mux_has_exact_video_and_generated_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            script='''
import sys
from pathlib import Path
import torch
from video.ltx_worker import export_media
torch.set_num_threads(2)
frames=49
images=torch.rand((frames,64,64,3))
t=torch.arange(frames*1000)/24000
sound=(.1*torch.sin(2*torch.pi*440*t))[None,None,:].repeat(1,2,1)
export_media(images,dict(waveform=sound,sample_rate=24000),Path(sys.argv[1]),Path(sys.argv[2]),dict(width=64,height=64,frames=frames,fps=24))
'''
            env=dict(os.environ,PYTHONPATH=str(ROOT/'App/server'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='-1')
            result=subprocess.run([str(RUNTIME/'python/python.exe'),'-B','-c',script,temp,str(RUNTIME)],env=env,capture_output=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace')[-1500:])
            probe=subprocess.check_output([str(RUNTIME/'ffmpeg/bin/ffprobe.exe'),'-v','error','-count_frames','-show_streams','-of','json',str(Path(temp)/'result.mp4')])
            streams=json.loads(probe)['streams']
            video=next(s for s in streams if s['codec_type']=='video')
            audio=next(s for s in streams if s['codec_type']=='audio')
            self.assertEqual((video['codec_name'],video['nb_read_frames'],video['r_frame_rate']),('h264','49','24/1'))
            self.assertEqual((audio['codec_name'],audio['sample_rate'],audio['channels']),('aac','48000',2))
            self.assertAlmostEqual(float(audio['duration']),49/24,delta=.05)


if __name__=='__main__':unittest.main()
