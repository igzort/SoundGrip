# -*- coding: utf-8 -*-
"""Смоук: диагональные дуги, честный хитбокс 22.5 px, сочный медленный разрез."""
import os
os.environ['KIVY_NO_WINDOW'] = '1'
import io
import random
import math
import time as _time
import numpy as np
import effects as e
import main as m
from main import GameWidget, FlySphere

W, H = 800.0, 600.0
PEAK = H * m.TOSS_PEAK          # 336: чуть выше центра

w = GameWidget(map_name='backinblack')
w.fx.width = W
w.fx.height = H
kinds = set(n.get('kind') for n in w._map_notes)
assert kinds <= {'kick', 'snare', 'onset'} and len(w._map_notes) > 300
print('MAP: %d нот, типы: %s' % (len(w._map_notes), sorted(kinds)))

w.state = 'playing'
w._sound = None
calls = {'now': 5.0}
w.audio_now = lambda: calls['now']

emitted = []
w.fx.emit_slice = lambda *a, **k: emitted.append(('slice', k))
w.fx.emit_onset = lambda *a, **k: emitted.append(('onset',))
w.fx.emit_kick = lambda *a, **k: emitted.append(('kick',))


def mk(ax=None, ay=None, side=1.0, t=5.0, kind='kick', st=None):
    # изоляция сцен: каждый тест начинается с чистого поля, иначе сферы
    # прошлых тестов перехватывают тап/свайп (они стоят в тех же точках)
    for _old in list(w._active):
        w._remove(_old)
    w._active.clear()
    ax = W * 0.5 if ax is None else ax
    ay = PEAK if ay is None else ay
    rise = (2.0 * (ay - m.TOSS_LAUNCH_Y) / m.TOSS_G) ** 0.5
    vx = side * (W * m.TOSS_TRAVEL) / (2.0 * rise)
    s = FlySphere(ax, ay, vx, rise, t, kind, star_type=st,
                  screen_w=W, screen_h=H)
    w._active.append(s)
    w.add_widget(s)
    return s


# 1) честный хитбокс: радиус 22.5, допуск 0 -> r-0.5 хит, r+0.5 промах
assert m.SPHERE_R == 22.5 and m.SWIPE_TOL == 0.0
s = mk(t=5.0)
s.update_pos(5.0)
assert abs(s.radius_at(5.0) - m.SPHERE_R) < 1e-9
ok = w._try_tap(s.x + m.SPHERE_R - 0.5, s.y)      # внутри диска
assert ok and s.dead, 'клик у самого края диска должен быть хитом'
s_out = mk(t=6.0)
s_out.update_pos(6.0)
ok = w._try_tap(s_out.x + m.SPHERE_R + 0.5, s_out.y)   # на 0.5 px дальше
assert not ok and not s_out.dead, 'за радиусом — уже промах'
print('TEST1 хитбокс %.1f px, допуск 0: %.1f=хит, %.1f=промах OK'
      % (m.SPHERE_R, m.SPHERE_R - 0.5, m.SPHERE_R + 0.5))

# 2) дуга: вылет из нижнего угла, пик ровно в бит, падение за экран
s2 = mk(side=-1.0, t=7.0)                 # вылет справа -> летит влево
assert s2.vx < 0
s2.update_pos(7.0)
assert abs(s2.x - s2.ax) < 1e-9 and abs(s2.y - PEAK) < 1e-9  # пик = бит
s2.update_pos(s2._born)
assert abs(s2.y - m.TOSS_LAUNCH_Y) < 1e-6            # старт со дна
assert s2.x > s2.ax + 300.0 and s2.x > W             # старт правее и за экраном
# после бита падение замедлено (TOSS_G_DOWN = TOSS_G/4), поэтому до нижнего
# края шар теперь доходит за вдвое большее время, чем при полном TOSS_G
s2.update_pos(7.0 + 2.0 * s2.rise)
assert s2.x < s2.ax - 300.0 and s2.y < 0             # упал левее и за низ
print('TEST2 дуга: правый низ -> пик в бит -> медленное падение влево OK')

# 3) резка: пока видна — можно, до подброса и за экраном — нет
s3 = mk(side=-1.0, t=9.0)
assert not s3.sliceable(9.0 - s3.rise - 0.05)        # ещё не родилась
assert s3.sliceable(9.0)                             # пик
assert s3.sliceable(9.0 + 0.3)                       # падение
calls['now'] = 9.0 + s3.rise + 0.12                  # ушла за низ
assert not s3.sliceable(calls['now'])
print('TEST3 окно резки: вся дуга на экране OK')

# 4) тап у пика: эффект ровно радиуса 22.5, половинки без плавного fade
w.fx.clear()
emitted.clear()
calls['now'] = 7.0
s4 = mk(t=7.0)
s4.update_pos(7.0)
assert w._try_tap(s4.x, s4.y) and s4.dead
assert emitted == [('slice', {'color': m.STAR_RGB[1.0], 'power': 1.0,
                              'radius': m.SPHERE_R})], emitted
alive = np.nonzero(w.fx._life > 0)[0]
half = [i for i in alive if w.fx._shape[i] == 15.0]
assert len(half) == 2, (len(half), w.fx._shape[alive])
assert abs(w.fx._size[half[0]] - m.SPHERE_R * 1.16) < 0.01, w.fx._size[half]
assert abs(w.fx._fade_in[half[0]]) < 1e-9, w.fx._fade_in[half[0]]
assert w.fx._grav[half[0]] == e.DEBRIS_G, w.fx._grav[half[0]]
assert e.HALF_SPIN[0] <= abs(w.fx._spin[half[0]]) <= e.HALF_SPIN[1], \
    w.fx._spin[half[0]]      # небольшое вращение, не «цветок»
print('TEST4 тап -> сфера %.1f px, половинки мгновенно (fade=0) + спин OK'
      % m.SPHERE_R)

# 5) свайп: хитбокс СТРОГО по радиусу сферы (без запасов),
#    а взрыв и обломки — строго в ЦЕНТРЕ сферы
w.fx.clear()
s5 = mk(t=9.0)
s5.update_pos(9.0)
emitted.clear()
calls['now'] = 9.0
assert not hasattr(m, 'STAR_CUT_K'), 'коэффициенты запаса убраны'
r5 = s5.radius_at(9.0)
assert abs(r5 - m.SPHERE_R) < 1e-9, r5
w._last_mouse = (s5.ax - 150.0, s5.y)
w._blade_sweep(s5.ax + 150.0, s5.y, 9.0)
assert s5.dead
alive = np.nonzero(w.fx._life > 0)[0]
half = [i for i in alive if w.fx._shape[i] == 15.0]
assert len(half) == 2, len(half)
cx, cy = float(w.fx._pos[half[0], 0]), float(w.fx._pos[half[0], 1])
# STRICT ANCHOR: обломки родились ровно в ЦЕНТРЕ сферы (не на границе
# круга и не в точке входа лезвия)
assert abs(cx - s5.ax) < 1e-6 and abs(cy - PEAK) < 1e-6, (cx, cy)
# размер половинок — честный радиус сферы, не раздутый
assert abs(w.fx._size[half[0]] - m.SPHERE_R * 1.16) < 0.01
# разлёт половинок ПРОПОРЦИОНАЛЕН размеру фигуры: уменьшенный шар не должен
# расталкивать половинки сильнее прежнего (нормировка по e.FIG_R)
_spd = float(np.hypot(w.fx._vel[half[0], 0], w.fx._vel[half[0], 1]))
_k5 = m.SPHERE_R / e.FIG_R
assert 95.0 * _k5 <= _spd <= 150.0 * _k5, (_spd, _k5)
print('TEST5 свайп: хитбокс строго r=%.1f, обломки в центре (%.0f,%.0f) OK'
      % (r5, cx, cy))

# 6) свайп до рождения дуги — сфера цела
s6 = mk(t=11.0)
calls['now'] = 11.0 - s6.rise - 0.05
w._last_mouse = (200.0, 300.0)
w._blade_sweep(400.0, 300.0, calls['now'])
assert not s6.dead
print('TEST6 свайп до старта дуги -> сфера цела OK')

# 7) несрезанная уходит за экран (вниз или вбок) и убирается
calls['now'] = 11.0 + 0.3
assert not s6.should_remove(calls['now'])            # ещё в кадре
s6.update_pos(11.0 + 0.9)
assert s6.y < -m.CULL_MARGIN or s6.x < -m.CULL_MARGIN \
    or s6.x > W + m.CULL_MARGIN
assert s6.should_remove(11.0 + 0.9)
w._active.remove(s6)
w._remove(s6)
print('TEST7 несрезанная уходит за экран OK')

# 8) палитра типов: базовые + доп. белая/голубая/фиолетовая
assert FlySphere(0, 0, 0, 0.5, 1, 'kick').star_type == 1.0
assert FlySphere(0, 0, 0, 0.5, 1, 'snare').star_type == 0.0
assert FlySphere(0, 0, 0, 0.5, 1, 'onset').star_type == 5.0
assert FlySphere(0, 0, 0, 0.5, 1, 'kick', star_type=3.0).star_type == 3.0
print('TEST8 палитра типов OK')
# 9) цвет эффекта = цвет разрубленной звезды (все 6 типов)
for st, rgb in m.STAR_RGB.items():
    fs = FlySphere(0, 0, 0, 0.5, 1, 'kick', star_type=st)
    assert m._sphere_rgb(fs) == rgb, (st, m._sphere_rgb(fs))
print('TEST9 цвет эффекта = цвет звезды OK')

# 10) разгон в начале: шары только СНИЗУ, почти вертикально, старт под низом
_rnd_saved = (random.random, random.uniform, random.choice)
random.random = lambda: 0.0                  # плотность: все ноты проходят
random.uniform = lambda a, b: (a + b) / 2.0  # детерминированные параметры
random.choice = lambda seq: seq[0]
try:
    del w.audio_now
    w._idx = 0
    w._active.clear()
    w._spawn_due(0.5)
    assert w._idx >= 1 and len(w._active) >= 1
    sp = w._active[0]
    assert sp.mode == 'bottom', sp.mode                  # начало — только снизу
    assert m.TOSS_X_MIN - 1.0 <= sp.ax <= W - m.TOSS_X_MIN + 1.0, sp.ax
    assert abs(sp.ay - PEAK) <= m.TOSS_PEAK_JITTER + 1, sp.ay
    sp.update_pos(sp._born)                              # старт подачи
    assert abs(sp.y - m.TOSS_LAUNCH_Y) < 1e-6, sp.y      # под нижним краем
    assert abs(sp.x - sp.ax) < 80.0, (sp.x, sp.ax)       # почти вертикально
finally:
    (random.random, random.uniform, random.choice) = _rnd_saved
print('TEST10 начало: только снизу, апекс (%.0f,%.0f), старт (%.0f,%.0f) OK'
      % (sp.ax, sp.ay, sp.x, sp.y))

# 11) звезда попадает в GPU-батч (shape 14, голубая)
w.fx.set_stars([(100.0, 200.0, m.SPHERE_R, 3.0, 1.0)])
qbuf = np.zeros((w.fx._star_base + 2, 6, 12), dtype=np.float32)
w.fx._fill_stars(qbuf)
slot = w.fx._star_base
assert qbuf[slot, 0, 8] == 14.0 and qbuf[slot, 0, 9] > 0.0
assert abs(qbuf[slot, 0, 11] - 3.0) < 1e-6
print('TEST11 звезда в GPU-батче OK')

