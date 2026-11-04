# -*- coding: utf-8 -*-
"""Headless smoke for the drum-hit effects (emit_kick/emit_snare/emit_hat):
checks that particles + rings spawn and scale with `power` and частота.
Запуск: python _fx_smoke.py  ->  _fx_smoke.txt
"""
import os
os.environ.setdefault('KIVY_NO_ARGS', '1')

import numpy as np
from effects import ParticleFx, WATER, SHAPE_DROP

W, H = 800.0, 600.0
ok = True
lines = []


def step(name, cond):
    global ok
    lines.append(f'{name}: {"OK" if cond else "FAIL"}')
    ok = ok and bool(cond)


fx = ParticleFx()
fx.width = W
fx.height = H


def n_particles():
    return int((fx._life > 0).sum())


def n_rings():
    return sum(1 for r in fx._rings if r is not None)


# --- 1. каждый удар создаёт частицы --------------------------------------
fx.emit_kick(W * 0.4, H * 0.4, power=1.0)
fx.emit_snare(W * 0.5, H * 0.4, power=1.0)
fx.emit_hat(W * 0.6, H * 0.4, power=1.0)
fx.update(1.0 / 60.0)
step('kick+snare+hat spawn particles', n_particles() > 10)

# --- 2. частота роста размера: при равной громкости ----------------------
# kick (низкие Гц) < snare (средние) < hat (высокие) — больше Гц = крупнее.
fx.clear()
fx.emit_kick(W * 0.4, H * 0.4, power=1.0)
pk = n_particles()
fx.clear()
fx.emit_snare(W * 0.4, H * 0.4, power=1.0)
ps = n_particles()
fx.clear()
fx.emit_hat(W * 0.4, H * 0.4, power=1.0)
ph = n_particles()
step('частицы растут с частотой (kick<=snare<=hat)',
     pk <= ps <= ph)

# --- 3. _FREQ_SCALE реально масштабирует геометрию ------------------------
fx.clear()
fx.emit_kick(W * 0.4, H * 0.4, power=1.0)
r1 = max((rg['r_max'] for rg in fx._rings if rg is not None), default=0.0)
ParticleFx._FREQ_SCALE = 2.0
fx.clear()
fx.emit_kick(W * 0.4, H * 0.4, power=1.0)
r2 = max((rg['r_max'] for rg in fx._rings if rg is not None), default=0.0)
ParticleFx._FREQ_SCALE = 1.0
step('_FREQ_SCALE масштабирует радиус кольца в ~2×', r2 > r1 * 1.4)

# --- 4. громкость (RMS) = размер ----------------------------------------
fx.clear()
fx.emit_snare(W * 0.5, H * 0.4, power=0.2)
weak = n_particles()
fx.clear()
fx.emit_snare(W * 0.5, H * 0.4, power=1.0)
strong = n_particles()
step('громче → больше частиц (RMS drives size)', strong > weak)


# --- 5. power=0 почти ничего не рисует ------------------------------------
fx.clear()
fx.emit_hat(W * 0.6, H * 0.4, power=0.0)
fx.update(1.0 / 60.0)
step('power=0 почти без частиц', n_particles() <= 2)

lines.append('RESULT: ' + ('PASS' if ok else 'FAIL'))
with open('_fx_smoke.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('FX_SMOKE_DONE', 'PASS' if ok else 'FAIL')
