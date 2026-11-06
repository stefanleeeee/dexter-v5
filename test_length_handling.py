"""Partial-budget completion, usage and history tests; no real API/model."""
import json
import socket
import struct
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np

from llm_stream import GroqTextStream
from llm_options import completion_options, usage_numbers
from streaming_reply import stream_reply
from session_runtime import CommandSession
import test_llm_stream as llm
import test_streaming_transport as transport
import test_session_runtime as sessions
import test_local_config as configs
from test_command_states import FRAME


def metrics(logs):
    return json.loads(next(x.split('] ',1)[1] for x in reversed(logs) if x.startswith('[STREAM METRICS]')))


class LengthTests(unittest.TestCase):
    def test_length_with_audio_completes_not_cancels(self):
        _,frames,_,results,logs=transport.StreamingTransportTests().exercise('truncated')
        self.assertEqual(results[0][1]['outcome'],'partial_length')
        self.assertEqual(metrics(logs)['finish_reason'],'length')
        self.assertGreater(metrics(logs)['visible_chars'],0)
        self.assertEqual([t for t,_ in frames].count(0x13),1)
        self.assertFalse(any(t==0x15 for t,_ in frames))
        self.assertFalse(any('CANCEL REQUESTED' in x for x in logs))

    def test_length_without_visible_text_no_empty_playback(self):
        _,frames,calls,results,logs=transport.StreamingTransportTests().exercise('empty_length')
        self.assertEqual(frames,[]);self.assertEqual(calls,[])
        m=metrics(logs)
        self.assertEqual(m['outcome'],'empty_length');self.assertEqual(m['visible_chars'],0)
        self.assertIsNone(m['first_pcm_s']);self.assertIsNone(m['ack_s'])
        self.assertEqual(m['usage'],usage_numbers(None))

    def test_unfinished_tail_is_preserved_not_invented(self):
        _,frames,calls,results,logs=transport.StreamingTransportTests().exercise('partial_tail')
        self.assertEqual(results[0][0],'Полученный незавершённый хвост')
        self.assertEqual(calls[0]['text'],'Полученный незавершённый хвост')
        self.assertEqual(metrics(logs)['completed_phrases'],0)
        self.assertEqual(metrics(logs)['phrases'],1)
        self.assertEqual([t for t,_ in frames].count(0x13),1)

    def test_stop_is_done(self):
        *_,logs=transport.StreamingTransportTests().exercise('normal')
        self.assertEqual(metrics(logs)['outcome'],'done')
        self.assertEqual(metrics(logs)['finish_reason'],'stop')

    def test_manual_cancel_is_distinct(self):
        *_,logs=transport.StreamingTransportTests().exercise('cancel_llm')
        self.assertEqual(metrics(logs)['outcome'],'cancelled')
        self.assertEqual(metrics(logs)['cancel_source'],'user_cancel')

    def test_stale_connection_is_distinct(self):
        *_,logs=transport.StreamingTransportTests().exercise('disconnect')
        self.assertEqual(metrics(logs)['outcome'],'cancelled')
        self.assertEqual(metrics(logs)['cancel_source'],'session_inactive')

    def test_user_cancel_origin_survives_later_connection_fallback(self):
        requested,closed,close_observed=threading.Event(),threading.Event(),threading.Event()
        conn=Mock();conn.begin_playback.return_value=b'x'*32
        def check():
            if closed.is_set():raise ConnectionAbortedError()
            if requested.is_set():
                from session_runtime import CommandCancelled
                raise CommandCancelled()
        conn.check_active.side_effect=check
        class Source(llm.FakeStream):
            def close(self):
                super().close();closed.set();close_observed.set()
        source=Source([llm.chunk('Первая фраза. Вторая'),llm.chunk(reason='stop')])
        def send(data):
            requested.set()
            if not close_observed.wait(2):raise RuntimeError('watcher did not observe cancel')
            raise ConnectionAbortedError()
        conn.sendall.side_effect=send;logs=[]
        model=NS(apply_tts=lambda **kw:NS(numpy=lambda:np.array([.1])))
        with self.assertRaises(ConnectionAbortedError):
            stream_reply(conn,Mock(return_value=source),[],model,'aidar',True,logs.append)
        self.assertEqual(metrics(logs)['outcome'],'cancelled')
        self.assertEqual(metrics(logs)['cancel_source'],'user_cancel')

    def test_api_error_still_cancels_partial_audio(self):
        _,frames,_,results,logs=transport.StreamingTransportTests().exercise('api_error')
        self.assertEqual(metrics(logs)['outcome'],'error')
        self.assertEqual(metrics(logs)['cancel_source'],'error_cleanup')
        self.assertEqual(metrics(logs)['error_stage'],'groq')
        self.assertTrue(any(t==0x15 for t,_ in frames));self.assertEqual(results,[])

    def test_usage_only_chunk_and_reasoning_never_becomes_text(self):
        usage=NS(prompt_tokens=13,completion_tokens=23,total_tokens=36,
                 completion_tokens_details=NS(reasoning_tokens=11))
        hidden=NS(choices=[NS(index=0,delta=NS(content=None,reasoning='PRIVATE_REASONING'),finish_reason=None)])
        for final in [NS(choices=[],usage=usage),NS(choices=[],x_groq=NS(usage=usage))]:
            source=llm.FakeStream([hidden,llm.chunk('Видимый текст'),llm.chunk(reason='length'),final])
            with GroqTextStream(Mock(return_value=source),lambda:None,messages=[]) as reader:
                self.assertEqual(''.join(reader),'Видимый текст')
            self.assertEqual(reader.usage,dict(prompt_tokens=13,completion_tokens=23,total_tokens=36,reasoning_tokens=11))
            self.assertNotIn('PRIVATE_REASONING',reader.text)

    def test_missing_or_invalid_usage_stays_null(self):
        self.assertEqual(usage_numbers({'prompt_tokens':True,'completion_tokens':-1,'total_tokens':'unknown'}),usage_numbers(None))

    def test_request_parameters_and_no_incompatible_format(self):
        options=completion_options(streaming=True)
        self.assertEqual(options['max_completion_tokens'],2048)
        self.assertEqual(options['reasoning_effort'],'low')
        self.assertIs(options['include_reasoning'],False)
        self.assertEqual(options['extra_body'],{'stream_options':{'include_usage':True}})
        self.assertNotIn('reasoning_format',options);self.assertNotIn('max_tokens',options)
        with self.assertRaises(ValueError):
            GroqTextStream(Mock(),lambda:None,messages=[],options={'include_reasoning':False,'reasoning_format':'hidden'})

    def test_config_budget_effort_and_full_mode_rollback(self):
        load=configs.ConfigTests().load
        c=load('GROQ_API_KEY=synthetic')
        self.assertEqual((c.llm_streaming,c.llm_max_completion_tokens,c.llm_reasoning_effort,c.llm_include_reasoning),(True,2048,'low',False))
        for limit in [3072,4096]:
            c=load('GROQ_API_KEY=synthetic\nLLM_MAX_COMPLETION_TOKENS='+str(limit)+'\nLLM_STREAMING=false')
            self.assertEqual(c.llm_max_completion_tokens,limit);self.assertFalse(c.llm_streaming)
        for setting in ['LLM_MAX_COMPLETION_TOKENS=0','LLM_MAX_COMPLETION_TOKENS=bad','LLM_REASONING_EFFORT=none','LLM_INCLUDE_REASONING=maybe']:
            with self.assertRaises(ValueError):load('GROQ_API_KEY=synthetic\n'+setting)

    def test_partial_stream_is_readable_and_not_complete_history(self):
        process,env,_,llmcall,tts=sessions.SessionTests().pipeline()
        raw='Python помогает, но предложение не закончено'
        env.update(CONFIG=NS(tts_normalize_en=True,llm_streaming=True),silero_model=Mock(),
                   stream_reply=Mock(return_value=(raw,{'outcome':'partial_length'})))
        process(Mock(),FRAME,{'mode':'PROCESSING'})
        self.assertEqual(env['CHAT_HISTORY'],[]);tts.assert_not_called();llmcall.assert_not_called()
        self.assertTrue(any(raw in str(c.args) and 'ЧАСТИЧНЫЙ' in str(c.args) for c in env['log'].call_args_list))

    def test_full_mode_partial_also_does_not_claim_complete_history(self):
        process,env,_,llmcall,tts=sessions.SessionTests().pipeline()
        env['CONFIG']=NS(tts_normalize_en=True,llm_streaming=False)
        llmcall.return_value=NS(choices=[NS(message=NS(content='Полученный текст'),finish_reason='length')])
        process(Mock(),FRAME,{'mode':'PROCESSING'})
        self.assertEqual(env['CHAT_HISTORY'],[]);tts.assert_called_once()
        options=llmcall.call_args.kwargs
        self.assertEqual(options['max_completion_tokens'],2048)
        self.assertNotIn('extra_body',options);self.assertNotIn('reasoning_format',options)

    def test_partial_then_normal_same_connection_no_old_pcm(self):
        a,b=socket.socketpair();a.settimeout(3);b.settimeout(3)
        logs=[];session=CommandSession(a,logs.append);seen=[];errors=[]
        def exact(sock,n):
            data=bytearray()
            while len(data)<n:
                part=sock.recv(n-len(data))
                if not part:raise EOFError()
                data.extend(part)
            return bytes(data)
        def peer():
            try:
                for _ in range(2):
                    pcm=bytearray();token=None
                    while True:
                        h=exact(b,5);p=exact(b,struct.unpack('>I',h[1:])[0])
                        if h[0]==0x11:token=p
                        elif h[0]==0x12:pcm.extend(p)
                        elif h[0]==0x13:
                            assert p==token;seen.append((token,bytes(pcm)))
                            b.sendall(struct.pack('>BI',0x14,32)+token);break
                        else:raise RuntimeError('Unexpected cancellation')
            except BaseException as e:errors.append(type(e).__name__)
        thread=threading.Thread(target=peer,daemon=True);thread.start()
        results=[]
        try:
            for i,reason in enumerate(['length','stop'],1):
                source=llm.FakeStream([llm.chunk('Фраза '+str(i)),llm.chunk(reason=reason)])
                model=NS(apply_tts=lambda **kw:NS(numpy=lambda:np.full(10,i/10,dtype=np.float32)))
                def work(writer,*args,**kw):results.append(stream_reply(writer,Mock(return_value=source),[],model,'aidar',True,logs.append))
                session.start(work,FRAME)
                h=exact(a,5);token=exact(a,struct.unpack('>I',h[1:])[0]);self.assertTrue(session.receive_playback_ack(token))
                session._worker.join(2);self.assertFalse(session._worker.is_alive());session.poll()
            thread.join(2);self.assertEqual(errors,[])
            self.assertNotEqual(seen[0][0],seen[1][0])
            for i,(_,pcm) in enumerate(seen,1):
                expected=(np.full(10,i/10,dtype=np.float32)*32767).astype('<i2').tobytes()+bytes(12000)
                self.assertEqual(pcm,expected)
            self.assertEqual([r[1]['outcome'] for r in results],['partial_length','done'])
        finally:session.close();b.close();thread.join(2)


if __name__=='__main__':unittest.main()