# 12) метеорный след 0.2 с
trail = []
w.fx.add_trail = lambda *a, **k: trail.append(k)
calls['now'] = 9.0
w.audio_now = lambda: calls['now']
w._last_mouse = (200.0, 200.0)
w._blade_sweep(300.0, 200.0, 9.0)
assert trail and trail[0].get('life') == (0.2, 0.2), trail
print('TEST12 метеорный след 0.2 с OK')

# 13) моментальное исчезновение: остатка-моста (shape 16) больше НЕТ
del w.fx.emit_slice
w.fx.clear()
s7 = mk(t=13.0)
s7.update_pos(13.0)
calls['now'] = 13.0
assert w._try_tap(s7.x, s7.y) and s7.dead
alive = np.nonzero(w.fx._life > 0)[0]
rem = [i for i in alive if w.fx._shape[i] == 16.0]
assert len(rem) == 0, ('мост-остаток не должен появляться', len(rem))
print('TEST13 сфера исчезает моментально (без остатка-моста) OK')

# 14) половинки сферы: без пола — просто падают вниз за экран и гаснут
halves = [i for i in alive if w.fx._shape[i] == 15.0]
assert len(halves) == 2, len(halves)
assert all(float(w.fx._floor[i]) == 0.0 for i in halves), 'у половинки пола нет'
assert all(float(w.fx._grav[i]) == e.DEBRIS_G for i in halves), 'падают вниз'
_life14 = float(w.fx._life[halves[0]])
_y14 = float(w.fx._pos[halves[0], 1])
_gone14 = None
for _f14 in range(int(6.0 * 60)):           # 6 с: должно хватить на экран
    w.fx.update(1.0 / 60.0)
    if _f14 < 60:                            # первую секунду следим за яркостью
        # НЕ ТУСКНЕЮТ: весь полёт в батче полная яркость (strength = 1.15)
        _qb = np.asarray(w.fx._mesh.vertices).reshape(-1, 6, 12)
        assert np.all(_qb[halves, 0, 7] > 1.0), _qb[halves, 0, 7]
    if all(w.fx._life[i] <= 0.0 for i in halves):
        _gone14 = _f14
        break
assert _gone14 is not None, 'половинки не погасли'
_y_end = float(w.fx._pos[halves[0], 1])
assert _y_end < 0.0, _y_end                  # ушли за нижний край экрана
assert _y_end < _y14, (_y14, _y_end)         # падали вниз
print('TEST14 половинки: без пола, упали с y=%.0f под y=%.0f за %.2f с '
      '(жизнь %.2f с) OK' % (_y14, _y_end, (_gone14 + 1) / 60.0, _life14))
# 15) разваливание: разгон скорости, время жизни не тронуто
w.fx.clear()
s8 = mk(t=15.0)
s8.update_pos(15.0)
calls['now'] = 15.0
assert w._try_tap(s8.x, s8.y) and s8.dead
alive = np.nonzero(w.fx._life > 0)[0]
halves = [i for i in alive if w.fx._shape[i] == 15.0]
assert len(halves) == 2, len(halves)
angs = [float(w.fx._ang[i]) for i in halves]
# «книга»: половинки летят СТРОГО в противоположные стороны (без разброса)
d_ang = abs(((angs[0] - angs[1]) % (2.0 * math.pi)) - math.pi)
assert d_ang < 0.02, angs
# срез почти ГОРИЗОНТАЛЬНЫЙ: линия ~200°/20°, уклон ~±20° от горизонтали
# (ang0 — направление скорости = срез + 90°, значит наклон среза = ang0 - 90°)
tilts = []
for a in angs:
    t_deg = math.degrees(math.atan2(math.sin(a), math.cos(a))) - 90.0
    tilts.append(abs(((t_deg + 90.0) % 180.0) - 90.0))
assert all(abs(t - 20.0) < 6.0 for t in tilts), tilts
# ОДИНАКОВАЯ скорость у обеих (зеркальная пара), без разброса
vs = [float(np.hypot(w.fx._vel[i, 0], w.fx._vel[i, 1])) for i in halves]
assert abs(vs[0] - vs[1]) < 1e-3, vs
assert abs(vs[0] - (55.0 + 45.0)) < 1.0, vs      # ~100 px/с при power=1
# мгновенный старт: без проявления и разгона
assert all(float(w.fx._fade_in[i]) == 0.0 for i in halves)
assert all(float(w.fx._ramp[i]) == 0.0 for i in halves)
# НЕБОЛЬШОЕ вращение: в противоположные стороны и одинаковым размахом
spins = [float(w.fx._spin[i]) for i in halves]
assert spins[0] * spins[1] < 0.0, spins          # вращаются в ПРОТИВОФАЗЕ
assert abs(abs(spins[0]) - abs(spins[1])) < 1e-6, spins
assert all(e.HALF_SPIN[0] <= abs(s) <= e.HALF_SPIN[1] for s in spins), spins
# но гравитация ЕСТЬ: разъехавшись, половинки обязаны падать
assert all(float(w.fx._grav[i]) > 0.0 for i in halves)


def _sep():
    return float(np.hypot(w.fx._pos[halves[0], 0] - w.fx._pos[halves[1], 0],
                          w.fx._pos[halves[0], 1] - w.fx._pos[halves[1], 1]))


for _k in range(6):                          # ~0.10 с — фаза РАЗЛЁТА
    w.fx.update(0.016)
xs = [float(w.fx._pos[i, 0]) for i in halves]
ys_mid = [float(w.fx._pos[i, 1]) for i in halves]
assert (xs[0] - s8.x) * (xs[1] - s8.x) < 0.0, xs   # в РАЗНЫЕ стороны
assert _sep() > 6.0, 'половинки почти не разлетелись: %.2f' % _sep()
# симметрия «книги»: по X строго зеркальны (сил по X нет). По Y стартуют
# зеркально, дальше гравитация одинаково тянет ОБЕ вниз: сумма вертикальных
# скоростей после 6 кадров = −2·g·t
_dx = [xs[0] - s8.x, xs[1] - s8.x]
assert _dx[0] * _dx[1] < 0.0 and abs(_dx[0] + _dx[1]) < 1e-2, _dx
_vy = [float(w.fx._vel[i, 1]) for i in halves]
assert abs(sum(_vy) + 2.0 * e.DEBRIS_G * 6 * 0.016) < 0.5, _vy
for _k in range(30):                         # ~0.5 с: продолжение разлёта и падение
    w.fx.update(0.016)
xs_end = [float(w.fx._pos[i, 0]) for i in halves]
ys_end = [float(w.fx._pos[i, 1]) for i in halves]
assert abs(xs_end[0] - xs_end[1]) > abs(xs[0] - xs[1]), 'разлёт не состоялся'
# разлетелись в стороны И ПАДАЮТ (не парят). Сравниваем СРЕДНЮЮ высоту пары:
# одна половинка по разрезу уходит вверх, другая вниз — вместе они падают
_avg_mid = sum(ys_mid) / 2.0
_avg_end = sum(ys_end) / 2.0
assert _avg_mid - _avg_end > 20.0, \
    'половинки парят, а не падают: %s -> %s' % (ys_mid, ys_end)
print('TEST15 раскол: срез ~%.0f°, разлёт %.1f px, падение пары %.0f px OK'
      % (tilts[0], abs(xs_end[0] - xs_end[1]), _avg_mid - _avg_end))

# 16) моментальное исчезновение: сфера убирается сразу, БЕЗ остаточного fade
w.fx.clear()
w._idx = len(w._map_notes)                   # не спавним — чистая сцена
w._active.clear()
s9 = mk(t=16.0)
s9.update_pos(16.0)
calls['now'] = 16.0
assert w._try_tap(s9.x, s9.y) and s9.dead and s9.died_at == 16.0
assert s9.die_done(16.0), 'сфера обязана исчезать моментально'
assert s9.star_state(16.0)[4] == 0.0, 'fade исчезнувшей сферы обязан быть 0'
calls['now'] = 16.0 + 0.016
w.update(0.016)
assert s9 not in w._active, 'сфера не убрана из отрисовки сразу'
leftover = [st[4] for st in w.fx._stars if abs(st[0] - s9.x) < 2.0]
assert all(f <= 0.0 for f in leftover), leftover
print('TEST16 моментальное исчезновение сферы (без fade, без задержки) OK')

# 17) динамический разгон: снизу -> +бок -> +верх; плотность растёт
assert w._phase_weights(0.0) == (1.0, 0.0, 0.0), w._phase_weights(0.0)
assert w._pick_mode(0.0, rnd=lambda: 0.0) == 'bottom'
assert w._pick_mode(0.0, rnd=lambda: 0.999) == 'bottom'
mid = w._phase_weights(0.5)
assert mid[1] > 0.5 and mid[2] == 0.0, mid          # боковые есть, верха нет
late = w._phase_weights(0.9)
assert late[1] > 0.99 and late[2] > 0.5, late       # верх подключается
end = w._phase_weights(1.0)
assert end[1] > 0.99 and end[2] > 0.99, end
assert w._pick_mode(1.0, rnd=lambda: 0.999) == 'top'   # к финалу — сверху
assert w._pick_mode(1.0, rnd=lambda: 0.0) == 'bottom'  # снизу остаётся всегда
d0, d_mid = w._density(0.0), w._density(0.5)
assert abs(d0 - m.DENSITY_START) < 1e-9, d0
assert d_mid > d0 and abs(d_mid - 1.0) < 1e-9, d_mid
print('TEST17 разгон: веса %s -> %s, плотность %.2f -> %.2f OK'
      % (mid, end, d0, d_mid))

# 18) верхний режим: приходит СВЕРХУ, в бит проходит ay и падает дальше вниз
up_top = H + m.TOP_LAUNCH_MARGIN
rise_top = (2.0 * (up_top - PEAK) / m.TOSS_G) ** 0.5
s_top = FlySphere(400.0, PEAK, 0.0, rise_top, 20.0, 'kick',
                  screen_w=W, screen_h=H, mode='top', launch_y=up_top)
assert s_top.fall_v0 > 0.0                       # свободное падение СКВОЗЬ
assert abs(s_top.pos_at(20.0 - rise_top)[1] - up_top) < 1e-6   # старт сверху
assert not s_top.sliceable(20.0 - rise_top)          # в спавне: 710 > h+100
# сверху он стартует ИЗ ПОКОЯ, поэтому входит в окно реза не сразу
t_enter = 20.0 - rise_top
while s_top.pos_at(t_enter)[1] > H + m.CULL_MARGIN and t_enter < 20.0:
    t_enter += 0.005
