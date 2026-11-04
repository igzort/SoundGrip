# -*- coding: utf-8 -*-
"""РЕГРЕСС: краш в update() из-за затенения переменной n.

Исходный баг (Kivy-лог kivy_26-10-21_10/11.txt):
    File "main.py", line 2200, in update
        k0 = (i - 1) / float(n)
    TypeError: float() argument must be a string or a real number, not 'dict'

Причина: `n` -- счётчик подшагов рез-логики (200 Гц), но внутри цикла его
перезаписывали на словарь ноты (`n = {'t':..., 'kind':...}`). На следующей
итерации цикла float(n) получал dict -> TypeError -> падение ВСЕЙ игры.

ВАЖНО: span берётся из РЕАЛЬНОГО времени между кадрами (time.perf_counter),
поэтому в тесте нужен фейковый клок с шагом ~16 мс -- иначе steps==1, цикл
делает одну итерацию и затенение не проявляется.

Запуск: python _test_substep_shadow.py
"""
import os
import sys
import traceback

os.environ.setdefault('KIVY_NO_ARGS', '1')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import main as m  # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print('%-58s %s %s' % (name, 'OK  ' if cond else 'FAIL', detail))
    if not cond:
        FAILS.append(name)


class FakeClock:
    """Подмена time.perf_counter: каждый update() сдвигает время на 16 мс."""

    def __init__(self, step=0.016):
        self.t = 1000.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def build_widget():
    gw = m.GameWidget(track=m._track_for_map('backinblack'), volume=0.0)
    return gw


def main():
    real_perf = m.time.perf_counter
    clock = FakeClock()
    m.time.perf_counter = clock

    try:
        # --- 1) ловим TypeError: float(dict) --------------------------------
        gw = build_widget()
        gw.start_track()
        check('старт игры: state=playing', gw.state == 'playing', gw.state)

        crashed = None
        try:
            for _ in range(600):
                gw.update(1.0 / 60.0)
        except TypeError as e:
            crashed = e
        check('update() x600 без TypeError', crashed is None,
              repr(crashed) if crashed else '')

        # --- 2) игра реально живёт: ноты спавнятся ----------------------------
        check('ноты заспавнены (note_pointer > 0)', gw.note_pointer > 0,
              'note_pointer=%d' % gw.note_pointer)

        # --- 2b) сферы реально появились (затенение не сломало _spawn_one) ---
        check('сферы созданы (len(_active) > 0)', len(gw._active) > 0,
              'active=%d' % len(gw._active))

        # --- 3) затенения переменной n больше нет ----------------------------
        src = open('main.py', encoding='utf-8').read().splitlines()
        body = '\n'.join(src[2190:2225])           # блок подшагов в update()
        check('в блоке подшагов нет `n = {...}`',
              "n = {'t'" not in body and 'n = {"t"' not in body)

        # --- 4) разные длительности кадра (1/30, 1/144, плавающий) -----------
        bad = []
        for step in (0.033, 0.0069, 0.021, 0.05):
            clock2 = FakeClock(step=step)
            m.time.perf_counter = clock2
            g2 = m.GameWidget(track=m._track_for_map('backinblack'), volume=0.0)
            g2.start_track()
            try:
                for _ in range(300):
                    g2.update(step)
            except TypeError as e:
                bad.append((step, e))
        check('update() устойчив при разном dt', not bad, repr(bad[:1]))

    except Exception:
        traceback.print_exc()
        FAILS.append('НЕОЖИДАННОЕ ИСКЛЮЧЕНИЕ')
    finally:
        m.time.perf_counter = real_perf

    print()
    if FAILS:
        print('RESULT: FAIL -> %s' % ', '.join(FAILS))
        return 1
    print('RESULT: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
