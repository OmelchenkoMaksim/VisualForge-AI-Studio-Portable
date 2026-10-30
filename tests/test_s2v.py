"""Focused S2V profile, audio-boundary and dispatch checks without model inference."""
import argparse,base64,json,os,subprocess,sys,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'App/server'))
from video.video_registry import validate
from video.video_service import Jobs
from video.video_adapters import build_command
from video.s2v_audio import prepare
from video.wan_support import S2V_ID,WAN_ID,S2V_FILES,WAN_FILES,engine_missing

class S2VTests(unittest.TestCase):
    def test_voice_progress_accepts_speaker_name(self):
        import contextlib,io
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'App/server/video'))
        from s2v_worker import event
        output=io.StringIO()
        with contextlib.redirect_stdout(output):event('voice_start',name='Wan S2V')
        self.assertEqual(json.loads(output.getvalue()),dict(event='voice_start',name='Wan S2V'))
    def request(self,**extra):
        return dict(engine=S2V_ID,prompt='The person speaks.',source_image='data:image/png;base64,AA==',speech_mode='clone',voice_audio='data:audio/wav;base64,AA==',speech_text='Привет!',**extra)
    def test_profile_and_speech_validation(self):
        result=validate(self.request())
        self.assertEqual((result['mode'],result['fps'],result['frames'],result['quality']),('s2v',16,77,'standard'))
        self.assertNotIn('voice_audio',result)
        for extra in ({'fps':24},{'frames':81},{'quality':'preview'},{'mode':'t2v'},{'source_image':None},{'speech_text':' '},{'speech_text':'x'*161},{'language':'unsupported'},{'source_audio':'extra'},{'width':832}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):validate(self.request()|extra)
        ready=self.request()|dict(speech_mode='audio',source_audio='data:audio/wav;base64,AA==')
        ready.pop('voice_audio');ready.pop('speech_text')
        self.assertEqual(validate(ready)['speech_mode'],'audio')
        with self.assertRaises(ValueError):validate(dict(prompt='test',speech_text='Unexpected speech'))

    def test_long_structures_validate_and_keep_the_single_defaults(self):
        self.assertEqual(validate(self.request())['structure'],'single')
        long_text='Слово '*100
        for structure in ('scenes',):
            result=validate(self.request()|dict(structure=structure,segment_prompts=['a','b','c'],speech_text=long_text))
            self.assertEqual((result['structure'],len(result['segment_prompts'])),(structure,3))
            self.assertEqual(result['frames'],77,'frames stay the per-segment size; the worker plans the real length from the speech')
        # a long line needs a long structure; prompts only belong to long structures; other engines have no structure
        for extra in ({'speech_text':long_text},{'structure':'wide'},{'structure':'chain'},{'segment_prompts':['a','b']},{'structure':'scenes','segment_prompts':[]},
                      {'structure':'scenes','segment_prompts':['a']*21},{'structure':'scenes','segment_prompts':['  ','b']},{'structure':'scenes','speech_text':'x'*1001}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):validate(self.request()|extra)
        with self.assertRaises(ValueError):validate(dict(engine=WAN_ID,prompt='x',structure='scenes'))

    def test_speech_plan_chain_scenes_and_pauses(self):
        import numpy as np
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'App/server/video'))
        import s2v_worker as worker
        rate=worker.RATE
        def speech(seconds,pauses=()):
            """Noise bursts that stand in for words, with silent gaps at the given times (seconds)."""
            wave_=np.random.default_rng(1).normal(0,.2,int(seconds*rate)).astype(np.float32)
            for at in pauses:wave_[int(at*rate):int((at+.4)*rate)]=0
            return wave_
        self.assertEqual(worker.plan_segments(speech(3),'single',16)['frames'],77)
        scenes=worker.plan_segments(speech(14,pauses=(4.1,8.6)),'scenes',16)
        starts=[a/rate for a,b in scenes['slices']]
        self.assertEqual(len(scenes['slices']),4)
        self.assertAlmostEqual(scenes['slices'][0][1]/rate,4.3,delta=.25,msg='the first cut falls into the pause at 4.1-4.5 s')
        self.assertAlmostEqual(scenes['slices'][1][1]/rate,8.8,delta=.25)
        for a,b in scenes['slices']:self.assertLessEqual((b-a)/rate,77/16+1e-6)
        self.assertEqual(scenes['slices'][-1][1],int(14*rate))
        self.assertEqual(scenes['frames'],sum(scenes['counts']))
        self.assertEqual(scenes['counts'][:2],[int(np.ceil((b-a)/rate*16)) for a,b in scenes['slices'][:2]],'a shot keeps as many pictures as its speech lasts')
        self.assertEqual(scenes['counts'][-1],min(77,int(np.ceil((scenes['slices'][-1][1]-scenes['slices'][-1][0])/rate*16))+8))
        continuous=worker.plan_segments(speech(12),'scenes',16)                 # no pause: cuts every 4.8125 s
        self.assertEqual([round((b-a)/rate,4) for a,b in continuous['slices']][:2],[4.8125,4.8125])

    def test_adapter_and_readiness_use_distinct_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);h3=root/'Models/H3';wan=root/'Models/Wan22TI2V5B';s2v=root/'Models/Wan22S2V14B';runtime=root/'Runtime';folder=root/'job';folder.mkdir()
            names=[runtime/n for n in ('python/python.exe','ffmpeg/bin/ffprobe.exe','ffmpeg/bin/ffmpeg.exe','vendor/ComfyUI/comfy_extras/nodes_wan.py','vendor/ComfyUI/comfy_extras/nodes_audio_encoder.py')]+[s2v/n for n in S2V_FILES]+[wan/WAN_FILES[1]]
            for path in names:path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            with patch.dict(os.environ,VISUALFORGE_WAN_MODELS=str(wan),VISUALFORGE_S2V_MODELS=str(s2v)):
                self.assertFalse(engine_missing(runtime,h3,S2V_ID))
                self.assertTrue(engine_missing(runtime,h3,WAN_ID))
                command=build_command(runtime,h3,folder,validate(self.request()))
                self.assertIn('s2v_worker.py',str(command));self.assertIn(str(s2v.resolve()),command);self.assertIn(str(wan.resolve()),command)
                self.assertNotIn('video_pipeline.py',str(command))

    def test_audio_duration_and_receipt_excludes_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'input.wav'
            with wave.open(str(source),'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0'*(16000*2*3))
            data=dict(speech_mode='audio',source_audio='data:audio/wav;base64,'+base64.b64encode(source.read_bytes()).decode())
            jobs=Jobs(argparse.Namespace(root=str(root/'Runtime'),models=str(root/'Models/H3'),user=str(root/'UserData'),state=str(root/'state')))
            response=dict(streams=[dict(codec_type='audio')],format=dict(duration='3.0'))
            with patch('video.s2v_audio.subprocess.run',return_value=argparse.Namespace(stdout=json.dumps(response))):
                result=prepare(jobs,data,root)
                self.assertEqual(result['speech_input_duration'],3)
                self.assertNotIn('source_audio',result)
            response['format']['duration']='4.9'
            with patch('video.s2v_audio.subprocess.run',return_value=argparse.Namespace(stdout=json.dumps(response))),self.assertRaises(ValueError):prepare(jobs,data,root)

if __name__=='__main__':unittest.main()