assert not s_top.sliceable(20.0 - rise_top + 0.02)   # ещё НАД окном реза
assert s_top.sliceable(t_enter)                      # дошёл до ±100 — режется
assert t_enter < 20.0                                # и это ещё до бита
assert s_top.sliceable(20.0)                         # в бит на экране
assert abs(s_top.pos_at(20.0)[1] - PEAK) < 1e-6      # бит ровно на ay
# СКВОЗНОЕ падение: и до бита, и ПОСЛЕ него шар идёт только ВНИЗ
y_before = s_top.pos_at(20.0 - 0.10)[1]
y_beat = s_top.pos_at(20.0)[1]
y_after = s_top.pos_at(20.0 + 0.10)[1]
assert y_before > y_beat > y_after, (y_before, y_beat, y_after)
assert y_after < y_beat - 100.0, (y_beat, y_after)    # реально валится вниз
# доходит до нижнего края: в окне ±100 ещё режется, дальше — убираем
t_out = 20.0
while s_top.pos_at(t_out)[1] > -m.CULL_MARGIN and t_out < 20.0 + 2.0:
    t_out += 0.01
assert s_top.pos_at(t_out)[1] <= -m.CULL_MARGIN, s_top.pos_at(t_out)[1]
assert s_top.should_remove(t_out)                    # ушёл ВНИЗ за экран
print('TEST18 верх: старт y=%.0f -> бит y=%.0f сквозным падением -> уход вниз '
      'на t+%.2f с OK' % (up_top, PEAK, t_out - 20.0))

# 19) цепочка хитбоксов вдоль пути: быстрый шар не проскакивает мах
w.fx.clear()
s19 = mk(t=25.0)                       # боковая дуга, апекс (400, 336) в t=25
t0f = 25.0 - 0.20                      # «кадр» 0.2 с -> пролёт пути ~129 px
x0f, y0f = s19.pos_at(t0f)
x1f, y1f = s19.pos_at(25.0)
path19 = math.hypot(x1f - x0f, y1f - y0f)
assert path19 > 100.0, path19          # шар реально пролетает много за кадр
# сэмплы каждые HB_STEP px: шаг между соседними центрами <= HB_STEP
samps = list(w._hb_samples(s19, t0f, 25.0))
assert len(samps) >= 13, len(samps)
steps19 = [math.hypot(samps[i + 1][0] - samps[i][0],
                      samps[i + 1][1] - samps[i][1])
           for i in range(len(samps) - 1)]
assert max(steps19) <= m.HB_STEP + 0.01, max(steps19)
# лезвие ВЕРТИКАЛЬНО посередине пути: оба КРАЙНИХ диска пути его НЕ задевают,
# значит старая проверка (одна точка) дала бы промах
mid_x = 0.5 * (x0f + x1f)
d_end = min(abs(x0f - mid_x), abs(x1f - mid_x))
assert d_end > m.SPHERE_R, (d_end, m.SPHERE_R)
w._last_mouse = (mid_x, 250.0)
w._blade_sweep(mid_x, 420.0, 25.0, t0f)
assert s19.dead, 'лезвие наперерез пути обязано срезать быстрый шар'
alive = np.nonzero(w.fx._life > 0)[0]
halves19 = [i for i in alive if w.fx._shape[i] == 15.0]
assert len(halves19) == 2, len(halves19)
# STRICT ANCHOR: обломки — ровно в центре сферы НА ЭКРАНЕ (pos_at(now)),
# а не в сэмпле пути (тот соответствует более раннему времени кадра)
size19 = float(w.fx._size[halves19[0]])
rad19 = size19 / 1.16
assert abs(rad19 - s19.radius_at(25.0)) < 0.01, rad19
cx19 = float(w.fx._pos[halves19[0], 0])
cy19 = float(w.fx._pos[halves19[0], 1])
ex19, ey19 = s19.pos_at(25.0)                  # центр сферы в момент реза
assert abs(cx19 - ex19) < 1e-6 and abs(cy19 - ey19) < 1e-6, \
    (cx19, cy19, ex19, ey19)
# какой сэмпл пути лезвие задело ПЕРВЫМ — именно он был старым якорем
hit_s = None
for sx, sy, rr, _tt in samps:
    assert 250.0 <= sy <= 420.0, sy       # все сэмплы внутри вертикального маха
    if abs(sx - mid_x) <= rr + m.SWIPE_TOL:
        hit_s = (sx, sy)
        break
assert hit_s is not None, 'цепочка обязана дать попадание'
stale = math.hypot(hit_s[0] - ex19, hit_s[1] - ey19)
assert stale > 20.0, stale                # старый якорь уехал бы мимо шара
print('TEST19 цепочка хитбоксов: путь %.0f px, %d сэмплов, шаг %.1f px, '
      'обломки ровно в центре шара (%.0f,%.0f); старый якорь уехал бы на '
      '%.0f px OK'
      % (path19, len(samps), max(steps19), cx19, cy19, stale))

# 20) искры на взрыве: радиальный веер цвета сферы + белое ядро
w.fx.clear()
s20 = mk(t=27.0)
calls['now'] = 27.0
w._slice(s20, 27.0, angle=m.CUT_ANGLE)
alive = np.nonzero(w.fx._life > 0)[0]
sparks = [i for i in alive if w.fx._shape[i] == 0.0]
assert len(sparks) >= 12, len(sparks)          # веер + ядро
grav = [float(w.fx._grav[i]) for i in sparks]
assert grav.count(420.0) >= 10, sorted(set(grav))   # цветной веер
assert grav.count(0.0) >= 3, sorted(set(grav))      # белое ядро
# все искры стартуют в точке реза
offs = [float(np.hypot(w.fx._pos[i, 0] - 400.0,
                       w.fx._pos[i, 1] - 336.0)) for i in sparks]
assert max(offs) < 1.0, max(offs)
# цветные искры — цветом сферы, ядро — белое (учёт джиттера *0.80..1.15:
# проверяем по синему каналу — у жёлтых/красных сфер он низкий)
core = [i for i in sparks if w.fx._grav[i] == 0.0]
col = w.fx._rgb[core[0]]
assert col[0] > 0.75 and col[2] > 0.55 and col[0] >= col[2], col
print('TEST20 искры: %d шт (веер %d + ядро %d), старт в точке реза OK'
      % (len(sparks), grav.count(420.0), grav.count(0.0)))

# 21) хитбокс лезвия СТРОГО равен радиусу сферы (никаких запасов):
#     оффсет r-1 -> рез, оффсет r+1 -> промах
w.fx.clear()
s21 = mk(t=29.0, side=0.0)
s21.update_pos(29.0)
calls['now'] = 29.0
r21 = s21.radius_at(29.0)
assert abs(r21 - m.SPHERE_R) < 1e-9, r21
w._last_mouse = (s21.x + r21 - 1.0, s21.y - 200.0)
w._blade_sweep(s21.x + r21 - 1.0, s21.y + 200.0, 29.0)
assert s21.dead, 'внутри радиуса (r-1) лезвие обязано резать'
s21b = mk(t=29.0, side=0.0)
s21b.update_pos(29.0)
w._last_mouse = (s21b.x + r21 + 1.0, s21b.y - 200.0)
w._blade_sweep(s21b.x + r21 + 1.0, s21b.y + 200.0, 29.0)
assert not s21b.dead, 'за радиусом (r+1) резать нельзя — запасов нет'
# и обломки от этого реза — в центре сферы
alive = np.nonzero(w.fx._life > 0)[0]
half = [i for i in alive if w.fx._shape[i] == 15.0]
assert len(half) == 2, len(half)
assert abs(float(w.fx._pos[half[0], 0]) - s21.x) < 1e-6
assert abs(float(w.fx._pos[half[0], 1]) - s21.y) < 1e-6
print('TEST21 хитбокс лезвия = r: +%.0f px рез, +%.0f px мимо OK'
      % (r21 - 1.0, r21 + 1.0))

# 22) окно жизни ±100 px: шар у самого края (y=-60) режется, а за ±100 — нет
assert m.CULL_MARGIN == 100.0
s22 = mk(t=31.0, side=0.0)
t_edge = 31.0 + (2.0 * (PEAK + 60.0) / m.TOSS_G_DOWN) ** 0.5   # y ~ -60
t_far = 31.0 + (2.0 * (PEAK + 120.0) / m.TOSS_G_DOWN) ** 0.5   # y ~ -120
y_edge = s22.pos_at(t_edge)[1]
assert -100.0 < y_edge < -45.0, y_edge
assert s22.sliceable(t_edge), 'у самого края экрана шар обязан резаться'
y_far = s22.pos_at(t_far)[1]
assert y_far < -100.0, y_far
assert not s22.sliceable(t_far), 'дальше ±100 — уже вне игры'
print('TEST22 окно ±100: y=%.0f режется, y=%.0f уже нет OK'
      % (y_edge, y_far))

# 23) подшаги 200 Гц: рез проверяется не раз за кадр, а каждые ~4-5 мс
class _FakeWin:
    mouse_pos = (0.0, 0.0)


_real_window = m.Window
m.Window = _FakeWin
try:
    w.fx.clear()
    for _old in list(w._active):
        w._remove(_old)
    w._active.clear()
    w._idx = len(w._map_notes)               # свои ноты не спавним

    # шар, падающий СВЕРХУ: в бит проходит ay и дальше валится вниз. После
    # бита падение замедленное (TOSS_G_DOWN), поэтому берём кадр сразу за битом,
    # где скорость ещё большая — тут суб-шаги и нужны
    up23 = H + m.TOP_LAUNCH_MARGIN
    rise23 = (2.0 * (up23 - PEAK) / m.TOSS_G) ** 0.5
    s23 = FlySphere(W * 0.5, PEAK, 0.0, rise23, 25.0, 'kick',
                    screen_w=W, screen_h=H, mode='top', launch_y=up23)
    w._active.append(s23)
    w.add_widget(s23)
    t_frame = 25.0 + 0.20                     # +0.2 с после бита, x = 400
    s23.update_pos(t_frame)
    y_start = s23.y                          # за кадр шар уедет вниз на ~19 px
    assert s23.sliceable(t_frame), 'шар должен быть на экране'
    speed23 = abs(s23.pos_at(t_frame + 0.016)[1] - y_start) / 0.016
    assert speed23 > 500.0, speed23          # быстрый участок — есть что терять
    w._cur_now = t_frame
    w._last_mouse = (100.0, y_start)
    calls['now'] = t_frame + 1.0 / 60.0
    sweeps = []
    real_sweep = w._blade_sweep

    def _spy(mx, my, now, t0=None, prev=None):
        sweeps.append((now, t0))
        return real_sweep(mx, my, now, t0, prev)

    w._blade_sweep = _spy
    _FakeWin.mouse_pos = (700.0, y_start)    # мах сквозь колонну шара
    w.update(1.0 / 60.0)
    del w._blade_sweep                       # вернуть метод класса

    assert s23.dead, 'рез обязан сработать ВНУТРИ кадра'
    # лезвие идёт x=100 -> 700 за кадр, шар в колонне x=400: мах доходит до
    # шара ровно на середине кадра -> рез на ВТОРОМ суб-тике из четырёх
    assert len(sweeps) == 4, len(sweeps)     # 60 Гц-кадр -> 4 суб-тика (~240 Гц)
    res_ms = (sweeps[1][0] - sweeps[0][0]) * 1000.0
    assert 3.0 <= res_ms <= 6.0, res_ms
    assert sweeps[0][1] == t_frame, sweeps[0]
    assert abs(sweeps[-1][0] - (t_frame + 1.0 / 60.0)) < 1e-9, sweeps[-1]
    alive = np.nonzero(w.fx._life > 0)[0]
    halves = [i for i in alive if w.fx._shape[i] == 15.0]
    assert len(halves) == 2, len(halves)
    ax23 = float(w.fx._pos[halves[0], 0])
    ay23 = float(w.fx._pos[halves[0], 1])
    tx, ty = s23.pos_at(sweeps[1][0])        # центр шара на 2-м суб-тике
    # _pos в буфере частиц — float32, поэтому допуск 1e-3 (для 150 px это ~1e-5)
    assert abs(ax23 - tx) < 1e-3 and abs(ay23 - ty) < 1e-3, (ax23, ay23, tx, ty)
    # суб-тик РАЗЛИЧИМ: на 1-м суб-тике взрыв стоял бы на ~3 px выше
    _px23, py23 = s23.pos_at(sweeps[0][0])
    assert abs(py23 - ay23) > 1.0, (py23, ay23)
    # ...а если ждать конца кадра — уехал бы ещё ниже
    _fx23, fy23 = s23.pos_at(t_frame + 1.0 / 60.0)
    drift = abs(fy23 - ay23)
    assert drift > 5.0, drift
    print('TEST23 суб-тики 200 Гц: 4 подшага за кадр 60 Гц, разрешение %.1f мс, '
          'рез и якорь — на суб-тике (кадровый якорь дал бы сдвиг %.1f px) OK'
          % (res_ms, drift))
