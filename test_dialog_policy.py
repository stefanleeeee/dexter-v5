"""Verify actual request instructions and explicit unsupported follow-up; no API."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from local_config import followup_status
from test_command_states import FRAME
import test_dialog_memory as memories
import test_local_config as configs


class PolicyTests(unittest.TestCase):
    def request(self, text, streaming=False):
        process, env, stt, llm, tts = memories.IntegrationTests().pipeline(streaming)
        stt.return_value = NS(text=text)
        process(Mock(), FRAME, {'mode': 'PROCESSING'})
        messages = env['stream_reply'].call_args.args[2] if streaming else llm.call_args.kwargs['messages']
        return messages, env, stt, llm, tts

    def test_fact_question_and_topic_have_voice_length_guidance(self):
        for question in ['Сколько дней в неделе?', 'Почему небо голубое?', 'Расскажи про Марс']:
            messages, *_ = self.request(question)
            prompt = messages[0]['content']
            for expected in ['простой факт — одно-два', 'обычный вопрос — примерно два-четыре',
                             'объяснение темы — примерно три-шесть', 'не жёсткие ограничения']:
                self.assertIn(expected, prompt)
            self.assertEqual(messages[-1]['content'], question)

    def test_explicit_detail_count_shorter_and_continuation_reach_model(self):
        for question in ['Подробно расскажи про Марс', 'Расскажи в десяти предложениях',
                         'А подробнее?', 'Продолжай', 'Скажи короче', 'В двух словах']:
            messages, env, stt, llm, _ = self.request(question)
            prompt = messages[0]['content']
            self.assertIn('указанное пользователем число предложений', prompt)
            self.assertIn('«подробнее», «продолжай», «расскажи больше»', prompt)
            self.assertIn('«короче», «в двух словах», «кратко»', prompt)
            self.assertEqual(messages[-1]['content'], question)
            stt.assert_called_once(); llm.assert_called_once()
            self.assertEqual(llm.call_args.kwargs['max_completion_tokens'], 2048)

    def test_uncertainty_clarification_and_natural_speech_instructions(self):
        messages, *_ = self.request('Когда это было?')
        prompt = messages[0]['content']
        for text in ['разговорным русским', 'неоднозначен', 'уточни',
                     'Не придумывай точные числа, даты и характеристики',
                     'не заменяй неизвестные сведения вымышленными',
                     'Не повторяй длинные вступления и заключения']:
            self.assertIn(text, prompt)

    def test_existing_phonetic_and_literal_rules_preserved(self):
        messages, *_ = self.request('Python API')
        prompt = messages[0]['content']
        for text in ['Python — Пайтон', 'GitHub — Гитхаб', 'Windows — Виндоус',
                     'Не изменяй код, URL, email, пути, команды', 'Не выдумывай произношение неизвестных',
                     'Запрещено использовать Markdown', 'Все числа пиши словами']:
            self.assertIn(text, prompt)

    def test_previous_topic_and_new_prompt_used_in_both_modes(self):
        for streaming in [False, True]:
            p, env, stt, llm, tts = memories.IntegrationTests().pipeline(streaming)
            memories.add(env['dialog_memory'], 'Расскажи про Марс', 'Марс — планета.')
            stt.return_value = NS(text='А подробнее?')
            p(Mock(), FRAME, {'mode': 'PROCESSING'})
            messages = env['stream_reply'].call_args.args[2] if streaming else llm.call_args.kwargs['messages']
            self.assertEqual(messages[1:3], [{'role':'user','content':'Расскажи про Марс'},
                                           {'role':'assistant','content':'Марс — планета.'}])
            self.assertIn('примерно три-шесть', messages[0]['content'])
            self.assertEqual(messages[-1]['content'], 'А подробнее?')
            self.assertEqual((env['stream_reply'] if streaming else llm).call_count, 1)
            stt.assert_called_once()

    def test_no_post_generation_truncation_or_budget_change(self):
        text = 'Полное предложение. ' * 80
        for streaming in [False, True]:
            p, env, _, llm, tts = memories.IntegrationTests().pipeline(streaming)
            llm.return_value = NS(choices=[NS(message=NS(content=text), finish_reason='stop')])
            env['stream_reply'].return_value = (text, {'outcome': 'done'})
            p(Mock(), FRAME, {'mode': 'PROCESSING'})
            if not streaming:
                self.assertEqual(tts.call_args.args[1], text)
                self.assertEqual(llm.call_args.kwargs['max_completion_tokens'], 2048)
            else:
                self.assertEqual(env['stream_reply'].call_args.kwargs['options']['max_completion_tokens'], 2048)
            self.assertTrue(any(text in str(call.args) for call in env['log'].call_args_list))

    def test_empty_stt_makes_no_llm_playback_or_memory_in_both_modes(self):
        for streaming in [False, True]:
            for text in ['', ' \n\t']:
                p, env, stt, llm, tts = memories.IntegrationTests().pipeline(streaming)
                stt.return_value = NS(text=text)
                p(Mock(), FRAME, {'mode':'PROCESSING'})
                llm.assert_not_called();tts.assert_not_called();env['stream_reply'].assert_not_called()
                self.assertEqual(env['dialog_memory'].status()['memory_turns'], 0)

    def test_stt_error_makes_no_llm_playback_or_memory(self):
        p, env, stt, llm, tts = memories.IntegrationTests().pipeline()
        stt.side_effect = RuntimeError('synthetic STT failure')
        p(Mock(), FRAME, {'mode':'PROCESSING'})
        llm.assert_not_called();tts.assert_not_called()
        self.assertEqual(env['dialog_memory'].status()['memory_turns'], 0)


class FollowupGateTests(unittest.TestCase):
    def test_default_request_is_explicitly_blocked_not_fake_support(self):
        config = configs.ConfigTests().load('GROQ_API_KEY=synthetic')
        status = followup_status(config)
        self.assertEqual(status, dict(requested=True, supported=False, active=False,
                                     timeout_s=12.0, reason='awaiting_firmware_capability'))

    def test_disabled_gate_and_environment_override(self):
        config = configs.ConfigTests().load('GROQ_API_KEY=synthetic\nDIALOG_FOLLOWUP_ENABLED=true',
                                           {'DIALOG_FOLLOWUP_ENABLED':'0','DIALOG_FOLLOWUP_TIMEOUT_S':'8.5'})
        status = followup_status(config)
        self.assertFalse(status['active']);self.assertFalse(status['requested'])
        self.assertEqual(status['reason'], 'disabled');self.assertEqual(status['timeout_s'], 8.5)

    def test_configuration_rejects_invalid_infinite_and_unbounded_waits(self):
        for key, values in {'DIALOG_FOLLOWUP_ENABLED':['yes','maybe',''],
                            'DIALOG_FOLLOWUP_TIMEOUT_S':['0','-1','61','inf','nan','bad','']}.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    configs.ConfigTests().load('GROQ_API_KEY=synthetic\n'+key+'='+value)

    def test_startup_requires_per_connection_confirmation(self):
        tree = ast.parse(Path('server.py').read_text(encoding='utf-8'))
        logs = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value,str)]
        self.assertTrue(any('no window before fresh capture confirmation' in text for text in logs))

    def test_receiver_uses_existing_session_for_followup(self):
        tree = ast.parse(Path('server.py').read_text(encoding='utf-8'))
        receiver = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='start_server')
        source = ast.unparse(receiver)
        self.assertIn("state['mode'] == 'PROCESSING'", source)
        self.assertIn("state['mode'] == 'WAKE_WORD'", source)
        self.assertIn('session.followup_speech', source)


if __name__ == '__main__':
    unittest.main()
