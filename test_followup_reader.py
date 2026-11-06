"""Actual server receiver + CommandSession over socketpair, fake speech/clock."""
import json
import select
import socket
import struct
import threading
import time
import unittest
import uuid
from types import SimpleNamespace as NS
from test_command_states import load_function, Endpoint, StopServer
from session_runtime import CommandSession


def pcm(value,count=1):return (bytes([value,0])*480)*count


class ReaderTests(unittest.TestCase):
    def launch(self,enabled=True):
        conn,peer=socket.socketpair();peer.settimeout(2)
        self.addCleanup(peer.close);self.addCleanup(conn.close)
        listener=Endpoint();clock=[0.0];sessions=[];calls=[];logs=[]
        opened=threading.Event();connected=threading.Event();dropped=threading.Event()
        accepted=False
        def accept():
            nonlocal accepted
            if accepted:raise StopServer()
            accepted=True;return conn,('offline',0)
        listener.accept=accept
        class Observed(CommandSession):
            def __init__(self,*a,**kw):
                kw['clock']=lambda:clock[0];super().__init__(*a,**kw);sessions.append(self);connected.set()
        def log(text):
            logs.append(text)
            if 'WINDOW OPENED' in text:opened.set()
            if 'Слишком короткая' in text:dropped.set()
        last=[0]
        def recognize(frame):last[0]=frame[0];return False
        recognizer=NS(AcceptWaveform=recognize,PartialResult=lambda:json.dumps({'partial':'dexter' if last[0]==7 else ''}),Reset=lambda:None)
        def process(writer,audio,state,session):
            calls.append(bytes(audio));token=writer.begin_playback();writer.finish_playback(token);state['followup_success']=True
        recv=load_function('recvall',dict(time=time,select=select,log=log))
        run=load_function('start_server',dict(socket=NS(socket=lambda *a:listener,AF_INET=0,SOCK_STREAM=0,SOL_SOCKET=0,SO_REUSEADDR=0),
            webrtcvad=NS(Vad=lambda _:NS(is_speech=lambda frame,*a:frame[0] in (1,7))),
            np=NS(frombuffer=lambda data,**kw:data,int16=None,abs=lambda x,**kw:x,max=lambda _:2000),
            CONFIG=NS(dialog_followup_enabled=enabled,dialog_followup_timeout_s=12),
            wake_recognizer=recognizer,log=log,HOST='',PORT=3333,recvall=recv,struct=struct,json=json,uuid=uuid,
            threading=threading,CommandSession=Observed,process_command=process))
        def serve():
            try:run()
            except StopServer:pass
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        def cleanup():
            conn.close();peer.close();thread.join(2)
        self.addCleanup(cleanup);self.assertTrue(connected.wait(2))
        self.current=sessions[0]
        return peer,sessions[0],clock,calls,logs,opened,dropped

    def mic(self,peer,data):
        for i in range(0,len(data),24960):
            part=data[i:i+24960];peer.sendall(b'\x02'+struct.pack('>I',len(part))+part)

    def command(self,peer,wake=True,voiced=12):
        if wake:self.mic(peer,pcm(7))
        self.mic(peer,pcm(1,voiced)+pcm(0,51))

    def exact(self,peer,n):
        data=bytearray()
        while len(data)<n:
            part=peer.recv(n-len(data))
            if not part:raise EOFError()
            data.extend(part)
        return bytes(data)

    def finish_reply(self,peer,opened,old_pcm=False,fresh=True):
        token=None;opened.clear()
        while True:
            head=self.exact(peer,5);kind=head[0];length=struct.unpack('>I',head[1:])[0]
            body=self.exact(peer,length)
            if kind==0x17:peer.sendall(b'\x18\0\0\0\0')
            elif kind==0x11:token=body
            elif kind==0x13:
                self.assertEqual(body,token);peer.sendall(b'\x14\0\0\0\x20'+body)
                if old_pcm:self.mic(peer,pcm(1,20)+pcm(0,51))
                if not fresh:
                    worker=self.current._worker
                    if worker is not None:worker.join(2)
                    self.mic(peer,pcm(0))
                    return token
            elif kind==0x19:
                self.assertEqual(body,token)
                # Wrong old connection ID must not confirm this request.
                peer.sendall(b'\x1a\0\0\0\x20'+b'x'*32)
                peer.sendall(b'\x1a\0\0\0\x20'+body)
                worker=self.current._worker
                if worker is not None:worker.join(2)
                self.mic(peer,pcm(0))
                self.assertTrue(opened.wait(2));return token
            else:raise AssertionError(kind)

    def test_old_pcm_ignored_until_ready_then_followup_reopens(self):
        peer,s,clock,calls,logs,opened,_=self.launch()
        self.command(peer);first=self.finish_reply(peer,opened,old_pcm=True)
        self.assertEqual(len(calls),1)
        self.command(peer,wake=False);second=self.finish_reply(peer,opened)
        self.assertEqual(len(calls),2);self.assertNotEqual(first,second)
        self.assertEqual(s.state['mode'],'FOLLOWUP')

    def test_timeout_requires_wake_word_again(self):
        peer,s,clock,calls,logs,opened,_=self.launch()
        self.command(peer);self.finish_reply(peer,opened)
        clock[0]=12;self.command(peer,wake=False)
        self.command(peer,wake=True);self.finish_reply(peer,opened)
        self.assertEqual(len(calls),2)
        self.assertTrue(any('WINDOW EXPIRED' in x for x in logs))

    def test_short_noise_consumes_window_without_whisper_job(self):
        peer,s,clock,calls,logs,opened,dropped=self.launch()
        self.command(peer);self.finish_reply(peer,opened)
        self.command(peer,wake=False,voiced=3);self.assertTrue(dropped.wait(2))
        self.assertEqual(len(calls),1);self.assertEqual(s.state['mode'],'WAKE_WORD')
        self.assertFalse(s.cancel_followup())

    def test_disabled_preserves_wake_gate(self):
        peer,s,clock,calls,logs,opened,_=self.launch(enabled=False)
        self.command(peer);self.finish_reply(peer,opened,fresh=False)
        self.command(peer,wake=False)
        self.command(peer,wake=True);self.finish_reply(peer,opened,fresh=False)
        self.assertEqual(len(calls),2);self.assertFalse(opened.is_set())


if __name__=='__main__':unittest.main()