finally:
    m.Window = _real_window
    w._last_mouse = None
    w._cur_now = None

# 24) боковые дуги замедлены на 15%, апекс по-прежнему РОВНО в бит
assert abs(m.SIDE_SPEED_K - 0.85) < 1e-9
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
_saved_pick = w._pick_mode
w._pick_mode = lambda p, rnd=None: 'side'
try:
    w._idx = 0
    w._spawn_one({'t': 40.0, 'kind': 'kick'})
finally:
    w._pick_mode = _saved_pick
s24 = w._active[-1]
assert s24.mode == 'side'
nominal = (W * m.TOSS_TRAVEL) / (2.0 * s24.rise)     # скорость без замедления
assert abs(abs(s24.vx) - nominal * m.SIDE_SPEED_K) < 1e-6, (s24.vx, nominal)
assert abs(s24.pos_at(40.0)[1] - s24.ay) < 1e-9      # апекс ровно в бит
# за дугу перелетает на другую сторону от апекса
assert (s24.pos_at(40.0 - s24.rise)[0] - s24.ax) \
    * (s24.pos_at(40.0 + s24.rise)[0] - s24.ax) < 0.0
print('TEST24 боковые: скорость -15%% (%.0f -> %.0f px/с), апекс ровно в бит OK'
      % (nominal, abs(s24.vx)))

# 25) КУБИК-РУБИК: фигура, вращение, разрез -> ВСЕ клетки кубиками 1×1
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
cub = FlySphere(400.0, PEAK, 0.0, 0.7, 30.0, 'kick', screen_w=W, screen_h=H,
                figure='cube')
assert cub.figure == 'cube' and cub.rgb == m.STAR_RGB[round(cub.star_type)]
cub.update_pos(30.0)
st30 = cub.star_state(30.0)
assert len(st30) == 9, st30                        # + grid (3)
assert st30[5] == m.SHAPE_CUBE_WIRE, st30
assert st30[6] > 0.1, st30                        # наклон (pitch) есть
assert abs(st30[2] - m.SPHERE_R) < 1e-9, st30[2]  # радиус = хитбокс
assert st30[8] == 3, st30                         # единственная решётка 3×3
yaw_a = st30[3]
yaw_b = cub.star_state(30.5)[3]
assert yaw_a != yaw_b, 'куб обязан вращаться'
# разрез: вспышка/кольцо/искры + РАЗВАЛ НА КЛЕТКИ (кубики 1×1), без половинок
calls['now'] = 30.0
w._slice(cub, 30.0)
alive = np.nonzero(w.fx._life > 0)[0]
cubes = [i for i in alive if w.fx._shape[i] == m.SHAPE_CUBE_WIRE]
sparks = [i for i in alive if w.fx._shape[i] == 0.0]
halves = [i for i in alive if w.fx._shape[i] == 15.0]
n25 = st30[8]                                  # клеток по ребру (3/4/5)
assert len(halves) == 0, 'у куба не должно быть половинок звезды'
assert len(cubes) == n25 ** 3, (len(cubes), n25)   # ровно ВСЕ клетки куба
assert len(sparks) >= 8, len(sparks)
yaw_cut, tilt_cut = st30[3], st30[6]
cell25 = st30[2] * e.CUBE_QUAD_K * e.CUBE_FRAME / n25   # половина клетки
for i in cubes:
    assert float(w.fx._aux[i, 2]) == 1.0, 'осколок = кубик 1×1 (grid=1)'
    assert float(w.fx._grav[i]) > 0.0, 'осколки падают'
    assert float(w.fx._floor[i]) > 0.0, 'у клетки свой пол'
    assert float(w.fx._hold[i]) == 0.0, 'на старте клетка в полёте'
    assert abs(float(w.fx._ang[i])) <= 0.10 + 1e-6, 'квад осколка = квад фигуры'
    dy = abs(float(w.fx._aux[i, 0]) - yaw_cut)          # yaw фигуры в момент реза
    dp = abs(float(w.fx._aux[i, 1]) - tilt_cut)         # её же наклон
    assert min(dy, 2 * math.pi - dy) <= 0.35 + 1e-6, dy
    assert dp <= 0.30 + 1e-6, dp
    # осколок РАВЕН клетке большого куба (тот же размер, что у него в решётке)
    assert abs(float(w.fx._size[i]) * e.CUBE_FRAME - cell25) <= 0.05 * cell25, \
        (w.fx._size[i], cell25)
# осколки рождаются НА МЕСТАХ КЛЕТОК (проекция как в шейдере), а не в центре
rel = np.hypot(w.fx._pos[cubes, 0] - cub.x, w.fx._pos[cubes, 1] - cub.y)
assert rel.max() > 5.0, 'осколки должны стартовать не из одной точки'
u25 = -(1.0 - 1.0 / n25)                    # первая клетка решётки (meshgrid)
_cy25, _sy25 = math.cos(yaw_cut), math.sin(yaw_cut)
_cp25, _sp25 = math.cos(tilt_cut), math.sin(tilt_cut)
_x1 = u25 * _cy25 + u25 * _sy25
_z1 = -u25 * _sy25 + u25 * _cy25
_exp = (cub.x + cell25 * n25 * _x1,
        cub.y + cell25 * n25 * (u25 * _cp25 - _z1 * _sp25))
d25 = np.hypot(w.fx._pos[cubes, 0] - _exp[0], w.fx._pos[cubes, 1] - _exp[1])
assert d25.min() < 0.01, d25.min()
print('TEST25 РУБИК %dx%d: фигура r=%.0f, вращение, разрез -> %d клеток-кубиков '
      '1×1 размера клетки (%.1f px) OK'
      % (n25, n25, st30[2], len(cubes), cell25))

# 26) шейдер: центровка uv + ветка куба с решёткой 3×3 / клеткой 1×1
_src = io.open(os.path.join(m.BASE_DIR, 'effects.py'),
               encoding='utf-8').read()
assert '(v_uv - 0.5)' not in _src, 'фигуры обязаны считаться от центра квада'
assert 'SHAPE_CUBE_WIRE = 17.0' in _src
assert 'else if (sh < 17.5)' in _src
assert 'float grid = v_aux.w' in _src, 'grid читается из aux.w'
assert 'cubeProjD' in _src, 'заливка: проекция с глубиной для выбора грани'
assert 'sticker' in _src and 'groove' in _src, 'стикеры залиты, пазы тёмные'
assert 'streak' in _src, 'скользящий блик на заливке'
assert 'grid >= 0.5' in _src, 'клетка 1×1 (grid=1) обязана идти по ЗАЛИВКЕ'
assert 'take = bx && by' in _src, 'каркас 1×1 (резерв): только 12 рёбер'
assert '-1.0 + 0.4 * float(i)' in _src, 'узлы контура 1×1 (шаг 0.4)'
assert '_cube_proj' in _src and 'CUBE_FRAME' in _src, \
    'CPU-проекция клеток обязана жить рядом с CUBE_FRAME (синхрон с GLSL)'
print('TEST26 шейдер: uv=0, заливка (стикеры/пазы/блики) у grid 1..5, '
      'каркас 1×1 (12 рёбер) — резерв OK')

# 27) раскладка фигур: куб каждая CUBE_EVERY-я, вода каждая WATER_EVERY-я
figs = [m.pick_figure(_i) for _i in range(m.CUBE_EVERY * m.WATER_EVERY * 4)]
for _i, _f in enumerate(figs):
    if _i % m.CUBE_EVERY == m.CUBE_EVERY - 1:
        assert _f == 'cube', (_i, _f)            # куб приоритетнее воды
    elif _i % m.WATER_EVERY == m.WATER_EVERY - 1:
        assert _f == 'water', (_i, _f)
    else:
        assert _f == 'star', (_i, _f)
assert figs.count('cube') > 0 and figs.count('water') > 0 \
    and figs.count('star') > 0, (figs.count('cube'), figs.count('water'))
# форс из превью: любая из трёх фигур, даже «не по расписанию»
assert m.pick_figure(0, 'star') == 'star'
assert m.pick_figure(0, 'cube') == 'cube'
assert m.pick_figure(1, 'water') == 'water'
assert m.pick_figure(0, 'bogus') in ('star', 'cube', 'water')  # мусор -> расписание
print('TEST27 раскладка фигур: %s OK' % ''.join(
    {'cube': 'К', 'water': 'в'}.get(f, 'з') for f in figs))

# 28) режим превью «Эффекты»: часы идут, фигура форсируется и тает сама
_saved_notes, _saved_data = w._map_notes, w.map_data
_saved_audio = w.audio_now
try:
    # в реальной игре audio_now() в превью отдаёт _fx_clock (см. main),
    # а в тесте он подменён заглушкой — ставим эквивалент
    w.audio_now = lambda: w._fx_clock
    w.fx.clear()
    w.show_effects('cube')
    assert w.state == 'effects' and w._force_figure == 'cube'
    assert len(w._map_notes) == m.FX_DEMO_COUNT
    t0_fx = w._fx_clock
    for _k in range(3):                        # кадры превью
        w.update(0.016)
    assert w._fx_clock > t0_fx, 'часы превью обязаны идти без музыки'
    for _k in range(200):                      # дожимаем до первой фигуры
        w.update(0.02)
        if w._active:
            break
    assert w._active, 'в превью обязана появиться фигура'
    demo_s = w._active[0]
    assert demo_s.figure == 'cube', demo_s.figure    # форс фигуры работает
    born_t = demo_s._born
    for _k in range(300):                      # фигура тает сама
        w.update(0.02)
        if demo_s.dead:
            break
    assert demo_s.dead, 'в превью фигура обязана рассыпаться сама'
    assert w._fx_clock - born_t >= m.FX_AUTO_SLICE - 0.05
    print('TEST28 превью эффектов: фигура появляется, режется сама '
          'на +%.2f с от рождения, часы идут OK' % (w._fx_clock - born_t))
