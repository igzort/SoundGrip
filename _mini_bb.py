# -*- coding: utf-8 -*-
"""Мини-проб: подаём ACDC кусками 128, смотрим rms/primed на первых 10 вызовах."""
import os
os.environ.setdefault('KIVY_NO_ARGS', '1')
import numpy as np
from scipy.io import wavfile
from beat_detector import BeatDetector

SR = 48000
_, sig = wavfile.read('_backinblack.wav')
sig = sig.astype(np.int16)[:5 * SR]

det = BeatDetector(sample_rate=SR)
chunk = 128
for i in range(0, len(sig) - chunk, chunk):
    ev = det.analyze(sig[i:i + chunk])
    rms = ev.get('level', 0)
    if i < chunk * 10:
        print(f'call i={i:5d}  buf_len={len(det._buf):6d}  '
              f'primed={det._primed}  fed={det._fed}  '
              f'win_end={det._win_end}  rms={rms:.4f}')
    if ev.get('kick'):
        print(f'  KICK at t={i/SR:.3f}s  power={ev.get("kick_power",0):.2f}')

print('done')
