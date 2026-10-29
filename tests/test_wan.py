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

if __name__=='__main__':unittest.main()
