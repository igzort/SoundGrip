"""
audio_capture.py — захват аудио для SoundGrip (Android + ПК).

Android:
    Системный звук через MediaProjection + AudioRecord (jnius).
    Запускается после подтверждения системного диалога «Запись экрана»
    (для захвата системного звука разрешение на микрофон не требуется).

ПК/десктоп (для отладки на рабочей машине). Автоматически выбирается
первый доступный из списка:
    1) soundcard   -> системный звук (WASAPI loopback на Windows);
    2) soundcard   -> микрофон;
    3) sounddevice -> микрофон;
    4) pyaudio     -> микрофон;
    5) демо-режим  -> синтетические биты каждые 0.5 с (вообще без железа).

Все бекенды возвращают numpy int16, моно, длиной block_size.
"""

import collections
import os
import sys
import threading
import time

import numpy as np


def _is_android():
    try:
        from kivy.utils import platform
    except Exception:
        platform = None
    return platform == 'android'


IS_ANDROID = _is_android()


def _raise_process_priority():
    """Windows: поднять класс приоритета процесса до HIGH_PRIORITY_CLASS.

    Аудио-тред и рендер живут в одном процессе — игра обязана реагировать
    на звук мгновенно. HIGH_PRIORITY_CLASS (0x80) выдаётся обычному
    пользователю без прав администратора (REALTIME 0x100 не пробуем:
    может заморить окна). Обёрнуто в try — неудача не критична.
    """
    if os.name != 'nt':
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        return bool(k.SetPriorityClass(k.GetCurrentProcess(), 0x00000080))
    except Exception:
        return False


def _to_int16_mono(data):
    """Любые данные -> numpy int16 моно.

    Стерео сводится знаковым максимумом амплитуды: берём сэмпл канала
    с большей |амплитудой| (с его знаком). Это защищает от ВЗАИМНОГО
    ВЫЧИТАНИЯ ФАЗ: при сведении (L+R)/2 низкие частоты в противофазе
    каналов (расширение стереобазы в электронике) уничтожаются и
    детектор бочки слепнет.
    """
    arr = np.asarray(data)
    if arr.ndim == 2 and arr.shape[1] > 1:
        m = arr.astype(np.float64)
        idx = np.argmax(np.abs(m), axis=1)
        arr = m[np.arange(len(m)), idx]
    elif arr.ndim == 2:
        arr = arr[:, 0]
    arr = arr.reshape(-1)
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.clip(arr, -1.0, 1.0) * 32767.0  # float[-1..1] -> int16
    return np.clip(arr, -32768, 32767).astype(np.int16)


def _drain_queue(queue, size):
    """Читает НЕ МЕНЕЕ size сэмплов из очереди (для callback-фолбэков).
    Если данных мало — сразу отдаёт то, что есть (никогда не спит), чтобы не
    вводить накопленную задержку."""
    parts = []
    total = 0
    while total < size and queue:
        chunk = _to_int16_mono(queue.popleft())
        if not chunk.size:
            continue
        take = min(len(chunk), size - total)
        parts.append(chunk[:take])
        total += take
        if take < len(chunk):
            queue.appendleft(chunk[take:])
    if total < size:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


# ---------------------------------------------------------------------------
# Android
# ---------------------------------------------------------------------------

class _AndroidCapture:
    """Захват системного аудио на Android (MediaProjection + AudioRecord)."""

    REQUEST_CODE = 1001

    def __init__(self, sample_rate=48000, block_size=1024):
        print(f'[DEBUG] AudioCapture.__init__ called with block_size={block_size}', flush=True)
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.name = 'android: системный звук (MediaProjection)'
        self.recorder = None
        self.error = None

        from jnius import autoclass, cast
        from android.activity import bind as android_bind

        self._cast = cast
        self._bind = android_bind

        self.PythonActivity = autoclass('org.kivy.android.PythonActivity')
        self.Context = autoclass('android.content.Context')
        self.AudioRecord = autoclass('android.media.AudioRecord')
        self.AudioFormat = autoclass('android.media.AudioFormat')
        self.AudioAttributes = autoclass('android.media.AudioAttributes')
        self.AudioPlaybackCaptureConfiguration = autoclass('android.media.AudioPlaybackCaptureConfiguration')
        self.MediaProjectionManager = autoclass('android.media.projection.MediaProjectionManager')

        self.activity = self.PythonActivity.mActivity
        self.channels = self.AudioFormat.CHANNEL_IN_MONO
        self.encoding = self.AudioFormat.ENCODING_PCM_16BIT
        self.buffer_size = self.AudioRecord.getMinBufferSize(
            sample_rate, self.channels, self.encoding)

        try:
            # JVM-дескриптор массива short: '[S'.
            # Раньше было autoclass('[]s') — падало с "Invalid descriptor: ]s".
            array_cls = autoclass('[S')
            self.java_buffer = array_cls(block_size)
        except Exception as e:  # pragma: no cover
            self.error = f'Не удалось создать short[] для AudioRecord: {e}'

    def request_permission(self):
        if self.error or self.java_buffer is None:
            return
        self._bind(on_activity_result=self._on_activity_result)

        manager = self.activity.getSystemService(self.Context.MEDIA_PROJECTION_SERVICE)
        projection_manager = self._cast(
            'android.media.projection.MediaProjectionManager', manager)
        intent = projection_manager.createScreenCaptureIntent()
        self.activity.startActivityForResult(intent, self.REQUEST_CODE)
        print('[SoundGrip] Запрос на захват системного звука отправлен...')

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code != self.REQUEST_CODE or result_code != -1:
            return  # пользователь отклонил диалог
        try:
            manager = self.activity.getSystemService(self.Context.MEDIA_PROJECTION_SERVICE)
            projection_manager = self._cast(
                'android.media.projection.MediaProjectionManager', manager)
            media_projection = projection_manager.getMediaProjection(result_code, intent)
            self.start_capture(media_projection)
        except Exception as e:
            self.error = f'Не удалось получить MediaProjection: {e}'

    def start_capture(self, media_projection):
        try:
            builder = self.AudioPlaybackCaptureConfiguration.Builder(media_projection)
            builder.addMatchingUsage(self.AudioAttributes.USAGE_MEDIA)
            config = builder.build()

            af_builder = self.AudioFormat.Builder()
            af_builder.setEncoding(self.encoding)
            af_builder.setSampleRate(self.sample_rate)
            af_builder.setChannelMask(self.channels)

            record_builder = self.AudioRecord.Builder()
            record_builder.setAudioFormat(af_builder.build())
            record_builder.setBufferSizeInBytes(self.buffer_size)
            record_builder.setAudioPlaybackCaptureConfig(config)

            self.recorder = record_builder.build()
            self.recorder.startRecording()
            print('[SoundGrip] Захват системного аудио запущен!')
        except Exception as e:
            self.error = f'Ошибка инициализации AudioRecord: {e}'

    def get_data(self):
        if self.error:
            return None
        if not self.recorder or self.recorder.getRecordingState() != self.AudioRecord.RECORDSTATE_RECORDING:
            return None

        read_size = self.recorder.read(self.java_buffer, 0, self.block_size)
        if read_size > 0:
            # Читаем элементы из Java-массива в Python-список (надёжно в любых версиях jnius)
            samples = [self.java_buffer[i] for i in range(read_size)]
            return np.array(samples, dtype=np.int16)
        return None


