"""The GPU coordinator: one claim() for every start route; fakes only, no GPU."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
from gpu import Busy, GpuCoordinator, GpuUnavailable, MODULES


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make(busy=(), free=lambda: 16000, **extra):
    clock, calls = Clock(), []
    probes = {m: (lambda m=m: m in busy) for m in MODULES}
    releasers = {m: (lambda m=m: calls.append(m)) for m in ('image', 'text')}
    coordinator = GpuCoordinator(probes, releasers | extra.pop('releasers', {}), free, clock, clock.sleep)
    return coordinator, calls, clock


class CoordinatorTests(unittest.TestCase):
    def test_idle_start_releases_everyone_else_but_itself(self):
        for module, expected in (('image', ['text']), ('video', ['image', 'text']), ('music', ['image', 'text']), ('text', ['image'])):
            with self.subTest(module=module):
                coordinator, calls, _ = make()
                self.assertEqual(coordinator.claim(module), expected)
                self.assertEqual(calls, expected)

    def test_refuses_while_another_module_works_and_frees_nothing(self):
        for working in MODULES:
            for module in MODULES:
                if module == working:
                    continue
                with self.subTest(working=working, start=module):
                    coordinator, calls, _ = make(busy=(working,))
                    with self.assertRaisesRegex(Busy, 'GPU занят'):
                        coordinator.claim(module)
                    self.assertEqual(calls, [])

    def test_a_module_does_not_block_itself(self):
        coordinator, _, _ = make(busy=('video',))
        self.assertEqual(coordinator.claim('video'), ['image', 'text'])

    def test_message_names_every_working_module(self):
        coordinator, _, _ = make(busy=('image', 'music'))
        with self.assertRaisesRegex(Busy, 'Image и Music'):
            coordinator.claim('text')

    def test_waits_for_memory_and_gives_up_with_numbers(self):
        readings = iter([4000, 9000, 15000])
        coordinator, _, clock = make(free=lambda: next(readings))
        coordinator.claim('video', need_vram_mb=12000)
        self.assertEqual(clock.now, 1.0)
        coordinator, _, clock = make(free=lambda: 4096)
        with self.assertRaisesRegex(Busy, 'не менее 12.0 ГБ, свободно 4.0 ГБ'):
            coordinator.claim('video', need_vram_mb=12288, wait=3)
        self.assertGreaterEqual(clock.now, 3)

    def test_unreadable_memory_is_a_separate_error(self):
        def broken():
            raise OSError('nvidia-smi missing')
        coordinator, _, _ = make(free=broken)
        with self.assertRaises(GpuUnavailable):
            coordinator.claim('music', need_vram_mb=8000)
        self.assertEqual(make(free=broken)[0].claim('image'), ['text'])

    def test_failing_release_or_probe_never_blocks_a_start(self):
        def explode():
            raise OSError('ComfyUI is not running')
        coordinator = GpuCoordinator(dict(image=explode, video=lambda: False), dict(image=explode, text=lambda: None))
        self.assertEqual(coordinator.claim('video'), ['text'])
        self.assertEqual(coordinator.snapshot(), dict(busy=False, modules=[], labels=[]))

    def test_a_release_that_finds_text_answering_stops_the_start(self):
        def refuse():
            raise Busy('GPU занят Text: идёт ответ модели.')
        coordinator, _, _ = make(releasers=dict(text=refuse))
        with self.assertRaisesRegex(Busy, 'Text'):
            coordinator.claim('video')

    def test_snapshot_and_unknown_module(self):
        coordinator, _, _ = make(busy=('music', 'text'))
        self.assertEqual(coordinator.snapshot(), dict(busy=True, modules=['music', 'text'], labels=['Music', 'Text']))
        with self.assertRaises(ValueError):
            coordinator.claim('audio')


if __name__ == '__main__':
    unittest.main()
