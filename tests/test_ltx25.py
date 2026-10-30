"""LTX 2.5 profiles, independent readiness and mandatory generated audio; CPU only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'App/server'))
from video.video_registry import validate
from video.video_adapters import build_command
from video.ltx_support import LTX_ID, ASSETS, model_root, inference_plan, validate_audio, engine_missing


class LtxTests(unittest.TestCase):
    def test_explicit_profiles_and_image_validation(self):
        for mode in ('t2v','i2v'):
            row = validate(dict(engine=LTX_ID,mode=mode,prompt='A bell rings.',quality='standard',width=768,height=512,
                frames=49,fps=24,source_image='data:image/png;base64,AA=='))
            self.assertEqual(row['engine'],LTX_ID)
        for extra in ({'frames':48},{'frames':1439},{'fps':30},{'mode':'characters'},{'mode':'first_last'},
                      {'quality':'high'},{'width':1920,'height':1080},{'mode':'i2v'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                validate(dict(engine=LTX_ID,prompt='Test',frames=49)|extra)

    def test_audio_to_video_validation(self):
        base = dict(engine=LTX_ID, mode='a2v', prompt='A man talks.', frames=97, source_audio='data:audio/wav;base64,AA==')
        self.assertEqual(validate(base)['mode'], 'a2v')
        self.assertEqual(validate(base | dict(source_image='data:image/png;base64,AA=='))['mode'], 'a2v')
        for extra in ({'source_audio': None}, {'frames': 360}, {'frames': 1440}, {'source_image': 5}, {'speech_text': 'x'}, {'voice_audio': 'data:audio/wav;base64,AA=='}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate(base | extra)
        with self.assertRaises(ValueError):   # a recording belongs only to audio-to-video
            validate(dict(engine=LTX_ID, mode='t2v', prompt='x', frames=49, source_audio='data:audio/wav;base64,AA=='))

    def test_first_last_frame_validation(self):
        base = dict(engine=LTX_ID, mode='first_last', prompt='A door opens.', frames=97, source_image='data:image/png;base64,AA==', end_image='data:image/png;base64,AA==')
        self.assertEqual(validate(base)['mode'], 'first_last')
        for extra in ({'end_image': None}, {'source_image': None}, {'frames': 360}, {'frames': 1440}, {'source_audio': 'data:audio/wav;base64,AA=='}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate(base | extra)

    def test_prompt_conditioning_is_cached_by_prompt_and_encoder(self):
        from video import ltx_worker
        with tempfile.TemporaryDirectory() as root:
            jobs = Path(root) / 'Jobs'
            first, second = jobs / 'a', jobs / 'b'
            one = ltx_worker.conditioning_cache(first, 'A fox runs.')
            self.assertEqual(one, ltx_worker.conditioning_cache(second, 'A fox runs.'), 'the same prompt of another job shares the entry')
            self.assertNotEqual(one, ltx_worker.conditioning_cache(first, 'A fox walks.'))
            self.assertEqual(one.parent, jobs / ltx_worker.CACHE_NAME)
            folder = one.parent
            folder.mkdir(parents=True)
            for index in range(ltx_worker.CACHE_LIMIT + 5):
                path = folder / ('%02d.pt' % index)
                path.write_bytes(b'x')
                os.utime(path, (1000 + index, 1000 + index))
            ltx_worker.prune_cache(folder)
            kept = sorted(path.name for path in folder.glob('*.pt'))
            self.assertEqual(len(kept), ltx_worker.CACHE_LIMIT)
            self.assertNotIn('00.pt', kept)          # the oldest go first

    def test_two_stages_use_the_published_distilled_schedule(self):
        row = validate(dict(engine=LTX_ID,prompt='Test',width=768,height=512,frames=49,quality='standard',seed='42'))
        plan = inference_plan(row)
        self.assertEqual((plan['base_width'],plan['base_height']),(384,256))
        self.assertEqual((len(plan['sigmas'])-1,len(plan['refine_sigmas'])-1),(8,3))
        self.assertEqual(plan['sigmas'],[1.0,.99375,.9875,.98125,.975,.909375,.725,.421875,0.0])
        plan = inference_plan(row|dict(quality='preview'))
        self.assertEqual((plan['base_width'],plan['base_height']),(768,512))
        self.assertEqual(plan['refine_sigmas'],[])

    def test_missing_or_wrong_size_asset_disables_only_ltx(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); runtime=root/'runtime'; models=root/'models'
            for rel in ('python/python.exe','ffmpeg/bin/ffmpeg.exe','ffmpeg/bin/ffprobe.exe',
                'vendor/ComfyUI/comfy_extras/nodes_lt.py','vendor/ComfyUI/comfy_extras/nodes_lt_audio.py',
                'vendor/ComfyUI/comfy_extras/nodes_lt_upsampler.py','vendor/ComfyUI/comfy_extras/nodes_hunyuan.py'):
                path=runtime/rel;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            small = [dict(path='vae/test.safetensors',bytes=4)]
            with patch.dict(os.environ,VISUALFORGE_LTX_MODELS=str(models)),patch('video.ltx_support.ASSETS',small):
                self.assertTrue(engine_missing(runtime,root/'H3'))
                path=models/small[0]['path'];path.parent.mkdir(parents=True);path.write_bytes(b'bad')
                self.assertTrue(engine_missing(runtime,root/'H3'))
                path.write_bytes(b'good')
                self.assertFalse(engine_missing(runtime,root/'H3'))

    def test_adapter_passes_private_paths_and_no_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'job';folder.mkdir()
            row=validate(dict(engine=LTX_ID,prompt='Test',frames=49))
            command=build_command(root,root/'Models/Video/H3',folder,row)
            self.assertIn('ltx_worker.py',' '.join(command))
            self.assertIn(str(model_root(root/'Models/Video/H3')),command)
            self.assertEqual(json.loads((folder/'ltx-request.json').read_text('utf-8'))['engine'],LTX_ID)

    def test_audio_is_required_and_must_cover_the_clip(self):
        video=dict(codec_type='video',duration='2.041667')
        audio=dict(codec_type='audio',sample_rate='48000',channels=2,duration='2.041667')
        validate_audio([video,audio],49/24)
        for streams in ([video],[video,audio|dict(duration='.5')],[video,audio|dict(channels=0)]):
            with self.assertRaises(ValueError):
                validate_audio(streams,49/24)

    def test_catalog_pins_every_asset_to_the_same_official_revision(self):
        root=Path(__file__).resolve().parents[1]
        catalog=json.loads((root/'App/config/component-catalog.json').read_text('utf-8'))
        component=next(c for c in catalog['components'] if c['id']=='video.model.ltx25')
        self.assertIn('video.video_runtime',component['requires'])
        self.assertEqual({Path(f['path']).name for f in component['files']},{Path(f['path']).name for f in ASSETS})
        self.assertTrue(all(len(f['sha256'])==64 and '/49b093a39c19169a8a81721dcc541e07e945232f/' in f['urls'][0] for f in component['files']))


if __name__=='__main__':unittest.main()