# ---------------------------------------------------------------------------
# ПК: soundcard (системный звук loopback или микрофон)
# ---------------------------------------------------------------------------

class _SoundCardCapture:
    """Системный звук (loopback) либо микрофон через библиотеку soundcard."""

    def __init__(self, sample_rate, block_size, loopback=True):
        import soundcard as sc
        self.block_size = block_size
        if loopback:
            # В soundcard loopback = «виртуальный микрофон» для колонок:
            # sc.get_microphone(id=имя_колонок, include_loopback=True)
            speaker = sc.default_speaker()
            source = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            self.name = f'системный звук [{source.name}]'
        else:
            source = sc.default_microphone()
            self.name = f'микрофон [{source.name}]'
        self._cm = source.recorder(samplerate=sample_rate, channels=1)
        self._rec = self._cm.__enter__()
        # прогревочный кадр: первый блок у многих драйверов немой
        self._rec.record(numframes=self.block_size)

    def get_data(self):
        return _to_int16_mono(self._rec.record(numframes=self.block_size))

    def close(self):
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ПК: микрофон через sounddevice / pyaudio (callback + очередь)
# ---------------------------------------------------------------------------

class _SoundDeviceCapture:

    def __init__(self, sample_rate, block_size):
        import sounddevice as sd
        self.block_size = block_size
        self.name = 'микрофон [sounddevice]'
        self._queue = collections.deque()

        def callback(indata, frames, time_info, status):
            self._queue.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=sample_rate, channels=1, dtype='int16',
            blocksize=block_size, callback=callback)
        self._stream.start()

    def get_data(self):
        return _drain_queue(self._queue, self.block_size)

    def close(self):
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class _PyAudioCapture:

    def __init__(self, sample_rate, block_size):
        import pyaudio
        self.block_size = block_size
        self.name = 'микрофон [pyaudio]'
        self._queue = collections.deque()
        self._pa = pyaudio.PyAudio()

        def callback(in_data, frame_count, time_info, status):
            self._queue.append(np.frombuffer(in_data, dtype=np.int16))
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=1, rate=sample_rate,
            input=True, frames_per_buffer=block_size, stream_callback=callback)
        self._stream.start_stream()

    def get_data(self):
        return _drain_queue(self._queue, self.block_size)

    def close(self):
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Демо-режим (синтетический бит) — для проверки логики без реального аудио
# ---------------------------------------------------------------------------

class _DemoCapture:
    """Синтетический драм-паттерн 120 BPM: кич на каждую долю, снейр на
    2 и 4, хэты восьмыми. Нужен, чтобы проверять привязку эффектов к
    полосам (кик/снейр/хэт) даже на машине совсем без звукового ввода."""

    BEAT = 0.5  # 120 BPM

    def __init__(self, sample_rate, block_size, beat_interval=0.5):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.name = 'демо (кик+снейр+хэт, 120 BPM)'
        self._pos = 0  # позиция по сэмплам от старта

    def get_data(self):
        sr = self.sample_rate
        n = self.block_size
        t = (self._pos + np.arange(n, dtype=np.float64)) / sr
        self._pos += n
        out = np.zeros(n, dtype=np.float64)

        # --- кич: каждая доля (0.5 с), тон 50-90 Гц с падением высоты ---
        tk = t % self.BEAT
        fk = 50.0 + 45.0 * np.exp(-tk / 0.03)
        ph_k = 2 * np.pi * np.cumsum(fk) / sr  # фаза с интегрированием
        out += 23000.0 * np.exp(-tk / 0.09) * np.sin(ph_k)

        # --- снейр: доли 2 и 4 (смещение 0.5 c в такте 2 с) ---
        ts = t % (self.BEAT * 4.0)
        tn = np.where(ts >= self.BEAT * 2.0, ts - self.BEAT * 2.0, 9.9)
        env_s = np.exp(-tn / 0.07)
        out += env_s * (11000.0 * np.random.standard_normal(n)
                        + 3800.0 * np.sin(2 * np.pi * 190.0 * t))

        # --- хэты: восьмые (0.25 с), короткий шумовой тик ---
        th = t % (self.BEAT * 0.5)
        out += 4200.0 * np.exp(-th / 0.012) * np.random.standard_normal(n)

        return np.clip(out, -32768, 32767).astype(np.int16)


# ---------------------------------------------------------------------------
# ПК/Windows: процесс-специфичный WASAPI loopback (чистый ctypes, без
# внешних зависимостей). Игра слушает рендер-потоки ТОЛЬКО целевого
# процесса (плеера): голоса из Discord/TeamSpeak и системные уведомления
# отсекаются самим аудио-движком (AUDCLNT_ACTIVATION_TYPE_PROCESS_LOOPBACK
# через ActivateAudioInterfaceAsync, Windows 10 2004+).
# ---------------------------------------------------------------------------

