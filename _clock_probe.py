# -*- coding: utf-8 -*-
"""Драйвер update() без окна: спавнятся ли кики по кадрам."""
import os
os.environ['KIVY_NO_WINDOW'] = '1'
import time
from main import GameWidget

w = GameWidget(map_name='backinblack')
w.state = 'playing'
w._sound = None
w._paused_pos = 0.0
w._perf_start = time.perf_counter()

snap = []
for i in range(180):
    w.update(1.0 / 60.0)
    time.sleep(1.0 / 120.0)
    now = w.audio_now()
    if i % 30 == 0 or (i < 60 and i % 6 == 0):
        snap.append('frame %3d now=%.2f active=%d idx=%d'
                    % (i, now, len(w._active), w._idx))

print('\n'.join(snap))
print('FINAL now=%.2f active=%d idx=%d spawned_total=%d'
      % (w.audio_now(), len(w._active), w._idx, w._idx))
assert w._idx > 0, 'НИ ОДНОЙ ноты не заспавнено за 3 с — вот и чёрный экран'
print('SPAWN DRIVER OK')

