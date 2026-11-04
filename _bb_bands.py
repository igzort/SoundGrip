# -*- coding: utf-8 -*-
"""Пробник: значения полос ACDC Back In Black — ищем реальные пороги."""
import os
import time
os.environ.setdefault('KIVY_NO_ARGS', '1')

import numpy as np
import scipy.io.wavfile as w

from beat_detector import BeatDetector, ONSET_ABS_MIN

SR = 48000
CHUNK = 1024

wav_path = os.path.join(os.path.dirname(__file__) or '.', '_bb.wav')
sr_in, data = w.read(wav_path)
if data.ndim > 1:
    sig = data.mean(axis=1).astype(np.int16)
else:
    sig = data

det = BeatDetector(sample_rate=SR)

kick_vals = []
snare_vals = []
hat_vals = []
onset_vals = []

pos = 0
total = len(sig)
while pos < total:
    end = min(pos + CHUNK, total)
    chunk = sig[pos:end]
    if len(chunk) < CHUNK:
        break
    ev = det.analyze(chunk)
    dbg = ev.get('_dbg', {})
    k = dbg.get('kick')
    s = dbg.get('snare')
    h = dbg.get('hat')
    o = dbg.get('onset')
    if k is not None:
        kick_vals.append(k[0])
    if s is not None:
        snare_vals.append(s[0])
    if h is not None:
        hat_vals.append(h[0])
    if o is not None:
        onset_vals.append(o[0])
    pos += CHUNK
    time.sleep(0.004)  # обход CPU-гейта для офлайн-теста

def stats(name, vals):
    if not vals:
        return f'{name}: no data'
    a = np.array(vals)
    return (f'{name}: n={len(a)} min={a.min():.1f} max={a.max():.1f} '
            f'mean={a.mean():.1f} median={np.median(a):.1f} '
            f'p90={np.percentile(a, 90):.1f} p99={np.percentile(a, 99):.1f}')

lines = [
    stats('kick', kick_vals),
    stats('snare', snare_vals),
    stats('hat', hat_vals),
    stats('onset', onset_vals),
    f'ONSET_ABS_MIN={ONSET_ABS_MIN}',
]

if kick_vals:
    ka = np.array(kick_vals)
    med = np.median(ka)
    peaks = ka[ka > med * 2.0]
    lines.append(f'kick peaks (>2x median={med:.1f}): n={len(peaks)} '
                 f'min={peaks.min():.1f} max={peaks.max():.1f}')

with open('_bb_bands.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('BB_BANDS_DONE')
