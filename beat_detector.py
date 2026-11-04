"""
beat_detector.py — частотный детектор ударов для SoundGrip.

Вместо простой энергии полосы используется СПЕКТРАЛЬНЫЙ ПОТОК (Spectral
Flux): половинно-выпрямленная разность спектров соседних FFT-кадров.
Бочка — это мгновенный спектральный взрыв: на транзиенте flux ~= энергии,
а на плотном устойчивом звуке (жирный бас, синт-пэды, средние частоты)
flux ~= 0 — адаптивный пол НЕ задирается, и детектор не «слепнет»
на ломаном бите (Skrillex-style).

Полосы:

    kick  — 30-160 Гц     (бочка / кик / бас)
    snare — 200-2000 Гц   (рабочий барабан, тело снейра)
    hat   — 4000-12000 Гц (тарелки, хэты, "воздух")

О каждой полосе ведётся адаптивный "пол шума" потока (экспоненциальное
среднее) и ЛОК-АУТ: после зарегистрированного удара на lockout секунд
замораживаются И порог, И сам детект. Между реальными киками физически
не может быть меньше ~200 мс, а жирный снейр, пришедший следом, обязан
не маскировать следующую бочку — поэтому пол бочки в лок-ауте не
пересчитывается.

Дополнительно оценивается ТЕМП: медиана недавних интервалов между
ударами -> detector.bpm / detector.beat_interval.

analyze() возвращает словарь:
    {'kick': bool, 'snare': bool, 'hat': bool, 'beat': bool,
     'onset': bool, 'onset_power': float, 'onset_hue': float,
     'level': float, '_dbg': {...отладочные величины полос...}}

'onset' — УНИВЕРСАЛЬНЫЙ канал: срабатывает на ЛЮБОЙ транзиент полного
спектра (вокал, синт-стаб, том, клэп, wobble — не только барабаны).
onset_power — сила (0..1), onset_hue — спектральный центроид как цвет
(бас тёплый, верха холодные). Плотность эффектов = плотность музыки.
"""

import collections
import time

import numpy as np

# окно FFT: 512 @ 48 кГц = ~93.75 Гц на бин, окно всего 10.7 мс.
# Это в 2 раза быстрее, чем 1024: эвент замечается на половину окна раньше
# (глаз не видит отставания визуала от звука).
# Полосы при этом:
#   kick 30-160 Гц  -> бины 0..1 (0-187.5 Гц)
#   snare 200-2000  -> бины 2..21
#   hat 4000-12000  -> бины 43..128
WIN = 512
# Шаг анализа: 128 сэмплов = 2.7 мс — окна перекрыты на 75%. Транзиент
# в ЛЮБОЙ фазе виден в 3-4 окнах подряд (детект 100%), отклик <= 2.7 мс:
# глаз не замечает отставания визуала от звука.
HOP = WIN // 4
# Детект выполняется каждые DETECT_EVERY окон (4 × 2.7 мс = 10.7 мс
# АУДИО-времени). Пачки сэмплов (лаг окна, быстрый фид, WASAPI-burst)
# обрабатываются целиком: удар в середине пачки больше не теряется —
# решений столько же, сколько окон/4, лок-ауты работают в аудио-времени.
DETECT_EVERY = 4

# Полосы: (f_lo, f_hi, множитель пола, абсолютный минимум, ЛОК-АУТ, с).
# ЛОК-АУТ = после удара полосы на столько секунд заморожены порог и детект.
# kick 0.20 — по ТЗ: реальные бочки не чаще ~200-250 мс, хвост бочки
# (160 мс) и утечка снейра не дают двойных срабатываний, сетка 240 BPM
# (250 мс) проходит. snare 0.12 / hat 0.08 — плотный бит (8 звуков/с =
# 125 мс между хэтами) ловится полностью. В лок-ауте пол только угасает.
# mult=6.0 для кика: только настоящие бочки (flux 10-16 при floor 2-4).
# Двойные удары томов и перкуссия не проходят — остаются только бочки.
# snare/hat mult=1.3/1.2: они тише и короче, но всё ещё выше пола.
BANDS = {
    'kick':  (30.0, 160.0, 6.0, 0.5, 0.20),
    'snare': (200.0, 2000.0, 1.3, 0.3, 0.12),
    'hat':   (4000.0, 12000.0, 1.2, 0.2, 0.08),
}

