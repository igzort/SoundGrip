# -*- coding: utf-8 -*-
"""Пробник: реально ли seek/get_pos работают для _bb.wav под SDL2 (Kivy 2.3.1).

Громкость 0, чтобы ничего не было слышно. ВАЖНО: SoundLoader требует
вызывать внутри работающего EventLoop? -- на практике для probe хватает
прямых вызовов; если Kivy ругается, пробник печатает traceback.
"""
import time

from kivy.core.audio import SoundLoader

WAV = r'C:\python projects\SoundGrip\_bb.wav'

s = SoundLoader.load(WAV)
print('PROVIDER: %s.%s' % (type(s).__module__, type(s).__name__))
if s is None:
    raise SystemExit('SoundLoader.load вернул None')

s.volume = 0.0
s.play()
time.sleep(0.5)
print('A) get_pos после play()        :', s.get_pos())
try:
    s.seek(30.0)
except Exception as e:
    print('   seek(30) ИСКЛЮЧЕНИЕ:', e)
time.sleep(0.5)
p = s.get_pos()
print('B) get_pos после seek(30.0)    :', p, '=> SEEK OK' if p and abs(p - 30) < 3 else '=> SEEK НЕ РАБОТАЕТ/НЕВИДИМ')
try:
    s.seek(65.0)
except Exception as e:
    print('   seek(65) ИСКЛЮЧЕНИЕ:', e)
time.sleep(0.5)
p2 = s.get_pos()
print('C) get_pos после seek(65.0)    :', p2, '=> SEEK OK' if p2 and abs(p2 - 65) < 3 else '=> SEEK НЕ РАБОТАЕТ/НЕВИДИМ')

# поведение pause()/play() -- кандидат на замену stop()+seek
try:
    s.pause()
    time.sleep(0.3)
    pos_paused = s.get_pos()
    s.play()
    time.sleep(0.4)
    pos_resumed = s.get_pos()
    print('D) pause->play: paused_pos=%s resumed_pos=%s => %s'
          % (pos_paused, pos_resumed,
             'RESUME (позиция сохранена)' if pos_resumed and pos_paused
             and abs(pos_resumed - pos_paused) < 3 else 'RESTART с нуля/непонятно'))
except Exception as e:
    print('   pause/play ИСКЛЮЧЕНИЕ:', e)

s.stop()
print('PROBE DONE')
