"""Offline memory and actual command integration: no SDK/model imports."""
import io
import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from dialog_memory import DialogMemory, estimate_tokens, MODEL_CONTEXT_TOKENS, CONTEXT_RESERVE
from cancel_control import CancelControl
from session_runtime import CommandCancelled
from test_command_states import FRAME
import test_session_runtime as sessions
import test_local_config as configs


def history(memory):
    return memory.prepare('', '')[0][1:-1]


def add(memory, user='question', answer='answer'):
    return memory.commit(memory.version(), user, answer)


class MemoryTests(unittest.TestCase):
    def test_first_then_multiple_followups_original_order(self):
        m=DialogMemory()
        self.assertEqual(m.prepare('system','first')[0],[{'role':'system','content':'system'},{'role':'user','content':'first'}])
        for i in range(3):
            messages,version,_=m.prepare('system',f'q{i}')
            self.assertEqual(len(messages),2+2*i)
            self.assertTrue(m.commit(version,f'q{i}',f'a{i}'))
        self.assertEqual([x['content'] for x in history(m)],['q0','a0','q1','a1','q2','a2'])

    def test_turn_limit_whole_pairs(self):
        m=DialogMemory(max_turns=2)
        for i in range(4):add(m,str(i),'a')
        self.assertEqual([x['content'] for x in history(m)],['2','a','3','a'])
        self.assertEqual(m.status()['memory_evicted_turns'],2)

    def test_token_limit_whole_pairs(self):
        m=DialogMemory(max_tokens=70)
        add(m,'q','a');add(m,'q','a');add(m,'q','a')
        self.assertEqual(m.status()['memory_turns'],2)
        self.assertEqual(m.status()['memory_tokens_est'],68)

    def test_oversized_question_is_skipped_without_erasing_previous_turn(self):
        m=DialogMemory(max_tokens=50);add(m)
        self.assertFalse(add(m,'x'*100,'y'))
        self.assertEqual(history(m),[{'role':'user','content':'question'}, {'role':'assistant','content':'answer'}])
        self.assertEqual(m.status()['memory_last_store'],'skipped_budget')

    def test_disabled_no_storage_or_context(self):
        m=DialogMemory(enabled=False);self.assertFalse(add(m))
        self.assertEqual(len(m.prepare('sys','now')[0]),2)

    def test_clear_invalidates_inflight_and_fresh_turn_works(self):
        m=DialogMemory();add(m);v=m.version()
        self.assertEqual(m.clear(),1);self.assertFalse(m.commit(v,'old','old'))
        self.assertTrue(add(m,'fresh','fresh'));self.assertEqual(len(history(m)),2)

    def test_empty_pairs_rejected(self):
        m=DialogMemory()
        for u,a in [('','a'),('q',''),(' ','a'),('q','  ')]:self.assertFalse(add(m,u,a))
        self.assertEqual(history(m),[])

    def test_context_budget_preserves_current_command(self):
        m=DialogMemory();add(m)
        system='s'*(MODEL_CONTEXT_TOKENS-CONTEXT_RESERVE-2048-32-1)
        messages,_,metrics=m.prepare(system,'q')
        self.assertEqual(messages[-1]['content'],'q');self.assertEqual(len(messages),2)
        self.assertEqual(metrics['memory_turns'],0)
        with self.assertRaises(ValueError):m.prepare(system+'x'*100,'q')

    def test_estimate_byte_bound_and_snapshots_are_detached(self):
        m=DialogMemory();add(m,'Python','GitHub')
        messages,_,_=m.prepare('sys','current');messages[1]['content']='changed'
        self.assertEqual(history(m)[0]['content'],'Python')
        self.assertGreater(estimate_tokens('Марс'),len('Марс'))

    def test_concurrent_snapshot_only_one_commit_and_clear_linearizes(self):
        m=DialogMemory();version=m.version();barrier=threading.Barrier(9);results=[]
        def work(i):
            barrier.wait();results.append(m.commit(version,str(i),'a'))
        threads=[threading.Thread(target=work,args=(i,)) for i in range(8)]
        for t in threads:t.start()
        barrier.wait()
        for t in threads:t.join(2);self.assertFalse(t.is_alive())
        self.assertEqual(sum(results),1);self.assertEqual(m.clear(),1)
        self.assertEqual(history(m),[])

    def test_threaded_clear_status_prepare_do_not_corrupt(self):
        m=DialogMemory(max_turns=3);errors=[]
        def worker():
            try:
                for i in range(100):
                    _,v,_=m.prepare('s','u');m.commit(v,'u','a');m.status()
                    if i%3==0:m.clear()
            except Exception as e:errors.append(type(e).__name__)
        threads=[threading.Thread(target=worker) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join(3);self.assertFalse(t.is_alive())
        self.assertEqual(errors,[]);self.assertLessEqual(m.status()['memory_turns'],3)

    def test_console_commands_do_not_touch_session(self):
        m=DialogMemory();add(m,'private question','private answer');logs=[]
        c=CancelControl(logs.append,stream=io.StringIO('memory status\nmemory clear\nmemory status\n'),memory=m)
        session=Mock();c.attach(session);c.run()
        session.request_cancel.assert_not_called()
        self.assertEqual(m.status()['memory_turns'],0)
        self.assertTrue(any('Cleared 1' in line for line in logs))
        self.assertNotIn('private',str(logs))

    def test_config_defaults_validation_and_env_precedence(self):
        load=configs.ConfigTests().load
        c=load('GROQ_API_KEY=synthetic')
        self.assertEqual((c.dialog_memory_enabled,c.dialog_memory_max_turns,c.dialog_memory_max_tokens),(True,6,1500))
        for key,values in {'DIALOG_MEMORY_ENABLED':['maybe'], 'DIALOG_MEMORY_MAX_TURNS':['0','-1','101','1.5'], 'DIALOG_MEMORY_MAX_TOKENS':['0','32769','bad']}.items():
            for value in values:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):load('GROQ_API_KEY=synthetic\n'+key+'='+value)
        c=load('GROQ_API_KEY=synthetic\nDIALOG_MEMORY_ENABLED=false\nDIALOG_MEMORY_MAX_TURNS=3\nDIALOG_MEMORY_MAX_TOKENS=90')
        self.assertEqual((c.dialog_memory_enabled,c.dialog_memory_max_turns,c.dialog_memory_max_tokens),(False,3,90))
        c=load('GROQ_API_KEY=synthetic\nDIALOG_MEMORY_MAX_TURNS=3',{'DIALOG_MEMORY_MAX_TURNS':'2','DIALOG_MEMORY_ENABLED':'0'})
        self.assertEqual((c.dialog_memory_enabled,c.dialog_memory_max_turns),(False,2))


