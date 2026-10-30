"""Focused Wan registry, isolation, adapter and bounded log checks; no GPU."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'App/server'))
from video.video_registry import validate
from video.video_adapters import build_command
from video.wan_support import archive_log, engine_missing, WAN_ID, WAN_FILES

class WanTests(unittest.TestCase):
    def test_modes_and_profiles(self):
        for mode in ('t2v','i2v'):
            row=validate(dict(engine=WAN_ID,prompt='A ball rolls.',mode=mode,frames=33,quality='standard',source_image='data:image/png;base64,AA==',seed='18446744073709551615'))
            self.assertEqual(row['engine'],WAN_ID)
            self.assertEqual(row['seed'],'18446744073709551615')
        for extra in ({'mode':'characters'},{'mode':'first_last'},{'frames':39},{'frames':1440},{'width':1280},{'mode':'i2v'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                validate(dict(engine=WAN_ID,prompt='test',frames=33)|extra)

    def test_native_and_portrait_sizes(self):
        for width,height in ((832,480),(480,832),(1280,704),(704,1280)):
            row=validate(dict(engine=WAN_ID,prompt='A ball rolls.',width=width,height=height,frames=121))
            self.assertEqual((row['width'],row['height'],row['frames']),(width,height,121))
        # 512x288 produced severe shape artifacts and is no longer offered.
        with self.assertRaises(ValueError):
            validate(dict(engine=WAN_ID,prompt='test',width=512,height=288,frames=33))

    def test_negative_prompt_only_for_wan_ti2v(self):
        row=validate(dict(engine=WAN_ID,prompt='A ball rolls.',frames=33,negative_prompt='  blur, watermark  '))
        self.assertEqual(row['negative_prompt'],'blur, watermark')
        self.assertNotIn('negative_prompt',validate(dict(engine=WAN_ID,prompt='A ball rolls.',frames=33,negative_prompt='  ')))
        for engine in ('freevideo-h3-int8','ltx25-distilled','wan22-s2v-14b'):
            with self.subTest(engine=engine),self.assertRaises(ValueError):
                validate(dict(engine=engine,prompt='test',negative_prompt='blur'))
        for bad in ('x'*2001,5):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                validate(dict(engine=WAN_ID,prompt='test',frames=33,negative_prompt=bad))

    def test_independent_assets_and_bounded_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime=root/'runtime';h3=root/'Models/H3';wan=root/'Models/Wan'
            for path in ('python/python.exe','ffmpeg/bin/ffprobe.exe','ffmpeg/bin/ffmpeg.exe','vendor/ComfyUI/comfy_extras/nodes_wan.py'):
                file=runtime/path;file.parent.mkdir(parents=True,exist_ok=True);file.touch()
            for path in WAN_FILES:
                file=wan/path;file.parent.mkdir(parents=True,exist_ok=True);file.touch()
            with patch.dict(os.environ,VISUALFORGE_WAN_MODELS=str(wan)):
                self.assertFalse(engine_missing(runtime,h3,WAN_ID))
                self.assertTrue(engine_missing(runtime,h3,'freevideo-h3-int8'))
                folder=root/'job';folder.mkdir()
                row=validate(dict(engine=WAN_ID,prompt='A ball rolls.',frames=33))
                command=build_command(runtime,h3,folder,row)
                self.assertIn('wan_worker.py',str(command))
                self.assertIn(str(wan.resolve()),command)
                self.assertNotIn('pipeline_worker.py',str(command))
                (folder/'backend.log').write_bytes(b'x'*100000)
                log=archive_log(root/'UserData',folder,'test')
                self.assertEqual(log.stat().st_size,48*1024)

    def test_sibling_workers_only_import_names_wan_worker_defines(self):
        """The S2V and LTX workers share helpers from wan_worker; renaming one broke S2V at start (ImportError) once."""
        import ast
        folder=Path(__file__).resolve().parents[1]/'App/server/video'
        defined={node.name for node in ast.parse((folder/'wan_worker.py').read_text(encoding='utf-8')).body if isinstance(node,(ast.FunctionDef,ast.ClassDef))}
        for name in ('s2v_worker.py','ltx_worker.py'):
            for node in ast.walk(ast.parse((folder/name).read_text(encoding='utf-8'))):
                if isinstance(node,ast.ImportFrom) and node.module in ('wan_worker','video.wan_worker'):
                    for alias in node.names:
                        self.assertIn(alias.name,defined,name+' imports '+alias.name)

if __name__=='__main__':unittest.main()
