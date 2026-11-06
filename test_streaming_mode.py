import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import test_session_runtime as sessions
import test_local_config as configs
from test_command_states import FRAME


class ModeTests(unittest.TestCase):
    def test_default_stream_and_explicit_switch(self):
        load=configs.ConfigTests().load
        self.assertTrue(load('GROQ_API_KEY=synthetic').llm_streaming)
        self.assertTrue(load('GROQ_API_KEY=synthetic\nLLM_STREAMING=true').llm_streaming)
        self.assertFalse(load('GROQ_API_KEY=synthetic\nLLM_STREAMING=0').llm_streaming)
        with self.assertRaises(ValueError):load('GROQ_API_KEY=synthetic\nLLM_STREAMING=maybe')

    def test_old_mode_uses_existing_completion_and_sender(self):
        process,env,stt,llm,tts=sessions.SessionTests().pipeline()
        env['CONFIG']=NS(tts_normalize_en=True,llm_streaming=False)
        process(Mock(),FRAME,{'mode':'PROCESSING'})
        llm.assert_called_once();tts.assert_called_once()
        self.assertNotIn('stream',llm.call_args.kwargs)
        self.assertEqual(env['CHAT_HISTORY'][-1]['content'],'reply')

    def test_stream_mode_passes_frozen_voice_and_commits_original_after_success(self):
        process,env,stt,llm,tts=sessions.SessionTests().pipeline()
        raw='Python и GitHub.'
        env['CONFIG']=NS(tts_normalize_en=True,llm_streaming=True)
        model=Mock();env['silero_model']=model
        runner=Mock(return_value=(raw,{}));env['stream_reply']=runner
        process(Mock(),FRAME,{'mode':'PROCESSING'})
        runner.assert_called_once();llm.assert_not_called();tts.assert_not_called()
        self.assertIs(runner.call_args.args[3],model)
        self.assertEqual(runner.call_args.args[4],'aidar')
        self.assertEqual(env['CHAT_HISTORY'][-1]['content'],raw)
        self.assertTrue(any('[STREAM ОТВЕТ]' in str(c.args) and 'до ACK' in str(c.args) for c in env['log'].call_args_list))

    def test_stream_error_does_not_commit_partial_history(self):
        process,env,stt,llm,tts=sessions.SessionTests().pipeline()
        env.update(CONFIG=NS(tts_normalize_en=True,llm_streaming=True),silero_model=Mock(),
                   stream_reply=Mock(side_effect=RuntimeError('synthetic partial failure')))
        process(Mock(),FRAME,{'mode':'PROCESSING'})
        self.assertEqual(env['CHAT_HISTORY'],[]);tts.assert_not_called()

    def test_stream_empty_has_no_history_or_second_audio_path(self):
        process,env,stt,llm,tts=sessions.SessionTests().pipeline()
        env.update(CONFIG=NS(tts_normalize_en=True,llm_streaming=True),silero_model=Mock(),
                   stream_reply=Mock(return_value=('',{})))
        process(Mock(),FRAME,{'mode':'PROCESSING'})
        self.assertEqual(env['CHAT_HISTORY'],[]);tts.assert_not_called()


if __name__=='__main__':unittest.main()
