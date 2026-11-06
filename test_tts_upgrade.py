"""Focused TTS text, voice and config tests. No models, SDKs or cloud calls."""
import io
import re
import struct
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np

from tts_text_normalizer import normalize_tts_text, remaining_latin_count
from voice_settings import VoiceSettings
from cancel_control import CancelControl
import test_command_states as states
import test_session_runtime as sessions
import test_local_config as configs


class NormalizerTests(unittest.TestCase):
    def test_all_requested_terms(self):
        source = 'Python GitHub Windows ChatGPT NVIDIA API Wi-Fi ESP32 USB CPU GPU RAM TCP HTTP JSON'
        expected = 'Пайтон Гитхаб Виндоус Чат Джи-Пи-Ти Энвидиа Эй-пи-ай Вай-фай И-Эс-Пэ тридцать два Ю-Эс-Би Си-Пи-Ю Джи-Пи-Ю Рэм Ти-Си-Пи Эйч-Ти-Ти-Пи Джейсон'
        self.assertEqual(normalize_tts_text(source), expected)

    def test_case_punctuation_and_word_boundaries(self):
        self.assertEqual(normalize_tts_text('python, GITHUB! WINDOWS?'), 'Пайтон, Гитхаб! Виндоус?')
        for text in ['myPython', 'Pythonic', 'Python3', 'API_KEY', 'GitHubClient', '--API', 'ESP32-S3', 'ESP32_S3', 'ПайтонPython']:
            self.assertEqual(normalize_tts_text(text), text)

    def test_idempotent_and_original_preserved(self):
        original = 'Python и Гитхаб, API.'
        once = normalize_tts_text(original)
        self.assertEqual(normalize_tts_text(once), once)
        self.assertEqual(original, 'Python и Гитхаб, API.')

    def test_unknown_latin_retained_and_counted(self):
        self.assertEqual(normalize_tts_text('UnknownXYZ и RESTful'), 'UnknownXYZ и RESTful')
        self.assertEqual(remaining_latin_count(normalize_tts_text('Python UnknownXYZ')), 1)

    def test_urls_emails_paths_and_files_are_literal(self):
        protected = ['https://GitHub.com/API/Python?q=JSON', 'www.GitHub.com/Windows',
                     'Python@GitHub.example', r'C:\Python\API\Windows.json',
                     r'\\server\Python\GitHub', '/home/Python/API.json', '~/Python/JSON',
                     './Python/API', 'Python.py', 'obj.API', 'HTTP/2']
        for text in protected:
            with self.subTest(text=text): self.assertEqual(normalize_tts_text(text), text)

    def test_code_and_commands_preserved(self):
        texts = ['`Python API`', '```python\nprint("GitHub API")\n```',
                 '```Python API', 'python -m JSON.tool', 'git clone https://GitHub.com/API',
                 'pip install Python', 'curl -H API https://GitHub.com',
                 'powershell -Command "Python API"', 'voice eugene', 'npm install JSON']
        for text in texts:
            with self.subTest(text=text): self.assertEqual(normalize_tts_text(text), text)

    def test_normal_prose_outside_protected_span_changes(self):
        self.assertEqual(normalize_tts_text('Python и `API` на Windows.'), 'Пайтон и `API` на Виндоус.')

    def test_empty_disabled_and_long(self):
        self.assertEqual(normalize_tts_text(''), '')
        self.assertEqual(normalize_tts_text('Python API', enabled=False), 'Python API')
        self.assertEqual(normalize_tts_text('Python ' * 10000), 'Пайтон ' * 10000)

    def test_dictionary_extension(self):
        self.assertEqual(normalize_tts_text('Rust Python', terms={'Rust':'Раст'}), 'Раст Python')


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.settings = VoiceSettings('v4_ru', ['aidar','eugene','kseniya','random'])

    def test_list_status_and_invalid_voice(self):
        log = Mock()
        self.settings.command('list', log)
        self.assertIn('eugene', log.call_args.args[0])
        self.assertNotIn('random', log.call_args.args[0])
        self.settings.command('status', log)
        self.assertIn('v4_ru', log.call_args.args[0])
        self.settings.command('invalid-sensitive-input', log)
        self.assertNotIn('invalid-sensitive-input', log.call_args.args[0])
        self.assertEqual(self.settings.snapshot(), 'aidar')

    def test_invalid_initial_and_random_rejected(self):
        for voice in ['missing','random']:
            with self.assertRaises(ValueError): VoiceSettings('v4_ru',['aidar','random'],voice)

    def test_switch_without_model_reload(self):
        self.settings.command('EUGENE', Mock())
        self.assertEqual(self.settings.snapshot(), 'eugene')
        self.assertEqual(self.settings.model_name, 'v4_ru')

    def test_console_voice_and_cancel_share_reader(self):
        log, session = Mock(), Mock()
        session.command_id = 'test-command'
        control = CancelControl(log, io.StringIO('voice list\nvoice eugene\ncancel\nVOICE status\nvoice missing\n'), voices=self.settings)
        control.attach(session)
        control.run()
        session.request_cancel.assert_called_once_with(expected_id='test-command')
        self.assertEqual(self.settings.snapshot(), 'eugene')
        session.sendall.assert_not_called()

    def test_concurrent_selection_keeps_valid_snapshot(self):
        start = threading.Barrier(3)
        def select(voice):
            start.wait()
            for _ in range(200):
                self.settings.command(voice, lambda _:None)
                self.assertIn(self.settings.snapshot(), ['aidar','eugene','kseniya'])
        threads = [threading.Thread(target=select,args=(voice,)) for voice in ['eugene','kseniya']]
        for t in threads:t.start()
        start.wait()
        for t in threads:t.join(2)
        self.assertTrue(all(not t.is_alive() for t in threads))

    def test_actual_sender_freezes_voice_for_whole_response(self):
        calls = []
        def synth(**kwargs):
            calls.append(kwargs)
            if len(calls)==1:self.settings.command('eugene',Mock())
            return NS(numpy=lambda: np.array([.1],dtype=np.float32))
        log=Mock(); model=NS(apply_tts=synth)
        sender=states.load_function('send_audio_to_esp32',dict(
            np=np,re=re,struct=struct,time=NS(time=lambda:0,sleep=lambda _:None),
            log=log,silero_model=model,voice_settings=self.settings))
        conn=Mock(); source='Python. GitHub.'
        self.assertTrue(sender(conn,source))
        self.assertEqual([x['speaker'] for x in calls],['aidar','aidar'])
        self.assertEqual([x['text'] for x in calls],['Пайтон.','Гитхаб.'])
        self.assertEqual(source,'Python. GitHub.')
        self.assertTrue(sender(conn,'Windows'))
        self.assertEqual(calls[-1]['speaker'],'eugene')
        self.assertTrue(all(x['sample_rate']==24000 for x in calls))

    def test_sender_unknown_term_diagnostic_has_no_text(self):
        log=Mock()
        sender=states.load_function('send_audio_to_esp32',dict(
            np=np,re=re,struct=struct,time=NS(time=lambda:0,sleep=lambda _:None),log=log,
            silero_model=NS(apply_tts=lambda **kwargs:NS(numpy=lambda:np.array([.1])))))
        self.assertTrue(sender(Mock(),'SensitiveUnknownXYZ'))
        self.assertNotIn('SensitiveUnknownXYZ',' '.join(str(x.args) for x in log.call_args_list))
        self.assertTrue(any('Latin fragments retained' in x.args[0] for x in log.call_args_list))


