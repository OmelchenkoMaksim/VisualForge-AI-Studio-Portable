"""Focused S2V profile, audio-boundary and dispatch checks without model inference."""
import argparse,base64,json,os,subprocess,sys,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'App/server'))
from video.registry import validate
from video.service import Jobs
from video.adapters import build_command
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
                self.assertNotIn('pipeline.py',str(command))

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