# Универсальный onset-канал: ЛЮБОЙ транзиент полного спектра (30-15000 Гц).
# Лок-аут 45 мс: один транзиент не плодит фантомов, но 8+ звуков в секунду
# ловятся все (4-8 звуков/с = период 125-250 мс — запас 3х). Порог ratio
# 1.6: «биения» утечки устойчивого тона (ratio ~1.3) не спамят, транзиент
# из тишины (ratio 5+) проходит всегда.
ONSET_ABS_MIN = 12.0
ONSET_LOCKOUT = 0.045

# Граница "тёплых" частот для hue: центроид ниже этой частоты = тёплый цвет.
# 2500 Гц отделяет бас/томы/низ снейра от хэтов и верха гитары.
HUE_CUTOFF_HZ = 2500.0


class BeatDetector:
    def __init__(self, sample_rate=48000, threshold=0.0):
        # threshold оставлен для совместимости со старым кодом
        self.sample_rate = max(8000, int(sample_rate))
        self._buf = np.zeros(0, dtype=np.float32)
        self._fed = 0        # всего сэмплов пришло (абс. позиция потока)
        self._win_end = 0    # абс. конец последнего проанализированного окна
        self._floor = {k: 0.0 for k in BANDS}
        self._last_fire = {k: 0.0 for k in BANDS}
        # средний уровень спектрального потока (~90 мс EMA) — ONSET-гейт:
        # кик должен быть в разы выше СВОЕГО среднего. Устойчивый тон
        # (особенно с мелким окном) создаёт НЕБОЛЬШИЕ колебания flux —
        # они меньше порога, и ложных киков нет.
        self._flux_env = {k: 0.0 for k in BANDS}
        # огибающая «провалов» энергии полосы: кик — это РОСТ энергии над
        # тихим уровнем (ratio); у устойчивого тона ratio ≈ 1. Асимметрия:
        # на пиках растёт медленно, на спадах падает быстро — всегда знает
        # тихий провал, а не среднее. Работает на ЛЮБОМ темпе (125-500 мс).
        self._ebase = {k: 0.0 for k in BANDS}
        # --- пер-оконные деки (COLA): поток/энергия последних 4 окон -----
        # Сумма потоков 4 окон с шагом N/4 = константа COLA (x2.0) —
        # полная амплитуда транзиента независимо от его фазы в окне.
        self._fxy = {k: collections.deque(maxlen=4)
                     for k in list(BANDS) + ['onset']}
        self._exy = {k: collections.deque(maxlen=4)
                     for k in list(BANDS) + ['onset']}
        # Максимум COLA-суммы/энергии ПО ОКНАМ ТЕКУЩЕГО ВЫЗОВА. Вызов может
        # обработать 4-64 окна (неравномерный фид) — фиксированная дека
        # вытеснила бы транзиент до детекта. Максимум за вызов это чинит.
        self._call_max = {k: 0.0 for k in list(BANDS) + ['onset']}
        self._call_max_en = {k: 0.0 for k in list(BANDS) + ['onset']}
        self._since_detect = 0     # окон накоплено с последнего детекта
        self._sub_sound = False    # было ли среди них не-тихое окно
        self._cen_dot = 0.0         # накопитель центроида за вызов
        self._cen_tot = 0.0
        self._cen_dot_lo = 0.0       # накопитель НЧ-центроида (< HUE_CUTOFF_HZ)
        self._cen_tot_lo = 0.0
        self._last_analysis = -1.0
        # спектр предыдущего кадра для спектрального потока
        self._prev_spec = np.zeros(WIN // 2 + 1, dtype=np.float64)
        self._primed = False   # спектр-эталон полного окна ещё не снят
        # --- FPS-кэш: всё, что не меняется от кадра к кадру -----------------
        # analyze() гоняется на 240 Гц; пересчёт rfftfreq/hanning/индексов
        # бинов на каждый вызов — это аллокации и фризы. Считаем ОДИН раз.
        self._hann = np.hanning(WIN).astype(np.float32)
        self._freqs = np.fft.rfftfreq(WIN, 1.0 / self.sample_rate)
        self._bins = {
            name: np.nonzero((self._freqs >= lo) & (self._freqs <= hi))[0]
            for name, (lo, hi, _m, _a, _l) in BANDS.items()}
        # onset-бины: весь слышимый диапазон 30..15000 Гц
        self._onset_bins = np.nonzero((self._freqs >= 30.0) &
                                      (self._freqs <= 15000.0))[0]
        self._freq_w = self._freqs.astype(np.float64)   # веса центроида
        self._hue_cutoff_idx = int(np.searchsorted(
            self._freqs, HUE_CUTOFF_HZ))
        # --- состояние onset-канала (в общих словарях: тишина гасит
        # их тем же циклом, что и полосы) -----------------------------------
        self._floor['onset'] = 0.0
        self._flux_env['onset'] = 0.0
        self._ebase['onset'] = 0.0
        self._last_fire['onset'] = 0.0
        # --- темп (BPM): медиана недавних интервалов между ударами ---
        self._beat_times = collections.deque(maxlen=12)
        self.bpm = 0.0            # 0.0 = темп ещё не определён
        self.beat_interval = 0.0  # секунд между ударами

    # ------------------------------------------------------------------

    def _register_beat(self, now):
        """Обновить оценку темпа по интервалам между ударами."""
        times = self._beat_times
        if times and now - times[-1] < 0.05:
            return  # дребезг одного удара
        times.append(now)
        if len(times) < 3:
            return
        ts = list(times)
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        # интервалы осмысленного темпа: 0.24..1.5 c (40..250 BPM)
        gaps = [g for g in gaps if 0.24 <= g <= 1.5]
        if len(gaps) < 2:
            return
        gap = float(np.median(gaps))
        bpm = 60.0 / gap
        # складываем октавы в музыкальный диапазон 70..180
        while bpm < 70.0:
            bpm *= 2.0
        while bpm > 180.0:
            bpm /= 2.0
        self.bpm = bpm
        self.beat_interval = 60.0 / bpm

    def analyze(self, audio_data):
        out = {'kick': False, 'snare': False, 'hat': False,
               'beat': False, 'level': 0.0, '_dbg': {},
               'kick_n': 0, 'snare_n': 0, 'hat_n': 0, 'onset_n': 0}
        if audio_data is None or len(audio_data) == 0:
            return out

        # скользящий буфер: WIN + запас под крупные блоки (WASAPI). Окна
        # снимаются шагом HOP=WIN//4 (перекрытие 75%): окна встык давали
        # «фазовые» пропуски — транзиент у края окна имел нулевые веса
        # Ханна и почти нулевой поток, удары терялись.
        chunk = np.asarray(audio_data, dtype=np.float32) / 32768.0
        self._fed += len(chunk)
        self._buf = np.concatenate([self._buf, chunk])
        if len(self._buf) > WIN + 8192:
            self._buf = self._buf[-(WIN + 8192):]
            # старые сэмплы вытеснены — окна, которые их требовали, пропустим
            self._win_end = max(self._win_end, self._fed - 8192)

        # Гейт по аудио-времени (не wall-clock): работает и в реалтайме, и при
        # офлайн-прогоне. Минимум 3 мс аудио-времени между анализами —
        # иначе на 240 Гц игрового цикла детект вытесняет CPU.
        audio_time = self._fed / self.sample_rate
        if len(self._buf) < 16 or audio_time - self._last_analysis < 0.003:
            return out
        self._last_analysis = audio_time

        # --- прогрев: первый полный кадр = эталон потока (без детекта) ---
        if not self._primed:
            try:
                if len(self._buf) >= WIN:
                    spec0 = np.abs(np.fft.rfft(self._buf[-WIN:] * self._hann))
                    self._prev_spec[:] = 0
                    self._prev_spec[:spec0.size] = spec0
                    self._primed = True
                    self._win_end = self._fed
                    print(f'PRIMED at fed={self._fed} buf_len={len(self._buf)}',
                          flush=True)
            except Exception as e:
                print(f'PRIME_ERROR: {e!r}', flush=True)
            return out

        # --- потоковый анализ шагом HOP (2.7 мс, перекрытие 75%) ----------
        # Снимаем ВСЕ полные окна, отстоящие на HOP: транзиент в любой
        # фазе виден в 3-4 окнах подряд -> COLA-сумма даёт полную
        # амплитуду, детект 100%, отклик <= 2.7 мс.
        #
        # АУДИО-ЧАСЫ детектора: позиция потока в секундах. Лок-ауты и
        # регистрация битов живут в аудио-времени, а не в настенном:
        # подача блоков может опережать реальное время (быстрый фид в
        # тестах, лаги окна, буферы WASAPI), и «между ударами бочки
        # физически не может быть меньше 200 мс» — утверждение именно
        # про АУДИО. Настенное время осталось только для CPU-гейта выше.
        at = self._fed / self.sample_rate
        buf_start = self._fed - len(self._buf)
        any_sound = False
        while self._fed - self._win_end >= HOP:
            wend = self._win_end + HOP
            if wend - WIN < buf_start:
                self._win_end = wend      # данные вытеснены — пропуск
                continue
            skip = self._fed - wend
            if skip:
                x = self._buf[-skip - WIN:-skip]
            else:
                x = self._buf[-WIN:]
            self._win_end = wend
            got = self._window_frame(x, out)
            any_sound = any_sound or got
            self._since_detect += 1
            self._sub_sound = self._sub_sound or got
            if self._since_detect >= DETECT_EVERY:
                # ПОДВЫБОРКА COLA: детект каждые 4 окна по аудио-времени
                # конца окна — пачка обрабатывается вся, без потерь.
                self._detect_and_track(wend / self.sample_rate, out,
                                       self._sub_sound)
                self._since_detect = 0
                self._sub_sound = False
        # хвост вызова: сбрасываем накопители (или гасим состояния
        # тишиной, если новых окон не пришло — чистый вызов без фида)
        if self._since_detect or not any_sound:
            self._detect_and_track(at, out, self._sub_sound)
            self._since_detect = 0
            self._sub_sound = False
        return out

    def _window_frame(self, x, out):
        """ОДНО HOP-окно: spec/flux -> пер-полосные деки.

        Без детекта и без EMAs — они считаются РАЗ ЗА ВЫЗОВ в
        _detect_and_track (иначе EMA на 375 Гц убивает чувствительность).
        Возвращает True, если окно не тишина."""
        x = x.astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)))
        if rms > out['level']:
            out['level'] = rms
        if rms < 0.002:
            # Тихое окно: деки получают нули (COLA-сумма честно падает),
            # спектр-эталон угасает, чтобы первый кадр после паузы не дал
            # ложного потока. Прайминг в тишине: буфер уже полный, эталон
            # = ~нулевой спектр — транзиент из тишины не пропадает.
            for deq in self._fxy.values():
                deq.append(0.0)
            for deq in self._exy.values():
                deq.append(0.0)
            self._prev_spec = self._prev_spec * 0.85
            if not self._primed and len(self._buf) >= WIN:
                self._prev_spec = np.zeros(WIN // 2 + 1, dtype=np.float64)
                self._primed = True
            return False
        spec = np.abs(np.fft.rfft(x * self._hann))
        # --- СПЕКТРАЛЬНЫЙ ПОТОК (half-wave rectified) -------------------
        # разность с ПРЕДЫДУЩИМ ОКНОМ (перекрытие 75%): устойчивый звук
        # (бас, пэды, плотные средние) поток не даёт, транзиент — даёт.
        flux = spec - self._prev_spec
        np.maximum(flux, 0.0, out=flux)
        self._prev_spec = spec
        for name in self._fxy:
            b = self._onset_bins if name == 'onset' else self._bins[name]
            f_v = float(np.sqrt(np.sum(flux[b] ** 2)))
            e_v = float(np.sqrt(np.sum(spec[b] ** 2)))
            self._fxy[name].append(f_v)
            self._exy[name].append(e_v)
            if 0.5 * float(sum(self._fxy[name])) > self._call_max[name]:
                self._call_max[name] = 0.5 * float(sum(self._fxy[name]))
            if e_v > self._call_max_en[name]:
                self._call_max_en[name] = e_v
        tot = float(np.sum(flux))
        if tot > 1e-9:
            self._cen_dot += float(np.dot(flux, self._freq_w))
            self._cen_tot += tot
            # Низкочастотный центроид (только до HUE_CUTOFF_HZ): бас/томы
            # дают низкий центроид → тёплый hue, хэты — высокий → холодный.
            flux_lo = flux[:self._hue_cutoff_idx]
            freq_lo = self._freq_w[:self._hue_cutoff_idx]
            tot_lo = float(np.sum(flux_lo))
            if tot_lo > 1e-9:
                self._cen_dot_lo += float(np.dot(flux_lo, freq_lo))
                self._cen_tot_lo += tot_lo
        return True

    def _detect_and_track(self, at, out, sub_sound):
        """Детект каждые DETECT_EVERY окон на COLA-агрегатах (at = аудио-время).

        Сумма потоков последних 4 окон (шаг N/4) по свойству constant
        overlap-add даёт x2.0 к амплитуде транзиента НЕЗАВИСИМО от его
        фазы в окне; делим на 2 — калибровка прежнего полного окна.
        Максимум энергии 4 окон >= 0.73 полной (max веса Ханна >= 0.854).
        В тишине все состояния угасают на каждом детекте."""
        # снимок максимумов с последнего детекта и сброс накопителей
        # (КАЖДЫЙ детект, включая тишину — иначе устаревший транзиент
        # сработает позже)
        band_max = dict(self._call_max)
        en_max = dict(self._call_max_en)
        for k in self._call_max:
            self._call_max[k] = 0.0
            self._call_max_en[k] = 0.0
        cen = (self._cen_dot / self._cen_tot
               if self._cen_tot > 1e-9 else 0.0)
        self._cen_dot = 0.0
        self._cen_tot = 0.0
        cen_lo = (self._cen_dot_lo / self._cen_tot_lo
                  if self._cen_tot_lo > 1e-9 else 0.0)
        self._cen_dot_lo = 0.0
        self._cen_tot_lo = 0.0
        if not sub_sound:
            for k in self._floor:
                self._floor[k] *= 0.85
            for k in self._flux_env:
                self._flux_env[k] *= 0.85
            for k in self._ebase:
                self._ebase[k] *= 0.80
            self._prev_spec = self._prev_spec * 0.85
            return
        for name, (lo, hi, mult, abs_min, lockout) in BANDS.items():
            band = band_max[name]       # пик COLA-суммы окон вызова
            enow = en_max[name]         # пик энергии окон вызова
            floor = self._floor[name]
            env = self._flux_env[name]
            ebase = self._ebase[name]
            out['_dbg'][name] = (round(band, 1), round(floor, 1))

            # ЛОК-АУТ: сразу после удара полосы поток идёт «хвостом»
            # (снейр/бас следом). В лок-ауте пол ТОЛЬКО УГАСАЕТ: жирный
            # снейр не может задрать порог бочки, а пол не остаётся
            # высоким — следующий кик через 125-250 мс остаётся видимым.
            in_lockout = at - self._last_fire[name] < lockout
            if in_lockout:
                self._floor[name] = floor * 0.90
            else:
                # пол шума тянем к текущему потоку
                self._floor[name] = floor * 0.995 + band * 0.005
                # и скользящую огибающую потока (для onset-гейта)
                self._flux_env[name] = max(env * 0.99 + band * 0.01,
                                           band * 0.002)

            # Onset = flux-всплеск над полом шума и огибающей. Устойчивый
            # тон: flux ≈ 0 → band мал → НЕТ триггера. Транзиент: flux > 0
            # и резко выше фона → триггер. Энергетическое отношение убрано:
            # на плотном реальном аудио ebase успевает подняться до уровня
            # сигнала и душит детект. Spectral flux сам по себе разделяет
            # трансиенты и устойчивые тоны.
            if not in_lockout and band > max(floor * mult, env * 1.5):
                self._last_fire[name] = at
                out[name] = True
                out[name + '_n'] = out.get(name + '_n', 0) + 1
                out['beat'] = True
                # удар резко поднимает пол — не ловим "хвост" удара
                self._floor[name] = max(self._floor[name], band * 0.65)

        # --- УНИВЕРСАЛЬНЫЙ ONSET: ЛЮБОЙ звук -> визуал ----------------------
        # Полный спектр 30-15000 Гц: вокал, синт, томы, клэпы, wobble —
        # всё, что не попало в барабанные полосы, тоже даёт эффект.
        # Центроид — по накопителю за вызов (все окна, утечка усредняется).
        # Onset = fallback: срабатывает ТОЛЬКО если ни одна полоса не сработала
        # (иначе кик дублируется и в кике, и в онсете → спам).
        tflux = band_max['onset']       # пик COLA-суммы окон вызова
        ofloor = self._floor['onset']
        oenv = self._flux_env['onset']
        out['_dbg']['onset'] = (round(tflux, 1), round(ofloor, 1))
        o_lock = at - self._last_fire['onset'] < ONSET_LOCKOUT
        if o_lock:
            self._floor['onset'] = ofloor * 0.90
        else:
            self._floor['onset'] = ofloor * 0.97 + tflux * 0.03
            self._flux_env['onset'] = max(oenv * 0.95 + tflux * 0.05,
                                          tflux * 0.02)
        # Onset срабатывает только если НЕ было барабанного удара (fallback).
        # Порог 1.5: транзиент должен быть на 50% выше пола и огибающей.
        if not out['beat'] and not o_lock \
                and tflux > max(ofloor * 1.5, oenv * 1.5):
            self._last_fire['onset'] = at
            out['onset'] = True
            out['onset_n'] = out.get('onset_n', 0) + 1
            # сила: насколько выброс выше своего среднего потока
            out['onset_power'] = min(1.0, tflux / max(oenv * 6.0, 30.0))
            # тон звука -> цвет: бас тёплый (hue~0), верха холодные (hue~0.8).
            # Низкочастотный центроид (< 3500 Гц): бас/томы/низ снейра дают
            # низкий центроид (тёплый), хэты и верх гитары — не влияют.
            # Опора 50 Гц — низшая фундаментала: 55-80 Гц бас даёт hue<0.3.
            hue = np.log2(max(cen_lo, 50.0) / 50.0) / np.log2(HUE_CUTOFF_HZ / 50.0)
            out['onset_hue'] = float(min(0.85, max(0.0, hue)))
            self._floor['onset'] = max(self._floor['onset'], tflux * 0.65)

        if out['beat']:
            self._register_beat(at)

        return out

    def is_beat(self, audio_data):
        """Совместимость со старым кодом: был ли хоть какой-то удар."""
        return self.analyze(audio_data)['beat']
