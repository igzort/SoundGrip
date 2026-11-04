# -*- coding: utf-8 -*-
"""beatmap_gen.py — генератор детерминированной карты из WAV.

Прогоняет проверенный BeatDetector по WAV (по блокам 1024, как в тесте),
собирает таймлайн нот и сохраняет JSON в beatmaps/<имя>.json.

Структура ноты: {'t': float, 'kind': 'kick'|'snare'|'onset', 'hue': float}

Фильтры играбельности:
  * onset в пределах 30 мс от kick/snare выбрасывается (не дублируем удар);
  * глобальный лимит плотности MIN_GAP — между нотами не меньше 0.16 с
    (приоритет у kick/snare, лишние onsets пропускаются).

Ядро вынесено в функции — тот же код использует `tracks.py`, когда меню
в игре создаёт карту для нового трека (MP3 -> ffmpeg -> WAV -> сюда).

CLI:  python beatmap_gen.py
"""
import json
import os

os.environ.setdefault('KIVY_NO_ARGS', '1')

import numpy as np
import scipy.io.wavfile as wavfile

from beat_detector import BeatDetector

SR = 44100
CHUNK = 1024
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WAV_PATH = os.path.join(BASE_DIR, '_bb.wav')
MAP_NAME = 'backinblack'
MAPS_DIR = os.path.join(BASE_DIR, 'beatmaps')

HUE_KICK = 0.55    # циан — звёздный взрыв (генезис-стайл)
HUE_SNARE = 0.12   # янтарный — мощный разрез
MIN_GAP = 0.16     # минимальная плотность нот, с
DUP_WINDOW = 0.03  # onset рядом с kick/snare — дубликат, с


def read_mono16(wav_path, sr=SR):
    """WAV -> (моно int16, длительность в секундах). Ждём ровно sr Гц."""
    sr_in, data = wavfile.read(wav_path)
    if sr_in != sr:
        raise ValueError('wav sr=%s, expected %s (декодируй через ffmpeg)'
                         % (sr_in, sr))
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)
    return np.ascontiguousarray(data), len(data) / float(sr)


def analyze_signal(sig, sr=SR, chunk=CHUNK):
    """Прогон BeatDetector по блокам -> сырые события [(t, kind, hue)]."""
    det = BeatDetector(sample_rate=sr)
    raw = []
    pos = 0
    total = len(sig)
    while pos < total:
        end = min(pos + chunk, total)
        block = sig[pos:end]
        if len(block) < chunk:
            pad = np.zeros(chunk, dtype=np.int16)
            pad[:len(block)] = block
            block = pad
        ev = det.analyze(block)
        t = pos / float(sr)
        if ev.get('kick'):
            raw.append((t, 'kick', HUE_KICK))
        if ev.get('snare'):
            raw.append((t, 'snare', HUE_SNARE))
        if ev.get('onset'):
            raw.append((t, 'onset', float(ev.get('onset_hue') or 0.15)))
        pos += chunk
    raw.sort(key=lambda n: (n[0], n[1] != 'kick'))
    return raw


def filter_notes(raw, min_gap=MIN_GAP, dup_window=DUP_WINDOW):
    """Дубликаты onsets + лимит плотности -> финальные ноты карты."""
    strong = [n for n in raw if n[1] in ('kick', 'snare')]
    notes = []
    for t, kind, hue in raw:
        if kind == 'onset':
            if any(abs(t - st) <= dup_window for st, _k, _h in strong):
                continue
        notes.append({'t': round(t, 4), 'kind': kind, 'hue': round(hue, 3)})
    filtered = []
    last_t = -1e9
    for n in notes:
        if n['t'] - last_t < min_gap:
            continue
        filtered.append(n)
        last_t = n['t']
    return filtered


def build_map(wav_path, map_name, song_name=None, verbose=True):
    """WAV -> словарь карты (без записи на диск)."""
    sig, dur = read_mono16(wav_path)
    if verbose:
        print('audio: %.1fs, %d samples' % (dur, len(sig)))
    raw = analyze_signal(sig)
    if verbose:
        print('raw events: %d' % len(raw))
    notes = filter_notes(raw)
    if verbose:
        print('notes: %d (kick=%d snare=%d onset=%d)' % (
            len(notes),
            sum(1 for n in notes if n['kind'] == 'kick'),
            sum(1 for n in notes if n['kind'] == 'snare'),
            sum(1 for n in notes if n['kind'] == 'onset')))
    return {
        'name': map_name,
        'song': song_name or os.path.basename(wav_path),
        'sample_rate': SR,
        'duration': round(dur, 3),
        'notes': notes,
    }


def save_map(data, map_name, maps_dir=MAPS_DIR):
    """Записать карту в beatmaps/<имя>.json, вернуть путь."""
    os.makedirs(maps_dir, exist_ok=True)
    path = os.path.join(maps_dir, map_name + '.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def generate(wav_path=WAV_PATH, map_name=MAP_NAME, song_name=None):
    """CLI-путь: WAV -> JSON в beatmaps/<имя>.json."""
    data = build_map(wav_path, map_name, song_name)
    path = save_map(data, map_name)
    print('saved:', path)
    return data


if __name__ == '__main__':
    generate()