IS_WINDOWS = (os.name == 'nt')

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    _ole32 = ctypes.WinDLL('ole32')
    try:
        _mmdevapi = ctypes.WinDLL('mmdevapi.dll')
    except OSError:
        _mmdevapi = ctypes.WinDLL('mmdeviceapi.dll')
    _kernel32 = ctypes.WinDLL('kernel32')

    HRESULT = ctypes.HRESULT

    class _GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),
                    ('Data3', ctypes.c_ushort),
                    ('Data4', ctypes.c_ubyte * 8)]

    def _guid(s):
        s = s.strip('{}').replace('-', '')
        g = _GUID()
        g.Data1 = int(s[0:8], 16)
        g.Data2 = int(s[8:12], 16)
        g.Data3 = int(s[12:16], 16)
        for i in range(8):
            g.Data4[i] = int(s[16 + i * 2:18 + i * 2], 16)
        return g

    def _guid_eq(a, b):
        return (a.Data1 == b.Data1 and a.Data2 == b.Data2
                and a.Data3 == b.Data3 and bytes(a.Data4) == bytes(b.Data4))

    _IID_IUnknown = _guid('00000000-0000-0000-C000-000000000046')
    _CLSID_MMDeviceEnumerator = _guid('BCDE0395-E52F-467C-8E3D-C4579291692E')
    _IID_IMMDeviceEnumerator = _guid('A95664D2-9614-4F35-A746-DE8DB63617E6')
    _IID_IAudioClient = _guid('1CB9AD4C-DBFA-4C32-B178-C2F568A703B2')
    _IID_IAudioCaptureClient = _guid('C8ADBD64-E71E-48A0-A4DE-185C395CD317')
    _IID_IAudioSessionManager2 = _guid('77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F')
    _IID_IAudioSessionEnumerator = _guid('E2F5BB11-0570-40CA-ACDD-3AA01277DEE8')
    _IID_IAudioSessionControl2 = _guid('BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D')
    _IID_IAudioMeterInformation = _guid('C02216F6-8C67-4B5B-9D00-D008E73E0064')
    _IID_IActivateAudioInterfaceCompletionHandler = _guid(
        '94EA2B74-E905-4DAD-9F31-318F5C7772A5')
    # маркер «агильности»: без него COM не доставит колбэк между
    # апартаментами и активация повиснет
    _IID_IAgileObject = _guid('94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90')

    # ActivateAudioInterfaceAsync("VAD::ProcessLoopback", ...)
    VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = 'VAD::ProcessLoopback'
    _AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
    _PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
    _VT_BLOB = 65
    _CLSCTX_ALL = 0x17

    _AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
    _AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
    _AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
    _AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000

    class _BLOB(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('pBlobData', ctypes.c_void_p)]

    class _PROPVAR_UNION(ctypes.Union):
        _fields_ = [('blob', _BLOB), ('pad', ctypes.c_ubyte * 16)]

    class _PROPVARIANT(ctypes.Structure):
        _anonymous_ = ('u',)
        _fields_ = [('vt', ctypes.c_ushort), ('wReserved1', wt.WORD),
                    ('wReserved2', wt.WORD), ('wReserved3', wt.WORD),
                    ('u', _PROPVAR_UNION)]

    # AUDIOCLIENT_ACTIVATION_PARAMS: union из одного члена — распрямляем
    class _AudioClientActivationParams(ctypes.Structure):
        _fields_ = [('ActivationType', ctypes.c_int),
                    ('TargetProcessId', wt.DWORD),
                    ('ProcessLoopbackMode', ctypes.c_int)]

    class _WAVEFORMATEX(ctypes.Structure):
        _fields_ = [('wFormatTag', wt.WORD), ('nChannels', wt.WORD),
                    ('nSamplesPerSec', wt.DWORD), ('nAvgBytesPerSec', wt.DWORD),
                    ('nBlockAlign', wt.WORD), ('wBitsPerSample', wt.WORD),
                    ('cbSize', wt.WORD)]

    def _as_addr(punk):
        """c_void_p/int/None -> целочисленный адрес (0 = NULL)."""
        if isinstance(punk, ctypes.c_void_p):
            return punk.value or 0
        if punk is None:
            return 0
        return int(punk)

    def _vtfn(punk, index):
        """Адрес функции №index из vtable COM-объекта."""
        addr = _as_addr(punk)
        if not addr:
            raise ValueError('COM: NULL pointer')
        vtbl = ctypes.cast(ctypes.c_void_p(addr),
                           ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not vtbl:
            raise ValueError('COM: NULL vtable')
        return ctypes.cast(
            ctypes.c_void_p(vtbl + index * ctypes.sizeof(ctypes.c_void_p)),
            ctypes.POINTER(ctypes.c_void_p)).contents.value

    def _call(punk, index, restype, argtypes=(), *args):
        """Вызов метода №index vtable COM-объекта (0..2 = IUnknown)."""
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(_vtfn(punk, index))(ctypes.c_void_p(_as_addr(punk)),
                                         *args)

    _ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wt.DWORD)
    _ole32.CoInitializeEx.restype = HRESULT
    _ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(_GUID), ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
    _ole32.CoCreateInstance.restype = HRESULT

    def _com_init():
        """CoInitializeEx(None, 0) для текущего потока с защитой от краша.

        Игнорирует RPC_E_CHANGED_MODE (0x80010106 = -2147417850): COM уже
        инициализирован в другом апартаментном режиме (Kivy/SDL/pythonnet могли
        сделать процесс STA) — повторный MTA-вызов падает с WinError
        -2147417850 'Изменение режима для потока'. В этом случае COM уже
        живёт — используем активную модель и не трогаем режим.
        """
        try:
            return _ole32.CoInitializeEx(None, 0)
        except OSError as e:
            try:
                code = e.winerror
            except AttributeError:
                code = e.args[0] if e.args else None
            if code in (0x80010106, -2147417850):
                return 0
            raise

    def _cocreate(clsid, iid):
        punk = ctypes.c_void_p()
        hr = _ole32.CoCreateInstance(
            ctypes.byref(clsid), None, _CLSCTX_ALL, ctypes.byref(iid),
            ctypes.byref(punk))
        if hr != 0:
            raise OSError(f'CoCreateInstance: {hr:#010x}')
        return punk

    _kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.QueryFullProcessImageNameW.argtypes = (
        ctypes.c_void_p, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    _kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _kernel32.CloseHandle.restype = wt.BOOL
    _kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                       wt.LPCWSTR)
    _kernel32.CreateEventW.restype = ctypes.c_void_p
    _kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, wt.DWORD)
    _kernel32.WaitForSingleObject.restype = wt.DWORD

    def _proc_image_name(pid):
        """Имя exe процесса (нижний регистр) или ''."""
        h = _kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                    ctypes.byref(size)):
                return buf.value.replace('/', '\\').split('\\')[-1].lower()
            return ''
        finally:
            _kernel32.CloseHandle(h)

    # --- выбор окна мышью (Discord-стиль) ---------------------------------
    _user32 = ctypes.WinDLL('user32')

    class _POINT(ctypes.Structure):
        _fields_ = [('x', wt.LONG), ('y', wt.LONG)]

    _user32.GetCursorPos.argtypes = (ctypes.POINTER(_POINT),)
    _user32.GetCursorPos.restype = wt.BOOL
    _user32.WindowFromPoint.argtypes = (_POINT,)
    _user32.WindowFromPoint.restype = ctypes.c_void_p
    _user32.GetWindowThreadProcessId.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wt.DWORD))
    _user32.GetWindowThreadProcessId.restype = wt.DWORD
    _user32.GetWindowTextLengthW.argtypes = (ctypes.c_void_p,)
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = (ctypes.c_void_p, wt.LPWSTR, ctypes.c_int)
    _user32.GetWindowTextW.restype = wt.BOOL

    def pick_window_under_cursor():
        """HWND под курсором -> (pid, window_title, exe_name).

        Курсор над окном плеера + клик => GetCursorPos + WindowFromPoint
        выдают HWND, GetWindowThreadProcessId — его PID. Чистый ctypes, без
        pywin32: эквивалентно win32gui.WindowFromPoint +
        win32process.GetWindowThreadProcessId.
        """
        pt = _POINT()
        if not _user32.GetCursorPos(ctypes.byref(pt)):
            return (0, None, None)
        hwnd = _user32.WindowFromPoint(pt)
        if not hwnd:
            return (0, None, None)
        pid = wt.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid = int(pid.value)
        title = None
        ln = _user32.GetWindowTextLengthW(hwnd)
        if ln > 0:
            buf = ctypes.create_unicode_buffer(ln + 1)
            if _user32.GetWindowTextW(hwnd, buf, ln + 1):
                title = buf.value or None
        exe = _proc_image_name(pid) if pid else ''
        return (pid, title, exe)  # noqa: E501  -- плеер

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD),
                    ('th32ProcessID', wt.DWORD),
                    ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                    ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                    ('th32ParentProcessID', wt.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                    ('szExeFile', wt.WCHAR * 260)]

    def _find_pids_by_names(basenames):
        """Все запущенные процессы, чьё имя (без .exe) входит в набор."""
        TH32CS_SNAPPROCESS = 0x2
        _kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        _kernel32.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        _kernel32.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        found = []
        if not snap or snap == -1:
            return found
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                exe = entry.szExeFile or ''
                base = exe.lower()
                if base.endswith('.exe'):
                    base = base[:-4]
                if base in basenames:
                    found.append((int(entry.th32ProcessID), exe))
                ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snap)
        return found

