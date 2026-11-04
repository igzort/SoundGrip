# -*- coding: utf-8 -*-
"""Пробник ACDC: печатает band/floor/env в моменты ударов и между ними."""
import os
os.environ.setdefault('KIVY_NO_ARGS', '1')

import numpy as np
import scipy.io.wavfile as wavfile
from beat_detector import BeatDetector

SR, data = wavfile.read('c:/python projects/SoundGrip/_backinblack.wav')
if data.ndim > 1:
    data = data.mean(axis=1)
data = data.astype(np.float64) / (np.abs(data).max() + 1e-9)

det = BeatDetector(sample_rate=SR)

CHUNK = 512
lines = []
fires = []
for i in range(0, len(data) - CHUNK, CHUNK):
    chunk = (data[i:i + CHUNK] * 32767).astype(np.int16)
    ev = det.analyze(chunk)
    d = ev.get('_dbg', {})
    kick_d = d.get('kick')
    onset_d = d.get('onset')
    if kick_d is not None:
        band, floor = kick_d
        onset_band = onset_d[0] if onset_d else 0.0
        t = i / SR
        fired_k = ev.get('kick', False)
        fired_o = ev.get('onset', False)
        marker = ''
        if fired_k:
            marker += ' [KICK]'
        if fired_o:
            marker += ' [ONSET]'
        if fired_k or fired_o or (i % (CHUNK * 200) == 0):
            lines.append(
                f't={t:7.2f}s kick_band={band:8.1f} kick_floor={floor:8.1f} '
                f'onset_band={onset_band:8.1f} ratio={band/max(floor,1):5.2f}'
                f'{marker}')
        if fired_k:
            fires.append(t)

with open('_bb_values.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print(f'fired kicks: {len(fires)}')
print(f'first fires: {fires[:10]}')
print('BB_VALUES_DONE')
