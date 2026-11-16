"""Read-only Windows render-session meters. No recording or volume setters."""
import ctypes
import math
import os
import threading
import time
import uuid

from .model import CircuitError


class GUID(ctypes.Structure):
    _fields_ = [('data1',ctypes.c_uint32), ('data2',ctypes.c_uint16),
                ('data3',ctypes.c_uint16), ('data4',ctypes.c_ubyte*8)]

    @classmethod
    def from_text(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


def check_hr(value):
    if value < 0:
        raise CircuitError('AUDIO_COM_ERROR: 0x%08x' % (value & 0xffffffff))


class COM:
    def __init__(self, pointer):
        self.pointer = ctypes.c_void_p(pointer) if isinstance(pointer,int) else pointer

    def call(self, index, result, types=(), args=()):
        table = ctypes.cast(self.pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(result,ctypes.c_void_p,*types)(table[index])
        return method(self.pointer,*args)

    def query(self, name):
        iid, out = GUID.from_text(name), ctypes.c_void_p()
        check_hr(self.call(0,ctypes.c_long,(ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)),
                           (ctypes.byref(iid),ctypes.byref(out))))
        return COM(out)

    def output(self, index, kind, types=(), args=()):
        value = kind()
        check_hr(self.call(index,ctypes.c_long,(*types,ctypes.POINTER(kind)),(*args,ctypes.byref(value))))
        return value.value

    def close(self):
        if self.pointer:
            self.call(2,ctypes.c_ulong)
            self.pointer = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


class WindowsAudio:
    def __enter__(self):
        if os.name != 'nt':
            raise CircuitError('AUDIO_PLATFORM_UNSUPPORTED: Windows render-session meters only')
        self.ole = ctypes.OleDLL('ole32')
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p,ctypes.c_uint32]
        self.ole.CoInitializeEx.restype = ctypes.c_long
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID),ctypes.c_void_p,ctypes.c_uint32,
                                             ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
        self.ole.CoCreateInstance.restype = ctypes.c_long
        self.ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self.ole.CoUninitialize.argtypes = []
        check_hr(self.ole.CoInitializeEx(None,0))
        self.initialized = True
        clsid = GUID.from_text('BCDE0395-E52F-467C-8E3D-C4579291692E')
        iid = GUID.from_text('A95664D2-9614-4F35-A746-DE8DB63617E6')
        pointer = ctypes.c_void_p()
        try:
            check_hr(self.ole.CoCreateInstance(ctypes.byref(clsid),None,23,ctypes.byref(iid),ctypes.byref(pointer)))
            self.enumerator = COM(pointer)
        except Exception:
            self.ole.CoUninitialize()
            self.initialized = False
            raise
        return self

    def __exit__(self, *unused):
        self.enumerator.close()
        if self.initialized:
            self.ole.CoUninitialize()

    def read(self, pid):
        rows, errors = [], []
        # eRender=0, DEVICE_STATE_ACTIVE=1. Capture/microphone endpoints are never enumerated.
        devices = self.enumerator.output(3,ctypes.c_void_p,(ctypes.c_int,ctypes.c_uint32),(0,1))
        with COM(devices) as collection:
            for index in range(collection.output(3,ctypes.c_uint32)):
                try:
                    with COM(collection.output(4,ctypes.c_void_p,(ctypes.c_uint32,),(index,))) as device:
                        name = device.output(5,ctypes.c_void_p)
                        try:
                            endpoint = ctypes.wstring_at(name)
                        finally:
                            self.ole.CoTaskMemFree(name)
                        iid = GUID.from_text('77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F')
                        manager = device.output(3,ctypes.c_void_p,
                            (ctypes.POINTER(GUID),ctypes.c_uint32,ctypes.c_void_p),(ctypes.byref(iid),23,None))
                        with COM(manager) as mgr, COM(mgr.output(5,ctypes.c_void_p)) as sessions:
                            for slot in range(sessions.output(3,ctypes.c_int)):
                                with COM(sessions.output(4,ctypes.c_void_p,(ctypes.c_int,),(slot,))) as session:
                                    with session.query('BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D') as control:
                                        owner = control.output(14,ctypes.c_uint32)
                                    if owner != pid:
                                        continue
                                    with session.query('C02216F6-8C67-4B5B-9D00-D008E73E0064') as meter:
                                        peak = meter.output(3,ctypes.c_float)
                                    with session.query('87CE5498-68D6-44E5-9215-6DA47EF883D8') as volume:
                                        level = volume.output(4,ctypes.c_float)
                                        muted = bool(volume.output(6,ctypes.c_int))
                                    if not math.isfinite(peak) or peak < 0 or not math.isfinite(level):
                                        raise CircuitError('AUDIO_INVALID_METER_VALUE')
                                    rows.append({'process_id':owner,'endpoint_id':endpoint,'peak':peak,
                                                 'volume':level,'muted':muted,
                                                 'session_state':session.output(3,ctypes.c_int)})
                except (CircuitError,OSError) as error:
                    errors.append({'endpoint_index':index,'error':str(error)})
        return rows, errors


def summarize_audio(frames, errors=(), threshold=0.001):
    rows = [row for frame in frames for row in frame['sessions']]
    detected = [r for r in rows if r['peak'] > threshold]
    unmuted = [r for r in detected if not r['muted'] and r['volume'] > 0]
    if errors:
        status = 'partial' if frames else 'unavailable'
    elif not rows:
        status = 'no_session'
    elif unmuted:
        status = 'output_detected'
    elif detected or all(r['muted'] or r['volume'] == 0 for r in rows):
        status = 'muted_or_zero_volume'
    else:
        status = 'silent'
    return {'status':status,'poll_count':len(frames),'session_samples':len(rows),
            'nonzero_samples':len(detected),'unmuted_nonzero_samples':len(unmuted),
            'max_peak':max((r['peak'] for r in rows),default=None),
            'endpoint_ids':sorted({r['endpoint_id'] for r in rows}),
            'errors':list(errors),'threshold':threshold,'recording':False,'volume_changed':False,
            'audibility_verified':False,
            'scope':'Per-process Windows render-session meter; not a recording, analogue measurement or listening test'}


class AudioCapture:
    def __init__(self, pid, interval_ms=50, provider=WindowsAudio):
        self.pid, self.interval, self.provider = pid, interval_ms/1000, provider
        self.frames, self.errors = [], []
        self.stopping, self.ready = threading.Event(), threading.Event()

    def _run(self):
        try:
            with self.provider() as audio:
                while not self.stopping.is_set() and len(self.frames) < 10000:
                    start = time.monotonic()
                    rows, errors = audio.read(self.pid)
                    self.frames.append({'wall_time_ms':time.time_ns()//1000000,'sessions':rows})
                    self.errors.extend(errors)
                    self.ready.set()
                    self.stopping.wait(max(0,self.interval-(time.monotonic()-start)))
        except Exception as error:
            self.errors.append({'error':str(error)})
        finally:
            self.ready.set()

    def __enter__(self):
        self.thread = threading.Thread(target=self._run,name='proteus-render-meter',daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.stopping.set()
            raise CircuitError('AUDIO_INITIALIZATION_TIMEOUT')
        return self

    def __exit__(self, *unused):
        self.stopping.set()
        self.thread.join(5)
        if self.thread.is_alive():
            raise CircuitError('AUDIO_SHUTDOWN_TIMEOUT')

    def summary(self):
        return summarize_audio(self.frames,self.errors)
