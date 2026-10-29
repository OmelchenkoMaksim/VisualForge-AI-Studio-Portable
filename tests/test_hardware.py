"""Compatibility boundaries: lower-memory offload is not a blanket impossibility verdict."""
from pathlib import Path
import sys
import shutil
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
import hardware_inventory as hardware


def machine(vram=16, ram=64):
    return dict(os='Windows', architecture='AMD64', cpu='Test CPU', logical_cores=16, ram_gb=ram,
        gpu_detection='nvidia-smi', gpus=[dict(index=0, name='Test NVIDIA', vram_gb=vram, cuda=True)])


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.profiles = hardware.profiles([dict(name='local:latest', label='Local', ok=True, size=15 * hardware.GIB)])

    def test_green_yellow_red_and_comparisons(self):
        profile = self.profiles['video:ltx25-distilled']
        for vram, status in ((16, 'green'), (8, 'yellow'), (6, 'red')):
            with self.subTest(vram=vram):
                row = hardware.assess(machine(vram), profile)
                self.assertEqual(row['status'], status)
                self.assertEqual(row['actual_vram_gb'], vram)
                self.assertEqual(row['profile']['recommended_vram_gb'], 16)
                self.assertTrue(row['reasons'])
        self.assertEqual(hardware.assess(machine(15.92), profile)['status'], 'green')
        self.assertIn('Система подходит', hardware.assess(machine(), profile)['title'])

    def test_each_shipped_model_has_a_starting_profile(self):
        from engines.image_registry import ENGINES as image
        from video.video_registry import ENGINES as video
        from music.music_registry import ENGINES as music
        for module, engines in (('image', image), ('video', video), ('music', music)):
            for ident in engines:
                self.assertEqual(hardware.assess(machine(), self.profiles[module+':'+ident])['status'], 'green')

    def test_no_cuda_blocks_native_engines_but_not_text_cpu_offload(self):
        pc = machine()
        pc.update(gpu_detection='windows', gpus=[dict(index=0, name='AMD GPU', cuda=False, vram_gb=None)])
        self.assertEqual(hardware.assess(pc, self.profiles['video:ltx25-distilled'])['status'], 'red')
        self.assertEqual(hardware.assess(pc, self.profiles['text:local:latest'])['status'], 'yellow')

    def test_unknown_gpu_and_unknown_ram_cannot_produce_green_or_false_block(self):
        pc = machine()
        pc.update(gpu_detection='unknown', gpus=[], ram_gb=None)
        row = hardware.assess(pc, self.profiles['video:ltx25-distilled'])
        self.assertEqual(row['status'], 'yellow')
        self.assertIsNone(row['actual_vram_gb'])
        pc['gpus'] = [dict(index=0, name='NVIDIA (driver unavailable)', cuda=None, vram_gb=None)]
        pc['gpu_detection'] = 'windows'
        self.assertEqual(hardware.assess(pc, self.profiles['image:qwen-edit-2511'])['status'], 'yellow')

    def test_no_sum_of_multiple_cards_or_hidden_pick_of_the_largest(self):
        pc = machine(6)
        pc['gpus'].append(dict(index=1, name='Second NVIDIA', vram_gb=24, cuda=True))
        row = hardware.assess(pc, self.profiles['video:ltx25-distilled'])
        self.assertEqual(row['status'], 'red')
        self.assertEqual(row['actual_vram_gb'], 6)
        self.assertEqual(row['gpu_name'], 'Test NVIDIA')

    def test_ram_recommendation_is_not_an_unproven_hard_minimum(self):
        self.assertEqual(hardware.assess(machine(ram=32), self.profiles['video:ltx25-distilled'])['status'], 'yellow')
        self.assertEqual(hardware.assess(machine(ram=8), self.profiles['music:ace-step-15-turbo'])['status'], 'red')
        pc = machine(); pc['architecture'] = 'ARM64'
        self.assertEqual(hardware.assess(pc, self.profiles['text:local:latest'])['status'], 'red')

    def test_nvidia_rows_are_bounded_numeric_and_do_not_use_wmi_adapterram(self):
        rows = hardware.parse_nvidia('1, Second GPU, 24576\n0, First GPU, 16303\nbad\n2, Invalid, NaN\n3, Invalid, -1\n')
        self.assertEqual([r['index'] for r in rows], [0, 1])
        self.assertAlmostEqual(rows[0]['vram_gb'], 15.92)

    def test_inventory_is_cached_copied_and_expires(self):
        with patch.object(hardware, '_cached', None), patch.object(hardware, '_at', 0), patch.object(hardware, 'scan', return_value=machine()) as scan:
            with patch.object(hardware.time, 'monotonic', return_value=100):
                first = hardware.inventory();first['gpus'].clear()
                self.assertTrue(hardware.inventory()['gpus'])
                self.assertEqual(scan.call_count, 1)
            with patch.object(hardware.time, 'monotonic', return_value=161):
                hardware.inventory()
                self.assertEqual(scan.call_count, 2)
            with patch.object(hardware.time, 'monotonic', return_value=163):
                hardware.inventory(refresh=True)
                self.assertEqual(scan.call_count, 2)
            with patch.object(hardware.time, 'monotonic', return_value=167):
                hardware.inventory(refresh=True)
                self.assertEqual(scan.call_count, 3)


class HardwarePopupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_popup_preferences_and_timers(self):
        result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name('hardware_popup_test.js'))],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
