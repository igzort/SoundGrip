# -*- coding: utf-8 -*-
"""Пробник: кормим ACDC в BeatDetector и печатаем kick-полосу."""
import os
os.environ.setdefault('KIVY_NO_ARGS', '1')

import numpy as np
import scipy.io.wavfile as w

from beat_detector import BeatDetector

SR = 48000
CHUNK = 1024

wav_path = '_bb.wav'
sr_in, data = w.read(wav_path)
if data.ndim > 1:
    sig = data.mean(axis=1).astype(np.int16)
else:
    sig = data

det = BeatDetector(sample_rate=SR)

pos = 0
total = len(sig)
step = CHUNK
printed = 0
while pos < total and printed < 60:
    end = min(pos + step, total)
    chunk = sig[pos:end]
    if len(chunk) < step:
        pad = np.zeros(step, dtype=np.int16)
        pad[:len(chunk)] = chunk
        chunk = pad
    ev = det.analyze(chunk)
    dbg = ev.get('_dbg', {}).get('kick')
    if dbg is not None:
        flux, floor = dbg
        if flux > floor * 0.3:
            env = det._flux_env['kick']
            mult = 1.0
            threshold = max(floor * mult, env * 1.5)
            fired = ev.get('kick', False)
            print(f't={pos/SR:7.2f}s flux={flux:8.1f} floor={floor:8.1f} '
                  f'env={env:8.1f} thr={threshold:8.1f} '
                  f'{"PASS" if flux > threshold else "block"} '
                  f'fire={"Y" if fired else "n"}')
            printed += 1
    pos += step
print('BB_PROBE_DONE')