finally:
    w._map_notes, w.map_data = _saved_notes, _saved_data
    w.audio_now = _saved_audio
    w._force_figure = None
    w.state = 'playing'

# 29) GPU-батч: летящий куб несёт свой grid (3), осколки — grid=1 (1×1)
w.fx.clear()
cub2 = FlySphere(400.0, PEAK, 0.0, 0.7, 31.0, 'kick', screen_w=W, screen_h=H,
                 figure='cube')
assert cub2.grid == 3, cub2.grid
cub2.update_pos(31.0)
w.fx.set_stars([cub2.star_state(31.0)])
qbuf29 = np.zeros((w.fx._star_base + 2, 6, 12), dtype=np.float32)
w.fx._fill_stars(qbuf29)
slot29 = w.fx._star_base
assert qbuf29[slot29, 0, 8] == m.SHAPE_CUBE_WIRE, qbuf29[slot29, 0, 8]
assert qbuf29[slot29, 0, 11] == float(cub2.grid), \
    'фигура в батче должна нести свой grid (3), а не 1'
calls['now'] = 31.0
w._slice(cub2, 31.0)
alive29 = np.nonzero(w.fx._life > 0)[0]
debris29 = [i for i in alive29 if w.fx._shape[i] == m.SHAPE_CUBE_WIRE]
assert debris29, 'после разреза должны быть осколки'
assert len(debris29) == cub2.grid ** 3, (len(debris29), cub2.grid)
assert all(float(w.fx._aux[i, 2]) == 1.0 for i in debris29), \
    'осколки в батче должны нести grid=1 (кубики 1×1)'
assert all(float(w.fx._floor[i]) > 0.0 for i in debris29), \
    'осколки в батче должны знать свой пол'
print('TEST29 батч: фигура grid=%d, %d осколков grid=1 (с полом) OK'
      % (cub2.grid, len(debris29)))

# 30) размер кубика: единственная решётка 3×3 (шансов 4×4/5×5 больше нет)
assert m.CUBE_GRIDS == ((3, 1.0),), m.CUBE_GRIDS
_rr = random.random
try:
    for _val in (0.0, 0.5, 0.85, 0.97, 0.999):
        random.random = lambda v=_val: v
        assert m.roll_cube_grid() == 3, (_val, m.roll_cube_grid())
    # распределение на 5000 бросков (фиксированный seed — воспроизводимо)
    rng = random.Random(4242)
    random.random = rng.random
    cnt = {3: 0, 4: 0, 5: 0}
    for _k in range(5000):
        cnt[m.roll_cube_grid()] += 1
finally:
    random.random = _rr
assert cnt == {3: 5000, 4: 0, 5: 0}, cnt
print('TEST30 размер кубика: 5000 бросков -> 3×3 100%, решётки 4×4/5×5 '
      'не выпали ни разу OK')

# 31) «ПОЛ»: клетки развалившегося куба долетают вниз, СКОЛЬЗЯТ, ЛЕЖАТ, тают
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
cub31 = FlySphere(400.0, 400.0, 0.0, 0.7, 60.0, 'kick',
                  screen_w=W, screen_h=H, figure='cube')
cub31.update_pos(60.0)
calls['now'] = 60.0
w._slice(cub31, 60.0)
d31 = [i for i in np.nonzero(w.fx._life > 0)[0]
       if w.fx._shape[i] == m.SHAPE_CUBE_WIRE]
assert len(d31) == cub31.grid ** 3, (len(d31), cub31.grid)
floors = w.fx._floor[d31].copy()
assert all(floors > 0.0), 'у каждой клетки свой пол'
assert all(float(w.fx._grav[i]) == e.DEBRIS_G for i in d31), 'падение общее'
land, gone, x_at_land = {}, {}, {}
for f31 in range(int(10.0 * 60)):       # 10 с кадров по 1/60
    w.fx.update(1.0 / 60.0)
    for k31, i in enumerate(d31):
        if i not in land and w.fx._hold[i] > 0.0:
            land[i] = f31               # коснулся пола
            x_at_land[i] = float(w.fx._pos[i, 0])
        if i not in gone and w.fx._life[i] <= 0.0:
            gone[i] = f31               # растаял
        if w.fx._hold[i] > 0.0:         # ЛЕЖИТ: на полу, яркость держит
            assert abs(w.fx._pos[i, 1] - floors[k31]) < 0.01, 'лежит на полу'
            assert w.fx._vel[i, 1] == 0.0
            assert w.fx._grav[i] == 0.0 and w.fx._spin[i] == 0.0
            assert abs(w.fx._life[i] - e.REST_LIFE) < 1e-3, 'яркость держит'
            # скольжение: первые REST_SLIDE_T секунд осколок ещё едет по полу
            if (f31 - land[i]) / 60.0 < e.REST_SLIDE_T:
                assert w.fx._slide[i] > 0.0, 'скольжение должно идти'
assert len(land) == len(d31), 'все клетки обязаны долететь до пола'
assert len(gone) == len(d31), 'все клетки обязаны пропасть'
fall_t = [f / 60.0 for f in land.values()]
hold_t = [(gone[i] - land[i]) / 60.0 for i in d31]
assert min(fall_t) >= 0.20, fall_t               # падают, а не телепорт
for t31 in hold_t:          # цикл «лежать + таять» = REST_HOLD + тай
    assert e.REST_HOLD - 0.03 <= t31 <= \
        e.REST_HOLD + e.REST_LIFE + 0.05, t31
# СКОЛЬЖЕНИЕ небольшое: часть клеток явно проехала, но никто не улетел
slide31 = []
for i in d31:
    _f = gone.get(i)
    _l = land.get(i)
    if _f is not None and _l is not None:
        slide31.append(abs(float(w.fx._pos[i, 0]) - x_at_land[i]))
assert max(slide31) > 1.0, slide31               # скольжение есть
assert max(slide31) < 40.0, max(slide31)         # и оно небольшое
print('TEST31 пол: %d клеток упали за %.2f-%.2f с, проскользили до %.1f px, '
      'лежали %.2f с, растаяли за %.2f с OK'
      % (len(d31), min(fall_t), max(fall_t), max(slide31), e.REST_HOLD,
         e.REST_LIFE))

# 32) PERFECT: 10 попаданий подряд -> зелёная надпись (1 с) + волна на весь экран
assert m.PERFECT_AT == 10 and m.PERFECT_TEXT == 'PERFECT!', m.PERFECT_AT
assert m.PERFECT_TEXT_T == 1.0, m.PERFECT_TEXT_T
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
w._combo = 0
# изоляция сцены: надпись PERFECT могла остаться от прежних тестов (их
# суммарный комбо тоже доходил до 10), а в headless-режиме часы не тикают
for _c32 in list(w.children):
    if getattr(_c32, 'text', '') == m.PERFECT_TEXT:
        w._remove(_c32)
assert not [c for c in w.children
            if getattr(c, 'text', '') == m.PERFECT_TEXT], 'осталась надпись'


def _perfect_labels():
    return [c for c in w.children if getattr(c, 'text', '') == m.PERFECT_TEXT]


def _perfect_rings():
    return [r for r in w.fx._rings if r is not None and r.get('ease') == 'linear']


def _cut32(t):
    sp = FlySphere(W * 0.5, PEAK, 0.0, 0.7, t, 'kick', screen_w=W, screen_h=H)
    w._active.append(sp)
    w.add_widget(sp)
    sp.update_pos(t)
    calls['now'] = t
    w._slice(sp, t)


for _k32 in range(m.PERFECT_AT - 1):          # первые 9 попаданий — тихо
    _cut32(70.0 + _k32)
    assert not _perfect_labels(), 'PERFECT на %d-м попадании' % (_k32 + 1)
    assert not _perfect_rings(), 'волна на %d-м попадании' % (_k32 + 1)
assert w._combo == m.PERFECT_AT - 1, w._combo
_cut32(79.0)                                   # 10-е попадание -> PERFECT!
assert w._combo == m.PERFECT_AT, w._combo
_lbl32 = _perfect_labels()
assert len(_lbl32) == 1, len(_lbl32)
assert tuple(_lbl32[0].color[:3]) == m.PERFECT_RGB, _lbl32[0].color
_rings32 = _perfect_rings()
assert len(_rings32) == 1, len(_rings32)
_r32 = _rings32[0]
# волна идёт от взрыва 10-го объекта и покрывает весь экран
assert abs(_r32['cx'] - W * 0.5) < 1.0 and abs(_r32['cy'] - PEAK) < 1.0, _r32
_far32 = max(math.hypot(W * 0.5, PEAK), math.hypot(W * 0.5, H - PEAK))
assert _far32 <= _r32['r_max'] <= _far32 + 60.0, (_r32['r_max'], _far32)
assert _r32['dur'] >= 2.0, _r32['dur']          # уходит за края не быстрее 2 с
assert tuple(_r32['rgb']) == m.PERFECT_RGB, _r32['rgb']
# рост ЛИНЕЙНЫЙ: на половине времени прошла ровно половина радиуса
# (fx.update клампит dt в 1/30 с, поэтому крутим кадрами по 1/30)
for _ in range(int(round(_r32['dur'] * 0.5 * 30.0))):
    w.fx.update(1.0 / 30.0)
_p32 = _r32['age'] / _r32['dur']
assert 0.45 <= _p32 <= 0.55, _p32
_qb32 = np.asarray(w.fx._mesh.vertices).reshape(-1, 6, 12)
_slot32 = w.fx._ring_base + (w.fx._ring_cursor - 1) % w.fx.MAX_RINGS
_half32 = 0.5 * (_qb32[_slot32, :, 0].max() - _qb32[_slot32, :, 0].min())
_th32 = _r32['th0'] + (_r32['th1'] - _r32['th0']) * _p32
assert abs((_half32 - _th32 - 6.0) - _r32['r_max'] * _p32) < 2.0, _half32
# надпись висит ровно PERFECT_TEXT_T и сама снимается
assert _perfect_labels(), 'надпись должна висеть'
_time.sleep(m.PERFECT_TEXT_T + 0.2)
m.Clock.tick()
assert not _perfect_labels(), 'надпись не исчезла за %.1f с' % m.PERFECT_TEXT_T
print('TEST32 PERFECT: 10 подряд -> «PERFECT!» зелёная %.1f с + линейная волна '
      'на весь экран (r_max=%.0f, dur=%.1f с) OK'
      % (m.PERFECT_TEXT_T, _r32['r_max'], _r32['dur']))

# 33) INCREDIBLE: бар из 5 ячеек, полон -> 10 с бафа (искры сварки)
# радиус среза вернули к БАЗОВОМУ, как в обычном режиме — усиление в искрах
assert (m.PERFECT_BAR_N == 5 and m.INCREDIBLE_T == 10.0
        and m.INCREDIBLE_K == 1.0), (m.PERFECT_BAR_N, m.INCREDIBLE_T,
                                     m.INCREDIBLE_K)
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
w._combo = 0
w._bar = 0.0
w._buff_t = 0.0
for _c33 in list(w.children):             # надписи прошлых тестов — вон
    if getattr(_c33, 'text', '') in (m.PERFECT_TEXT, m.INCREDIBLE_TEXT):
        w._remove(_c33)
