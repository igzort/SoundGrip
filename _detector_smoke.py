# -*- coding: utf-8 -*-
"""Headless-смоук детектора на плотном электронном бите (Skrillex-style).

Проверяет три вещи из реворка beat_detector.py:
  1. Спектральный поток: устойчивый жирный бас/средние НЕ дают ложных
     ударов и НЕ задирают адаптивный пол (детектор не слепнет).
  2. Лок-аут 200 мс: жирный снейр, пришедший следом за киком, не
     маскирует следующую бочку (кики на сетке 240 BPM ловятся все).
  3. Знаковый max(|L|,|R|) даунмикс: антифазные низкие частоты
     стереоканалов больше не уничтожаются.

Запуск: python _detector_smoke.py  ->  _detector_result.txt
"""
import time
import traceback

import numpy as np

from audio_capture import _to_int16_mono, _frames_to_mono_int16
from beat_detector import BeatDetector

SR = 48000
ok = True
lines = []


def step(name, cond):
    global ok
    lines.append(f'{name}: {"OK" if cond else "FAIL"}')
    print(name, 'OK' if cond else 'FAIL', flush=True)
    ok = ok and bool(cond)


def kick_wave(dur, f=58.0, amp=0.5):
    t = np.arange(int(dur * SR)) / SR
    return amp * np.sin(2 * np.pi * f * t) * np.exp(-t * 16.0)


def snare_wave(dur, amp=0.6):
    t = np.arange(int(dur * SR)) / SR
    rng = np.random.default_rng(7)
    n = rng.uniform(-1.0, 1.0, t.size)
    return amp * np.exp(-t * 22.0) * (
        0.8 * n + 0.4 * np.sin(2 * np.pi * 190.0 * t))


def hat_wave(dur, amp=0.22):
    t = np.arange(int(dur * SR)) / SR
    rng = np.random.default_rng(3)
    hp = np.diff(rng.uniform(-1.0, 1.0, t.size), prepend=0.0)
    return amp * np.exp(-t * 40.0) * hp


def build_dense_pattern(seconds=4.0):
    """Ломаный бит: кик каждые 0.25 с, ЖИРНЫЙ снейр на полтакта позже,
    хэты каждые 0.125 с — плотность уровня Bangarang."""
    total = int(seconds * SR)
    mix = np.zeros(total, dtype=np.float64)
    t_kick = np.arange(0.30, seconds - 0.3, 0.25)      # 240 BPM сетка кика
    t_snare = t_kick + 0.125                            # снейр МЕЖДУ киками
    t_hat = np.arange(0.3625, seconds - 0.3, 0.125)
    for t0 in t_kick:
        w = kick_wave(0.16)
        i = int(t0 * SR)
        mix[i:i + len(w)] += w
    for t0 in t_snare:
        w = snare_wave(0.18)
        i = int(t0 * SR)
        mix[i:i + len(w)] += w
    for t0 in t_hat:
        w = hat_wave(0.05)
        i = int(t0 * SR)
        mix[i:i + len(w)] += w
    peak = np.max(np.abs(mix))
    return (mix / max(peak, 1e-9) * 0.9 * 32767).astype(np.int16), t_kick


def feed_realtime(det, sig, seconds, kicks_out, snares_out,
                  onsets_out=None):
    """Кормим кусками 512 В РЕАЛЬНОМ ВРЕМЕНИ (лок-аут живёт на часах)."""
    n = 512
    frame = n / SR
    t0 = time.monotonic()
    pos = 0
    while pos < min(len(sig), int(seconds * SR)):
        ev = det.analyze(sig[pos:pos + n])
        t = (pos + n) / SR
        if ev['kick']:
            kicks_out.append(t)
        if ev['snare']:
            snares_out.append(t)
        if onsets_out is not None and ev.get('onset'):
            onsets_out.append((t, ev.get('onset_hue'),
                               ev.get('onset_power')))
        pos += n
        # держим темп потока: настенные часы = аудио-часы
        target = t0 + pos / SR
        d = target - time.monotonic()
        if d > 0:
            time.sleep(d)


