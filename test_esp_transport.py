"""Execute the actual C transport helpers with fault-injected platform calls.

Uses already installed Espressif clang and llvmlite, not ESP hardware. clang's
portable LLVM IR is retargeted to the host for these isolated helper functions;
the full ESP32 binary is compiled separately with the real ESP-IDF toolchain.
"""
import ctypes as C
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import threading


FIRMWARE = Path(os.environ.get('DEXTER_ESP_SOURCE', str(Path(__file__).parent / 'esp32/main/hello_world_main.c')))
CLANG = os.environ.get('DEXTER_ESP_CLANG', 'clang')


class PlaybackStats(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ('queued_bytes', 'high_water_bytes',
                'backpressure', 'starvation', 'bytes_written', 'write_errors')]


class AudioRx(C.Structure):
    _fields_ = [('remaining', C.c_uint32), ('buffered', C.c_size_t),
                ('deadline', C.c_int64), ('data', C.c_ubyte * 2048)]


def helper_source():
    source = FIRMWARE.read_text(encoding='utf-8-sig')
    start = source.index('// TCP I/O limits:')
    end = source.index('static void tcp_client_task(', start)
    mic = source[source.index('static uint32_t mic_capture_generation('):source.index('static void mic_i2s_task(')]
    event = source[source.index('static void event_handler('):source.index('void wifi_init_sta(')]
    playback = (FIRMWARE.parent / 'playback.c').read_text(encoding='utf-8-sig')
    header = (FIRMWARE.parent / 'playback.h').read_text(encoding='utf-8-sig')
    playback = re.sub(r'^#include.*$', '', playback, flags=re.M)
    header = re.sub(r'^#(?:include|pragma).*$', '', header, flags=re.M)
    return (header + event + mic + playback + source[start:end]).replace('static ', '')


class EspTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import llvmlite.binding as llvm
        prefix = '''
        #include <stdint.h>
        #include <stddef.h>
        #include <stdbool.h>
        int test_errno;
        #define errno test_errno
        #define EINTR 4
        #define EAGAIN 11
        #define EWOULDBLOCK 11
        #define EINPROGRESS 115
        #define MSG_DONTWAIT 8
        #define F_GETFL 3
        #define F_SETFL 4
        #define O_NONBLOCK 2048
        #define SOL_SOCKET 1
        #define SO_ERROR 4
        #define pdTRUE 1
        #define ESP_OK 0
        #define ESP_ERR_TIMEOUT 0x107
        #define ESP_ERR_INVALID_STATE 0x103
        #define IRAM_ATTR
        typedef int portMUX_TYPE;
        #define portMUX_INITIALIZER_UNLOCKED 0
        #define portENTER_CRITICAL(m) test_dma_take()
        #define portEXIT_CRITICAL(m) test_dma_give()
        #define portENTER_CRITICAL_ISR(m) test_dma_take()
        #define portEXIT_CRITICAL_ISR(m) test_dma_give()
        void test_dma_take(void);
        void test_dma_give(void);
        #define RINGBUF_TYPE_BYTEBUF 2
        #define portMAX_DELAY 0xffffffffU
        #define pdMS_TO_TICKS(n) (n)
        #define ESP_LOGE(...) ((void)0)
        #define ESP_LOGI(...) ((void)0)
        typedef void *esp_event_base_t;
        #define WIFI_EVENT ((void *)1)
        #define IP_EVENT ((void *)2)
        #define WIFI_EVENT_STA_START 1
        #define WIFI_EVENT_STA_DISCONNECTED 2
        #define IP_EVENT_STA_GOT_IP 3
        #define WIFI_CONNECTED_BIT 1
        void *s_wifi_event_group;
        int esp_wifi_connect(void);
        unsigned xEventGroupClearBits(void *, unsigned);
        unsigned xEventGroupSetBits(void *, unsigned);
        #define malloc test_alloc
        #define free test_free
        typedef int esp_err_t;
        void *tx_chan;
        void *mic_mutex = (void *)1, *audio_ringbuf = (void *)1;
        bool mic_connected;
        uint32_t mic_generation = 1;
        uint32_t mic_drops;
        typedef void *RingbufHandle_t;
        typedef void *SemaphoreHandle_t;
        typedef void *i2s_chan_handle_t;
        typedef struct { void *dma_buf; size_t size; } i2s_event_data_t;
        typedef bool (*i2s_isr_callback_t)(i2s_chan_handle_t, i2s_event_data_t *, void *);
        typedef struct { i2s_isr_callback_t on_sent; } i2s_event_callbacks_t;
        typedef struct { unsigned total_dma_buf_size; } i2s_chan_info_t;
        int i2s_channel_get_info(void *, i2s_chan_info_t *);
        int i2s_channel_register_event_callback(void *, const i2s_event_callbacks_t *, void *);
        typedef int BaseType_t;
        typedef unsigned socklen_t;
        struct sockaddr { int unused; };
        struct timeval { long tv_sec, tv_usec; };
        typedef struct { unsigned bits; } fd_set;
        #define FD_ZERO(p) ((p)->bits = 0)
        #define FD_SET(fd,p) ((p)->bits |= (1U << (fd)))
        int fcntl(int, int, int);
        int connect(int, const struct sockaddr *, socklen_t);
        int select(int, void *, void *, void *, struct timeval *);
        int getsockopt(int, int, int, void *, socklen_t *);
        int64_t esp_timer_get_time(void);
        int xSemaphoreTake(void *, unsigned);
        int xSemaphoreGive(void *);
        void *xSemaphoreCreateMutex(void);
        void vSemaphoreDelete(void *);
        void *xRingbufferCreate(size_t, int);
        void vRingbufferDelete(void *);
        void *xRingbufferReceive(void *, size_t *, unsigned);
        void *xRingbufferReceiveUpTo(void *, size_t *, unsigned, size_t);
        void vRingbufferReturnItem(void *, void *);
        int xRingbufferSend(void *, const void *, size_t, unsigned);
        void *test_alloc(size_t);
        void *memcpy(void *, const void *, size_t);
        void *memset(void *, int, size_t);
        int memcmp(const void *, const void *, size_t);
        void test_free(void *);
        int send(int, const void *, size_t, int);
        int recv(int, void *, size_t, int);
        void vTaskDelay(unsigned);
        int i2s_channel_write(void *, const void *, size_t, size_t *, unsigned);
        int i2s_channel_disable(void *);
        int i2s_channel_enable(void *);
        int i2s_channel_preload_data(void *, const void *, size_t, size_t *);
        '''
        with tempfile.TemporaryDirectory() as tmp:
            cfile, irfile = Path(tmp)/'transport.c', Path(tmp)/'transport.ll'
            cfile.write_text(prefix + helper_source(), encoding='utf-8')
            subprocess.run([CLANG, '--target=riscv64-unknown-elf', '-ffreestanding',
                            '-S', '-emit-llvm', '-O0', str(cfile), '-o', str(irfile)],
                           check=True, capture_output=True, text=True)
            ir = irfile.read_text()
        # Target attributes refer to the compiler's embedded target, not the
        # isolated C logic. No platform code or architecture intrinsics are used.
        ir = re.sub(r'^attributes #\d+ = .*\n', '', ir, flags=re.M)
        ir = re.sub(r' #\d+', '', ir)
        ir = re.sub(r'^!llvm.module.flags = .*\n', '', ir, flags=re.M)
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        target = llvm.Target.from_default_triple().create_target_machine()
        module = llvm.parse_assembly(ir)
        module.triple = llvm.get_default_triple()
        module.data_layout = str(target.target_data)
        module.verify()
        cls.callbacks = []
        cls.crt = C.CDLL('msvcrt' if os.name == 'nt' else None)
        for name in ('memcpy', 'memset', 'memcmp'):
            llvm.add_symbol(name, C.cast(getattr(cls.crt, name), C.c_void_p).value)
        # Callbacks resolve the current test case; keep them alive for the JIT.
        def bind(symbol, prototype, method):
            callback = prototype(lambda *args: getattr(cls.current, method)(*args))
            cls.callbacks.append(callback)
            llvm.add_symbol(symbol, C.cast(callback, C.c_void_p).value)
        io = C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p, C.c_size_t, C.c_int)
        bind('send', io, 'send')
        bind('recv', io, 'recv')
        bind('test_alloc', C.CFUNCTYPE(C.c_void_p, C.c_size_t), 'alloc')
        bind('test_free', C.CFUNCTYPE(None, C.c_void_p), 'free')
        bind('vTaskDelay', C.CFUNCTYPE(None, C.c_uint), 'delay')
        bind('esp_timer_get_time', C.CFUNCTYPE(C.c_int64), 'timer')
        bind('test_dma_take', C.CFUNCTYPE(None), 'dma_take')
        bind('test_dma_give', C.CFUNCTYPE(None), 'dma_give')
        bind('i2s_channel_get_info', C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(C.c_uint)), 'dma_info')
        bind('i2s_channel_register_event_callback', C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_void_p), 'dma_register')
        bind('i2s_channel_disable', C.CFUNCTYPE(C.c_int, C.c_void_p), 'i2s_disable')
        bind('i2s_channel_enable', C.CFUNCTYPE(C.c_int, C.c_void_p), 'i2s_enable')
        bind('i2s_channel_preload_data', C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)), 'i2s_preload')
        bind('esp_wifi_connect', C.CFUNCTYPE(C.c_int), 'wifi_connect')
        bind('xEventGroupClearBits', C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint), 'wifi_clear')
        bind('xEventGroupSetBits', C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint), 'wifi_set')
        bind('fcntl', C.CFUNCTYPE(C.c_int, C.c_int, C.c_int, C.c_int), 'fcntl')
        bind('connect', C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p, C.c_uint), 'connect')
        bind('select', C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p), 'select')
        bind('getsockopt', C.CFUNCTYPE(C.c_int, C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_void_p), 'getsockopt')
        bind('xSemaphoreTake', C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint), 'take')
        bind('xSemaphoreGive', C.CFUNCTYPE(C.c_int, C.c_void_p), 'give')
        bind('xSemaphoreCreateMutex', C.CFUNCTYPE(C.c_void_p), 'create_mutex')
        bind('vSemaphoreDelete', C.CFUNCTYPE(None, C.c_void_p), 'delete_mutex')
        bind('xRingbufferCreate', C.CFUNCTYPE(C.c_void_p, C.c_size_t, C.c_int), 'create_ring')
        bind('vRingbufferDelete', C.CFUNCTYPE(None, C.c_void_p), 'delete_ring')
        bind('xRingbufferReceive', C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.POINTER(C.c_size_t), C.c_uint), 'ring_receive')
        bind('xRingbufferReceiveUpTo', C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.POINTER(C.c_size_t), C.c_uint, C.c_size_t), 'ring_receive_up_to')
        bind('vRingbufferReturnItem', C.CFUNCTYPE(None, C.c_void_p, C.c_void_p), 'ring_return')
        bind('xRingbufferSend', C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_size_t, C.c_uint), 'ring_send')
        bind('i2s_channel_write', C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p,
             C.c_size_t, C.POINTER(C.c_size_t), C.c_uint), 'write')
        cls.engine = llvm.create_mcjit_compiler(module, target)
        cls.engine.finalize_object()
        cls.error = C.c_int.from_address(cls.engine.get_global_value_address('test_errno'))
        proto = C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p, C.c_size_t, C.c_int64)
        cls.send_mic_until = staticmethod(proto(cls.engine.get_function_address('tcp_send_mic')))
        cls.handle = staticmethod(C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p)(
            cls.engine.get_function_address('tcp_handle_message')))
        cls.receive_header = staticmethod(C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p,
            C.POINTER(C.c_int), C.POINTER(C.c_int64))(cls.engine.get_function_address('tcp_receive_header')))
        cls.connect_until = staticmethod(C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p, C.c_uint)(
            cls.engine.get_function_address('tcp_connect_with_timeout')))
        cls.capture = staticmethod(C.CFUNCTYPE(C.c_uint32)(cls.engine.get_function_address('mic_capture_generation')))
        cls.set_connected = staticmethod(C.CFUNCTYPE(None, C.c_bool)(cls.engine.get_function_address('mic_set_connected')))
        cls.enqueue = staticmethod(C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_size_t, C.c_uint32)(
            cls.engine.get_function_address('mic_enqueue')))
        cls.mic_flag = C.c_bool.from_address(cls.engine.get_global_value_address('mic_connected'))
        cls.mic_epoch = C.c_uint32.from_address(cls.engine.get_global_value_address('mic_generation'))
        cls.event = staticmethod(C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_int32, C.c_void_p)(
            cls.engine.get_function_address('event_handler')))
        cls.play_init = staticmethod(C.CFUNCTYPE(C.c_bool, C.c_void_p)(cls.engine.get_function_address('playback_init')))
        cls.play_deinit = staticmethod(C.CFUNCTYPE(None)(cls.engine.get_function_address('playback_deinit')))
        cls.play_set = staticmethod(C.CFUNCTYPE(None, C.c_bool)(cls.engine.get_function_address('playback_set_connected')))
        cls.play_enqueue = staticmethod(C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_size_t)(cls.engine.get_function_address('playback_enqueue')))
        cls.play_step = staticmethod(C.CFUNCTYPE(C.c_int)(cls.engine.get_function_address('playback_step')))
        cls.play_failed = staticmethod(C.CFUNCTYPE(C.c_bool)(cls.engine.get_function_address('playback_failed')))
        cls.poll_audio = staticmethod(C.CFUNCTYPE(C.c_int, C.c_int)(cls.engine.get_function_address('tcp_audio_poll')))
        cls.stats = PlaybackStats.from_address(cls.engine.get_global_value_address('playback_stats'))
        cls.rx = AudioRx.from_address(cls.engine.get_global_value_address('audio_rx'))
        cls.play_bind = staticmethod(C.CFUNCTYPE(C.c_int, C.c_void_p)(cls.engine.get_function_address('playback_bind_i2s')))
        cls.play_begin = staticmethod(C.CFUNCTYPE(C.c_bool, C.c_void_p)(cls.engine.get_function_address('playback_begin')))
        cls.play_end = staticmethod(C.CFUNCTYPE(C.c_bool, C.c_void_p)(cls.engine.get_function_address('playback_end')))
        cls.play_ack = staticmethod(C.CFUNCTYPE(C.c_bool, C.c_void_p)(cls.engine.get_function_address('playback_take_ack')))
        cls.control_poll = staticmethod(C.CFUNCTYPE(C.c_int, C.c_int)(cls.engine.get_function_address('tcp_control_poll')))
        cls.send_ack = staticmethod(C.CFUNCTYPE(C.c_int, C.c_int)(cls.engine.get_function_address('tcp_send_playback_ack')))
        class DmaEvent(C.Structure):
            _fields_ = [('dma_buf', C.c_void_p), ('size', C.c_size_t)]
        cls.DmaEvent = DmaEvent
        cls.on_sent = staticmethod(C.CFUNCTYPE(C.c_bool, C.c_void_p, C.POINTER(DmaEvent), C.c_void_p)(
            cls.engine.get_function_address('playback_on_sent')))
        cls.eof_count = C.c_uint32.from_address(cls.engine.get_global_value_address('dma_eof_count'))
        cls.play_cancel = staticmethod(C.CFUNCTYPE(C.c_bool, C.c_void_p)(cls.engine.get_function_address('playback_cancel')))
        cls.cancel_ack = staticmethod(C.CFUNCTYPE(C.c_bool, C.c_void_p)(cls.engine.get_function_address('playback_take_cancel_ack')))

    def setUp(self):
        type(self).current = self
        self.sent, self.played = bytearray(), bytearray()
        self.send_steps, self.recv_steps, self.write_steps = [], [], []
        self.incoming = bytearray()
        self.allocations, self.freed = [], []
        self.fail_alloc = False
        self.recv_calls = self.write_calls = self.delays = 0
        self.error.value = 0
        self.now_us = 0
        self.send_advance = self.recv_advance = 0
        self.send_default = self.recv_default = None
        self.connect_result, self.connect_errno = 0, 0
        self.select_result, self.select_errno, self.socket_error = 1, 0, 0
        self.getsockopt_result = 0
        self.wifi_bits, self.wifi_attempts = 1, 0
        self.fcntl_steps, self.fcntl_calls = [], []
        self.lock = threading.Lock()
        self.play_lock = threading.Lock()
        self.dma_lock = threading.Lock()
        self.dma_info_result = self.dma_register_result = 0
        self.dma_bytes = 2880  # Host fixture only; production reads real channel info.
        self.channel_enabled = True
        self.disable_result = self.enable_result = 0
        self.preload_steps = []
        self.preload_advance = 0
        self.preload_offset = 0
        self.dma_storage = bytearray(b'x' * self.dma_bytes)
        self.channel_ops = []
        self.play_queue, self.play_returned, self.play_buffers, self.play_borrowed = [], [], [], {}
        self.play_used = 0
        self.fail_ring_create = self.fail_mutex_create = False
        self.deleted_rings, self.deleted_mutexes = [], []
        self.ring_creates = []
        self.write_timeouts = []
        self.write_advance = 0
        self.queue, self.returned = [], []
        self.ring_buffers = []
        self.mic_flag.value, self.mic_epoch.value = False, 1
        C.memset(C.addressof(self.stats), 0, C.sizeof(PlaybackStats))
        C.memset(C.addressof(self.rx), 0, C.sizeof(AudioRx))
        self.assertTrue(self.play_init(None))
        self.assertEqual(self.play_bind(None), 0)
        self.eof_count.value = 0
        self.emit_dma()
        self.play_set(True)

    def send_mic(self, fd, data, length):
        return self.send_mic_until(fd, data, length, self.now_us + 10000000)

    def timer(self):
        return self.now_us

    def dma_take(self):
        self.dma_lock.acquire()

    def dma_give(self):
        self.dma_lock.release()

    def dma_info(self, channel, info):
        info[0] = self.dma_bytes
        return self.dma_info_result

    def dma_register(self, *args):
        return self.dma_register_result

    def i2s_disable(self, channel):
        self.channel_ops.append('disable')
        if self.disable_result == 0:
            self.channel_enabled = False
            self.preload_offset = 0
        return self.disable_result

    def i2s_preload(self, channel, data, size, loaded):
        self.channel_ops.append(('preload', size))
        self.now_us += self.preload_advance
        result, count = self.preload_steps.pop(0) if self.preload_steps else (0, size)
        loaded[0] = count
        if result == 0 and 0 < count <= size:
            self.dma_storage[self.preload_offset:self.preload_offset+count] = C.string_at(data, count)
            self.preload_offset += count
        return result

    def i2s_enable(self, channel):
        self.channel_ops.append('enable')
        if self.enable_result == 0: self.channel_enabled = True
        return self.enable_result

    def emit_dma(self, count=1, size=480):
        for _ in range(count):
            event = self.DmaEvent(None, size)
            self.on_sent(None, C.byref(event), None)

    def wifi_connect(self):
        self.wifi_attempts += 1
        return 0

    def wifi_clear(self, group, bits):
        self.wifi_bits &= ~bits
        return self.wifi_bits

    def wifi_set(self, group, bits):
        self.wifi_bits |= bits
        return self.wifi_bits

    def fcntl(self, fd, command, flags):
        self.fcntl_calls.append((command, flags))
        return self.fcntl_steps.pop(0) if self.fcntl_steps else 0

    def connect(self, *args):
        self.error.value = self.connect_errno
        return self.connect_result

    def select(self, nfds, reads, writes, errors, timeout):
        self.now_us += 10000000 if self.select_result <= 0 else 1
        self.error.value = self.select_errno
        return self.select_result

    def getsockopt(self, fd, level, option, value, size):
        C.cast(value, C.POINTER(C.c_int))[0] = self.socket_error
        return self.getsockopt_result

    def take(self, semaphore, ticks):
        (self.play_lock if semaphore == 2 else self.lock).acquire()
        return 1

    def give(self, semaphore):
        (self.play_lock if semaphore == 2 else self.lock).release()
        return 1

    def create_mutex(self):
        return None if self.fail_mutex_create else 2

    def delete_mutex(self, semaphore):
        self.deleted_mutexes.append(semaphore)

    def create_ring(self, size, kind):
        self.ring_creates.append(size)
        return None if self.fail_ring_create else 2

    def delete_ring(self, ring):
        self.deleted_rings.append(ring)

    def ring_receive(self, ring, size, ticks):
        if ring == 2:
            if self.play_borrowed:
                return None  # Real BYTEBUF forbids another outstanding receive.
            if not self.play_queue: return None
            data = self.play_queue.pop(0)
            size[0] = len(data.raw)
            self.play_borrowed[C.addressof(data)] = size[0]
            return C.addressof(data)
        if not self.queue: return None
        data = self.queue.pop(0)
        size[0] = len(data.raw)
        return C.addressof(data)

    def ring_return(self, ring, item):
        if ring == 2:
            self.play_returned.append(item)
            self.play_used -= self.play_borrowed.pop(item)
            return
        self.returned.append(item)

    def ring_receive_up_to(self, ring, size, ticks, maximum):
        # Every enqueued production fragment is <= maximum; keeping separate
        # fragments here is a legal BYTEBUF receive segmentation.
        return self.ring_receive(ring, size, ticks)

    def ring_send(self, ring, pcm, size, ticks):
        data = C.create_string_buffer(C.string_at(pcm, size), size)
        if ring == 2:
            if self.play_used + size > 16384: return 0
            self.play_used += size
            self.play_buffers.append(data)
            self.play_queue.append(data)
            return 1
        self.ring_buffers.append(data)
        self.queue.append(data)
        return 1

    def send(self, fd, data, length, flags):
        self.now_us += self.send_advance
        count = self.send_steps.pop(0) if self.send_steps else (self.send_default if self.send_default is not None else length)
        if isinstance(count, tuple):
            self.error.value = count[1]
            return count[0]
        if count > 0:
            self.sent.extend(C.string_at(data, count))
        return count

    def recv(self, fd, data, length, flags):
        self.recv_calls += 1
        self.now_us += self.recv_advance
        count = self.recv_steps.pop(0) if self.recv_steps else (self.recv_default if self.recv_default is not None else min(length, len(self.incoming)))
        if isinstance(count, tuple):
            self.error.value = count[1]
            return count[0]
        if count > 0:
            C.memmove(data, bytes(self.incoming[:count]), count)
            del self.incoming[:count]
        return count

    def alloc(self, length):
        if self.fail_alloc:
            return None
        buf = C.create_string_buffer(length)
        self.allocations.append(buf)
        return C.addressof(buf)

    def free(self, pointer):
        self.freed.append(pointer)

    def delay(self, ticks):
        self.delays += 1
        self.now_us += ticks * 1000

    def write(self, channel, data, length, written, timeout):
        self.write_calls += 1
        self.write_timeouts.append(timeout)
        self.now_us += self.write_advance
        status, count = self.write_steps.pop(0) if self.write_steps else (0, length)
        written[0] = count
        if status in (0, 0x107) and 0 < count <= length:
            self.played.extend(C.string_at(data, count))
        return status

    def packet(self, length, kind=0x12):
        # Legacy regression scenarios drive the real asynchronous producer and
        # consumer cooperatively; new tests below schedule them independently.
        self.play_set(True)
        header = C.create_string_buffer(bytes([kind]) + length.to_bytes(4, 'big'))
        result = self.handle(7, header)
        if result < 0 or kind != 0x12 or length == 0: return result
        for _ in range(20000):
            result = self.poll_audio(7)
            if result < 0:
                self.play_set(False)
                return -1
            if self.play_step() < 0: return -1
            if result == 1:
                while self.play_queue:
                    if self.play_step() < 0: return -1
                return 0
            self.now_us += 1000
        self.fail('audio poll exceeded test iteration budget')

    def test_short_sends_preserve_header_and_payload(self):
        payload = b'\x01\x02\x03\x04'
        self.send_steps = [1, 2, 2, 1, 3]
        self.assertEqual(self.send_mic(7, payload, len(payload)), 0)
        self.assertEqual(self.sent, b'\x02\0\0\0\x04' + payload)

    def test_interrupted_send_retries_same_bytes(self):
        self.send_steps = [(-1, 4), 5, (-1, 4), 2]
        self.assertEqual(self.send_mic(7, b'ab', 2), 0)
        self.assertEqual(self.sent, b'\x02\0\0\0\x02ab')

    def test_send_zero_and_errors_abort_without_next_frame(self):
        for failure in (0, (-1, 104)):
            with self.subTest(failure=failure):
                self.sent.clear()
                self.send_steps = [2, failure]
                self.assertEqual(self.send_mic(7, b'ab', 2), -1)
                self.assertEqual(self.sent, b'\x02\0')

    def test_mic_lengths_are_checked_before_send(self):
        for size in (0, 1, 3, 32001, 32002):
            with self.subTest(size=size):
                self.assertEqual(self.send_mic(7, b'', size), -1)
        self.assertEqual(self.sent, b'')

    def test_maximum_mic_packet_remains_server_compatible(self):
        pcm = b'ab' * 16000
        self.assertEqual(self.send_mic(7, pcm, len(pcm)), 0)
        self.assertEqual(self.sent[:5], b'\x02\0\0\x7d\0')
        self.assertEqual(self.sent[5:], pcm)

    def test_fragmented_recv_and_partial_i2s_preserve_order(self):
        self.incoming = bytearray(b'abcdefgh')
        self.recv_steps = [1, (-1, 4), 3, (-1, 11), 4]
        self.write_steps = [(0, 2), (0, 4), (0, 2)]
        self.assertEqual(self.packet(8), 0)
        self.assertEqual(self.played, b'abcdefgh')
        self.assertEqual(self.delays, 0)  # EAGAIN now returns to TCP/mic loop.
        self.assertEqual(len(self.freed), 0)

    def test_pcm_receive_has_no_per_packet_malloc_failure(self):
        self.fail_alloc = True
        self.incoming = bytearray(b'abcd')
        self.assertEqual(self.packet(4), 0)
        self.assertEqual(self.played, b'abcd')
        self.assertEqual(self.allocations, [])
        self.assertEqual(self.freed, [])

    def test_invalid_lengths_are_rejected_before_allocation(self):
        for size in (1, 3, 16383, 16385, 16386, 0x80000000, 0xffffffff):
            with self.subTest(size=size):
                self.assertEqual(self.packet(size), -1)
        self.assertEqual(self.allocations, [])

    def test_empty_audio_and_control_packets_are_noops(self):
        for kind in (0x12, 0x11, 0x13, 0x99):
            self.assertEqual(self.packet(0, kind), 0)
        self.assertEqual(self.allocations, [])
        self.assertEqual(self.write_calls, 0)

    def test_unexpected_control_payload_aborts(self):
        for kind in (0x11, 0x13, 0x99):
            self.assertEqual(self.packet(2, kind), -1)
        self.assertEqual(self.recv_calls, 0)

    def test_maximum_audio_packet(self):
        self.incoming = bytearray(b'ab' * 8192)
        self.assertEqual(self.packet(16384), 0)
        self.assertEqual(len(self.played), 16384)
        self.assertEqual(len(self.freed), 0)

    def test_recv_eof_and_fatal_error_free_buffer_without_playback(self):
        for failure in (0, (-1, 104)):
            with self.subTest(failure=failure):
                self.recv_steps = [2, failure]
                self.incoming = bytearray(b'abcd')
                self.assertEqual(self.packet(4), -1)
        self.assertEqual(self.write_calls, 0)
        self.assertEqual(len(self.freed), 0)

    def test_i2s_error_zero_progress_odd_and_excess_counts_abort_and_free(self):
        for result in ((1, 0), (1, 2), (0, 0), (0, 1), (0, 6)):
            with self.subTest(result=result):
                self.incoming = bytearray(b'abcd')
                self.write_steps = [result]
                self.assertEqual(self.packet(4), -1)
        self.assertEqual(len(self.freed), 0)

    def test_send_backpressure_is_retried_but_has_total_deadline(self):
        self.send_steps = [(-1, 11), 5, 2]
        self.assertEqual(self.send_mic(7, b'ab', 2), 0)
        self.assertEqual(self.sent, b'\x02\0\0\0\x02ab')
        self.sent.clear()
        self.send_default = (-1, 11)
        started = self.now_us
        self.assertEqual(self.send_mic(7, b'ab', 2), -1)
        self.assertEqual(self.now_us - started, 10000000)

    def test_send_trickle_does_not_reset_deadline_between_header_and_body(self):
        self.send_steps = [1] * 10
        self.send_advance = 2000000
        self.assertEqual(self.send_mic(7, b'ab', 2), -1)
        self.assertEqual(self.now_us, 10000000)
        self.assertEqual(len(self.sent), 5)  # No late body starts a broken frame.

    def test_body_timeout_frees_buffer_without_playback(self):
        self.recv_default = (-1, 11)
        self.assertEqual(self.packet(4), -1)
        self.assertEqual(self.now_us, 10000000)
        self.assertEqual(len(self.freed), 0)
        self.assertEqual(self.played, b'')

    def test_body_trickle_and_late_last_fragment_are_rejected(self):
        self.incoming = bytearray(b'abcd')
        self.recv_steps = [1] * 4
        self.recv_advance = 2500000
        self.assertEqual(self.packet(4), -1)
        self.assertGreaterEqual(self.now_us, 10000000)
        self.assertLessEqual(self.now_us, 10005000)  # Cooperative polling adds scheduler ticks.
        self.assertEqual(len(self.freed), 0)
        self.assertEqual(self.write_calls, 0)

    def test_partial_header_has_deadline_and_resets_on_new_header(self):
        header, position, started = C.create_string_buffer(5), C.c_int(), C.c_int64()
        self.incoming = bytearray(b'\x12\0\0\0\x02')
        self.recv_steps = [1]
        self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), 0)
        self.now_us = 10000000
        self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), -1)
        position.value = 0  # Same reset used for a fresh connected socket.
        self.incoming = bytearray(b'\x11\0\0\0\0')
        self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), 1)
        self.assertEqual(header.raw, b'\x11\0\0\0\0')

    def test_idle_downlink_does_not_disconnect_during_long_tts(self):
        header, position, started = C.create_string_buffer(5), C.c_int(), C.c_int64()
        self.recv_default = (-1, 11)
        for seconds in (0, 10, 60):
            self.now_us = seconds * 1000000
            self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), 0)

    def test_header_fragmentation_eintr_eof_and_fatal_error(self):
        for failure in (0, (-1, 104)):
            with self.subTest(failure=failure):
                header, position, started = C.create_string_buffer(5), C.c_int(), C.c_int64()
                self.incoming = bytearray(b'\x11\0\0\0\0')
                self.recv_steps = [2, (-1, 4), failure]
                self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), 0)
                self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), 0)
                self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), -1)

    def test_connect_immediate_and_async_success_restore_flags(self):
        for result, error in ((0, 0), (-1, 115)):
            with self.subTest(result=result):
                self.fcntl_calls.clear()
                self.connect_result, self.connect_errno = result, error
                self.assertEqual(self.connect_until(7, None, 0), 0)
                self.assertEqual(self.fcntl_calls, [(3, 0), (4, 2048), (4, 0)])

    def test_connect_timeout_interruption_and_refusal_are_bounded(self):
        self.connect_result, self.connect_errno = -1, 115
        for select_result, select_errno, socket_error in ((0, 0, 0), (-1, 4, 0), (1, 0, 111)):
            with self.subTest(select_result=select_result, socket_error=socket_error):
                self.now_us = 0
                self.select_result, self.select_errno = select_result, select_errno
                self.socket_error = socket_error
                self.assertEqual(self.connect_until(7, None, 0), -1)
                self.assertLessEqual(self.now_us, 10000000)

    def test_connect_fcntl_failures_and_immediate_error(self):
        for sequence in ([-1], [0, -1], [0, 0, -1]):
            with self.subTest(sequence=sequence):
                self.fcntl_steps = sequence.copy()
                self.assertEqual(self.connect_until(7, None, 0), -1)
        self.connect_result, self.connect_errno = -1, 111
        self.assertEqual(self.connect_until(7, None, 0), -1)

    def test_connect_socket_error_query_failure_is_not_connected(self):
        self.connect_result, self.connect_errno = -1, 115
        self.getsockopt_result = -1
        self.assertEqual(self.connect_until(7, None, 0), -1)

    def test_header_trickle_at_deadline_is_rejected(self):
        header, position, started = C.create_string_buffer(5), C.c_int(), C.c_int64()
        self.incoming = bytearray(b'\x11\0\0\0\0')
        self.recv_steps, self.recv_advance = [1] * 5, 2500000
        for _ in range(4):
            self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), 0)
        self.assertEqual(self.receive_header(7, header, C.byref(position), C.byref(started)), -1)

    def test_wifi_disconnect_clears_ready_bit_until_got_ip(self):
        self.event(None, 1, 2, None)
        self.assertEqual(self.wifi_bits, 0)
        self.assertEqual(self.wifi_attempts, 1)
        self.event(None, 2, 3, None)
        self.assertEqual(self.wifi_bits, 1)
        self.assertEqual(self.capture(), 0)  # Wi-Fi ready alone is not TCP connected.

    def test_disconnected_and_preconnect_capture_never_enters_queue(self):
        disconnected_token = self.capture()
        self.assertEqual(disconnected_token, 0)
        self.enqueue(b'ab', 2, disconnected_token)
        self.assertEqual(self.queue, [])
        self.set_connected(True)
        self.enqueue(b'ab', 2, disconnected_token)
        self.assertEqual(self.queue, [])

    def test_reconnect_drains_queue_and_rejects_old_inflight_capture(self):
        self.set_connected(True)
        old = self.capture()
        self.enqueue(b'ab', 2, old)
        self.assertEqual(len(self.queue), 1)
        self.set_connected(False)
        self.assertEqual(self.queue, [])
        self.assertEqual(len(self.returned), 1)
        self.set_connected(True)
        self.enqueue(b'cd', 2, old)
        self.assertEqual(self.queue, [])
        current = self.capture()
        self.assertNotEqual(current, old)
        self.enqueue(b'ef', 2, current)
        self.assertEqual(self.queue[0].raw, b'ef')

    def test_epoch_wrap_never_publishes_disconnected_token(self):
        self.mic_epoch.value = 0xffffffff
        self.set_connected(True)
        self.assertEqual(self.capture(), 1)

    def test_disconnect_is_serialized_with_producer_and_clears_its_packet(self):
        self.set_connected(True)
        token = self.capture()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        original = self.ring_send
        def blocked_send(*args):
            entered.set()
            release.wait(2)
            return original(*args)
        self.ring_send = blocked_send
        producer = threading.Thread(target=lambda: self.enqueue(b'ab', 2, token))
        closer = threading.Thread(target=lambda: (self.set_connected(False), closed.set()))
        producer.start()
        try:
            self.assertTrue(entered.wait(2))
            closer.start()
            self.assertFalse(closed.wait(0.02))
        finally:
            release.set()
            producer.join(2)
            if closer.ident is not None: closer.join(2)
        self.assertFalse(producer.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(self.queue, [])
        self.assertEqual(len(self.returned), 1)

    def begin_audio(self, data):
        self.incoming = bytearray(data)
        header = C.create_string_buffer(b'\x12' + len(data).to_bytes(4, 'big'))
        self.assertEqual(self.handle(7, header), 0)

    def test_tcp_audio_producer_never_calls_i2s(self):
        self.begin_audio(b'abcd')
        self.assertEqual(self.poll_audio(7), 1)
        self.assertEqual(self.write_calls, 0)
        self.assertEqual(self.stats.queued_bytes, 4)
        self.assertEqual(self.send_mic(7, b'mi', 2), 0)
        self.assertEqual(self.play_step(), 1)
        self.assertEqual(self.played, b'abcd')
        self.assertEqual(self.stats.bytes_written, 4)
        self.assertEqual(self.write_timeouts, [20])

    def test_queue_backpressure_preserves_pending_pcm_and_mic_uplink(self):
        initial = b'ab' * 8192
        for offset in range(0, len(initial), 2048):
            self.assertEqual(self.play_enqueue(initial[offset:offset+2048], 2048), 1)
        self.assertEqual(self.stats.high_water_bytes, 16384)
        self.begin_audio(b'cd' * 2048)
        self.assertEqual(self.poll_audio(7), 0)
        self.assertEqual(self.rx.buffered, 2048)
        reads = self.recv_calls
        self.assertEqual(self.poll_audio(7), 0)
        self.assertEqual(self.recv_calls, reads)  # Do not read unboundedly into a full queue.
        self.assertEqual(self.send_mic(7, b'mi', 2), 0)
        for _ in range(20):
            self.play_step()
            result = self.poll_audio(7)
            self.assertLessEqual(self.play_used, 16384)
            if result == 1: break
        self.assertEqual(result, 1)
        while self.play_queue: self.play_step()
        self.assertEqual(self.played, initial + b'cd' * 2048)
        self.assertEqual(self.play_used, 0)
        self.assertGreater(self.stats.backpressure, 0)

    def test_queue_pressure_remains_subject_to_body_deadline(self):
        for _ in range(8): self.assertEqual(self.play_enqueue(b'ab' * 1024, 2048), 1)
        self.begin_audio(b'cd' * 1024)
        self.assertEqual(self.poll_audio(7), 0)
        self.now_us = 10000000
        self.assertEqual(self.poll_audio(7), -1)
        self.play_set(False)
        self.assertEqual(self.play_used, 0)
        self.assertEqual(self.stats.queued_bytes, 0)

    def test_empty_queue_counts_one_starvation_episode_and_resumes_short_tail(self):
        self.assertEqual(self.play_step(), 0)
        self.assertEqual(self.stats.starvation, 0)
        self.assertEqual(self.play_enqueue(b'ab', 2), 1)
        self.assertEqual(self.play_step(), 1)
        self.assertEqual(self.play_step(), 0)
        self.assertEqual(self.play_step(), 0)
        self.assertEqual(self.stats.starvation, 1)
        self.assertEqual(self.play_enqueue(b'cd', 2), 1)
        self.assertEqual(self.play_step(), 1)  # No EOS/prebuffer requirement for tiny tails.
        self.assertEqual(self.played, b'abcd')

    def test_timeout_with_partial_i2s_progress_is_not_replayed(self):
        self.write_steps = [(0x107, 2), (0, 2)]
        self.assertEqual(self.play_enqueue(b'abcd', 4), 1)
        self.assertEqual(self.play_step(), 1)
        self.assertEqual(self.played, b'abcd')
        self.assertFalse(self.play_failed())
        self.assertEqual(self.stats.bytes_written, 4)

    def test_repeated_i2s_timeout_is_bounded_and_reports_failure(self):
        self.write_steps = [(0x107, 0)] * 30
        self.assertEqual(self.play_enqueue(b'ab', 2), 1)
        self.assertEqual(self.play_step(), -1)
        self.assertEqual(self.now_us, 250000)
        self.assertTrue(self.play_failed())
        self.assertEqual(self.stats.write_errors, 1)
        self.assertEqual(self.play_used, 0)
        self.assertEqual(set(self.write_timeouts), {20})
        self.assertEqual(self.play_enqueue(b'cd', 2), -1)

    def test_i2s_failure_propagates_to_network_and_recovers_on_new_generation(self):
        self.write_steps = [(1, 0)]
        self.play_enqueue(b'ab', 2)
        self.assertEqual(self.play_step(), -1)
        self.begin_audio(b'cd')
        self.assertEqual(self.poll_audio(7), -1)
        self.play_set(False)
        self.play_set(True)
        self.begin_audio(b'ef')
        self.assertEqual(self.poll_audio(7), 1)
        self.assertEqual(self.play_step(), 1)
        self.assertEqual(self.played, b'ef')

    def test_invalid_queue_fragments_are_rejected_without_growth(self):
        for size in (0, 1, 3, 2049, 4096):
            self.assertEqual(self.play_enqueue(b'', size), -1)
        self.assertEqual(self.stats.queued_bytes, 0)
        self.assertEqual(self.play_used, 0)
        self.play_set(False)
        self.assertEqual(self.play_enqueue(b'ab', 2), -1)

    def test_playback_initialization_failure_releases_created_resources(self):
        self.play_deinit()
        self.deleted_mutexes.clear()
        self.fail_mutex_create = True
        self.assertFalse(self.play_init(None))
        self.assertEqual(self.deleted_mutexes, [])
        self.fail_mutex_create = False
        self.fail_ring_create = True
        self.assertFalse(self.play_init(None))
        self.assertEqual(self.deleted_mutexes, [2])
        self.fail_ring_create = False
        self.assertTrue(self.play_init(None))

    def test_reconnect_flushes_queued_playback(self):
        self.play_enqueue(b'old!', 4)
        self.play_set(False)
        self.assertEqual(self.play_queue, [])
        self.assertEqual(self.play_used, 0)
        self.play_set(True)
        self.play_enqueue(b'new!', 4)
        self.play_step()
        self.assertEqual(self.played, b'new!')

    def test_blocked_i2s_does_not_hold_queue_mutex_or_block_network(self):
        entered, release = threading.Event(), threading.Event()
        original_write = self.write
        result = []
        def blocked(*args):
            entered.set()
            release.wait(2)
            return original_write(*args)
        self.write = blocked
        self.play_enqueue(b'old!', 4)
        consumer = threading.Thread(target=lambda: result.append(self.play_step()))
        consumer.start()
        try:
            self.assertTrue(entered.wait(2))
            # A second thread exercises real producer/mutex/consumer overlap.
            self.begin_audio(b'next')
            self.assertEqual(self.poll_audio(7), 1)
            self.assertEqual(self.send_mic(7, b'mi', 2), 0)
            self.assertEqual(self.stats.queued_bytes, 4)
            self.assertEqual(self.play_borrowed, {})  # BYTEBUF item already returned.
        finally:
            release.set()
            consumer.join(2)
            self.write = original_write
        self.assertFalse(consumer.is_alive())
        self.assertEqual(result, [1])
        self.play_step()
        self.assertEqual(self.played, b'old!next')

    def test_old_consumer_error_cannot_fail_reconnected_session(self):
        entered, release = threading.Event(), threading.Event()
        original_write = self.write
        def blocked_failure(channel, data, length, written, timeout):
            entered.set()
            release.wait(2)
            written[0] = 0
            return 1
        self.write = blocked_failure
        self.play_enqueue(b'old!', 4)
        consumer = threading.Thread(target=self.play_step)
        consumer.start()
        try:
            self.assertTrue(entered.wait(2))
            self.play_enqueue(b'drop', 4)
            self.play_set(False)
            self.play_set(True)
            self.play_enqueue(b'new!', 4)
        finally:
            release.set()
            consumer.join(2)
            self.write = original_write
        self.assertFalse(consumer.is_alive())
        self.assertFalse(self.play_failed())
        self.assertEqual(self.stats.write_errors, 0)
        self.play_step()
        self.assertEqual(self.played, b'new!')

    def test_disconnect_stops_old_partial_block_at_next_driver_boundary(self):
        original_write = self.write
        def partial_then_disconnect(channel, data, length, written, timeout):
            self.write_steps = [(0, 2)]
            result = original_write(channel, data, length, written, timeout)
            self.play_set(False)
            self.play_set(True)
            return result
        self.write = partial_then_disconnect
        self.play_enqueue(b'abcd', 4)
        self.play_step()
        self.write = original_write
        self.assertEqual(self.played, b'ab')  # Already accepted DMA samples are not retractable.
        self.assertEqual(self.write_calls, 1)
        self.play_enqueue(b'ef', 2)
        self.play_step()
        self.assertEqual(self.played, b'abef')

    def test_repeated_playback_reconnects_reuse_fixed_storage(self):
        for _ in range(50):
            self.play_set(True)
            self.play_enqueue(b'ab' * 1024, 2048)
            self.play_enqueue(b'cd' * 1024, 2048)
            self.play_step()
            self.play_set(False)
            self.assertEqual(self.play_used, 0)
            self.assertEqual(self.play_borrowed, {})
        self.assertEqual(self.ring_creates, [16384])
        self.assertEqual(self.allocations, [])
        self.assertLessEqual(self.stats.high_water_bytes, 16384)

    def test_eos_waits_for_queue_write_and_fresh_dma_barrier(self):
        token = bytes(range(32))
        ack = C.create_string_buffer(32)
        self.assertTrue(self.play_begin(token))
        self.play_enqueue(b'abcd', 4)
        self.assertTrue(self.play_end(token))
        self.emit_dma(100)  # Events before the last write are not completion.
        self.assertFalse(self.play_ack(ack))
        self.play_step()
        self.assertFalse(self.play_ack(ack))
        self.play_step()  # Consumer alone arms the drain barrier after write.
        self.emit_dma(13)
        self.play_step()
        self.assertFalse(self.play_ack(ack))
        self.emit_dma()
        self.play_step()
        self.assertTrue(self.play_ack(ack))
        self.assertEqual(ack.raw, token)
        self.assertFalse(self.play_ack(ack))

    def test_eos_cannot_complete_while_last_i2s_write_is_inflight(self):
        token, ack = b'a' * 32, C.create_string_buffer(32)
        self.assertTrue(self.play_begin(token))
        self.play_enqueue(b'ab', 2)
        entered, release = threading.Event(), threading.Event()
        original_write = self.write
        def delayed(*args):
            entered.set()
            release.wait(2)
            return original_write(*args)
        self.write = delayed
        consumer = threading.Thread(target=self.play_step)
        consumer.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.play_end(token))
            self.emit_dma(100)
            self.assertFalse(self.play_ack(ack))
            self.assertFalse(self.play_begin(b'b' * 32))
        finally:
            release.set()
            consumer.join(2)
            self.write = original_write
        self.assertFalse(consumer.is_alive())
        self.play_step()
        self.assertFalse(self.play_ack(ack))
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.play_ack(ack))

    def test_missing_dma_callbacks_timeout_without_ack(self):
        token, ack = b'a' * 32, C.create_string_buffer(32)
        self.play_begin(token)
        self.play_end(token)
        self.play_step()
        self.now_us += 10000000
        self.play_step()
        self.assertTrue(self.play_failed())
        self.assertFalse(self.play_ack(ack))

    def test_i2s_failure_never_produces_success_ack(self):
        token, ack = b'a' * 32, C.create_string_buffer(32)
        self.play_begin(token)
        self.play_enqueue(b'ab', 2)
        self.play_end(token)
        self.write_steps = [(1, 0)]
        self.play_step()
        self.emit_dma(100)
        self.play_step()
        self.assertTrue(self.play_failed())
        self.assertFalse(self.play_ack(ack))

    def test_mismatched_duplicate_eos_and_data_after_eos_are_rejected(self):
        token = b'a' * 32
        self.assertFalse(self.play_end(token))
        self.assertTrue(self.play_begin(token))
        self.assertFalse(self.play_begin(b'b' * 32))
        self.assertFalse(self.play_end(b'b' * 32))
        self.assertTrue(self.play_end(token))
        self.assertFalse(self.play_end(token))
        self.assertEqual(self.play_enqueue(b'ab', 2), -1)

    def test_reconnect_discards_ready_ack_and_old_drain(self):
        old, new, ack = b'a' * 32, b'b' * 32, C.create_string_buffer(32)
        self.play_begin(old)
        self.play_end(old)
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.play_set(False)
        self.play_set(True)
        self.assertFalse(self.play_ack(ack))
        self.assertTrue(self.play_begin(new))
        self.assertFalse(self.play_end(old))
        self.play_end(new)
        self.play_step()
        self.assertFalse(self.play_ack(ack))
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.play_ack(ack))
        self.assertEqual(ack.raw, new)

    def test_dma_counter_wrap_preserves_completion_barrier(self):
        token, ack = b'a' * 32, C.create_string_buffer(32)
        self.eof_count.value = 0xfffffff8
        self.play_begin(token)
        self.play_end(token)
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.play_ack(ack))

    def test_fragmented_start_stop_metadata_preserves_token_and_mic(self):
        token = bytes(range(32))
        self.incoming = bytearray(token)
        self.assertEqual(self.handle(7, b'\x11\0\0\0\x20'), 0)
        self.recv_steps = [3, (-1, 4), (-1, 11), 29]
        self.assertEqual(self.control_poll(7), 0)
        self.assertEqual(self.send_mic(7, b'mi', 2), 0)
        self.assertEqual(self.control_poll(7), 0)
        self.assertEqual(self.control_poll(7), 0)
        self.assertEqual(self.control_poll(7), 1)
        self.assertEqual(self.handle(7, b'\x13\0\0\0\x20'), 0)
        self.incoming = bytearray(token)
        self.assertEqual(self.control_poll(7), 1)

    def test_control_timeout_disconnect_and_wrong_token_fail_closed(self):
        self.handle(7, b'\x11\0\0\0\x20')
        self.now_us = 10000000
        self.assertEqual(self.control_poll(7), -1)
        self.handle(7, b'\x11\0\0\0\x20')
        self.assertEqual(self.control_poll(7), -1)  # EOF, not a complete token.
        self.play_begin(b'a' * 32)
        self.handle(7, b'\x13\0\0\0\x20')
        self.incoming = bytearray(b'b' * 32)
        self.assertEqual(self.control_poll(7), -1)

    def test_ack_is_written_only_by_tcp_and_short_sends_keep_framing(self):
        token = bytes(range(32))
        self.play_begin(token)
        self.play_end(token)
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.assertEqual(self.sent, b'')
        self.send_steps = [1, 2, 34]
        self.assertEqual(self.send_ack(7), 0)
        self.assertEqual(self.sent, b'\x14\0\0\0\x20' + token)
        self.assertEqual(self.send_ack(7), 0)
        self.assertEqual(len(self.sent), 37)

    def test_bind_failure_is_not_silently_accepted(self):
        self.dma_info_result = 1
        self.assertEqual(self.play_bind(None), 1)

    def cancelled_barrier(self, token):
        self.assertTrue(self.play_cancel(token))
        self.assertEqual(self.play_step(), 0)
        self.emit_dma(14)
        self.assertEqual(self.play_step(), 0)
        ack = C.create_string_buffer(32)
        self.assertTrue(self.cancel_ack(ack))
        self.assertEqual(ack.raw, token)

    def test_cancel_clears_queue_dma_and_fifo_before_cancelled_ack(self):
        token, ack = b'a' * 32, C.create_string_buffer(32)
        self.play_begin(token)
        self.play_enqueue(b'old!' * 512, 2048)
        self.assertTrue(self.play_cancel(token))
        self.assertEqual(self.stats.queued_bytes, 0)
        self.assertEqual(self.play_used, 0)
        self.assertFalse(self.play_ack(ack))
        self.assertFalse(self.cancel_ack(ack))
        self.play_step()
        self.assertEqual(self.channel_ops, ['disable', ('preload', 2048), ('preload', 832), 'enable'])
        self.assertEqual(self.dma_storage, bytes(2880))
        self.assertTrue(self.channel_enabled)
        self.assertFalse(self.cancel_ack(ack))
        self.emit_dma(13)
        self.play_step()
        self.assertFalse(self.cancel_ack(ack))
        self.emit_dma()
        self.play_step()
        self.assertTrue(self.cancel_ack(ack))
        self.assertEqual(ack.raw, token)
        self.assertFalse(self.play_ack(ack))

    def test_cancel_waits_for_old_write_boundary_without_driver_race(self):
        token = b'a' * 32
        self.play_begin(token)
        self.play_enqueue(b'abcd', 4)
        entered, release = threading.Event(), threading.Event()
        original = self.write
        def delayed(*args):
            entered.set()
            release.wait(2)
            return original(*args)
        self.write = delayed
        consumer = threading.Thread(target=self.play_step)
        consumer.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.play_cancel(token))
            self.assertEqual(self.channel_ops, [])  # TCP does not disable an active writer.
            self.assertEqual(self.send_mic(7, b'mi', 2), 0)
        finally:
            release.set()
            consumer.join(2)
            self.write = original
        self.assertFalse(consumer.is_alive())
        self.play_step()
        self.assertEqual(self.dma_storage, bytes(2880))
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.cancel_ack(C.create_string_buffer(32)))

    def test_cancel_near_eos_suppresses_ready_success_ack(self):
        token, ack = b'a' * 32, C.create_string_buffer(32)
        self.play_begin(token)
        self.play_end(token)
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.play_cancel(token))
        self.assertFalse(self.play_ack(ack))
        self.cancelled_barrier(token)

    def test_cancel_can_confirm_silence_after_success_ack_was_already_sent(self):
        token, ack = b'a' * 32, C.create_string_buffer(32)
        self.play_begin(token)
        self.play_end(token)
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.play_ack(ack))
        self.cancelled_barrier(token)  # Server may have rejected that normal ACK after a local cancel.

    def test_stale_cancel_never_flushes_new_response(self):
        old, new = b'a' * 32, b'a' * 16 + b'b' * 16
        self.play_begin(old)
        self.cancelled_barrier(old)
        self.assertTrue(self.play_begin(new))
        self.play_enqueue(b'new!', 4)
        self.assertFalse(self.play_cancel(old))
        self.assertEqual(self.stats.queued_bytes, 4)
        self.play_step()
        self.assertEqual(self.played, b'new!')

    def test_cancelled_pcm_cannot_restart_without_a_new_start(self):
        old, new = b'a' * 32, b'a' * 16 + b'b' * 16
        self.play_begin(old)
        self.cancelled_barrier(old)
        self.assertEqual(self.play_enqueue(b'old!', 4), -1)
        self.assertFalse(self.play_begin(old))  # Exact response replay.
        self.assertTrue(self.play_begin(new))
        self.play_enqueue(b'new!', 4)
        self.play_step()
        self.assertEqual(self.played, b'new!')

    def test_start_must_keep_connection_prefix_and_increase_response_counter(self):
        token = b'a' * 32
        self.play_begin(token)
        self.cancelled_barrier(token)
        self.assertFalse(self.play_begin(b'b' * 32))
        self.assertFalse(self.play_begin(b'a' * 16 + b'0' * 16))
        self.play_set(False)
        self.play_step()
        self.play_set(True)
        self.assertTrue(self.play_begin(b'b' * 32))  # New TCP generation accepts its own prefix.

    def test_duplicate_cancel_is_idempotent_before_and_after_cancel_ack(self):
        token = b'a' * 32
        self.play_begin(token)
        self.assertTrue(self.play_cancel(token))
        self.assertTrue(self.play_cancel(token))
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.cancel_ack(C.create_string_buffer(32)))
        operations = self.channel_ops.copy()
        self.assertTrue(self.play_cancel(token))
        self.assertTrue(self.cancel_ack(C.create_string_buffer(32)))
        self.assertEqual(self.channel_ops, operations)

    def test_cancel_metadata_and_cancelled_wire_frame_keep_exact_id(self):
        token = bytes(range(32))
        self.play_begin(token)
        self.handle(7, b'\x15\0\0\0\x20')
        self.incoming = bytearray(token)
        self.recv_steps = [7, (-1, 11), 25]
        self.assertEqual(self.control_poll(7), 0)
        self.assertEqual(self.control_poll(7), 0)
        self.assertEqual(self.control_poll(7), 1)
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.send_steps = [2, 3, 32]
        self.assertEqual(self.send_ack(7), 0)
        self.assertEqual(self.sent, b'\x16\0\0\0\x20' + token)

    def test_stale_cancel_metadata_is_ignored_without_killing_current_reply(self):
        self.play_begin(b'a' * 32)
        self.play_enqueue(b'new!', 4)
        self.handle(7, b'\x15\0\0\0\x20')
        self.incoming = bytearray(b'b' * 32)
        self.assertEqual(self.control_poll(7), 1)
        self.assertEqual(self.stats.queued_bytes, 4)
        self.assertFalse(self.play_failed())

    def test_cancel_preload_zero_progress_fails_muted_without_ack(self):
        token = b'a' * 32
        self.play_begin(token)
        self.play_cancel(token)
        self.preload_steps = [(0, 0)]
        self.assertEqual(self.play_step(), -1)
        self.assertFalse(self.channel_enabled)
        self.assertTrue(self.play_failed())
        self.assertFalse(self.cancel_ack(C.create_string_buffer(32)))
        self.play_set(False)
        self.play_set(True)
        self.assertTrue(self.play_failed())  # No unsafe replay via automatic re-enable.
        self.assertFalse(self.play_begin(b'b' * 32))

    def test_cancel_disable_failure_never_reports_physical_stop(self):
        self.play_begin(b'a' * 32)
        self.play_cancel(b'a' * 32)
        self.disable_result = 1
        self.assertEqual(self.play_step(), -1)
        self.assertFalse(self.cancel_ack(C.create_string_buffer(32)))
        self.assertTrue(self.play_failed())

    def test_cancel_enable_failure_does_not_publish_ack(self):
        self.play_begin(b'a' * 32)
        self.play_cancel(b'a' * 32)
        self.enable_result = 1
        self.assertEqual(self.play_step(), -1)
        self.assertFalse(self.channel_enabled)
        self.assertFalse(self.cancel_ack(C.create_string_buffer(32)))

    def test_cancelled_ack_requires_fresh_dma_events_and_has_deadline(self):
        self.play_begin(b'a' * 32)
        self.play_cancel(b'a' * 32)
        self.emit_dma(100)
        self.play_step()
        self.assertFalse(self.cancel_ack(C.create_string_buffer(32)))
        self.now_us += 2000000
        self.assertEqual(self.play_step(), -1)
        self.assertFalse(self.cancel_ack(C.create_string_buffer(32)))
        self.assertFalse(self.play_ack(C.create_string_buffer(32)))

    def test_cancel_waits_for_first_known_dma_size_instead_of_false_success(self):
        self.emit_dma(size=0)
        self.play_begin(b'a' * 32)
        self.play_cancel(b'a' * 32)
        self.play_step()
        self.assertFalse(self.cancel_ack(C.create_string_buffer(32)))
        self.emit_dma()
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.assertTrue(self.cancel_ack(C.create_string_buffer(32)))

    def test_disconnect_resets_dma_and_discards_cancelled_ack_on_reconnect(self):
        old, new = b'a' * 32, b'b' * 32
        self.play_begin(old)
        self.play_enqueue(b'old!', 4)
        self.play_cancel(old)
        self.play_step()
        self.emit_dma(14)
        self.play_step()
        self.play_set(False)
        self.play_step()
        self.assertEqual(self.dma_storage, bytes(2880))
        self.play_set(True)
        self.assertFalse(self.cancel_ack(C.create_string_buffer(32)))
        self.assertTrue(self.play_begin(new))
        self.play_enqueue(b'new!', 4)
        self.play_step()
        self.assertEqual(self.played, b'new!')
        self.dma_info_result, self.dma_bytes = 0, 0
        self.assertNotEqual(self.play_bind(None), 0)
        self.dma_bytes, self.dma_register_result = 2880, 1
        self.assertEqual(self.play_bind(None), 1)


if __name__ == '__main__':
    unittest.main()