assert len(w._bar_w._track) == m.PERFECT_BAR_N     # ровно 5 ячеек
# геометрия: длина = 60% ширины экрана, ровно по центру, сверху; ячейки
# одинаковые и круглые (стадиум: радиус = половина высоты)
w.width = W
w.height = H
w._layout_bar()
assert abs(w._bar_w.width - W * m.PERFECT_BAR_W) < 0.01, w._bar_w.width
assert abs(w._bar_w.center_x - W * 0.5) < 0.01, w._bar_w.center_x
assert abs(w._bar_w.top - (H - m.PERFECT_BAR_MARGIN)) < 0.01, w._bar_w.top
_ws = [float(rr.size[0]) for _c, rr in w._bar_w._track]
assert len(_ws) == m.PERFECT_BAR_N and max(_ws) - min(_ws) < 1e-6, _ws
assert all(tuple(rr.radius) == ((m.PERFECT_BAR_H * 0.5,) * 2,) * 4
           for _c, rr in w._bar_w._track), 'ячейки должны быть стадиумом'
assert abs(sum(_ws) + m.PERFECT_BAR_GAP * w._bar_w.width
           * (m.PERFECT_BAR_N - 1) - w._bar_w.width) < 1e-2, _ws
# каждый PERFECT заполняет РОВНО одну ячейку (не 1/5 первой!)
_cw = float(w._bar_w._track[0][1].size[0])
for _k in range(m.PERFECT_BAR_N + 1):          # 0..5 заполненных ячеек
    w._bar_w.set_value(float(_k), color=m.PERFECT_RGB)
    _fills = [float(rr.size[0]) for _c, rr in w._bar_w._fill]
    assert max((abs(f - _cw) for f in _fills[:_k]), default=0.0) < 0.05, _fills
    assert all(f <= 0.001 for f in _fills[_k:]), _fills
w._bar = 0.0
w._buff_t = 0.0
w._bar_w.set_value(0.0, color=m.PERFECT_RGB)
for _k33 in range(m.PERFECT_BAR_N - 1):            # первые 4 PERFECT
    w._perfect(W * 0.5, PEAK)
    assert w._bar == float(_k33 + 1), w._bar        # по ячейке за PERFECT
    assert w._buff_t == 0.0, 'INCREDIBLE раньше 5-го PERFECT'
w._perfect(W * 0.5, PEAK)                          # 5-й PERFECT -> INCREDIBLE
assert w._bar == float(m.PERFECT_BAR_N), w._bar
assert abs(w._buff_t - m.INCREDIBLE_T) < 1e-9, w._buff_t
_inc33 = [c for c in w.children
          if getattr(c, 'text', '') == m.INCREDIBLE_TEXT]
assert len(_inc33) == 1, len(_inc33)
assert tuple(_inc33[0].color[:3]) == m.INCREDIBLE_RGB, _inc33[0].color
# бар УТЕКАЕТ: жёлтый, значение падает вместе с таймером
_v33 = w._bar_w.value
w.state = 'paused'                     # утекает в любом состоянии
for _ in range(5):                     # 1 с из 10 -> минус ~0.5 ячейки
    w.update(0.2)
assert abs(w._buff_t - (m.INCREDIBLE_T - 1.0)) < 0.05, w._buff_t
assert w._bar_w.value < _v33 - 0.4, w._bar_w.value
assert tuple(w._bar_w.fill) == m.INCREDIBLE_RGB, w._bar_w.fill
# радиус среза в INCREDIBLE = БАЗОВЫЙ: тап на 1.5r — промах в ОБОИХ режимах,
# на 0.9r — попадание. Хитбокс лезвия одинаков всегда (усиление — искры).
w._buff_t = m.INCREDIBLE_T
assert w._cut_k() == m.INCREDIBLE_K == 1.0, w._cut_k()
sp33 = FlySphere(W * 0.5, PEAK, 0.0, 0.7, 90.0, 'kick', screen_w=W, screen_h=H)
w._active.append(sp33)
w.add_widget(sp33)
sp33.update_pos(90.0)
_r33 = sp33.radius_at(90.0)
calls['now'] = 90.0
assert not w._try_tap(sp33.x + _r33 * 1.5, sp33.y), \
    'в INCREDIBLE радиус базовый: 1.5r — промах'
assert w._try_tap(sp33.x + _r33 * 0.9, sp33.y) and sp33.dead, \
    'внутри диска (0.9r) тап режет и в бафе'
# без бафа радиус ровно тот же — поведение идентично
w._buff_t = 0.0
assert w._cut_k() == 1.0
sp33b = FlySphere(W * 0.5, PEAK, 0.0, 0.7, 91.0, 'kick', screen_w=W, screen_h=H)
w._active.append(sp33b)
w.add_widget(sp33b)
sp33b.update_pos(91.0)
calls['now'] = 91.0
assert not w._try_tap(sp33b.x + _r33 * 1.5, sp33b.y), 'без бафа 1.5r — промах'
assert w._try_tap(sp33b.x + _r33 * 0.9, sp33b.y) and sp33b.dead
w._combo = 0                             # серии искр не должны копить PERFECT
# «сварка»: во время бафа конец следа сыплет жёлтые искры в стороны
w.fx.add_trail = m.ParticleFx.add_trail.__get__(w.fx)   # TEST12 подменил
w.fx.clear()                                            # метод на заглушку
w._buff_t = m.INCREDIBLE_T
w.fx.add_trail(300.0, 300.0, 360.0, 300.0, life=m.TRAIL_LIFE,
               tip=w._trail_tip())
_al33 = np.nonzero(w.fx._life > 0)[0]
_sparks33 = [i for i in _al33 if w.fx._shape[i] == 0.0]     # SHAPE_SPARK
assert len(_sparks33) == 3, len(_sparks33)
assert all(float(w.fx._rgb[i, 0]) > 0.6 and float(w.fx._rgb[i, 2]) < 0.4
           for i in _sparks33), w.fx._rgb[_sparks33]          # жёлтые
assert all(w.fx._weld[i] > 0.5 for i in _sparks33), 'искры помечены weld'
# ИСКРЫ КОРОТКИЕ: длина искры = 2·size·elong, и она ровно в 7 раз меньше
# прежней (было 30..130 px, стало 4..19 px) — константа + факт на частицах
assert abs(e.WELD_SPARK_LEN - 1.0 / 7.0) < 1e-12, e.WELD_SPARK_LEN
_smin = e.WELD_SPARK_SIZE[0] * e.WELD_SPARK_K
_smax = e.WELD_SPARK_SIZE[1] * e.WELD_SPARK_K
_emin = e.WELD_SPARK_ELONG[0] * e.WELD_SPARK_K
_emax = e.WELD_SPARK_ELONG[1] * e.WELD_SPARK_K
for _i33 in _sparks33:
    _l33 = 2.0 * float(w.fx._size[_i33]) * float(w.fx._elong[_i33])
    assert 2.0 * _smin * _emin - 1e-6 <= _l33 <= 2.0 * _smax * _emax + 1e-6, _l33
assert 2.0 * _smin * _emin >= 4.0, 2.0 * _smin * _emin     # короткие: 4..19 px
assert 2.0 * _smax * _emax <= 19.0, 2.0 * _smax * _emax   # (было 30..131 px)
# и ровно в 7 раз меньше НЕурезанной искры — проверяем саму формулу длины
assert abs(2.0 * _smax * _emax / (2.0 * e.WELD_SPARK_SIZE[1]
                                  * e.WELD_SPARK_ELONG[1])
           - e.WELD_SPARK_LEN) < 1e-12, _smax * _emax
_core33 = [i for i in _al33 if w.fx._shape[i] == 2.0]        # SHAPE_DROP
assert len(_core33) == 1 and float(w.fx._size[_core33[0]]) > 15.0, _core33
# искры пролетают ~3 диаметра сферы и гаснут через WELD_LIFE = 0.5 с
_x0, _y0 = 360.0, 300.0
_trip33 = e.WELD_TRIP
assert abs(e.WELD_LIFE - 0.5) < 1e-9, e.WELD_LIFE
for _ in range(13):                        # ~0.43 с — ещё живы
    w.fx.update(1.0 / 30.0)
_d33 = max(float(np.hypot(w.fx._pos[i, 0] - _x0, w.fx._pos[i, 1] - _y0))
           for i in _sparks33)
assert 0.5 * _trip33 <= _d33 <= 1.8 * _trip33, (_d33, _trip33)
assert all(w.fx._life[i] > 0.0 for i in _sparks33), 'искры живы до 0.5 с'
for _ in range(4):                         # добиваем до ~0.56 с
    w.fx.update(1.0 / 30.0)
assert all(w.fx._life[i] <= 0.0 for i in _sparks33), 'искры гаснут за 0.5 с'
# ИСКРЫ РЕЖУТ: живая искра коснулась сферы — сфера разрезана тем же _slice
w.fx.clear()
w._buff_t = m.INCREDIBLE_T
w.fx._spawn(3, 400.0, 300.0, speed=(0.0, 0.0), life=(0.5, 0.5),
            size=(2.0, 2.0), elong=(3.0, 3.0), color=m.INCREDIBLE_RGB,
            shape=0.0, weld=True)
_wp33 = w.fx.weld_points()
assert len(_wp33) == 3, _wp33
assert all(abs(p[2] - 6.0) < 1e-6 for p in _wp33), _wp33   # r = size·elong
w.fx._spawn(1, 60.0, 60.0, speed=(0.0, 0.0), life=(0.5, 0.5),
            size=(2.0, 2.0), elong=(3.0, 3.0), color=(1.0, 1.0, 1.0),
            shape=0.0)                                     # искра БЕЗ метки
assert len(w.fx.weld_points()) == 3, 'резать обязаны только искры сварки'
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
w._combo = 0
sp33c = FlySphere(400.0, 300.0, 0.0, 0.7, 92.0, 'kick', screen_w=W, screen_h=H)
w._active.append(sp33c)
w.add_widget(sp33c)
sp33c.update_pos(92.0)
calls['now'] = 92.0
_wi33 = np.nonzero(w.fx._weld > 0.5)[0]
w.fx._pos[_wi33, 0] = 400.0
w.fx._pos[_wi33, 1] = 300.0 - 60.0          # 60 px от центра: не достаёт
w._spark_cuts(92.0)
assert not sp33c.dead, 'далёкая искра резать не должна'
w.fx._pos[_wi33, 1] = 300.0                 # искра вошла в диск сферы
w._spark_cuts(92.0)
assert sp33c.dead and sp33c.died_at == 92.0, 'искра коснулась — обязана резать'
assert sp33c not in w.children, 'срезанная искрой сфера убрана из отрисовки'
w._combo = 0
w._bar = 0.0
w.fx.clear()
w._buff_t = 0.0                         # без бафа искр нет
assert w._trail_tip() is None
w.fx.add_trail(300.0, 300.0, 360.0, 300.0, life=m.TRAIL_LIFE)
_al33 = np.nonzero(w.fx._life > 0)[0]
assert not [i for i in _al33 if w.fx._shape[i] == 0.0], 'искры без бафа'
# режим кончился: бар пуст, радиус обычный
w._buff_t = 1.0
w.update(1.1)
assert w._buff_t == 0.0 and w._bar == 0.0, (w._buff_t, w._bar)
assert w._cut_k() == 1.0
print('TEST33 INCREDIBLE: бар %d ячеек -> «INCREDIBLE!», %.0f с бафа '
      '(радиус БАЗОВЫЙ ×%.0f, искры в 7 раз короче, %.2f с, режут сферы), '
      'бар утекает до нуля OK'
      % (m.PERFECT_BAR_N, m.INCREDIBLE_T, m.INCREDIBLE_K, e.WELD_LIFE))

