# -*- coding: utf-8 -*-
"""ИНТЕГРАЦИЯ: реальный путь меню -> выбор трека -> игра.

Это тот самый сценарий, где падало: игрок выбирает трек в меню SoundGrip.
Путь: SoundGripApp.start_game(track) -> _enter_game() -> load_track() ->
start_track() -> update() x N.

Ключевое: update() берёт span из РЕАЛЬНОГО времени между кадрами
(time.perf_counter), поэтому тест подменяет клок шагом ~16 мс -- иначе
steps==1, цикл подшагов делает одну итерацию и затенение переменной n
(счётчик подшагов <- словарь ноты) не проявляется.

Запуск: python _test_menu_track_flow.py
"""
import os
import sys
import traceback

os.environ.setdefault('KIVY_NO_ARGS', '1')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import main as m            # noqa: E402
import tracks as tracklib   # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print('%-56s %s %s' % (name, 'OK  ' if cond else 'FAIL', detail))
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


def main():
    real_perf = m.time.perf_counter
    clock = FakeClock()
    m.time.perf_counter = clock

    app = None
    try:
        app = m.RhythmSlasherApp()
        app.build()                       # тот же путь, что и при запуске игры

        found = tracklib.list_tracks()
        check('треки найдены', len(found) > 0, '%d шт.' % len(found))
        if not found:
            return 1

        # --- главный сценарий: выбор ПЕРВОГО трека из меню ------------------
        track = found[0]
        check('у трека есть карта', track.has_map, track.map_name)

        crashed = None
        try:
            app.start_game(track)          # <- выбор трека в меню
            gw = app.game
            check('игра в состоянии playing', gw.state == 'playing', gw.state)
            check('меню скрыто', app.menu.parent is not app.root_w)

            for _ in range(600):           # 10 секунд игры
                gw.update(1.0 / 60.0)
        except TypeError as e:
            crashed = e

        check('меню->игра: 600 кадров без TypeError', crashed is None,
              repr(crashed) if crashed else '')
        check('ноты заспавнены', gw.note_pointer > 0, 'ptr=%d' % gw.note_pointer)

        # --- выход в меню и выбор ДРУГОГО трека (второй load_track) ---------
        crashed2 = None
        try:
            app.show_menu()
            app.start_game(found[-1])
            gw = app.game
            for _ in range(600):
                gw.update(1.0 / 60.0)
        except TypeError as e:
            crashed2 = e
        check('повторный выбор трека: без TypeError', crashed2 is None,
              repr(crashed2) if crashed2 else '')

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
