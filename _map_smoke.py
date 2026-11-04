# -*- coding: utf-8 -*-
"""Смоук режима карты (без ручного геймплея): спавн из JSON, тап onset,
свайп kick, чёрная дыра, пауза, точность."""
import json
import math
import os

os.environ.setdefault('KIVY_NO_ARGS', '1')

from kivy.app import App
from kivy.clock import Clock

import main as m

MAP = json.load(open(os.path.join('beatmaps', 'backinblack.json'),
                     encoding='utf-8'))


class FakeSound:
    """Аудио-часы, движимые симуляцией: get_pos() возвращает _t."""

    def __init__(self):
        self._t = 0.0

    def get_pos(self):
        return self._t

    def play(self):
        pass

    def stop(self):
        pass

    def seek(self, pos):
        self._t = float(pos)


REPORT = {'errors': None}
OK = {'spawns': 0, 'tap_hits': 0, 'swipe_hits': 0, 'misses': 0,
      'holes': 0, 'slashes': 0}


def instrument(gw):
    orig_sp = gw._spawn_one
    orig_ft = gw._float_text
    orig_hole = gw._on_black_hole
    orig_slash = gw._on_slash
    orig_fin = gw._finish

    def sp(n, _o=orig_sp):
        OK['spawns'] += 1
        _o(n)

    def ft(x, y, text, color, _o=orig_ft):
        if text == 'Miss':
            OK['misses'] += 1
        _o(x, y, text, color)

    def hole(x, y, _o=orig_hole):
        OK['holes'] += 1
        _o(x, y)

    def slash(a, b, c, d, _o=orig_slash):
        OK['slashes'] += 1
        _o(a, b, c, d)

    def fin(note, grade, _o=orig_fin):
        if note.gesture == 'tap':
            OK['tap_hits'] += 1
        elif note.gesture == 'swipe':
            OK['swipe_hits'] += 1
        _o(note, grade)

    gw._spawn_one = sp
    gw._float_text = ft
    gw._on_black_hole = hole
    gw._on_slash = slash
    gw._finish = fin


class SmokeApp(App):
    def build(self):
        self.sound = FakeSound()
        self.gw = m.GameWidget(map_name='backinblack')
        self.gw._sound = self.sound     # инъекция фейка
        instrument(self.gw)
        self.elapsed = 0.0
        self.tap_done = self.swipe_done = self.hole_done = False
        self.paused_done = False
        Clock.schedule_once(self.start_track, 0.3)
        Clock.schedule_interval(self.tick, 0)
        Clock.schedule_once(lambda *_: self.stop(), 12.0)
        return self.gw

    def start_track(self, _dt):
        self.gw.start_track()
        assert self.gw.state == 'playing'

    def tick(self, dt):
        try:
            gw = self.gw
            self.elapsed += dt
            # аудио-часы идут в реальном времени симуляции
            self.sound._t += dt
            gw.update(dt)

            now = self.sound._t

            # 1) тап по onset-ноте (ищем в активных)
            if not self.tap_done:
                for n in list(gw._active):
                    if n.gesture == 'tap' and abs(now - n.hit_time) < 0.06:
                        gw._try_tap(n.x + 1.0, n.y + 1.0)
                        self.tap_done = True
                        break

            # 2) свайп по kick-ноте: отрезок через центр
            if not self.swipe_done:
                for n in list(gw._active):
                    if (n.gesture == 'swipe'
                            and abs(now - n.hit_time) < 0.06):
                        gw._try_swipe_segment(
                            n.x - 200.0, n.y, n.x + 200.0, n.y)
                        self.swipe_done = True
                        break

            # 3) чёрная дыра по прямому вызову жеста
            if not self.hole_done and self.elapsed > 10.0:
                gw._on_black_hole(400.0, 300.0)
                self.hole_done = True

            # 4) пауза/продолжение
            if not self.paused_done and self.elapsed > 14.0:
                gw.toggle_pause()
                assert gw.state == 'paused', 'pause failed'
                gw.start_track()
                assert gw.state == 'playing', 'resume failed'
                self.paused_done = True
        except Exception as e:
            import traceback
            REPORT['errors'] = traceback.format_exc()
            traceback.print_exc()
            self.stop()
            return False

    def on_stop(self):
        pass


app = SmokeApp()
app.run()
gw = app.gw
lines = [
    'spawns=%d tap_hits=%d swipe_hits=%d misses=%d' % (
        OK['spawns'], OK['tap_hits'], OK['swipe_hits'], OK['misses']),
    'holes=%d slashes=%d' % (OK['holes'], OK['slashes']),
    'score=%d acc=%s' % (gw._score, gw._acc_text()),
    'map notes=%d idx=%d' % (len(MAP['notes']), gw._idx),
]
ok = (REPORT['errors'] is None and OK['spawns'] > 10
      and OK['tap_hits'] >= 1 and OK['swipe_hits'] >= 1
      and OK['holes'] == 1 and app.paused_done)
lines.append('RESULT: ' + ('PASS' if ok else 'FAIL'))
if REPORT['errors']:
    lines.append(REPORT['errors'])
with open('_map_smoke.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('\n'.join(lines))