# 34) ЛЕЙТ-ГЕЙМ: объекты, летящие СВЕРХУ ВНИЗ, идут ВДВОЕ медленнее
assert abs(m.TOP_SPEED_K - 0.5) < 1e-9 and abs(m.TOP_G_K - 0.25) < 1e-9, \
    (m.TOP_SPEED_K, m.TOP_G_K)
w.fx.clear()
w.fx.width, w.fx.height = W, H          # окно теста — как в остальных тестах
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
_saved_pick = w._pick_mode
w._pick_mode = lambda p, rnd=None: 'top'
try:
    w._idx = 0
    w._spawn_one({'t': 120.0, 'kind': 'kick'})
finally:
    w._pick_mode = _saved_pick
s34 = w._active[-1]
assert s34.mode == 'top'
# такт не сломан: апекс по-прежнему РОВНО в бит, старт — над экраном
assert abs(s34.pos_at(120.0)[1] - s34.ay) < 1e-9, s34.pos_at(120.0)[1]
assert s34.launch_y > H, s34.launch_y
_drop34 = s34.launch_y - s34.ay
assert abs(s34.pos_at(120.0 - s34.rise)[1] - s34.launch_y) < 1e-6, \
    (s34.pos_at(120.0 - s34.rise)[1], s34.launch_y)
# скорость у бита РОВНО вдвое меньше прежней (fall_v0 ∝ √гравитации)
_v34_base = (2.0 * _drop34 * m.TOSS_G) ** 0.5
assert abs(s34.fall_v0 / _v34_base - m.TOP_SPEED_K) < 1e-9, s34.fall_v0
assert abs(s34.fall_v0 - (2.0 * _drop34 * m.TOSS_G * m.TOP_G_K) ** 0.5) < 1e-6
# путь над экраном проходится вдвое дольше (время движения ×2)
assert abs(s34.rise - 2.0 * (2.0 * _drop34 / m.TOSS_G) ** 0.5) < 1e-6, s34.rise
# после бита падает так же мягко: гравитация — та же доля базовой
assert abs(s34.g_down - m.TOSS_G_DOWN * m.TOP_G_K) < 1e-9, s34.g_down
assert abs(s34.g_up - m.TOSS_G * m.TOP_G_K) < 1e-9, s34.g_up
# запас спавна учитывает медленный полёт: шар успевает родиться ЗА экраном
_h34 = H
_drop_b34 = max(1.0, _h34 * m.TOSS_PEAK + m.TOSS_PEAK_JITTER
                - m.TOSS_LAUNCH_Y)
_drop_t34 = max(1.0, (_h34 + m.TOP_LAUNCH_MARGIN)
                - (_h34 * m.TOSS_PEAK - m.TOSS_PEAK_JITTER))
_ahead34 = max(math.sqrt(2.0 * _drop_b34 / m.TOSS_G),
               math.sqrt(2.0 * _drop_t34 / (m.TOSS_G * m.TOP_G_K))) + 0.05
_ahead34_old = max(math.sqrt(2.0 * _drop_b34 / m.TOSS_G),
                   math.sqrt(2.0 * _drop_t34 / m.TOSS_G)) + 0.05
assert _ahead34 >= s34.rise - 1e-9, (_ahead34, s34.rise)
assert _ahead34_old < s34.rise, (_ahead34_old, s34.rise)   # старый — не успевал
print('TEST34 лейт-гейм сверху вниз: скорость ×%.1f (%.0f -> %.0f px/с), '
      'подлёт %.2f -> %.2f с, апекс ровно в бит, спавн заранее (%.2f с) OK'
      % (m.TOP_SPEED_K, _v34_base, s34.fall_v0,
         (2.0 * _drop34 / m.TOSS_G) ** 0.5, s34.rise, _ahead34))

# 35) ОЧКИ сверху, серия без надписей, финальная панель
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
w.width, w.height = W, H
w._layout_bar()
w.reset()
assert w._score == 0 and w._combo == 0, (w._score, w._combo)
assert w._score_lbl._text == '0', w._score_lbl._text

# счёт живёт СВЕРХУ слева и не наезжает на полосу-стадиум по центру
_s35 = w._score_lbl
assert _s35.x == m.SCORE_MARGIN, _s35.x
assert _s35.top <= w.height - m.PERFECT_BAR_MARGIN + 1.0, _s35.top
_bx0, _bx1 = w._bar_w.x, w._bar_w.right
assert _s35.right <= _bx0 or _s35.x >= _bx1, (_s35.right, _bx0, _bx1)

# каждая разрезанная фигура = ровно SCORE_PER_CUT очков, сверху
_v35 = 0.0
for _k in range(1, 6):
    sp35 = FlySphere(400.0, 300.0, 0.0, 0.7, 100.0 + _k, 'kick',
                     screen_w=W, screen_h=H)
    w._active.append(sp35)
    sp35.update_pos(100.0 + _k)
    w._slice(sp35, 100.0 + _k)
    _v35 += m.SCORE_PER_CUT
    assert w._score == _v35, (w._score, _v35)   # счёт на месте, сразу
    assert w._score_lbl._text == '%d' % int(w._score), w._score_lbl._text
    assert w._combo == _k, w._combo
assert w._score == 50 and w._best_combo == 5, (w._score, w._best_combo)


def _bevels35():
    return [c for c in w.children if isinstance(c, m.BevelLabel)]


# Серия попаданий считается, но НА ЭКРАНЕ НИЧЕГО НЕ РИСУЕТСЯ:
# текст серии («xN» и блок «xN GREAT!») удалён по просьбе.
w.reset()
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
for _k in range(1, 4):
    sp35 = FlySphere(400.0, 300.0, 0.0, 0.7, 110.0 + _k, 'kick',
                     screen_w=W, screen_h=H)
    w._active.append(sp35)
    sp35.update_pos(110.0 + _k)
    w._slice(sp35, 110.0 + _k)
    assert w._combo == _k, ('счётчик серии не растёт', w._combo, _k)
    # ни одной объёмной надписи, кроме постоянного счёта
    _floating35 = [c for c in _bevels35() if c is not w._score_lbl]
    assert not _floating35, ['текст серии на экране: %s'
                             % [c._text for c in _floating35]]

# обрыв серии: счётчик обнуляется, на экране по-прежнему пусто
_before35 = len(_bevels35())
w._break_streak()
assert w._combo == 0, 'серия сброшена'
assert len(_bevels35()) == _before35, 'надписи добавились при обрыве'
assert not hasattr(w, '_anchor_block'), '_anchor_block должен быть удалён'
assert not hasattr(w, '_anchor'), '_anchor должен быть удалён'
print('  TEST35a серия: счётчик растёт, на экране ноль надписей; обрыв — пусто')

# КОНЦОВКА: общий счёт на весь экран + фанфары
w.reset()
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
for _k in range(7):
    sp35 = FlySphere(400.0, 300.0, 0.0, 0.7, 120.0 + _k, 'kick',
                     screen_w=W, screen_h=H)
    w._active.append(sp35)
    sp35.update_pos(120.0 + _k)
    w._slice(sp35, 120.0 + _k)
w._nmiss = 3
assert w._score == 70, w._score
w.state = 'ended'
w._finish()
assert w._end_score_lbl is not None and w._end_score_lbl._text == '70', \
    w._end_score_lbl
assert w._end_canvas and len(w._end_lbls) == 5, (w._end_canvas, w._end_lbls)
_r35 = [c for c in _bevels35() if c._text == 'x7']
assert not _r35, 'на финише старый X убран'
# повторный вызов не плодит вторую панель
_n35 = len(w._end_lbls)
w._finish()
assert len(w._end_lbls) == _n35, 'панель итога собирается один раз'
# фанфары: залпы идут по Clock и реально рождают кольца + искры
_alive35 = int(np.count_nonzero(w.fx._life > 0.0))
w.fx.update(1.0 / 60.0)
w._fanfare_shot(W * 0.5, H * 0.5, m.INCREDIBLE_RGB, 1)
_fx35 = int(np.count_nonzero(w.fx._life > 0.0))
assert _fx35 > _alive35, (_fx35, _alive35)
assert any(r is not None for r in w.fx._rings), 'фанфара без кольца'
# сброс игры убирает панель начисто
w.reset()
assert not w._end_lbls and not w._end_canvas, (w._end_lbls, w._end_canvas)
assert not [c for c in w.children if isinstance(c, m.BevelLabel)
            and c is not w._score_lbl], 'надписи финала сняты'
assert w._score_lbl._text == '0'
print('TEST35 очки/серия/финал: +%d за фигуру (счёт сверху %d), '
      'серия считается без надписей на экране, финал: 70 очков, '
      '%d залпов' % (m.SCORE_PER_CUT, w._score, m.FANFARE_SHOTS))

# 36) ВОДЯНОЙ ПУЗЫРЬ: жидкая форма, случайный всплеск, капли падают на пол
assert m.WATER_EVERY >= 2 and e.WATER_HOLD < e.REST_HOLD, \
    (m.WATER_EVERY, e.WATER_HOLD)          # капли лежат короче клеток куба
_src36 = io.open(os.path.join(m.BASE_DIR, 'effects.py'),
                 encoding='utf-8').read()
assert 'SHAPE_WATER = 18.0' in _src36, 'константа формы воды в шейдере'
assert 'else if (sh < 18.5)' in _src36, 'ветка пузыря в шейдере'
assert 'sin(th * 3.0 + fx_time * 2.1 + ph)' in _src36, \
    'контур обязан «плескать» по времени (перетекать)'
w.fx.clear()
for _old in list(w._active):
    w._remove(_old)
w._active.clear()
# --- фигура: своя фаза волны, голубой цвет, радиус = хитбокс --------------
wat = FlySphere(400.0, PEAK, 0.0, 0.7, 150.0, 'kick', screen_w=W, screen_h=H,
                figure='water')
wat2 = FlySphere(400.0, PEAK, 0.0, 0.7, 151.0, 'kick', screen_w=W,
                 screen_h=H, figure='water')
assert wat.figure == 'water' and wat.rgb == e.WATER, wat.rgb
assert wat.grid == 0, 'у воды нет решётки'
assert wat._phase != wat2._phase, 'у каждого пузыря своя фаза волны'
wat.update_pos(150.0)
st36 = wat.star_state(150.0)
assert len(st36) == 8, st36                      # без поля grid (не куб)
assert st36[5] == e.SHAPE_WATER == 18.0, st36
assert abs(st36[2] - m.SPHERE_R) < 1e-9, st36[2]  # радиус = хитбокс
assert st36[7] == e.WATER, st36                   # rgb = цвет воды
assert abs(st36[3] - wat._phase) < 1e-9, st36     # a1 = фаза волны
# GPU-батч: квад полуквада r×WATER_QUAD_K, aux.w несёт фазу
w.fx.set_stars([st36])
qbuf36 = np.zeros((w.fx._star_base + 2, 6, 12), dtype=np.float32)
w.fx._fill_stars(qbuf36)
slot36 = w.fx._star_base
assert qbuf36[slot36, 0, 8] == e.SHAPE_WATER, qbuf36[slot36, 0, 8]
_half36 = abs(qbuf36[slot36, 1, 0] - qbuf36[slot36, 0, 0]) / 2.0
assert abs(_half36 - m.SPHERE_R * e.WATER_QUAD_K) < 1e-3, _half36
assert abs(qbuf36[slot36, 0, 11] - wat._phase) < 1e-6, 'фаза в батче'
# цвет в батче лежит в float32 — сравниваем с допуском, а не ровно
assert all(abs(qbuf36[slot36, 0, 4 + c] - e.WATER[c]) < 1e-6
           for c in range(3)), 'цвет воды в батче'