# ---------------------------------------------------------------------------
# Публичный класс: выбор бекенда под платформу
# ---------------------------------------------------------------------------
        return punk

# ---------------------------------------------------------------------------
# Публичный класс: выбор бекенда под платформу
# ---------------------------------------------------------------------------
# Публичный класс: выбор бекенда под платформу
# ---------------------------------------------------------------------------

    # --- COM-обработчик активации (IActivateAudioInterfaceCompletionHandler) --
    # КАЖДЫЙ слот vtable получает ТОЧНУЮ сигнатуру: на x64 лишние аргументы
    # молча отбрасываются, поэтому универсальный «1 аргумент» ломал QI и
    # ActivateCompleted. Сигнатуры: QI(this, riid, ppv), AddRef/Release(this),
    # ActivateCompleted(this, operation).
    _COMPL_EVT = threading.Event()
    _ACTIVATION = {'op': 0}
    _FN_QI = ctypes.WINFUNCTYPE(
        HRESULT, ctypes.c_void_p,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
    _FN_AR = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    _FN_ACT = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p)

    def _h_any(this, *args):
        """Audit-прозрачный диспетчер: печатает входящие вызовы (отладка)."""
        import os as _os
        if _os.environ.get('SG_DEBUG_COM'):
            print(f'[dbg] handler nargs={len(args)}', flush=True)
        return 0

    def _h_qi(this, riid, ppv):
        """QueryInterface: IUnknown, сам обработчик и IAgileObject."""
        import os as _os
        if _os.environ.get('SG_DEBUG_COM'):
            g = ctypes.cast(riid, ctypes.POINTER(_GUID))[0]
            print('[dbg] QI for {%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}'
                  % (g.Data1, g.Data2, g.Data3, g.Data4[0], g.Data4[1],
                     g.Data4[2], g.Data4[3], g.Data4[4], g.Data4[5],
                     g.Data4[6], g.Data4[7]), flush=True)
        try:
            g = ctypes.cast(riid, ctypes.POINTER(_GUID))[0]
            if (_guid_eq(g, _IID_IUnknown)
                    or _guid_eq(g, _IID_IActivateAudioInterfaceCompletionHandler)
                    or _guid_eq(g, _IID_IAgileObject)):
                if ppv:
                    ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = this
                return 0
        except Exception:
            pass
        return 0x80004002  # E_NOINTERFACE

    def _h_addref(this):
        return 1

    def _h_release(this):
        return 1

    def _h_activated(this, operation):
        """ActivateCompleted: сохранить operation и взвести событие."""
        import os as _os
        if _os.environ.get('SG_DEBUG_COM'):
            print('[dbg] ActivateCompleted called', flush=True)
        try:
            _ACTIVATION['op'] = int(operation or 0)
        except Exception:
            _ACTIVATION['op'] = 0
        _COMPL_EVT.set()
        return 0

    class _CompletionVTable(ctypes.Structure):
        _fields_ = [('QueryInterface', _FN_QI), ('AddRef', _FN_AR),
                    ('Release', _FN_AR),
                    ('ActivateCompleted', _FN_ACT)]

    _completion_vtable = _CompletionVTable(
        _FN_QI(_h_qi), _FN_AR(_h_addref), _FN_AR(_h_release),
        _FN_ACT(_h_activated))
    _completion_objmem = ctypes.c_void_p(ctypes.addressof(_completion_vtable))
    COMPLETION_HANDLER = ctypes.c_void_p(ctypes.addressof(_completion_objmem))

    _mmdevapi.ActivateAudioInterfaceAsync.argtypes = (
        wt.LPCWSTR, ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p))
    _mmdevapi.ActivateAudioInterfaceAsync.restype = HRESULT

    _ACTIVATION_KEEPALIVE = []

    def _activate_process_loopback_client(pid):
        """Активировать IAudioClient process-loopback целевого PID."""
        _com_init()  # MTA; RPC_E_CHANGED_MODE игнорируем
        _COMPL_EVT.clear()
        _ACTIVATION['hr'] = 1
        _ACTIVATION['punk'] = 0

        params = _AudioClientActivationParams(
            _AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK, int(pid),
            _PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE)
        blob = _BLOB(ctypes.sizeof(params),
                     ctypes.cast(ctypes.byref(params), ctypes.c_void_p))
        pvar = _PROPVARIANT()
        pvar.vt = _VT_BLOB
        pvar.blob = blob
        _ACTIVATION_KEEPALIVE.append((params, blob, pvar))

        op = ctypes.c_void_p()
        hr = _mmdevapi.ActivateAudioInterfaceAsync(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
            ctypes.byref(_IID_IAudioClient), ctypes.byref(pvar),
            COMPLETION_HANDLER, ctypes.byref(op))
        if hr != 0:
            raise OSError(f'ActivateAudioInterfaceAsync: {hr:#010x}')
        if not _COMPL_EVT.wait(6.0):
            raise TimeoutError('ActivateAudioInterfaceAsync: нет ответа')
        op = ctypes.c_void_p(_ACTIVATION.get('op') or op.value)
        res_hr = HRESULT(-1)
        punk = ctypes.c_void_p()
        try:
            # GetActivateResult — метод №3 IActivateAudioInterfaceAsyncOperation
            _call(op, 3, HRESULT,
                  (ctypes.POINTER(HRESULT), ctypes.POINTER(ctypes.c_void_p)),
                  ctypes.byref(res_hr), ctypes.byref(punk))
        finally:
            if op.value:
                _call(op, 2, HRESULT, ())  # Release операции
        if res_hr.value != 0 or not punk.value:
            raise OSError(
                f'активация process loopback: {res_hr.value & 0xFFFFFFFF:#010x}')
        client = ctypes.c_void_p()
        hr = _call(punk, 0, HRESULT,
                   (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
                   ctypes.byref(_IID_IAudioClient), ctypes.byref(client))
        _call(punk, 2, HRESULT, ())  # Release IUnknown
        if hr != 0 or not client.value:
            raise OSError(f'QueryInterface(IAudioClient): {hr:#010x}')
        return client

    def _activate_default_render_client():
        """Синхронно получить IAudioClient устройства вывода по умолчанию
        (IMMDevice::Activate) — глобальный loopback."""
        enum_dev = _cocreate(_CLSID_MMDeviceEnumerator,
                             _IID_IMMDeviceEnumerator)
        dev = ctypes.c_void_p()
        hr = _call(enum_dev, 4, HRESULT,
                   (ctypes.c_int, ctypes.c_int,
                    ctypes.POINTER(ctypes.c_void_p)),
                   0, 1, ctypes.byref(dev))
        if hr != 0 or not dev.value:
            raise OSError(f'GetDefaultAudioEndpoint: {hr:#010x}')
        client = ctypes.c_void_p()
        hr = _call(dev, 3, HRESULT,
                   (ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p)),
                   ctypes.byref(_IID_IAudioClient), _CLSCTX_ALL, None,
                   ctypes.byref(client))
        if hr != 0 or not client.value:
            raise OSError(f'IMMDevice::Activate(IAudioClient): {hr:#010x}')
        return client

    def _os_supports_process_loopback():
        """Process loopback (VAD::ProcessLoopback) появился с Windows 10
        build 20348 (Server 2022 / Windows 11)."""
        try:
            return sys.getwindowsversion().build >= 20348
        except Exception:
            return False

    def _frames_to_mono_int16(raw, nch, bits, is_float):
        """Байты WASAPI -> numpy int16 моно (16/24/32-int/float32).

        Каналы сводятся знаковым максимумом амплитуды max(|L|, |R|):
        сэмпл канала с большей |амплитудой| берётся С ЕГО ЗНАКОМ.
        Среднее (L+R)/2 уничтожило бы низкие частоты в противофазе
        каналов (широкая стереобаза в электронных треках) — детектор
        бочки ослеп бы ровно на плотных дропах.
        """
        if is_float:
            arr = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
            if nch > 1:
                m = arr.reshape(-1, nch)
                idx = np.argmax(np.abs(m), axis=1)
                arr = m[np.arange(len(m)), idx]
            return np.clip(arr * 32767.0, -32768, 32767).astype(np.int16)
        if bits == 16:
            arr = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        elif bits == 32:
            arr = np.frombuffer(raw, dtype=np.int32).astype(np.float64)
        elif bits == 24:
            b = np.frombuffer(raw, dtype=np.uint8)
            b = b[:len(b) // 3 * 3].reshape(-1, 3)
            v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
                 | (b[:, 2].astype(np.int32) << 16))
            v = np.where(v & 0x800000, v - 0x1000000, v)
            arr = v.astype(np.float64)
        else:
            arr = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        if nch > 1:
            m = arr.reshape(-1, nch)
            idx = np.argmax(np.abs(m), axis=1)
            arr = m[np.arange(len(m)), idx]
        return np.clip(arr, -32768, 32767).astype(np.int16)

    def list_audio_sessions():
        """Активные аудио-сессии устройства вывода по умолчанию.

        Возвращает [{'pid', 'exe', 'state', 'peak', 'system'}]:
        state 1 = сессия сейчас играет; peak — текущий пик громкости.
        """
        try:
            _com_init()
            enum_dev = _cocreate(_CLSID_MMDeviceEnumerator,
                                 _IID_IMMDeviceEnumerator)
            dev = ctypes.c_void_p()
            # GetDefaultAudioEndpoint(eRender=0, eMultimedia=1)
            hr = _call(enum_dev, 4, HRESULT,
                       (ctypes.c_int, ctypes.c_int,
                        ctypes.POINTER(ctypes.c_void_p)),
                       0, 1, ctypes.byref(dev))
            if hr != 0 or not dev.value:
                return []
            sm2 = ctypes.c_void_p()
            # IMMDevice::Activate (метод №3)
            hr = _call(dev, 3, HRESULT,
                       (ctypes.POINTER(_GUID), wt.DWORD, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p)),
                       ctypes.byref(_IID_IAudioSessionManager2), _CLSCTX_ALL,
                       None, ctypes.byref(sm2))
            if hr != 0 or not sm2.value:
                return []
            ses_enum = ctypes.c_void_p()
            # GetSessionEnumerator — метод №5 IAudioSessionManager2
            hr = _call(sm2, 5, HRESULT, (ctypes.POINTER(ctypes.c_void_p),),
                       ctypes.byref(ses_enum))
            if hr != 0 or not ses_enum.value:
                return []
            count = ctypes.c_int()
            _call(ses_enum, 3, HRESULT, (ctypes.POINTER(ctypes.c_int),),
                  ctypes.byref(count))
            out = []
            for i in range(count.value):
                ctl = ctypes.c_void_p()
                if _call(ses_enum, 4, HRESULT,
                         (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                         i, ctypes.byref(ctl)) != 0 or not ctl.value:
                    continue
                state = ctypes.c_int()
                _call(ctl, 3, HRESULT, (ctypes.POINTER(ctypes.c_int),),
                      ctypes.byref(state))
                pid, system = 0, False
                ctl2 = ctypes.c_void_p()
                if _call(ctl, 0, HRESULT,
                         (ctypes.POINTER(_GUID),
                          ctypes.POINTER(ctypes.c_void_p)),
                         ctypes.byref(_IID_IAudioSessionControl2),
                         ctypes.byref(ctl2)) == 0 and ctl2.value:
                    p = wt.DWORD()
                    if _call(ctl2, 14, HRESULT, (ctypes.POINTER(wt.DWORD),),
                             ctypes.byref(p)) == 0:
                        pid = int(p.value)
                    # IsSystemSoundsSession (№15): S_OK => системные звуки
                    system = _call(ctl2, 15, HRESULT, ()) == 0
                    _call(ctl2, 2, HRESULT, ())
                peak = 0.0
                meter = ctypes.c_void_p()
                if _call(ctl, 0, HRESULT,
                         (ctypes.POINTER(_GUID),
                          ctypes.POINTER(ctypes.c_void_p)),
                         ctypes.byref(_IID_IAudioMeterInformation),
                         ctypes.byref(meter)) == 0 and meter.value:
                    pk = ctypes.c_float()
                    if _call(meter, 3, HRESULT, (ctypes.POINTER(ctypes.c_float),),
                             ctypes.byref(pk)) == 0:
                        peak = float(pk.value)
                    _call(meter, 2, HRESULT, ())
                out.append({'pid': pid, 'exe': _proc_image_name(pid) if pid
                            else '', 'state': int(state.value), 'peak': peak,
                            'system': system})
                _call(ctl, 2, HRESULT, ())
            return out
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'[SoundGrip] Не удалось перечислить аудио-сессии: {e}')
            return []

    class _WasapiLoopbackCapture:
        """Нативный WASAPI loopback (без внешних библиотек).

        pid=0  -> глобальный loopback устройства вывода по умолчанию
                  (синхронный IMMDevice::Activate).
        pid>0  -> process loopback (Windows 11 / Server 2022+): слышен ТОЛЬКО
                  целевой процесс и его потомки (ActivateAudioInterfaceAsync).
        Поток + очередь, на выходе numpy int16 моно.
        """

        def __init__(self, sample_rate=48000, block_size=512, pid=0,
                     exe_name=''):
            self.sample_rate = sample_rate
            self.block_size = block_size
            self.pid = int(pid)
            self.exe_name = exe_name
            if self.pid:
                self.name = (f'process loopback: '
                             f'{exe_name or "PID " + str(pid)} (PID {pid})')
            else:
                self.name = 'wasapi loopback (система)'
            self._fatal = None
            self._queue = collections.deque(maxlen=64)
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            # прогрев: ждём первый блок (до 4 с) или фейл старта
            t0 = time.monotonic()
            while (not self._queue and not self._fatal
                   and time.monotonic() - t0 < 4.0):
                if not self._thread.is_alive():
                    break
                time.sleep(0.01)
            if self._fatal:
                raise RuntimeError(f'wasapi: {self._fatal}')
            if not self._queue and not self._thread.is_alive():
                raise RuntimeError('поток захвата не запустился')

        def get_data(self):
            if self._fatal:
                return None
            return _drain_queue(self._queue, self.block_size)

        def close(self):
            self._stop.set()
            try:
                self._thread.join(timeout=1.5)
            except Exception:
                pass

        def _poll_packet(self, capture, block_align, nch, bits, is_float):
            """Один опрос WASAPI. Возврат: >0 фреймов, 0 = пусто, -1 = ошибка."""
            # IAudioCaptureClient vtable: 0 QI,1 AddRef,2 Release,
            # 3 GetBuffer, 4 ReleaseBuffer, 5 GetNextPacketSize
            frames = ctypes.c_uint32()
            if _call(capture, 5, HRESULT,
                     (ctypes.POINTER(ctypes.c_uint32),),
                     ctypes.byref(frames)) != 0:
                return -1
            if frames.value == 0:
                return 0
            pdata = ctypes.POINTER(ctypes.c_ubyte)()
            got = ctypes.c_uint32()
            fl = wt.DWORD()
            dp = ctypes.c_uint64()
            qp = ctypes.c_uint64()
            r = _call(capture, 3, HRESULT,
                      (ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
                       ctypes.POINTER(ctypes.c_uint32),
                       ctypes.POINTER(wt.DWORD),
                       ctypes.POINTER(ctypes.c_uint64),
                       ctypes.POINTER(ctypes.c_uint64)),
                      ctypes.byref(pdata), ctypes.byref(got),
                      ctypes.byref(fl), ctypes.byref(dp),
                      ctypes.byref(qp))
            if r != 0:
                return -1
            n = 0
            if got.value > 0:
                raw = ctypes.string_at(pdata, got.value * block_align)
                self._queue.append(_frames_to_mono_int16(
                    raw, nch, bits, is_float))
                n = got.value
            _call(capture, 4, HRESULT, (ctypes.c_uint,),
                  got.value)  # ReleaseBuffer
            return n

        def _run(self):
            client = None
            capture = None
            try:
                _com_init()
                if self.pid:
                    client = _activate_process_loopback_client(self.pid)
                else:
                    client = _activate_default_render_client()

                # loopback в SHARED-режиме с mix-форматом устройства
                # (проверенный паттерн ApplicationLoopback)
                mfp = ctypes.POINTER(_WAVEFORMATEX)()
                hr = _call(client, 8, HRESULT,
                           (ctypes.POINTER(ctypes.POINTER(_WAVEFORMATEX)),),
                           ctypes.byref(mfp))
                if hr != 0:
                    raise OSError(f'GetMixFormat: {hr:#010x}')
                fmt = mfp.contents
                hr = _call(client, 3, HRESULT,
                           (ctypes.c_uint, ctypes.c_uint, ctypes.c_longlong,
                            ctypes.c_longlong, ctypes.POINTER(_WAVEFORMATEX),
                            ctypes.c_void_p),
                           0, _AUDCLNT_STREAMFLAGS_LOOPBACK, 0, 0,
                           ctypes.byref(fmt), None)
                if hr != 0:
                    raise OSError(f'Initialize(LOOPBACK): {hr:#010x}')
                nch = fmt.nChannels
                bits = fmt.wBitsPerSample
                is_float = (fmt.wFormatTag == 3
                            or (fmt.wFormatTag == 0xFFFE and bits == 32))
                self.sample_rate = fmt.nSamplesPerSec

                capture = ctypes.c_void_p()
                hr = _call(client, 14, HRESULT,
                           (ctypes.POINTER(_GUID),
                            ctypes.POINTER(ctypes.c_void_p)),
                           ctypes.byref(_IID_IAudioCaptureClient),
                           ctypes.byref(capture))
                if hr != 0 or not capture.value:
                    raise OSError(
                        f'GetService(IAudioCaptureClient): {hr:#010x}')
                hr = _call(client, 10, HRESULT, ())  # Start
                if hr != 0:
                    raise OSError(f'IAudioClient::Start: {hr:#010x}')

                block_align = fmt.nBlockAlign
                fails = 0
                err_logged = False
                # Точность sleep на Windows ~15.6 мс: без timeBeginPeriod
                # поток спит слишком долго и обрабатывает лишь часть
                # пакетов -> звук поступает ЗАМЕДЛЕННЫМ (у нас было ~1/3
                # реального времени, кик 60 Гц уезжал ниже полосы 30 Гц).
                try:
                    _winmm = ctypes.windll.winmm
                    _winmm.timeBeginPeriod(1)
                    _timer_raised = True
                except Exception:
                    _timer_raised = False
                try:
                    while not self._stop.is_set():
                        try:
                            # Выкачиваем ВСЕ накопившиеся пакеты: за одну
                            # итерацию может прийти несколько (если поток
                            # проснулся позже периода микширования).
                            processed = 0
                            while not self._stop.is_set():
                                n = self._poll_packet(
                                    capture, block_align, nch, bits, is_float)
                                if n > 0:
                                    processed += n
                                else:
                                    break    # 0 = пусто, -1 = ошибка
                            if processed > 0:
                                fails = 0
                                continue     # сразу проверяем новые пакеты
                        except Exception as e:
                            # одна ошибка пакета не должна убивать захват
                            fails += 1
                            if not err_logged:
                                err_logged = True
                                print(f'[SoundGrip] wasapi capture: {e!r}')
                            if fails > 300:
                                self._fatal = f'цикл захвата остановлен: {e}'
                                break
                        # пакетов нет — короткая пауза (с timeBeginPeriod
                        # точность ~1-2 мс, латентность < 10 мс)
                        time.sleep(0.002)
                finally:
                    if _timer_raised:
                        try:
                            ctypes.windll.winmm.timeEndPeriod(1)
                        except Exception:
                            pass
            except Exception as e:
                self._fatal = str(e)
            finally:
                if client is not None:
                    try:
                        _call(client, 11, HRESULT, ())  # Stop
                    except Exception:
                        pass
                if capture is not None:
                    try:
                        _call(capture, 2, HRESULT, ())  # Release
                    except Exception:
                        pass
                if client is not None:
                    try:
                        _call(client, 2, HRESULT, ())  # Release
                    except Exception:
                        pass