class ConfigAndPromptTests(unittest.TestCase):
    def test_version_voice_normalization_config(self):
        helper=configs.ConfigTests()
        c=helper.load('GROQ_API_KEY=synthetic\nTTS_MODEL=v5_6_ru\nTTS_VOICE=Eugene\nTTS_NORMALIZE_EN=false')
        self.assertEqual((c.tts_model,c.tts_voice,c.tts_normalize_en),('v5_6_ru','eugene',False))
        c=helper.load('GROQ_API_KEY=synthetic')
        self.assertEqual(c.tts_model,'v5_6_ru')

    def test_explicit_v4_rollback_overrides_default(self):
        helper=configs.ConfigTests()
        c=helper.load('GROQ_API_KEY=synthetic\nTTS_MODEL=v4_ru\nTTS_VOICE=aidar\nTTS_NORMALIZE_EN=false')
        self.assertEqual((c.tts_model,c.tts_voice,c.tts_normalize_en),('v4_ru','aidar',False))
        c=helper.load('GROQ_API_KEY=synthetic', {'TTS_MODEL':'v4_ru'})
        self.assertEqual(c.tts_model,'v4_ru')

    def test_invalid_tts_config(self):
        for line in ['TTS_MODEL=other','TTS_VOICE=bad value','TTS_NORMALIZE_EN=maybe']:
            with self.assertRaises(ValueError): configs.ConfigTests().load('GROQ_API_KEY=synthetic\n'+line)

    def test_actual_groq_call_contains_prompt_and_history_stays_original(self):
        process,env,stt,llm,tts=sessions.SessionTests().pipeline()
        response='Python API https://GitHub.example/API'
        llm.return_value=NS(choices=[NS(message=NS(content=response))])
        process(Mock(),states.FRAME,{'mode':'PROCESSING','session_id':'offline'})
        prompt=llm.call_args.kwargs['messages'][0]['content']
        for phrase in ['Декстер','инженерные','Markdown','фонетической','Не изменяй код','неизвестных']:
            self.assertIn(phrase,prompt)
        self.assertEqual(env['CHAT_HISTORY'][-1]['content'],response)
        self.assertEqual(tts.call_args.args[1],response)


if __name__=='__main__':unittest.main()
