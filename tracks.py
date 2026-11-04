# -*- coding: utf-8 -*-
"""tracks.py — библиотека треков для меню «Выбор мелодии».

Сканирует папку игры, находит аудио (*.mp3/wav/ogg/flac/m4a), даёт каждому
треку slug и карту нот:

  * beatmaps/<slug>.json уже есть      -> играем сразу;
  * MAP_ALIAS — карты из ранних сборок -> играем сразу (AC/DC);
  * иначе карта строится на месте: ffmpeg (MP3/OGG/M4A -> WAV 48к моно)
    -> BeatDetector (beatmap_gen) -> beatmaps/<slug>.json.

Файлы, начинающиеся с «_», считаются служебными (дампы, исходники карт)
и в меню не попадают.

CLI:
  python tracks.py           # список треков и состояние их карт
  python tracks.py --build   # достроить недостающие карты (офлайн)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import beatmap_gen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAPS_DIR = os.path.join(BASE_DIR, 'beatmaps')

AUDIO_EXT = ('.mp3', '.wav', '.ogg', '.flac', '.m4a')
SKIP_PREFIX = '_'
SR = beatmap_gen.SR

# готовые карты из ранних сборок: slug трека -> имя карты
MAP_ALIAS = {'acdc_back_in_black': 'backinblack'}

_TITLE_JUNK = re.compile(r'\(\s*www\.[^)]*\)|\bwww\.[^\s)]+|lightaudio',
                         re.IGNORECASE)
_SLUG_KEEP = re.compile(r'[^0-9a-zа-я]+')


class Track(object):
    """Один трек-кандидат для игры."""

    def __init__(self, path, maps_dir=MAPS_DIR):
        self.path = os.path.abspath(path)
        self.file = os.path.basename(self.path)
        self.title = pretty_title(self.file)
        self.slug = slugify(os.path.splitext(self.file)[0])
        self.maps_dir = maps_dir
        self.map_name = MAP_ALIAS.get(self.slug, self.slug)
        self.map_path = os.path.join(maps_dir, self.map_name + '.json')
        self.has_map = os.path.exists(self.map_path)
        self.ready = self.has_map        # карта готова — играем сразу

    def __repr__(self):
        return '<Track %s map=%s%s>' % (self.title, self.map_name,
                                        '' if self.has_map else ' (нет)')

    def load_map(self):
        """Карта с диска (или None, если её пока нет)."""
        if not self.has_map:
            return None
        with open(self.map_path, encoding='utf-8') as f:
            data = json.load(f)
        notes = list(data.get('notes', []))
        notes.sort(key=lambda n: n['t'])
        data['notes'] = notes
        return data


def pretty_title(file_name):
    """Имя файла -> читаемый заголовок: без расширения и мусора сайтов."""
    stem = os.path.splitext(file_name)[0]
    stem = _TITLE_JUNK.sub(' ', stem)
    stem = re.sub(r'\s{2,}', ' ', stem).strip(' -–—_.')
    return stem or file_name


def slugify(text, max_len=48):
    """'ACDC - Back In Black' -> 'acdc_back_in_black' (стабильный slug)."""
    s = text.lower().replace('ё', 'e').replace('й', 'y')
    s = _SLUG_KEEP.sub('_', s).strip('_')
    return s[:max_len].strip('_') or 'track'


def has_ffmpeg():
    return shutil.which('ffmpeg') is not None


def list_tracks(base_dir=BASE_DIR, maps_dir=MAPS_DIR):
    """Все аудио в папке игры (кроме служебных) -> список Track."""
    out = []
    try:
        names = sorted(os.listdir(base_dir))
    except OSError:
        return out
    for name in names:
        if name.startswith(SKIP_PREFIX):
            continue
        if not name.lower().endswith(AUDIO_EXT):
            continue
        path = os.path.join(base_dir, name)
        if os.path.isfile(path):
            out.append(Track(path, maps_dir))
    out.sort(key=lambda t: t.title.lower())
    return out


def decode_to_wav(src, dst):
    """ffmpeg: любой аудиофайл -> WAV 44.1 кГц моно PCM16 (для детектора)."""
    if not has_ffmpeg():
        raise RuntimeError('ffmpeg не найден в PATH')
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', src, '-vn', '-f', 'wav',
           '-ac', '1', '-ar', str(SR), '-c:a', 'pcm_s16le', dst]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or not os.path.exists(dst):
        msg = res.stderr.decode('utf-8', 'replace').strip()[-300:]
        raise RuntimeError('ffmpeg не смог декодировать: %s' % (msg or '?'))


def _read_json(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    notes = list(data.get('notes', []))
    notes.sort(key=lambda n: n['t'])
    data['notes'] = notes
    return data


def ensure_map(track, status=None):
    """Карта для трека: готовая с диска либо построенная сейчас.

    status — функция(str) для показа прогресса в меню.
    Возвращает (map_dict | None, текст состояния).
    """
    say = status or (lambda _m: None)
    if os.path.exists(track.map_path):
        say('карта уже готова')
        return _read_json(track.map_path), 'готова'

    tmp = None
    try:
        bg = beatmap_gen
        fd, tmp = tempfile.mkstemp(prefix='soundgrip_', suffix='.wav')
        os.close(fd)
        if track.file.lower().endswith('.wav'):
            try:                       # уже WAV 48к — берём как есть
                say('читаю WAV...')
                data = bg.build_map(track.path, track.map_name,
                                    track.file, verbose=False)
            except ValueError:         # другая частота — перекодировать
                say('перекодирую WAV (ffmpeg)...')
                decode_to_wav(track.path, tmp)
                data = bg.build_map(tmp, track.map_name,
                                    track.file, verbose=False)
        else:
            say('декодирую трек (ffmpeg)...')
            decode_to_wav(track.path, tmp)
            data = bg.build_map(tmp, track.map_name, track.file,
                                verbose=False)
        say('ищу удары в такт...')
        path = bg.save_map(data, track.map_name, track.maps_dir)
        track.has_map = True
        track.ready = True
        track.map_path = path
        n = len(data.get('notes', []))
        print('[tracks] карта создана: %s (%d нот)' % (path, n))
        return data, 'создана (%d нот)' % n
    except Exception as e:              # noqa: BLE001 — показать текст в меню
        print('[tracks] карта не собралась: %s' % e)
        return None, 'ошибка: %s' % e
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def demo_map(map_name, notes_count=58, gap=0.5):
    """Запасная карта, если собрать ноты не удалось (игра без анализа)."""
    notes = [{'t': round(1.0 + i * gap, 3), 'kind': 'kick', 'hue': 0.55}
             for i in range(notes_count)]
    return {'name': map_name, 'song': None,
            'duration': round(1.0 + notes_count * gap, 3), 'notes': notes}


def build_missing(base_dir=BASE_DIR, maps_dir=MAPS_DIR):
    """CLI --build: собрать карты для всех треков, где их ещё нет."""
    built = 0
    for t in list_tracks(base_dir, maps_dir):
        if t.has_map:
            print('уже есть : %-52s -> %s' % (t.title, t.map_name))
            continue
        print('собираю  : %-52s -> %s' % (t.title, t.map_name))
        data, state = ensure_map(t, status=lambda m: print('   ' + m))
        print('   %s' % state)
        built += 1 if data else 0
    print('готово, новых карт: %d' % built)


if __name__ == '__main__':
    import sys as _sys
    if '--build' in _sys.argv:
        build_missing()
    else:
        _tracks = list_tracks()
        print('треков найдено: %d' % len(_tracks))
        for t in _tracks:
            print('  %-52s %-30s %s'
                  % (t.title, t.map_name,
                     'карта готова' if t.has_map else 'карты нет'))
        if not has_ffmpeg():
            print('ffmpeg не найден — новые карты собрать не получится')
