# -*- coding: utf-8 -*-
"""Пробник 2: pause()/play() и seek() для _bb.wav (SDL2). Всё пишется в
ASCII-файл _probe2.txt, чтобы консоль не портила вывод."""
import time

from kivy.core.audio import SoundLoader

OUT = r'C:\python projects\SoundGrip\_probe2.txt'
lines = []


def log(msg):
    lines.append(str(msg))


s = SoundLoader.load(r'C:\python projects\SoundGrip\_bb.wav')
log('provider=%s.%s' % (type(s).__module__, type(s).__name__))
s.volume = 0.0

# --- A: pause() -> play() (кандидат на паузу без перезапуска) ---------------
try:
    s.play()
    time.sleep(0.5)
    s.pause()
    time.sleep(0.5)
    s.play()
    time.sleep(0.5)
    s.stop()
    log('A) pause()+play() -> NO EXCEPTION (позицию слухом проверяет игрок)')
except Exception as e:
    log('A) pause()+play() EXCEPTION: %r' % (e,))

# --- B: stop() -> play() -> seek() как в игре --------------------------------
try:
    s.play()
    time.sleep(0.3)
    s.stop()
    time.sleep(0.2)
    s.play()
    s.seek(30.0)
    time.sleep(0.5)
    s.stop()
    log('B) stop()->play()->seek(30) -> NO EXCEPTION')
except Exception as e:
    log('B) stop/play/seek EXCEPTION: %r' % (e,))

# --- C: seek БЕЗ предварительного play (некоторые провайдеры так любят) ------
try:
    s.stop()
    time.sleep(0.2)
    s.seek(45.0)
    time.sleep(0.2)
    s.play()
    time.sleep(0.5)
    s.stop()
    log('C) seek(45) ДО play() -> NO EXCEPTION')
except Exception as e:
    log('C) seek-before-play EXCEPTION: %r' % (e,))

log('PROBE2 DONE')
open(OUT, 'w', encoding='ascii', errors='replace').write('\n'.join(lines) + '\n')
