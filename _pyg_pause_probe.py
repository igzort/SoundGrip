# -*- coding: utf-8 -*-
"""Реальная проба pygame-mixer на _bb.wav В конфигурации игры
(Kivy-окно уже создано). Доказательство: позиция трека замораживается
на паузе и продолжается после unpause для WAV."""
import time

OUT = r'C:\python projects\SoundGrip\_pyg_probe.txt'
lines = []


def log(m):
    lines.append(str(m))


WAV = r'C:\python projects\SoundGrip\_bb.wav'

try:
    from kivy.core.window import Window          # окно создаётся, как в игре
    log('kivy window: created %dx%d' % tuple(Window.size))
except Exception as e:
    log('kivy window FAILED: %r' % (e,))

from pygame import mixer

if not mixer.get_init():
    mixer.init()
log('mixer init: %s (freq=%d)' % (mixer.get_init()[:2], mixer.get_init()[0]))

mixer.music.load(WAV)
mixer.music.set_volume(0.0)                      # беззвучно для пробы
mixer.music.play()
time.sleep(0.8)
p1 = mixer.music.get_pos() / 1000.0
log('A) play 0.8 c            -> get_pos = %.2f c' % p1)

mixer.music.pause()
busy_paused = mixer.music.get_busy()
time.sleep(1.2)
p2 = mixer.music.get_pos() / 1000.0
log('B) pause (спим 1.2 c)    -> get_pos = %.2f c | get_busy = %s' % (p2, busy_paused))

mixer.music.unpause()
time.sleep(0.5)
p3 = mixer.music.get_pos() / 1000.0
log('C) unpause (+0.5 c)      -> get_pos = %.2f c' % p3)

# Бонус: мотаем WAV явно (SDL_mixer 2.8 в pygame 2.6 умеет WAV-позицию)
try:
    mixer.music.seek(30.0)
    time.sleep(0.4)
    p4 = mixer.music.get_pos() / 1000.0
    log('D) seek(30)+0.4 c       -> get_pos = %.2f c (ожидаемо ~30.4)' % p4)
    seek_ok = abs(p4 - 30.4) < 1.0
except Exception as e:
    log('D) seek FAILED: %r' % (e,))
    seek_ok = False

mixer.music.stop()

res = []
res.append('PAUSE-FREEZE: get_pos на паузе не убежал вперёд на 1.2 c: %s '
           '(p1=%.2f p2=%.2f)' % (abs(p2 - p1) < 0.6, p1, p2))
res.append('UNPAUSE-RESUME: пошла дальше: %s (p2=%.2f p3=%.2f)' % (p3 > p2, p2, p3))
res.append('WAV-SEEK под pygame: %s' % seek_ok)
res.append('VERDICT: %s' % ('PYGAME-MIXER ГОТОВ К ИНТЕГРАЦИИ'
                            if (p3 > p2) else 'ПРОБЛЕМА'))
open(OUT, 'w', encoding='utf-8').write('\n'.join(lines + res) + '\n')