class IntegrationTests(unittest.TestCase):
    def pipeline(self,streaming=False):
        process,env,stt,llm,tts=sessions.SessionTests().pipeline()
        env['CONFIG']=NS(tts_normalize_en=True,llm_streaming=streaming)
        env['silero_model']=Mock()
        env['stream_reply']=Mock(return_value=('Python and GitHub',{'outcome':'done'}))
        return process,env,stt,llm,tts

    def test_both_modes_original_text_after_playback_and_one_api(self):
        for streaming in [False,True]:
            p,e,stt,llm,tts=self.pipeline(streaming)
            raw='Python and GitHub';llm.return_value=NS(choices=[NS(message=NS(content=raw),finish_reason='stop')])
            def playback(*a,**k):
                if (e['stream_reply'] if streaming else tts).call_count == 1:
                    self.assertEqual(history(e['dialog_memory']),[])
                return (raw,{'outcome':'done'}) if streaming else True
            (e['stream_reply'] if streaming else tts).side_effect=playback
            p(Mock(),FRAME,{'mode':'PROCESSING'})
            self.assertEqual(history(e['dialog_memory'])[-1]['content'],raw)
            self.assertEqual((e['stream_reply'] if streaming else llm).call_count,1)
            stt.assert_called_once()
            p(Mock(),FRAME,{'mode':'PROCESSING'})
            messages=(e['stream_reply'].call_args.args[2] if streaming else llm.call_args.kwargs['messages'])
            self.assertEqual(messages[1:3],[{'role':'user','content':'new command'},{'role':'assistant','content':raw}])

    def test_stream_noncomplete_outcomes_no_history(self):
        for outcome,raw in [('partial_length','tail'),('empty_length',''),('cancelled','tail'),('error','tail')]:
            p,e,*_=self.pipeline(True);e['stream_reply'].return_value=(raw,{'outcome':outcome})
            p(Mock(),FRAME,{'mode':'PROCESSING'});self.assertEqual(history(e['dialog_memory']),[])

    def test_full_playback_failure_and_cancel_no_history(self):
        for result in [False,CommandCancelled(),OSError('playback')]:
            p,e,_,llm,tts=self.pipeline()
            if isinstance(result,Exception):tts.side_effect=result
            else:tts.return_value=result
            p(Mock(),FRAME,{'mode':'PROCESSING'});self.assertEqual(history(e['dialog_memory']),[])

    def test_clear_during_stt_generation_and_playback_blocks_old_commit(self):
        for stage in ['stt','llm','tts','stream']:
            p,e,stt,llm,tts=self.pipeline(stage=='stream');m=e['dialog_memory'];add(m)
            target={'stt':stt,'llm':llm,'tts':tts,'stream':e['stream_reply']}[stage]
            result=target.return_value
            target.side_effect=lambda **kw: (m.clear(),result)[1]
            if stage in ['tts','stream']:target.side_effect=lambda *a,**kw:(m.clear(),result)[1]
            p(Mock(),FRAME,{'mode':'PROCESSING'});self.assertEqual(history(m),[])

    def test_groq_error_and_cancel_during_tts_no_history(self):
        for stage in ['llm','stream']:
            p,e,_,llm,_=self.pipeline(stage=='stream')
            target=e['stream_reply'] if stage=='stream' else llm
            target.side_effect=RuntimeError('synthetic')
            p(Mock(),FRAME,{'mode':'PROCESSING'});self.assertEqual(history(e['dialog_memory']),[])

    def test_real_session_waits_for_matching_ack_before_memory_commit(self):
        p,e,_,_,tts=self.pipeline();eos=threading.Event()
        session,conn=sessions.SessionTests().session()
        self.addCleanup(session.close)
        def send(data):
            if data[0]==0x13:eos.set()
        conn.sendall.side_effect=send
        def playback(writer,*args):
            token=writer.begin_playback();writer.finish_playback(token);return True
        tts.side_effect=playback
        session.start(p,FRAME)
        self.assertTrue(eos.wait(2));self.assertEqual(history(e['dialog_memory']),[])
        token=conn.sendall.call_args.args[0][5:]
        self.assertFalse(session.receive_playback_ack(b'x'*32))
        self.assertEqual(history(e['dialog_memory']),[])
        self.assertTrue(session.receive_playback_ack(token))
        session._worker.join(2);self.assertFalse(session._worker.is_alive())
        self.assertEqual(len(history(e['dialog_memory'])),2)

    def test_disabled_integration_omits_previous_turns_both_modes(self):
        for streaming in [False,True]:
            p,e,_,llm,_=self.pipeline(streaming);e['dialog_memory']=DialogMemory(enabled=False)
            for i in range(2):p(Mock(),FRAME,{'mode':'PROCESSING'})
            messages=e['stream_reply'].call_args.args[2] if streaming else llm.call_args.kwargs['messages']
            self.assertEqual([m['role'] for m in messages],['system','user'])
            self.assertEqual(history(e['dialog_memory']),[])

    def test_disconnect_before_commit_rejected(self):
        p,e,_,_,tts=self.pipeline();s=Mock()
        s.commit.side_effect=ConnectionAbortedError()
        p(Mock(),FRAME,{'mode':'PROCESSING'},session=s)
        self.assertEqual(history(e['dialog_memory']),[])


if __name__=='__main__':unittest.main()