# ---------------------------------------------------------------------------
# Публичный класс: выбор бекенда под платформу
# ---------------------------------------------------------------------------
class AudioCapture:
    """Единая точка входа. Сам выбирает бекенд в зависимости от платформы.

    Android — сразу захватывает исходящий системный аудиопоток
    (MediaProjection, диалог «Запись экрана» запрашивается при старте).

    ПК/Windows — ЖЁСТКО глобальный системный loopback (рендер-микшер по
    умолчанию): слушается ВСЁ, что играет в системе — Spotify, Chrome,
    YouTube, игры. Изоляция по PID/окну УДАЛЕНА: Windows всё чаще держит
    аудио плееров и браузеров в фоновой службе audiodg.exe, из-за чего
    process loopback ловил тишину и эффекты пропадали.
    target_pid / target_process_names приняты для совместимости старых
    вызовов и игнорируются.
    """

    def __init__(self, sample_rate=48000, block_size=256, force_backend=None,
                 target_process_names=None, target_pid=None):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.backend_name = ''
        self.init_error = ''
        self._impl = None
        self._target_info = {}

        if not IS_ANDROID:
            _raise_process_priority()

        if IS_ANDROID:
            self._impl = _AndroidCapture(sample_rate, block_size)
            self.backend_name = self._impl.name
            self.init_error = self._impl.error or ''
        else:
            self._init_pc(force_backend, target_process_names, target_pid)
        # фактический темп сэмплирования бекенда (process loopback может
        # взять mix-формат устройства)
        self.sample_rate = getattr(self._impl, 'sample_rate', self.sample_rate)

    # --- выбор бекенда на ПК ------------------------------------------------

    def _init_pc(self, force_backend=None, target_process_names=None,
                 target_pid=None):
        """ПК: ЖЁСТКО глобальный системный loopback по умолчанию.

        Изоляция по PID удалена — Windows прячет аудиопоток плееров и
        браузеров в audiodg.exe, process loopback регулярно даёт тишину.
        Слушаем ВЕСЬ рендер-микшер: что бы ни играло — всё летит в
        beat_detector. target_pid/target_process_names игнорируются
        (приняты для совместимости сигнатуры).
        """
        if target_pid or target_process_names:
            print('[SoundGrip] Изоляция по PID отключена: слушаю ВЕСЬ '
                  'системный микшер (глобальный loopback).')

        candidates = []
        if IS_WINDOWS:
            candidates.append(('wasapi_loopback', _WasapiLoopbackCapture, {}))
        candidates += [
            ('soundcard_loopback', _SoundCardCapture, {'loopback': True}),
            ('soundcard_mic', _SoundCardCapture, {'loopback': False}),
            ('sounddevice_mic', _SoundDeviceCapture, {}),
            ('pyaudio_mic', _PyAudioCapture, {}),
        ]

        if force_backend == 'demo':
            print(f'[DEBUG] AudioCapture -> _DemoCapture block_size={self.block_size}', flush=True)
            self._impl = _DemoCapture(self.sample_rate, self.block_size)
            self.backend_name = self._impl.name
            print(f'[SoundGrip] Аудио-бекенд: {self.backend_name}')
            return

        if force_backend:
            candidates = [c for c in candidates if c[0] == force_backend]

        errors = []
        for key, cls, kwargs in candidates:
            try:
                impl = cls(self.sample_rate, self.block_size, **kwargs)
            except Exception as e:
                errors.append(f'{key}: {e}')
                print(f'[SoundGrip] Бекенд {key} недоступен: {e}')
                continue
            self._impl = impl
            self.backend_name = impl.name
            print(f'[SoundGrip] Аудио-бекенд: {self.backend_name}'
                  + (' [глобальный системный микшер]'
                     if 'loopback' in key else ''))
            return

        # реального аудио не вышло — включаем синтетику
        self._impl = _DemoCapture(self.sample_rate, self.block_size)
        self.backend_name = self._impl.name
        self.init_error = '; '.join(errors) if errors else 'ни один бекенд не доступен'
        print(f'[SoundGrip] Аудио-бекенд: {self.backend_name}')
        if errors:
            print(f'[SoundGrip] Причины: {self.init_error}')

        # --- общий интерфейс ----------------------------------------------------

    def request_permission(self):
        if self._impl is not None:
            method = getattr(self._impl, 'request_permission', None)
            if method:
                method()

    def get_data(self):
        if self._impl is None:
            return None
        return self._impl.get_data()

    def close(self):
        if self._impl is not None:
            method = getattr(self._impl, 'close', None)
            if method:
                method()





    # --- переподключение глобального loopback --------------------------------

    def reinit_global(self):
        """Жёстко переподключить ГЛОБАЛЬНЫЙ системный loopback «на лету».

        Вызывается кнопкой в UI: полезно при смене аудиоустройства,
        подвисшем WASAPI-потоке или после сна системы. Возвращает
        {'pid': 0, 'exe': 'system', 'title': ..., 'backend'} либо None,
        если loopback недоступен (включён demo).
        """
        if IS_ANDROID:
            return None     # MediaProjection переподключить тихо нельзя
        if self._impl is not None:
            try:
                self._impl.close()
            except Exception:
                pass
        self._impl = None
        title = 'Системный аудиопоток (Глобальный)'
        impl = None
        errors = []
        for key, cls, kwargs in (
                ('wasapi_loopback', _WasapiLoopbackCapture, {}),
                ('soundcard_loopback', _SoundCardCapture,
                 {'loopback': True})):
            try:
                impl = cls(self.sample_rate, self.block_size, **kwargs)
                break
            except Exception as e:
                errors.append(f'{key}: {e}')
                print(f'[SoundGrip] {key} недоступен: {e}')
        if impl is None:
            self._impl = _DemoCapture(self.sample_rate, self.block_size)
            self.backend_name = 'fallback:demo'
            self.init_error = '; '.join(errors)
            print(f'[SoundGrip] Loopback недоступен: {self.init_error}; '
                  'включён демо-режим.')
            return None
        self._impl = impl
        self.backend_name = impl.name
        self.sample_rate = getattr(impl, 'sample_rate', self.sample_rate)
        self.init_error = ''
        self._target_info = {'pid': 0, 'exe': 'system', 'title': title,
                             'backend': self.backend_name}
        print(f'[SoundGrip] Аудио переподключено: {self.backend_name} '
              '[глобальный системный микшер]')
        return self._target_info

    def retarget_by_window(self):
        """Deprecated: изоляция по окну удалена (Windows прячет аудио
        плееров в audiodg.exe). Теперь просто переподключаем глобальный
        loopback. Оставлено для совместимости старых вызовов."""
        return self.reinit_global()

    def retarget_by_pid(self, target_pid, title=None):
        """Deprecated: изоляция по PID удалена. pid игнорируется,
        переподключаем глобальный loopback."""
        return self.reinit_global()

    @property
    def target_info(self):
        return getattr(self, '_target_info', {})
