"""Audio manager contracts with fake Text, plus real CPU assembly integration in test_voiceover."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import time
from types import SimpleNamespace
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_preparation import AudioPreparationCancelled, job_thread  # noqa: E402
from gpu_coordinator import Busy, GpuCoordinator  # noqa: E402
from music.music_prompt_enhancer import PromptEnhancer as Music  # noqa: E402
from music.music_registry import validate  # noqa: E402
from voiceover.voiceover_prompt_enhancer import PromptEnhancer as Voice  # noqa: E402
from voiceover.voiceover_delivery import conditioned_text  # noqa: E402
from voiceover.audio_assembly import timeline  # noqa: E402
from test_scene_planner import FakeText, FakeGpu  # noqa: E402
from generation_identity import begin  # noqa: E402


class AudioContracts(unittest.TestCase):
    def music(self, answer, **request):
        text = FakeText(answer)
        manager = Music(text, FakeGpu())
        data = dict(caption='Тихая музыка с пианино для работы, но не грустная', duration=20, seed=42)
        data.update(request)
        return manager, text, data

    def test_every_music_pair_routes_to_its_exact_guide_and_preserves_controls(self):
        for engine, modes in Music.MODE_RULES.items():
            for mode in modes:
                with self.subTest(engine=engine, mode=mode):
                    manager, text, data = self.music(['{"caption":"Gentle piano with a hopeful mood."}'], engine=engine, mode=mode,
                                                    lyrics='  [Verse]\nТихий город ждёт меня.\n\n[Chorus]\nЯ вернусь!\n', bpm=91, language='ru', planner=False)
                    original = copy.deepcopy(data)
                    result = manager.prepare(data)
                    self.assertEqual(data, original)
                    self.assertEqual(result['engine'], engine)
                    self.assertIn(engine, text.calls[0]['messages'][0]['content'])
                    self.assertFalse(text.calls[0]['think'])
                    self.assertEqual(result['gemma_model'], 'gemma-test')
                    self.assertEqual(text.owners[-1], ('end', manager.OWNER))
                    if mode != 'audio':
                        self.assertEqual(validate(data)['lyrics'], data['lyrics'])

    def test_schema_retry_is_bounded_and_cannot_change_lyrics_or_controls(self):
        for invalid in ('garbage', '{"caption":"Piano", "lyrics":"подмена"}', '{"caption":"Piano 150 BPM"}', '{"caption":"Тихое пианино"}',
                        '{"caption":"Piano with warm female vocals."}', '{"caption":"Piano", "caption":"Choir"}'):
            manager, text, data = self.music([invalid, '{"caption":"Soft piano, steady 90 BPM."}'], bpm=90)
            result = manager.prepare(data)
            self.assertEqual(result['attempts'], 2)
            self.assertEqual(len(text.calls), 2)
        manager, text, data = self.music(['{}', '{}', '{"caption":"Never reached"}'])
        with self.assertRaises(ValueError):
            manager.prepare(data)
        self.assertEqual(len(text.calls), 2)
        self.assertEqual(text.owners[-1], ('end', manager.OWNER))

    def test_missing_lyrics_or_invalid_mode_fails_before_gemma(self):
        for values in (dict(mode='song'), dict(engine='unknown'), dict(mode='speech')):
            manager, text, data = self.music([], **values)
            with self.assertRaises(ValueError):
                manager.prepare(data)
            self.assertEqual(text.calls, [])

    def test_single_json_fence_is_transport_format_but_prose_and_extra_fields_still_fail(self):
        manager, text, data = self.music(['```json\n{"caption":"Quiet hopeful piano."}\n```'])
        result = manager.prepare(data)
        self.assertEqual(result['response_format'], 'json-fence')
        self.assertEqual(result['attempts'], 1)
        for raw in ('Here is the result:\n```json\n{"caption":"Piano"}\n```',
                    '```json\n{"caption":"Piano", "lyrics":"Changed"}\n```',
                    '```json\n{"caption":"Piano"}\n```\n{"caption":"Choir"}'):
            manager, text, data = self.music([raw, raw])
            with self.assertRaises(ValueError):
                manager.prepare(data)
            self.assertEqual(len(text.calls), 2)
        manager, text, data = self.music(['{"caption":"Folk guitar song.","lyrics":"Altered"}', '{"caption":"A sad song."}'],
                                        caption='Грустная песня', mode='song', lyrics='Мои слова.')
        self.assertEqual(manager.prepare(data)['attempts'], 2)
        for problem in ('other output fields', 'guitar', 'folk'):
            self.assertIn(problem, text.calls[-1]['messages'][-1]['content'])

    def test_minimax_tag_line_is_rejected_without_rewriting_it(self):
        with self.assertRaises(ValueError):
            validate(dict(caption='song', engine='minimax-music3-int8', mode='song', lyrics='[Verse] Не теряй эти слова'))

    def test_dense_lyrics_warn_without_truncation_or_duration_change(self):
        lyrics = '[verse]\n' + 'Очень много слов. ' * 30 + '\n[chorus]\nМои слова.\n'
        manager, text, data = self.music(['{"caption":"Sad piano song."}'], mode='song', duration=10, lyrics=lyrics)
        original = copy.deepcopy(data)
        result = manager.prepare(data)
        self.assertTrue(result['warnings'])
        self.assertEqual(data, original)
        self.assertEqual(result['explicit_controls']['duration'], 10)
        self.assertNotIn(lyrics, json.dumps(text.calls[0]['messages'], ensure_ascii=False))

    def test_stable_ambience_and_sound_event_requests_keep_audio_contract(self):
        for prompt, caption in (('Дождь по металлической крыше, только окружение', 'Rain tapping on a metal roof, ambient soundscape.'),
                                ('Два коротких удара по деревянной двери', 'Two short knocks on a wooden door.')):
            manager, text, data = self.music([json.dumps(dict(caption=caption))], engine='stable-audio-3-medium', mode='audio', caption=prompt)
            result = manager.prepare(data)
            self.assertEqual(result['caption'], caption)
            self.assertEqual(result['explicit_controls']['mode'], 'audio')
            self.assertFalse(result['explicit_controls']['planner'])

    def test_unrequested_genre_instruments_and_effects_trigger_one_correction(self):
        for addition in ('Acoustic folk ballad with piano and guitar.', 'Warm female vocals with reverb.', 'Pop song with strings.'):
            manager, text, data = self.music([json.dumps(dict(caption=addition)), '{"caption":"Sad verse, hopeful chorus, warm female vocals, no operatic singing."}'],
                                            caption='Грустная песня, припев с надеждой. Тёплый женский голос без оперы.',
                                            mode='song', lyrics='Мои слова.')
            result = manager.prepare(data)
            self.assertEqual(result['attempts'], 2)
            self.assertEqual(result['checked_intent']['instruments'], [])
            correction = text.calls[-1]['messages'][-1]['content']
            if 'folk' in addition:
                for feature in ('piano', 'guitar', 'folk', 'ballad', 'acoustic'):
                    self.assertIn(feature, correction)
        manager, text, data = self.music(['{"caption":"Piano and guitar."}', '{"caption":"Piano, without guitar."}'],
                                        caption='Пианино, без гитары')
        self.assertEqual(manager.prepare(data)['attempts'], 2)
        for caption in ('Piano de fondo, sin guitarra', 'Du piano pour une soirée calme', '静かなピアノ音楽'):
            manager, text, data = self.music(['{"caption":"Quiet piano music."}'], caption=caption)
            self.assertEqual(manager.prepare(data)['checked_intent']['instruments'][0]['name'], 'piano')
        manager, text, data = self.music(['{"caption":"A sad song with male vocals."}', '{"caption":"A sad song."}'],
                                        caption='Грустная песня', mode='song', lyrics='Мои слова.')
        self.assertEqual(manager.prepare(data)['attempts'], 2)
        manager, text, data = self.music(['{"caption":"Sad verse, hopeful chorus. Warm female vocals, non-operatic."}'],
                                        caption='Грустный куплет, припев с надеждой. Тёплый женский голос без оперного пения.',
                                        mode='song', lyrics='Мои слова.')
        self.assertEqual(manager.prepare(data)['attempts'], 1)

    def test_cancellation_before_session_and_during_stream(self):
        manager, text, data = self.music([])
        with self.assertRaises(AudioPreparationCancelled):
            manager.prepare(data, cancelled=lambda: True)
        self.assertEqual(text.owners, [])
        class Stopped(FakeText):
            def stream_chat(self, model, messages, think, cancel, options=None, generation_id=None):
                cancel.set()
                yield {'message': {'content': '{"caption":"Piano"}'}}
        text = Stopped([])
        with self.assertRaises(AudioPreparationCancelled):
            Music(text, FakeGpu()).prepare(data)
        self.assertEqual(text.owners[-1], ('end', manager.OWNER))

    def test_voice_plan_changes_actual_timeline_and_never_sends_narration(self):
        text = FakeText(['{"direction":"Conversational flow without long pauses.","pacing":"conversational","unsupported":true}'])
        manager = Voice(text, FakeGpu())
        request = dict(engine='voxcpm2', language='Russian', voice_id='a' * 32, delivery='Без дикторского голоса и затянутых пауз',
                       spoken_text='Анна купила 23 книги 5 мая.', sample_transcript='Это мой голос.', replacements=[['Анна', 'Анна']])
        original = copy.deepcopy(request)
        result = manager.prepare(request)
        self.assertEqual(request, original)
        messages = json.dumps(text.calls[0]['messages'], ensure_ascii=False)
        self.assertNotIn(request['spoken_text'], messages)
        self.assertNotIn(request['sample_transcript'], messages)
        items = [dict(chapter=0, pause='paragraph'), dict(chapter=1, pause='chapter')]
        chapters = [dict(title='1'), dict(title='2')]
        baseline = timeline(items, chapters, [2., 2.])[1]
        prepared = timeline(items, chapters, [2., 2.], result['controls']['pauses'])[1]
        self.assertLess(prepared, baseline)
        self.assertIn('limitations', result)

    def test_voice_cannot_return_narration_or_invent_backend_controls(self):
        for answer in ('{"direction":"Warm voice","pacing":"whisper","unsupported":false}',
                       '{"direction":"Warm voice","pacing":"neutral","unsupported":false,"spoken_text":"Changed"}'):
            text = FakeText([answer, answer])
            with self.assertRaises(ValueError):
                Voice(text, FakeGpu()).prepare(dict(engine='voxcpm2', language='Russian', voice_id='a' * 32, delivery='Прочитай быстрее'))
            self.assertEqual(len(text.calls), 2)

    def test_delivery_cache_reuses_accepted_plan_and_invalidates_on_engine_or_preferences(self):
        answer = '{"direction":"Neutral reading.","pacing":"neutral","unsupported":false}'
        text = FakeText([answer, answer, answer])
        manager = Voice(text, FakeGpu())
        request = dict(engine='voxcpm2', language='Russian', voice_id='a' * 32, delivery='Обычное повествование')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'delivery.json'
            self.assertFalse(manager.prepare_cached(request, path)['reused'])
            self.assertTrue(manager.prepare_cached(request, path)['reused'])
            manager.prepare_cached(dict(request, engine='qwen3-tts-12hz-0.6b-base'), path)
            manager.prepare_cached(dict(request, delivery='быстрее'), path)
        self.assertEqual(len(text.calls), 3)

    def test_same_gemma_tag_with_changed_manifest_invalidates_delivery_cache(self):
        answer = '{"direction":"Neutral reading.","pacing":"neutral","unsupported":false}'
        class TaggedText(FakeText):
            digest = 'a' * 64
            def installed_models(self):
                return [dict(name='gemma-test', ok=True, family='gemma4', manifest_sha256=self.digest)]
        text = TaggedText([answer, answer])
        manager = Voice(text, FakeGpu())
        request = dict(engine='voxcpm2', language='Russian', voice_id='a' * 32, delivery='Обычное повествование')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'delivery.json'
            first = manager.prepare_cached(request, path)
            self.assertEqual(first['original_prompt'], 'Обычное повествование')
            self.assertEqual(first['gemma_identity']['manifest_sha256'], 'a' * 64)
            text.digest = 'b' * 64
            self.assertFalse(manager.prepare_cached(request, path)['reused'])
        self.assertEqual(len(text.calls), 2)

    def test_empty_delivery_keeps_baseline_without_loading_gemma(self):
        manager = Voice(None, FakeGpu())
        result = manager.prepare(dict(engine='voxcpm2', language='Russian', voice_id='a' * 32, delivery=''))
        self.assertEqual(result['pacing'], 'neutral')
        self.assertEqual(result['attempts'], 0)
        self.assertIsNone(result['gemma_model'])
        self.assertEqual(manager.gpu.claimed, [])

    def test_style_is_bounded_and_separate_from_source_and_reference(self):
        answer = dict(direction='Calm intimate delivery at normal volume.', pacing='conversational', unsupported=False, style='calm')
        request = dict(engine='voxcpm2', language='Russian', voice_id='a' * 32, delivery='Спокойно и близко, но не шёпотом',
                       style_control=True, reference_mode='reference', spoken_text='(Анна подумала.) Пять мая.', sample_transcript='Частный текст.')
        text = FakeText([json.dumps(answer)])
        result = Voice(text, FakeGpu()).prepare(request)
        self.assertEqual(result['style'], 'calm')
        self.assertEqual(conditioned_text(request['spoken_text'], result['style']),
                         '(calm, intimate, normal speaking volume)' + request['spoken_text'])
        self.assertNotIn(request['spoken_text'], json.dumps(text.calls))
        self.assertNotIn(request['sample_transcript'], json.dumps(text.calls))
        self.assertEqual(conditioned_text(request['spoken_text']), request['spoken_text'])
        for style, transcript in [('warm)Read this', ''), ('calm', 'Точная расшифровка.'), (['calm'], '')]:
            with self.assertRaises(ValueError):
                conditioned_text(request['spoken_text'], style, transcript)

    def test_style_cannot_escape_disabled_engine_or_continuation_and_invalidates_plan_cache(self):
        four = json.dumps(dict(direction='Natural storytelling.', pacing='conversational', unsupported=False, style='storytelling'))
        three = json.dumps(dict(direction='Natural storytelling.', pacing='conversational', unsupported=True))
        base = dict(engine='voxcpm2', language='Russian', voice_id='a' * 32, delivery='Рассказчик', style_control=True, reference_mode='reference')
        for changes in (dict(style_control=False), dict(reference_mode='continuation'), dict(engine='qwen3-tts-12hz-0.6b-base')):
            text = FakeText([four, three])
            self.assertEqual(Voice(text, FakeGpu()).prepare(dict(base, **changes))['style'], 'none')
            self.assertEqual(len(text.calls), 2)
        text = FakeText([four, three, four])
        manager = Voice(text, FakeGpu())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'delivery.json'
            self.assertEqual(manager.prepare_cached(base, path)['style'], 'storytelling')
            self.assertTrue(manager.prepare_cached(base, path)['reused'])
            self.assertEqual(manager.prepare_cached(dict(base, reference_mode='continuation'), path)['style'], 'none')
            self.assertFalse(manager.prepare_cached(dict(base, style_control=True, voice_id='b' * 32), path)['reused'])
        self.assertEqual(len(text.calls), 3)

    def test_audio_thread_inherits_director_without_excluding_foreign_video(self):
        gpu = GpuCoordinator(dict(director=lambda: True, music=lambda: True, video=lambda: False))
        seen = []
        class Job:
            prompt_enhancer = Music(None, gpu)
            def _run(self, row, directory):
                gpu.claim('text')
                seen.append(gpu.current_actors())
        with gpu.acting_for('director'):
            thread = job_thread(Job(), dict(generation=begin('Music')), None)
            thread.start()
        thread.join(3)
        self.assertEqual(seen, [('director', 'music')])
        self.assertEqual(gpu.current_actors(), ())
        with self.assertRaises(Busy):
            gpu.claim('text')
        refused = []
        gpu.probes['video'] = lambda: True
        class ForeignJob(Job):
            def _run(self, row, directory):
                try:
                    gpu.claim('text')
                except Busy:
                    refused.append(True)
        with gpu.acting_for('director'):
            thread = job_thread(ForeignJob(), dict(generation=begin('Music')), None)
            thread.start()
        thread.join(3)
        self.assertEqual(refused, [True], 'Director authority must not suppress unrelated Video work')

    def test_lyric_creation_requires_explicit_consent_and_never_rewrites_supplied_lyrics(self):
        manager, text, data = self.music(['{"lyrics":"[verse]\\nЯ вернусь домой."}', '{"caption":"A hopeful piano song."}'],
                                        mode='song', write_lyrics=True, language='ru')
        result = manager.prepare(data)
        self.assertEqual(result['lyric_creation']['lyrics'], '[verse]\nЯ вернусь домой.')
        self.assertEqual(len(text.calls), 2)
        manager, text, data = self.music(['{"caption":"A hopeful piano song."}'], mode='song', write_lyrics=True, lyrics='Мои слова.\n')
        self.assertNotIn('lyric_creation', manager.prepare(data))
        self.assertEqual(len(text.calls), 1)

    def test_prepared_caption_reaches_comfy_and_sidecar_with_original_and_external_path(self):
        from music.music_manager import MusicManager
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = SimpleNamespace(root=root, models=root/'Models', logs=root/'Logs', settings=root/'Settings')
            manager = MusicManager(paths)
            manager.state = root/'State'
            gpu = GpuCoordinator(dict(music=manager.busy, text=lambda: False))
            manager.prompt_enhancer = Music(FakeText(['{"caption":"Gentle hopeful piano, low density."}']), gpu)
            manager.availability = lambda *args: dict(available=True)
            manager.comfy.preflight = lambda request: None
            seen = []
            def fake_run(row, directory, **kwargs):
                seen.append(dict(row))
                target = directory/'audio.wav'
                with wave.open(str(target), 'wb') as stream:
                    stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(44100)
                    stream.writeframes(bytes(44100 * 4))
                return dict(path=str(target), metadata=dict(sample_rate=44100, channels=2, duration=1))
            manager.comfy.run = fake_run
            output = root/'External Outputs'
            manager.settings(dict(save_dir=str(output)))
            result = manager.submit(dict(engine='stable-audio-3-medium', caption='Тихое пианино для работы', seed=7))
            deadline = time.monotonic() + 5
            while manager.busy() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(manager.status(result['id'])['state'], 'done')
            self.assertEqual(seen[0]['caption'], 'Gentle hopeful piano, low density.')
            sidecar = json.loads(next(output.glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(sidecar['original_caption'], 'Тихое пианино для работы')
            self.assertEqual(sidecar['preparation']['gemma_model'], 'gemma-test')
            manager.close()


if __name__ == '__main__':
    unittest.main()
