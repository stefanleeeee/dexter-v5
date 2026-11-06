"""3.4: real CommandSession/socketpair, fake Groq/TTS/ESP consumer."""
import socket
import struct
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np

from session_runtime import CommandSession
from streaming_reply import stream_reply
from test_llm_stream import FakeStream, chunk
from test_command_states import FRAME


class StreamingTransportTests(unittest.TestCase):
    def exercise(self, stage='normal'):
        a,b=socket.socketpair();a.settimeout(3);b.settimeout(3)
        logs=[]; session=CommandSession(a,logs.append)
        frames=[];results=[];errors=[];entered=threading.Event()
        def exact(sock,n):
            result=bytearray()
            while len(result)<n:
                part=sock.recv(n-len(result))
                if not part:raise EOFError()
                result.extend(part)
            return bytes(result)
        def peer():
            try:
                while True:
                    if stage=='slow_tcp':time.sleep(.002)
                    header=exact(b,5);length=struct.unpack('>I',header[1:])[0]
                    payload=exact(b,length);typ=header[0];frames.append((typ,payload))
                    if typ==0x12:
                        assert len(payload)%2==0 and len(payload)<=4096
                        if stage in ['cancel_send','disconnect'] and not entered.is_set():
                            entered.set()
                            if stage=='disconnect':session.close();b.close();return
                            session.request_cancel()
                    if typ==0x13 and stage=='cancel_ack':
                        assert session.request_cancel()
                        continue
                    if typ in [0x13,0x15]:
                        assert len(payload)==32
                        # ACK dispatch is a reader operation, never the command writer.
                        b.sendall(struct.pack('>BI',0x14 if typ==0x13 else 0x16,32)+payload)
                        header=exact(a,5);token=exact(a,struct.unpack('>I',header[1:])[0])
                        accepted=session.receive_playback_ack(token) if header[0]==0x14 else session.receive_cancel_ack(token)
                        assert accepted
                        return
            except (EOFError,OSError):pass
            except BaseException as error:errors.append(type(error).__name__)
        peer_thread=threading.Thread(target=peer,daemon=True);peer_thread.start()
        calls=[]
        def synth(**kw):
            calls.append(kw)
            if stage=='slow_tts':time.sleep(.015)
            if stage=='cancel_tts':session.request_cancel()
            size=20000 if stage in ['cancel_send','disconnect','slow_tcp'] else 100
            return NS(numpy=lambda:np.full(size,len(calls)/10,dtype=np.float32))
        def deltas():
            if stage=='empty_length':
                yield chunk(reason='length');return
            if stage=='partial_tail':
                yield chunk('Полученный незавершённый хвост');yield chunk(reason='length');return
            if stage=='cancel_before':
                session.request_cancel()
                yield chunk('STALE')
                return
            yield chunk('Первая фраза. ')
            yield chunk('Вторая ')
            if stage=='api_error':raise RuntimeError('synthetic API failure')
            if stage=='cancel_llm':
                session.request_cancel();yield chunk('STALE');return
            if stage=='slow_llm':time.sleep(.025)
            yield chunk('фраза. Конец!')
            yield chunk(reason='length' if stage=='truncated' else 'stop')
        source=FakeStream(deltas())
        def worker(writer,*args,**kwargs):
            results.append(stream_reply(writer,Mock(return_value=source),[],NS(apply_tts=synth),'aidar',True,logs.append))
        try:
            session.start(worker,FRAME)
            session._worker.join(4)
            self.assertFalse(session._worker.is_alive(),'worker stuck')
            session.poll()
            if stage not in ['cancel_before','cancel_tts','empty_length']:
                peer_thread.join(1)
            self.assertEqual(errors,[])
            self.assertTrue(source.closed)
            if stage!='disconnect':self.assertEqual(session.state['mode'],'WAKE_WORD')
            return session,frames,calls,results,logs
        finally:
            session.close();b.close();peer_thread.join(1)

    def test_one_start_eos_done_and_pcm_order(self):
        _,frames,calls,results,logs=self.exercise()
        kinds=[t for t,_ in frames]
        self.assertEqual(kinds.count(0x11),1);self.assertEqual(kinds.count(0x13),1)
        self.assertEqual(kinds.count(0x15),0)
        self.assertEqual(frames[0][1],frames[-1][1])
        self.assertEqual(len(calls),3)
        pcm=b''.join(p for t,p in frames if t==0x12)
        expected=b''.join((np.full(100,n/10,dtype=np.float32)*32767).astype('<i2').tobytes() for n in [1,2,3])+bytes(12000)
        self.assertEqual(pcm,expected)
        self.assertEqual(sum('[PLAYBACK DONE]' in x for x in logs),1)
        self.assertEqual(results[0][0],'Первая фраза. Вторая фраза. Конец!')

    def test_slow_llm(self):self.exercise('slow_llm')
    def test_slow_tts(self):self.exercise('slow_tts')
    def test_slow_tcp(self):self.exercise('slow_tcp')

    def test_cancel_during_generation_after_audio(self):
        _,frames,_,results,logs=self.exercise('cancel_llm')
        self.assertEqual([t for t,_ in frames].count(0x15),1)
        self.assertFalse(any(t==0x13 for t,_ in frames));self.assertEqual(results,[])
        self.assertFalse(any('[PLAYBACK DONE]' in x for x in logs))
        self.assertTrue(any('[PLAYBACK CANCELLED]' in x for x in logs))

    def test_cancel_during_send(self):
        _,frames,_,_,logs=self.exercise('cancel_send')
        self.assertTrue(any(t==0x15 for t,_ in frames))
        self.assertFalse(any('[PLAYBACK DONE]' in x for x in logs))

    def test_cancel_before_first_phrase(self):
        _,frames,calls,results,_=self.exercise('cancel_before')
        self.assertEqual(frames,[]);self.assertEqual(calls,[]);self.assertEqual(results,[])

    def test_cancel_during_first_tts(self):
        _,frames,_,_,_=self.exercise('cancel_tts')
        self.assertEqual(frames,[])

    def test_error_after_partial_voice_uses_cancel_not_eos(self):
        _,frames,_,results,logs=self.exercise('api_error')
        self.assertTrue(any(t==0x15 for t,_ in frames));self.assertEqual(results,[])
        self.assertFalse(any(t==0x13 for t,_ in frames))

    def test_truncation_flushes_received_tail_with_eos_ack_not_cancel(self):
        _,frames,_,results,logs=self.exercise('truncated')
        self.assertFalse(any(t==0x15 for t,_ in frames))
        self.assertEqual([t for t,_ in frames].count(0x13),1)
        self.assertEqual(results[0][1]['outcome'],'partial_length')
        self.assertTrue(any('[PLAYBACK DONE]' in x for x in logs))

    def test_cancel_while_waiting_final_ack(self):
        _,frames,_,results,logs=self.exercise('cancel_ack')
        self.assertEqual([t for t,_ in frames].count(0x13),1)
        self.assertEqual([t for t,_ in frames].count(0x15),1)
        self.assertEqual(results,[])
        self.assertFalse(any('[PLAYBACK DONE]' in x for x in logs))
        self.assertTrue(any('[PLAYBACK CANCELLED]' in x for x in logs))

    def test_new_connection_succeeds_after_stream_disconnect(self):
        _,old,_,_,_=self.exercise('disconnect')
        _,new,_,results,_=self.exercise('normal')
        self.assertNotEqual(old[0][1][:16],new[0][1][:16])
        self.assertEqual(len(results),1)

    def test_disconnect_drops_old_generation(self):
        session,frames,_,results,logs=self.exercise('disconnect')
        self.assertEqual(results,[])
        self.assertFalse(any('[PLAYBACK DONE]' in x for x in logs))
        with self.assertRaises(ConnectionError):session.check_active()


if __name__=='__main__':unittest.main()
