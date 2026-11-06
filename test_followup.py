"""Follow-up lifecycle with fake monotonic time, no API/models/hardware."""
import struct
import threading
import unittest
from unittest.mock import Mock
from session_runtime import CommandSession, CommandCancelled
from test_command_states import FRAME


class FollowupTests(unittest.TestCase):
    def make(self,enabled=True,supported=True,ready=True):
        clock=[100.0];logs=[];packets=[];connection=Mock()
        s=CommandSession(connection,logs.append,followup_enabled=enabled,clock=lambda:clock[0])
        self.addCleanup(s.close)
        def send(data):
            kind=data[0];packets.append(data)
            if kind==0x17 and supported:s.receive_followup_capability()
            if kind==0x13:s.receive_playback_ack(data[5:])
            if kind==0x19 and ready:s.receive_fresh_capture(data[5:])
        connection.sendall.side_effect=send
        if not ready:s._fresh_event.wait=Mock(return_value=False)
        return s,clock,logs,packets

    def reply(self,s,success=True):
        def work(writer,audio,state,session):
            token=writer.begin_playback();writer.finish_playback(token)
            state['followup_success']=success
        s.state['mode']='PROCESSING';s.start(work,FRAME);s._worker.join(2)
        self.assertFalse(s._worker.is_alive());self.assertEqual(s.state['mode'],'PROCESSING')
        s.poll()

    def test_success_opens_only_after_worker_release_and_ready(self):
        s,clock,logs,packets=self.make();self.reply(s)
        self.assertEqual(s.state['mode'],'FOLLOWUP')
        self.assertEqual([p[0] for p in packets],[0x17,0x11,0x13,0x19])
        self.assertEqual(packets[-1][5:],packets[-2][5:])
        self.assertEqual(s._followup_deadline,112)
        self.assertTrue(any('FRESH CAPTURE CONFIRMED' in x for x in logs))

    def test_expiry_exact_boundary_and_no_noise_extension(self):
        s,clock,*_=self.make();self.reply(s)
        for i in range(11):
            clock[0]+=1;self.assertFalse(s.followup_speech(False))
        clock[0]=112;s.poll();self.assertEqual(s.state['mode'],'WAKE_WORD')
        self.assertFalse(s.followup_speech(True))

    def test_speech_requires_three_consecutive_frames_then_single_command(self):
        s,*_=self.make();self.reply(s)
        self.assertFalse(s.followup_speech(True));self.assertFalse(s.followup_speech(False))
        self.assertFalse(s.followup_speech(True));self.assertFalse(s.followup_speech(True))
        self.assertTrue(s.followup_speech(True));self.assertEqual(s.state['mode'],'COMMAND')
        self.assertFalse(s.followup_speech(True))

    def test_next_done_reopens_window_with_new_reply_id(self):
        s,clock,logs,packets=self.make();self.reply(s)
        old=packets[-1][5:];clock[0]+=5;self.reply(s)
        self.assertNotEqual(old,packets[-1][5:]);self.assertEqual(s._followup_deadline,117)
        self.assertEqual([p[0] for p in packets].count(0x17),1)

    def test_disabled_sends_no_new_control_frames(self):
        s,_,_,packets=self.make(enabled=False);self.reply(s)
        self.assertEqual(s.state['mode'],'WAKE_WORD');self.assertEqual([p[0] for p in packets],[0x11,0x13])

    def test_legacy_firmware_ignores_zero_probe_and_remains_wake_word(self):
        s,_,logs,packets=self.make(supported=False);self.reply(s)
        self.assertEqual(s.state['mode'],'WAKE_WORD')
        self.assertEqual(packets[0],b'\x17\0\0\0\0')
        self.assertNotIn(0x19,[p[0] for p in packets])
        self.assertTrue(any('FALLBACK' in x for x in logs))

    def test_missing_ready_does_not_open_window(self):
        s,*_=self.make(ready=False);self.reply(s)
        self.assertEqual(s.state['mode'],'WAKE_WORD')
        self.assertIsNone(s._fresh_expected)

    def test_unsolicited_and_stale_ready_are_rejected(self):
        s,*_=self.make(ready=False)
        self.assertFalse(s.receive_fresh_capture(b'x'*32));self.assertFalse(s.receive_followup_capability())
        self.reply(s);old=s._completed_playback_token
        self.assertFalse(s.receive_fresh_capture(old))
        new,*_=self.make();self.assertFalse(new.receive_fresh_capture(old))
        self.assertFalse(new.receive_fresh_capture(old[:31]))

    def test_reconnect_and_cancel_close_window(self):
        s,*_=self.make();self.reply(s);s.close();s.poll()
        self.assertFalse(s.followup_speech(True));self.assertEqual(s.state['mode'],'WAKE_WORD')
        new,*_=self.make();self.assertEqual(new.state['mode'],'WAKE_WORD')
        self.reply(new);self.assertTrue(new.cancel_followup());new.poll()
        self.assertEqual(new.state['mode'],'WAKE_WORD')

    def test_clear_invalidates_pending_success_and_open_window(self):
        s,*_=self.make();s.cancel_followup(invalidate=True)
        self.reply(s) # start begins a genuinely new command after clear.
        s.cancel_followup(invalidate=True);s.poll();self.assertEqual(s.state['mode'],'WAKE_WORD')

    def test_partial_error_or_empty_outcome_never_requests_fresh_capture(self):
        for _ in ['partial_length','empty_length','error']:
            s,_,_,packets=self.make();self.reply(s,success=False)
            self.assertEqual(s.state['mode'],'WAKE_WORD')
            self.assertNotIn(0x19,[p[0] for p in packets])

    def test_cancel_during_fresh_wait_suppresses_window(self):
        s,_,_,packets=self.make(ready=False)
        original=s.conn.sendall.side_effect
        def send(data):
            original(data)
            if data[0]==0x19:s.cancel_followup()
        s.conn.sendall.side_effect=send;self.reply(s)
        self.assertEqual(s.state['mode'],'WAKE_WORD')

    def test_clear_during_active_reply_prevents_eligibility(self):
        s,_,_,packets=self.make()
        def work(writer,audio,state,session):
            token=writer.begin_playback();s.cancel_followup(invalidate=True)
            writer.finish_playback(token);state['followup_success']=True
        s.state['mode']='PROCESSING';s.start(work,FRAME);s._worker.join(2);s.poll()
        self.assertEqual(s.state['mode'],'WAKE_WORD');self.assertNotIn(0x19,[p[0] for p in packets])

    def test_worker_failure_never_opens_and_duplicate_start_is_rejected(self):
        s,*_=self.make();entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def work(*a,**k):entered.set();release.wait(2);raise RuntimeError('synthetic')
        s.start(work,FRAME);self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeError):s.start(work,FRAME)
        release.set();s._worker.join(2);s.poll();self.assertEqual(s.state['mode'],'WAKE_WORD')


if __name__=='__main__':unittest.main()