def _run_all():
    # --- 1. плотный бит: все кики ловятся, снейры не маскируют --------------
    det = BeatDetector(sample_rate=SR)
    sig, t_kick = build_dense_pattern(4.0)
    kicks, snares = [], []
    feed_realtime(det, sig, 4.0, kicks, snares)

    step(f'кики на 240 BPM найдены ({len(kicks)}/{len(t_kick)})',
         len(kicks) >= 0.8 * len(t_kick))
    # каждое срабатывание лежит рядом с реальным киком (латентность FFT)
    near = sum(1 for k in kicks
               if any(abs(k - tk) < 0.09 for tk in t_kick))
    step('срабатывания совпадают с реальными киками', near == len(kicks))
    gaps = [b - a for a, b in zip(kicks, kicks[1:])]
    step('лок-аут: нет двойных срабатываний <180 мс',
         all(g >= 0.18 for g in gaps))
    lines.append(f'снейров поймано: {len(snares)}')
    step('снейры тоже детектятся', len(snares) >= 0.6 * len(t_kick))

    # --- 2. устойчивый жирный бас: БЕЗ ложных ударов ------------------------
    det2 = BeatDetector(sample_rate=SR)
    sust = (0.5 * np.sin(2 * np.pi * 55.0 *
                         np.arange(int(1.2 * SR)) / SR) * 32767
            ).astype(np.int16)
    sus_kicks, sus_snares = [], []
    feed_realtime(det2, sust, 1.2, sus_kicks, sus_snares)
    step(f'устойчивый бас без ложных киков ({len(sus_kicks)})',
         len(sus_kicks) <= 1)   # допускаем 1 — это старт тона (onset)
    step('устойчивый бас без ложных снейров', len(sus_snares) <= 1)

    # --- 3. тишина -> пол угас -> ТИХИЙ кик после паузы ловится -------------
    sil = np.zeros(SR // 2, dtype=np.int16)
    feed_realtime(det, sil, 0.5, [], [])
    quiet = (0.16 * np.sin(2 * np.pi * 58.0 *
                           np.arange(int(0.4 * SR)) / SR) * 32767
             ).astype(np.int16)
    qk, _q = [], []
    feed_realtime(det, quiet, 0.5, qk, _q)
    step('тихий кик после тишины детектится', len(qk) >= 1)

    # --- 4. антифазная стерео: max(|L|,|R|) спасает низы --------------------
    t = np.arange(SR // 4) / SR
    sine = 0.5 * np.sin(2 * np.pi * 58.0 * t)
    stereo = np.empty(sine.size * 2, dtype=np.float32)
    stereo[0::2] = sine          # L
    stereo[1::2] = -sine         # R в противофазе: (L+R)/2 == 0 !!!
    raw = stereo.tobytes()
    mono = _frames_to_mono_int16(raw, 2, 32, True)
    mean_mix = ((sine - sine) * 32767).astype(np.int16)  # что было бы со mean
    rms = lambda a: float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    step('антифазные низы выжили (max|L|,|R|)',
         rms(mono) > 0.6 * rms(_to_int16_mono(sine.astype(np.float32))))
    step('mean-даунмикс убил бы сигнал (контроль)', rms(mean_mix) < 1.0)
    # и через общий _to_int16_mono (2D массив int16, в шкале int16!)
    m2 = _to_int16_mono(np.stack([sine * 32767, -sine * 32767],
                                 axis=1).astype(np.int16))
    step('_to_int16_mono: антифаза выжила',
         rms(m2) > 0.6 * rms(sine * 32767))

    # --- 5. УНИВЕРСАЛЬНЫЙ ONSET: любой звук -> визуальный эффект ------------
    # Транзиенты 3500 Гц (между снейром 2000 и хэтом 4000): только onset.
    det3 = BeatDetector(sample_rate=SR)
    burst_len = int(0.075 * SR)
    t_b = np.arange(burst_len) / SR
    one = 0.5 * np.sin(2 * np.pi * 3500.0 * t_b) * np.exp(-t_b * 30.0)
    sig5 = np.zeros(int(2.4 * SR))
    for k in range(16):                      # 8 транзиентов в секунду
        s = int((0.20 + k * 0.125) * SR)
        sig5[s:s + burst_len] += one
    sig5 = np.clip(sig5 * 32767, -32768, 32767).astype(np.int16)
    onsets = []
    feed_realtime(det3, sig5, 2.3, [], [], onsets_out=onsets)
    step(f'серия 8 burst/с -> onset>=6 (поймано {len(onsets)})',
         len(onsets) >= 6)

    # устойчивый тон НЕ спамит onset (поток нулевой)
    det4 = BeatDetector(sample_rate=SR)
    on2 = []
    feed_realtime(det4, sust, 1.2, [], [], onsets_out=on2)
    step(f'устойчивый бас без onset-спама (поймано {len(on2)})',
         len(on2) <= 1)

    # hue из спектрального центроида: бас тёплый, верха холодные
    def one_burst_hue(freq):
        d5 = BeatDetector(sample_rate=SR)
        s6 = np.zeros(int(0.6 * SR))
        tb = np.arange(int(0.075 * SR)) / SR
        s6[int(0.25 * SR):int(0.25 * SR) + int(0.075 * SR)] = (
            0.5 * np.sin(2 * np.pi * freq * tb) * np.exp(-tb * 30.0))
        s6 = np.clip(s6 * 32767, -32768, 32767).astype(np.int16)
        hh = []
        feed_realtime(d5, s6, 0.6, [], [], onsets_out=hh)
        return hh[0][1] if hh else -1.0

    h_lo = one_burst_hue(120.0)
    h_hi = one_burst_hue(8000.0)
    lines.append(f'hue: бас={h_lo:.2f} верх={h_hi:.2f}')
    step(f'бас -> hue тёплый ({h_lo:.2f} < 0.30)', 0.0 <= h_lo < 0.30)
    step(f'верх -> hue холодный ({h_hi:.2f} > 0.55)', h_hi > 0.55)

    lines.append('RESULT: ' + ('PASS' if ok else 'FAIL'))


def _flush_result():
    with open('_detector_result.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


if __name__ == '__main__':
    try:
        _run_all()
    except Exception:       # noqa: BLE001 — трейс обязан попасть в отчёт
        lines.append('ERROR: ' + traceback.format_exc())
        ok = False
    finally:
        _flush_result()
        print('DETECTOR_SMOKE_DONE', 'PASS' if ok else 'FAIL', flush=True)