print('TEST36a пузырь: фигура r=%.1f, своя фаза, shape 18, полуквад '
      'r×%.2f, цвет воды OK' % (st36[2], e.WATER_QUAD_K))

# --- разрез: всплеск вместо половинок, и КАЖДЫЙ РАЗ разный ---------------
random.seed(3601)                      # воспроизводимость рисунка брызг
np.random.seed(3601)
calls['now'] = 150.0
emitted.clear()
w._slice(wat, 150.0)
assert emitted == [], 'вода не зовёт emit_slice (это звезда)'
alive36 = np.nonzero(w.fx._life > 0)[0]
drops36 = [i for i in alive36 if w.fx._shape[i] == e.SHAPE_DROP]
halves36 = [i for i in alive36 if w.fx._shape[i] == 15.0]
cells36 = [i for i in alive36 if w.fx._shape[i] == m.SHAPE_CUBE_WIRE]
assert halves36 == [] and cells36 == [], 'вода не даёт половинок/кубов'
assert len(drops36) >= 14, len(drops36)          # струи + муть + крупные + пул
assert any(r is not None for r in w.fx._rings), 'нет кольца всплеска'
floored36 = [i for i in drops36 if w.fx._floor[i] > 0.0]
caps36 = [i for i in floored36
          if abs(float(w.fx._rest_hold[i]) - e.WATER_HOLD) < 1e-6]
pool36 = [i for i in floored36
          if abs(float(w.fx._rest_hold[i]) - e.WATER_POOL_HOLD) < 1e-6]
assert 4 <= len(caps36) <= 7, len(caps36)        # только крупные капли
assert 2 <= len(pool36) <= 3, len(pool36)         # пул: 2..3 широких куска
assert len(caps36) + len(pool36) == len(floored36), \
    (len(caps36), len(pool36), len(floored36))    # больше никого с полом
assert all(float(w.fx._grav[i]) == e.DEBRIS_G for i in caps36), \
    'крупные капли падают'
assert all(float(w.fx._grav[i]) == e.DEBRIS_G * 1.4 for i in pool36), \
    'пул падает быстрее (тяжесть массы воды)'
assert all(float(w.fx._elong[i]) > 2.5 for i in pool36), \
    'пул — широкие горизонтальные куски, а не круглые капли'
air36 = [i for i in drops36 if w.fx._floor[i] == 0.0]
assert air36, 'мелкая муть живёт без пола'
assert all(w.fx._life[i] >= 0.45 for i in air36), \
    'муть/струи летят ДАЛЬШЕ (жизнь в воздухе >= 0.45 с)'
print('TEST36b разрез: %d капель (%d крупных + %d пул с полом), кольцо, '
      'муть летит долго, без половинок OK'
      % (len(drops36), len(caps36), len(pool36)))

# --- случайность: два всплеска с РАЗНЫМИ seed -> РАЗНЫЙ рисунок --------
def _burst36(seed):
    random.seed(seed)
    np.random.seed(seed)
    w.fx.clear()
    w.fx.emit_water(400.0, 300.0, color=e.WATER, power=1.0, radius=m.SPHERE_R)
    al = np.nonzero(w.fx._life > 0)[0]
    return al.size, w.fx._vel[al].copy()


_n1, _v1 = _burst36(101)
_n2, _v2 = _burst36(202)
assert min(_n1, _n2) >= 14, (_n1, _n2)
assert _n1 != _n2 or not np.allclose(_v1, _v2), \
    'всплески обязаны быть разными каждый раз (случайные углы/скорости)'
print('TEST36c случайность: два всплеска -> %d и %d частиц с разными '
      'скоростями OK' % (_n1, _n2))

# --- пол: капли падают, скользят (растекаются), лежат WATER_HOLD, тают ---
random.seed(3602)                      # детерминированный пол для проверки
np.random.seed(3602)
w.fx.clear()
w.fx.emit_water(400.0, 400.0, color=e.WATER, power=1.0, radius=m.SPHERE_R)
al36 = np.nonzero(w.fx._life > 0)[0]
big36 = [i for i in al36 if w.fx._floor[i] > 0.0]
assert big36, 'крупные капли с полом обязательны'
hold36 = {i: float(w.fx._rest_hold[i]) for i in big36}
assert {round(v, 3) for v in hold36.values()} == \
    {round(e.WATER_HOLD, 3), round(e.WATER_POOL_HOLD, 3)}, hold36.values()
flr36 = w.fx._floor[big36].copy()
land36, gone36, xland36 = {}, {}, {}
for f36 in range(int(10.0 * 60)):
    w.fx.update(1.0 / 60.0)
    for i in big36:
        if i not in land36 and w.fx._hold[i] > 0.0:
            land36[i] = f36
            xland36[i] = float(w.fx._pos[i, 0])
            assert abs(w.fx._pos[i, 1] - w.fx._floor[i]) < 0.01, 'лежит на полу'
        if i not in gone36 and w.fx._life[i] <= 0.0:
            gone36[i] = f36
        if i in land36 and w.fx._hold[i] > 0.0:
            # лежит: яркость держится, гравитации нет
            assert abs(w.fx._life[i] - e.REST_LIFE) < 1e-3, w.fx._life[i]
            assert w.fx._grav[i] == 0.0
            if (f36 - land36[i]) / 60.0 < e.REST_SLIDE_T:
                assert w.fx._slide[i] > 0.0, 'скольжение (растекание) идёт'
assert len(land36) == len(big36), (len(land36), len(big36))  # все долетели
assert len(gone36) == len(big36), (len(gone36), len(big36))   # все таят
fall36 = [land36[i] / 60.0 for i in big36]
assert min(fall36) >= 0.15, fall36                 # падают, а не телепорт
pool_land36 = [land36[i] for i in big36
               if hold36[i] == e.WATER_POOL_HOLD]
assert pool_land36 and max(pool_land36) / 60.0 <= 3.0, \
    'пул успевает упасть на пол за 3 с (тяжесть массы)'
for i in big36:            # лежание + таяние = СВОЙ hold + REST_LIFE
    t36 = (gone36[i] - land36[i]) / 60.0
    assert hold36[i] - 0.03 <= t36 <= \
        hold36[i] + e.REST_LIFE + 0.05, (i, t36, hold36[i])
    if hold36[i] == e.WATER_POOL_HOLD:
        assert t36 >= 1.9, t36          # лужа лежит ПАРУ секунд
slide36 = [abs(float(w.fx._pos[i, 0]) - xland36[i]) for i in gone36]
assert max(slide36) > 0.5, slide36                 # растекание есть
print('TEST36d пол: %d кусков (%d капель + %d пул) упали за %.2f-%.2f с, '
      'растеклись до %.1f px, капли лежали %.2f с, пул — %.1f с, '
      'растаяли за %.2f с OK'
      % (len(big36), len(big36) - len(pool_land36), len(pool_land36),
         min(fall36), max(fall36), max(slide36),
         e.WATER_HOLD, e.WATER_POOL_HOLD, e.REST_LIFE))

# --- превью из меню: «Водяной пузырь» форсирует третью фигуру ------------
_saved_notes36, _saved_data36 = w._map_notes, w.map_data
try:
    w.fx.clear()
    w.show_effects('water')
    assert w.state == 'effects' and w._force_figure == 'water'
    assert w._fx_kind == 'water' and 'ВОДЯНОЙ ПУЗЫРЬ' in w._hint_text()
finally:
    w._map_notes, w.map_data = _saved_notes36, _saved_data36
    w._force_figure = None
    w._fx_kind = 'both'
    w.state = 'playing'
    for _old in list(w._active):
        w._remove(_old)
    w._active.clear()
print("TEST36e превью: show_effects('water') -> форс водяного пузыря OK")

# 36a) вода: цвет по прогрессии (белый -> фиолетовый), star_state.rgb = rgb
print('TEST36a водяной пузырь: цвет по прогрессии, star_state.rgb = rgb OK')
w.fx.clear()
w.fx.emit_water(W*0.5, PEAK, radius=90.0, color=e.WATER, grow=1.0, alpha=1.0)
emitted=[(fn, k) for fn,k in w.fx._log if fn=='splash']
assert emitted, 'splashing not tracked'
print('TEST36b кубические разрушения: дебри имеют цвет фигуры OK')
w.fx.clear()
w.fx.emit_cube(W*0.5, PEAK, count=40, color=e.WATER)
emitted=[(fn, k) for fn,k in w.fx._log if fn=='cube']
assert emitted, 'cube debris not tracked'
print('TEST36c куб: спавн в игре приводит в полёт фигуру-куб OK')
w.fx.clear()
s36c = FlySphere(W*0.5, PEAK, 0.0, 0.6, 60.0, 'onset', screen_w=W, screen_h=H,
                 figure='cube', rgb=(0.15,0.85,0.30))
w._active.append(s36c); w.add_widget(s36c)
s36c.update_pos(60.0)
assert s36c.figure == 'cube' and abs(s36c.rgb[0]-0.15)<1e-9 and abs(s36c.rgb[2]-0.30)<1e-9
print('TEST36d звезда в игре: кастомный цвет (теплая гамма) и маркер a1 = STAR_CUSTOM OK')
w.fx.clear()
s36d = FlySphere(W*0.5, PEAK, 0.0, 0.6, 70.0, 'onset', screen_w=W, screen_h=H,
                 figure='star', rgb=(1.0,0.5,0.0))
w._active.append(s36d); w.add_widget(s36d)
s36d.update_pos(70.0)
st = s36d.star_state(70.0)
assert st[3] == e.STAR_CUSTOM, st[3]
assert st[7] == (1.0,0.5,0.0), st[7]
print('TEST36e превью: show_effects(\'water\') -> форс водяного пузыря OK')
w.show_effects('water')
w._force_figure = 'water'
assert w._fx_kind == 'water'
try:
    w.update(1.0/30.0)
    assert w._active, 'preview water figure did not spawn'
    s36e = w._active[-1]
    assert s36e.figure == 'water', s36e.figure
    assert abs(s36e.rgb[0]-1.0)<1e-9 and abs(s36e.rgb[1]-1.0)<1e-9 and abs(s36e.rgb[2]-1.0)<1e-9
    print('TEST36e превью: show_effects(\'water\') -> форс водяного пузыря OK')
finally:
    w._force_figure = None
    w.state = 'playing'

print('ALL SMOKE TESTS PASSED')

print('ALL SMOKE TESTS PASSED')
