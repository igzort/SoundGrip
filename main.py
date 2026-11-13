# -*- coding: utf-8 -*-
"""SoundGrip -- Fruit Ninja-вайб: сферы-ноты в такт музыки.

Каждой ноте карты (kick/snare/onset) соответствует СФЕРА-звезда: апекс её
дуги приходится РОВНО на бит ноты, поэтому по слуху попадать естественно.

ДИНАМИЧЕСКИЙ РАЗГОН по прогрессу трека:
  * начало  -- шары подбрасывает СНИЗУ, их немного (часть нот пропускаем);
  * середина -- плотность растёт, подключаются БОКОВЫЕ дуги через экран;
  * финал   -- ещё и падение СВЕРХУ ВНИЗ. Такт и ритм сохраняются всегда.
Резать можно в любой точке дуги, пока сфера видна. Разрез -- вспышка + кольцо
Genesis, сфера разлетается на ДВЕ половинки своего цвета. Не срезал -- сфера
уходит за экран ЦЕЛОЙ.

Запуск: python main.py [--offset=0.08]
Клавиши: клик/Пробел -- старт, Esc -- пауза,
         [ / ] -- подстройка синхрона звука (+/-10 мс), Z / X -- разрез.
"""
import json
import math
import os
import random
import sys
import time

os.environ.setdefault('KIVY_NO_ARGS', '1')

from kivy.config import Config
Config.set('graphics', 'maxfps', '0')   # рендер на частоте монитора (vsync)
# Красные кружки-отпечатки от ПКМ -- это эмуляция мультитача от мышиного
# провайдера Kivy. Отключаем её: multitouch_on_demand, т.е. точки появятся
# только если ПКМ держать (и тогда Kivy сам их спрячет), а не на каждый клик.
# Строка ОБЯЗАТЕЛЬНО до любого импорта Window/App -- конфиг читается один раз
# при инициализации графического движка, позже он уже не применяется.
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')


from kivy.app import App
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.audio import SoundLoader
from kivy.core.window import Window
from kivy.core.text import Label as CoreLabel
from kivy.graphics import (Color, Line, Mesh, PopMatrix, PushMatrix, Rectangle,
                           RenderContext, RoundedRectangle, Scale, Translate)
from kivy.properties import ListProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.slider import Slider
from kivy.uix.switch import Switch
from kivy.uix.widget import Widget
from effects import (ParticleFx, SHAPE_CUBE_WIRE,
                     SHAPE_GENESIS_STAR, SHAPE_WATER, STAR_CUSTOM, WATER)
from menu import MenuWidget, COL_NEON_CYAN
import tracks as tracklib

# --- аудио-провайдеры -----------------------------------------------------------
# SDL2 (встроен) НЕ поддерживает seek() для mp3 — это известное ограничение Kivy
# (см. https://github.com/kivy/kivy/issues/4139, статус "Not Planned").
# fffpyplayer поддерживает seek для mp3, но требует установки пакета ffpyplayer
# (и добавления в buildozer.spec для Android APK).
try:                                   # noqa: WPS433
    from kivy.core.audio.audio_ffpyplayer import SoundFFPy  # noqa: F401
    _FFPY_AVAILABLE = True
except Exception:                      # noqa: BLE001, WPS433
    _FFPY_AVAILABLE = False

# --- служебные пути ----------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAPS_DIR = os.path.join(BASE_DIR, 'beatmaps')
SONGS = {'chaoz_fantasy': 'Chaoz Fantasy.mp3',
         'backinblack': '_bb.wav',
         'sparkler': 'Sparkler.mp3',
         'blue_marine': 'blue-marine.mp3',
         'ephixa_diablo_dub_diablo_s_tristram_theme_remix': 'Ephixa — Diablo Dub (Diablo’s Tristram Theme Remix) (www.lightaudio.ru).mp3'}
# --- музыка меню (главное меню + пауза), зацикленная ---------------------------
MENU_TRACK = 'paragonx9-deflection_mini.mp3'
MENU_MUSIC_PATH = os.path.join(BASE_DIR, MENU_TRACK)
# ручное перекрытие длительности трека (секунды) — чтобы финиш наступил в нужную секунду
TRACK_DURATION_OVERRIDE = {
    'ephixa_diablo_dub_diablo_s_tristram_theme_remix': 206.5,  # 3:26 + 0.5 буфера
}
DEFAULT_AUDIO_OFFSET = 0.05   # подстройка синхрона звука на лету, с

# --- подброс сфер <как в Fruit Ninja> -----------------------------------------
TOSS_G = 1500.0         # гравитация подброса (ДО бита), px/с²
TOSS_G_DOWN = 375.0     # гравитация ПОСЛЕ бита: падение вниз за экран
                        # вдвое медленнее (TOSS_G/4)
TOSS_PEAK = 0.56        # пик полёта: доля высоты экрана (чуть выше центра)
TOSS_PEAK_JITTER = 45.0 # разброс высоты пика, px
TOSS_LAUNCH_Y = -90.0   # старт ниже нижнего края экрана, px
TOSS_X_MIN = 90.0       # отступ от боковых краёв экрана, px
CULL_MARGIN = 100.0     # +/-100 px за краем экрана: шар жив и РЕЖЕТСЯ, пока
                        # не ушёл дальше -- ни мёртвых зон, ни ранней цензуры
TOSS_SIDE = 0.10        # вылет из нижнего угла: доля ширины от края
TOSS_TRAVEL = 1.18      # горизонтальный пролёт: доля ширины экрана
SIDE_SPEED_K = 0.85     # боковые дуги: -15% горизонтальной скорости
STAR_DIE_T = 0.0        # сфера исчезает МОМЕНТАЛЬНО (мост-растворение убрано)
CUT_ANGLE = math.radians(200.0)  # линия среза: почти горизонталь, уклон ~20°
SLICE_EARLY = 0.25      # пауза перед стартом чистой синхронизации, с

# --- ДИНАМИЧЕСКИЙ РАЗГОН: направления и плотность по прогрессу песни ---------
PHASE_SIDE_IN = 0.22    # с этого прогресса появляются боковые дуги
PHASE_SIDE_FULL = 0.50  # к этому -- боковые на полную
PHASE_TOP_IN = 0.55     # с этого -- падение сверху
PHASE_TOP_FULL = 0.82   # к этому -- сверху на полную
# ЛЕЙТ-ГЕЙМ: объекты, летящие СВЕРХУ ВНИЗ, идут вдвое медленнее. Скорость
# ∝ √гравитации, поэтому гасим гравитацию множителем TOP_SPEED_K². Такт не
# страдает: апекс по-прежнему РОВНО в бит (rise считается от ay), просто путь
# над экраном проходится вдвое дольше -- спавн учитывается заранее.
TOP_SPEED_K = 0.5       # скорость падения сверху вниз (лейт-гейм): вдвое ниже
TOP_G_K = TOP_SPEED_K * TOP_SPEED_K   # 0.25 -- множитель гравитации режима top
DENSITY_START = 0.55    # в начале играем ~55% нот (шаров меньше)
DENSITY_FULL_AT = 0.40  # к этому прогрессу плотность = 100%
TOP_LAUNCH_MARGIN = 110.0   # старт сверху: насколько выше края экрана, px
SPHERE_R = 22.5         # честный радиус: хитбокс = видимый диск сферы
HB_STEP = 5.0           # плотность хитбоксов: сэмпл пути каждые 5 px
SUB_HZ = 200.0          # частота логики РЕЗА: подшаги внутри кадра, Гц
SUB_MAX = 8             # предел подшагов за кадр (защита от просадок fps)
HIT_WIN_300 = 0.06      # окно <ровно в бит> -- эффект мощнее, с
HIT_WIN_100 = 0.12      # окно <чуть мимо> -- эффект послабее, с
MAX_NOTES = 32          # защита от переполнения
CUBE_EVERY = 5          # каждая 5-я фигура -- КУБ (цельный, остальные -- звезда)
WATER_EVERY = 3         # каждая 3-я (не куб) -- ВОДЯНОЙ ПУЗЫРЬ
CUBE_TILT = math.radians(24.0)   # наклон вращающегося куба (pitch)
CUBE_GRIDS = ((3, 1.0),)     # единственная решётка: 3x3 (4x4 и 5x5 убраны)

# НИКАКИХ запасов: хитбокс лезвия = ровно радиус сферы r = radius_at(now).
# И тап, и свайп -- по нему же.
SWIPE_TOL = 0.0
TRAIL_LIFE = (0.2, 0.2)   # след лезвия живёт ровно 0.2 с

# --- PERFECT: серия попаданий подряд -------------------------------------
PERFECT_AT = 10               # столько попаданий подряд = «PERFECT!»
PERFECT_TEXT = 'PERFECT!'
PERFECT_TEXT_T = 1.0          # зелёная надпись висит ровно секунду
PERFECT_RGB = (0.35, 1.00, 0.55)   # зелёный неон

# --- INCREDIBLE: бар из 5 ячеек набирается PERFECT-ами --------------------
PERFECT_BAR_N = 5         # ячеек в полоске: по одной за каждый PERFECT
PERFECT_BAR_W = 0.60      # длина полоски: доля ширины экрана (по центру)
PERFECT_BAR_H = 14.0      # высота ячейки; радиус стадиона = половина высоты
PERFECT_BAR_MARGIN = 26.0  # отступ от верхнего края экрана, px
PERFECT_BAR_GAP = 0.012   # зазор между ячейками (доля длины полоски)
INCREDIBLE_TEXT = 'INCREDIBLE!'
INCREDIBLE_T = 10.0       # длительность усиленного режима, с
INCREDIBLE_RGB = (1.00, 0.85, 0.15)   # жёлтый <сварочный> цвет
INCREDIBLE_K = 1.0        # радиус среза в INCREDIBLE = БАЗОВЫЙ (усиление даёт
                          # не хитбокс, а <сварку>: её искры режут сферы)

# --- ПАУЗА: окно паузы по Esc ------------------------------------------------
PAUSE_W = 400.0           # ширина плашки паузы, px (фиксированная)
PAUSE_H = 430.0           # высота плашки паузы, px (крупный заголовок + 4 кнопки)
PAUSE_BTN_H = 54.0        # высота кнопки паузы, px (size_hint_y=None)
PAUSE_BTN_GAP = 16.0      # зазор между кнопками, px
PAUSE_VOL_STEP = 0.1      # шаг громкости в панели <Звук> паузы (как в меню)

# --- ОЧКИ: счёт ТОЛЬКО сверху ------------------------------------------------
SCORE_PER_CUT = 10        # очков за каждую разрезанную фигуру
SCORE_FONT = 35           # кегль счёта на верхнем краю (компактный неон)
SCORE_RGB = (0.0, 0.75, 1.0)   # счёт — неоновый голубой/лазерный
SCORE_MARGIN = 26.0       # отступ счёта от левого/верхнего края, px

# --- КОНЦОВКА: общий счёт на весь экран + фанфары ---------------------------
FANFARE_SHOTS = 6         # залпов фанфар подряд
FANFARE_STEP = 0.17       # пауза между залпами, с

# --- превью эффектов из меню (<Эффекты>): фигуры всплывают сами -------------
FX_DEMO_GAP = 2.2       # сек между фигурами
FX_DEMO_COUNT = 60      # сколько фигур в списке (хватит надолго)
FX_AUTO_SLICE = 0.9     # сек ОТ РОЖДЕНИЯ фигуры -- рассыпается сама
FX_COLOR_CYCLE = 12.0   # превью <Эффекты>: полный цикл палитры

# --- ДИСКРЕТНАЯ смена цвета (переключение РЫВКОМ, без плавных переходов) ----
# Длительность каждой стадии палитры, c:
#   10 -> 7 -> 7 -> 5 -> 5, дальше -- ФИНАЛЬНАЯ стадия.
COLOR_SCHEDULE = (10.0, 7.0, 7.0, 5.0, 5.0)
COLOR_BOUNDS = []       # накопленные границы стадий, c
_b = 0.0
for _d in COLOR_SCHEDULE:
    _b += _d
    COLOR_BOUNDS.append(_b)
COLOR_BOUNDS = tuple(COLOR_BOUNDS)          # (10.0, 17.0, 24.0, 29.0, 34.0)
COLOR_FINAL_T = COLOR_BOUNDS[-1]            # 34 c -- дальше случайные цвета

# Пулы случайных цветов для ФИНАЛЬНОЙ стадии: у каждой фигуры свой набор,
# каждая сфера/куб/пузырь выбирает из него СВОЙ цвет.
COLOR_POOLS = {
    'water': (                              # пузыри -- холодная гамма
        (0.10, 0.75, 0.95), (0.00, 0.90, 0.80), (0.35, 0.45, 1.00),
        (0.65, 0.20, 0.95), (0.05, 0.95, 0.60), (0.00, 0.60, 1.00),
        (0.85, 0.25, 0.90), (0.20, 1.00, 0.55),
        # добавленные: желтый, оранжевый, красный, розовый
        (1.00, 0.85, 0.15), (1.00, 0.55, 0.05), (1.00, 0.10, 0.25),
        (0.95, 0.20, 0.60),
    ),
    'star': (                               # сферы -- тёплая гамма
        (1.00, 0.85, 0.15), (1.00, 0.45, 0.05), (1.00, 0.10, 0.25),
        (1.00, 1.00, 1.00), (1.00, 0.60, 0.00), (0.95, 0.20, 0.60),
        (1.00, 0.75, 0.30), (0.90, 0.05, 0.10),
        # добавленные: синий, зеленый, фиолетовый
        (0.20, 0.35, 0.90), (0.15, 0.85, 0.30), (0.60, 0.03, 0.95),
    ),
    'cube': (                               # кубы -- зелёная гамма -> оранжевый/красный
        (0.15, 0.85, 0.30), (0.35, 1.00, 0.10), (0.10, 0.95, 0.65),
        (1.00, 0.52, 0.05), (1.00, 0.12, 0.12), (0.95, 0.30, 0.05),
        (0.60, 0.30, 1.00), (0.20, 0.70, 0.20),
        # добавленные: синий, голубой, фиолетовый, желтый
        (0.20, 0.35, 0.90), (0.72, 0.90, 1.00), (0.60, 0.03, 0.95),
        (1.00, 0.85, 0.15),
    ),
}

# палитра space_effect4: тип звезды -> цвет. Нужна, чтобы половинки и
# вспышка реза взяли тот же цвет, что и фигура.
STAR_RGB = {
    1.0: (1.00, 0.85, 0.15),    # Genesis-1: жёлтый
    2.0: (0.20, 0.35, 0.90),    # Genesis-2: бирюзовый
    3.0: (0.60, 0.03, 0.95),    # Genesis-3: фиолетовый
}

FIGURES = {
    'water': (
        (1.00, 1.00, 1.00),      # 0 -- белый
        (0.72, 0.90, 1.00),      # 1 -- светло-голубой
        (0.22, 0.42, 0.90),      # 2 -- синий
        (0.24, 0.78, 0.78),      # 3 -- бирюзовый
        (0.60, 0.35, 0.95),      # 4 -- фиолетовый
        # добавленные: желтый, оранжевый, красный, розовый
        (1.00, 0.85, 0.15),      # 5 -- желтый
        (1.00, 0.55, 0.05),      # 6 -- оранжевый
        (1.00, 0.10, 0.25),      # 7 -- красный
        (0.95, 0.20, 0.60),      # 8 -- розовый
    ),
    'star': (
        (1.00, 0.85, 0.15),      # 0 -- жёлтый
        (1.00, 1.00, 1.00),      # 1 -- белый
        (1.00, 0.55, 0.05),      # 2 -- оранжевый
        (1.00, 0.20, 0.10),      # 3 -- красный
        (0.60, 0.03, 0.10),      # 4 -- бардовый
        # добавленные: синий, зеленый, фиолетовый
        (0.20, 0.35, 0.90),      # 5 -- синий
        (0.15, 0.85, 0.30),      # 6 -- зеленый
        (0.60, 0.03, 0.95),      # 7 -- фиолетовый
    ),
    'cube': (
        (0.15, 0.85, 0.30),      # 0 -- зелёный
        (0.30, 1.00, 0.40),      # 1 -- лайм
        (1.00, 1.00, 1.00),      # 2 -- белый
        (1.00, 0.52, 0.05),      # 3 -- оранжевый (конец трека)
        (1.00, 0.12, 0.12),      # 4 -- красный (самый финал)
        # добавленные: синий, голубой, фиолетовый, желтый
        (0.20, 0.35, 0.90),      # 5 -- синий
        (0.72, 0.90, 1.00),      # 6 -- голубой
        (0.60, 0.03, 0.95),      # 7 -- фиолетовый
        (1.00, 0.85, 0.15),      # 8 -- желтый
    ),
}


def _sstep(x):
    """Smoothstep 0..1 -- плавный (без рывков) разгон фаз и плотности."""
    x = max(0.0, min(1.0, float(x)))
    return x * x * (3.0 - 2.0 * x)


def pick_figure(idx, force=None, active=None):
    """Фигура под индексом спавна: куб каждая CUBE_EVERY-я, вода каждая
    WATER_EVERY-я, иначе Genesis-звезда. force доминирует (превью).

    active (список активных типов из меню <Эффекты>) фильтрует результат:
    если задан и непуст, по индексу выбирается один из ВЫБРАННЫХ типов
    (по кругу), поэтому в игре летят только отмеченные галочкой фигуры.
    """
    if force is not None:
        return force
    if active:
        pool = [a for a in ('star', 'cube', 'water') if a in active]
        if not pool:
            return 'star'
        return pool[idx % len(pool)]
    if idx % CUBE_EVERY == 0:
        return 'cube'
    if idx % WATER_EVERY == 0:
        return 'water'
    return 'star'


def roll_cube_grid():
    """Размер кубика-Рубика: всегда 3x3 (шансы других решёток убраны)."""
    r = random.random()
    acc = 0.0
    for size, chance in CUBE_GRIDS:
        acc += chance
        if r < acc:
            return size
    return CUBE_GRIDS[-1][0]     # защита от края float


def figure_rgb(figure, t_sec):
    """Дискретный цвет фигуры по ВРЕМЕНИ (БЕЗ плавных переходов).

    Стадии идут по расписанию COLOR_SCHEDULE: 10 -> 7 -> 7 -> 5 -> 5 c и
    переключаются РЫВКОМ (границы -- COLOR_BOUNDS = 10/17/24/29/34 c).
    После COLOR_FINAL_T наступает финальная стадия: каждая фигура берёт
    СЛУЧАЙНЫЙ цвет из своего пула COLOR_POOLS (пул у каждой фигуры свой).

    t_sec -- секунды с начала трека (в превью <Эффекты> -- от его часов).
    """
    stages = FIGURES.get(figure, FIGURES['star'])
    t = max(0.0, float(t_sec))
    idx = 0
    for bound in COLOR_BOUNDS:
        if t < bound:
            break
        idx += 1
    if idx >= len(stages):
        # финальная стадия: случайный цвет из пула данной фигуры
        pool = COLOR_POOLS.get(figure) or COLOR_POOLS['star']
        return tuple(pool[random.randrange(len(pool))])
    return tuple(stages[idx])


def _sphere_rgb(sphere):
    """Цвет эффекта разреза = цвет самой фигуры (по её типу палитры)."""
    return sphere.rgb


def _map_from_argv():
    """Карта из CLI: python main.py --map=backinblack."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == '--map' and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith('--map='):
            return arg.split('=', 1)[1]
    return 'backinblack'


def _offset_from_argv():
    """Латентность звука из CLI: python main.py --offset=0.08 (сек)."""
    for arg in sys.argv[1:]:
        if arg.startswith('--offset='):
            try:
                return max(0.0, float(arg.split('=', 1)[1]))
            except ValueError:
                break
    return DEFAULT_AUDIO_OFFSET


def load_map(name):
    """JSON карты ЦЕЛИКОМ: каждая нота трека = кружок. Нет файла -- демо."""
    path = os.path.join(MAPS_DIR, name + '.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        notes = list(data.get('notes', []))
        notes.sort(key=lambda n: n['t'])
        data['notes'] = notes
        return data
    print('[SoundGrip] карта %s не найдена (%s) -- демо-карта (нота 0.5с)'
          % (name, path))
    notes = [{'t': round(t, 3), 'kind': 'kick', 'hue': 0.55}
             for t in [1.0 + i * 0.5 for i in range(58)]]
    return {'name': name, 'song': None, 'duration': 30.0, 'notes': notes}


def _sound_path_for(name):
    song = SONGS.get(name)
    if song:
        p = os.path.join(BASE_DIR, song)
        if os.path.exists(p):
            return p
    return None


def _track_for_map(name=None):
    """Трек из папки игры для карты (CLI --map=..., тесты).

    Нет подходящего трека -- None: игра уйдёт на запасной путь (WAV из SONGS).
    """
    try:
        found = tracklib.list_tracks()
    except Exception:                    # noqa: BLE001 -- игра важнее
        return None
    if not found:
        return None
    if name:
        for t in found:
            if t.map_name == name:
                return t
    return found[0]


def _seg_circle_dist(ax, ay, bx, by, cx, cy):
    """Расстояние от точки C до отрезка AB (для проверки свайпа по кругу)."""
    abx, aby = bx - ax, by - ay
    acx, acy = cx - ax, cy - ay
    ab2 = abx * abx + aby * aby
    if ab2 <= 1e-9:
        return math.hypot(acx, acy)
    t = (acx * abx + acy * aby) / ab2
    t = max(0.0, min(1.0, t))
    return math.hypot(acx - abx * t, acy - aby * t)


class FlySphere(Widget):
    """Сфера-нота. Три направления подачи (динамический разгон по песне):

    'bottom' -- вертикальный подброс СНИЗУ (начало трека, шаров мало);
    'side'   -- диагональная дуга с БОКОВОГО нижнего угла (середина);
    'top'    -- падение СВЕРХУ ВНИЗ, апекс (низ дуги) в бит (ближе к концу).

    Во всех режимах апекс (ax, ay) приходится РОВНО на бит -- ритм и такт
    сохранены. Резать можно в любой точке дуги, пока сфера видна.
    Хитбокс = видимый диск (radius_at), БЕЗ допусков.
    """

    # палитра space_effect4: kick=солнечно-жёлтая, snare=красная,
    # onset=изумрудная (те же 6 типов, что в Genesis)
    STAR_TYPE = {'kick': 1.0, 'snare': 0.0, 'onset': 5.0}

    def __init__(self, ax, ay, vx, rise, hit_time, kind='kick',
                 star_type=None, screen_w=0.0, screen_h=0.0,
                 mode='side', launch_y=TOSS_LAUNCH_Y, figure='star',
                 g_up=TOSS_G, g_down=TOSS_G_DOWN, rgb=None, pin=None, **kw):
        super().__init__(**kw)
        # pin (x, y) -- режим ВИТРИНЫ (<Эффекты>): фигура закреплена в точке,
        # стоит на месте и крутится (yaw/pitch/шейдер), НЕ летит по параболе,
        # НЕ режется и НЕ уходит за экран. None -- обычный полёт.
        self._pin = tuple(pin) if pin is not None else None
        # апекс (ax, ay) в момент бита; vx -- горизонтальная скорость
        self.ax, self.ay = float(ax), float(ay)
        self.vx = float(vx)          # +: летит слева направо, -: справа налево
        self.rise = float(rise)      # время от старта подачи до бита, с
        # направление подачи: 'bottom' снизу, 'side' с боков, 'top' сверху
        self.mode = mode
        self.launch_y = float(launch_y)
        # гравитация полёта: у режима 'top' (лейт-гейм) она МЯГЧЕ -- объект
        # летит сверху вниз вдвое медленнее, но апекс всё так же ровно в бит
        self.g_up = float(g_up)       # до бита (подлёт к апексу)
        self.g_down = float(g_down)   # после бита (падение за нижний край)
        # 'top': вертикальная скорость в момент бита = G·rise. Тогда шар
        # приходит СВЕРХУ, ровно в бит проходит ay и ПРОДОЛЖАЕТ падать
        # сквозь экран (а не отскакивает обратно вверх).
        # Снизу / с боков -- 0: симметричная дуга с апексом в бит.
        self.fall_v0 = self.g_up * self.rise if mode == 'top' else 0.0
        self.screen_w, self.screen_h = float(screen_w), float(screen_h)
        self.hit_time = float(hit_time)
        self.kind = kind
        self.star_type = (star_type if star_type is not None
                          else self.STAR_TYPE.get(kind, 2.0))
        # фигура ноты: 'star' -- Genesis-звезда, 'cube' -- кубик-Рубик 3x3,
        # 'water' -- водяной пузырь (жидкая деформируемая форма)
        self.figure = figure if figure in ('cube', 'water') else 'star'
        # размер кубика при спавне: единственная решётка 3x3
        self.grid = roll_cube_grid() if self.figure == 'cube' else 0
        # ВРАЩЕНИЕ КУБА -- случайное у каждого: свой стартовый угол, своё
        # направление и скорость. yaw -- вокруг вертикали (влево/вправо),
        # pitch -- качание наклона (вверх/вниз) вокруг базового CUBE_TILT.
        self._yaw0 = random.uniform(0.0, 2.0 * math.pi)
        self._yaw_v = random.choice((-1.0, 1.0)) * random.uniform(1.1, 2.3)
        self._pitch_v = random.choice((-1.0, 1.0)) * random.uniform(1.0, 2.2)
        self._pitch_amp = random.uniform(0.22, 0.50)
        # цвет фигуры (и её эффектов): по умолчанию -- палитра типа/вода;
        # при спавне rgb приходит из figure_rgb(t) -- дискретно по времени
        # (каждые COLOR_STEP_T c новая стадия) -- и перекрывает базовый.
        if self.figure == 'water':
            self.rgb = WATER
            self._phase = random.uniform(0.0, 2.0 * math.pi)  # фаза волны
        else:
            self.rgb = STAR_RGB.get(round(self.star_type),
                                    (1.0, 0.72, 0.30))
            self._phase = 0.0
        self._custom_rgb = rgb is not None
        if self._custom_rgb:
            self.rgb = tuple(rgb)
        self.dead = False
        self._born = self.hit_time - self.rise
        # разрез: помечаем исчезновение. Поза среза НЕ хранится -- взрыв берёт
        # центр сферы напрямую в _slice, промежуточных <точек реза> нет
        self.died_at = None
        # позиция на момент спавна (вне экрана, в нижнем углу)
        self.update_pos(self._born)

    def die(self, now):
        """Пометить сферу разрезанной (исчезает мгновенно).

        Позу среза НЕ храним: взрыв и обломки берут центр сферы напрямую
        (sphere.x, sphere.y) в _slice -- никаких промежуточных <точек реза>.
        """
        self.dead = True
        self.died_at = float(now)

    def die_done(self, now):
        """Растворение завершено -- можно убирать из списка отрисовки."""
        return (self.died_at is not None
                and now - self.died_at >= STAR_DIE_T)

    def pos_at(self, now):
        """Позиция (x, y) в момент now -- ЧИСТАЯ функция, без мутации.

        Нужна для цепочки хитбоксов: наматываем сэмплы вдоль пути сферы
        за кадр, не трогая её текущее состояние. В режиме витрины (_pin)
        фигура стоит на месте -- возвращаем закреплённую точку.
        """
        if self._pin is not None:
            return (self._pin[0], self._pin[1])
        t = now - self.hit_time
        # ПОСЛЕ бита (t > 0) включается замедленная фаза: точка в бит и
        # скорость на выходе из апекса те же, но ускорение вчетверо меньше
        # (TOSS_G_DOWN = TOSS_G/4) -- фигура падает вниз вдвое медленнее.
        # У режима 'top' обе гравитации свои (мягче) -- тот же закон, но
        # сверху вниз объект идёт медленнее.
        g = self.g_up if t <= 0.0 else self.g_down
        return (self.ax + self.vx * t,
                self.ay - self.fall_v0 * t - 0.5 * g * t * t)

    def update_pos(self, now):
        """Баллистика: X -- равномерно, Y -- парабола, проходящая ay в бит.

        y = ay - v0·t - ½G·t²,  где t = now - hit_time
          * снизу / с боков (fall_v0 = 0): апекс (верх дуги) -- РОВНО в бит,
            дальше шар падает вниз ЗАМЕДЛЕННО (TOSS_G_DOWN = G/4) и уходит
            за нижний край вдвое дольше обычного;
          * сверху (fall_v0 = G·rise): свободное падение -- шар приходит
            сверху, ровно в бит проходит ay и ПАДАЕТ ДАЛЬШЕ вниз за экран.
        """
        self.x, self.y = self.pos_at(now)

    def radius_at(self, now):
        """Радиус: растёт на взлёте, у пика -- полный (и хитбокс, и диск)."""
        grow = min(1.0, max(0.0,
                            (now - self._born) / max(self.rise, 0.05)))
        return SPHERE_R * (0.30 + 0.70 * grow)

    def on_screen(self, now):
        """Сфера <в игре> -- пока не ушла за экран дальше CULL_MARGIN (+/-100).

        Раньше окно было +/-45 px (= радиус), из-за чего у самых краёв шар
        становился неуязвимым. Теперь резать можно и едва показавшийся, и
        улетающий край -- мёртвых зон нет.
        """
        self.update_pos(now)
        m = CULL_MARGIN
        return (-m < self.y < self.screen_h + m
                and -m < self.x < self.screen_w + m)

    def sliceable(self, now):
        """Резать можно всё время, пока сфера видна: взлёт/пик/падение."""
        if self._pin is not None:
            return False        # витрина: демо-фигуру не режем
        if self.dead or now < self._born:
            return False
        return self.on_screen(now)

    def cube_yaw(self, now):
        """Поворот кубика вокруг вертикали (yaw) -- СВОЙ у каждого куба.

        Стартовый угол _yaw0 случайный, направление и скорость вращения тоже
        случайные: часть кубов крутится влево, часть вправо, и каждый со своей
        скоростью. Единый источник и для фигуры (star_state), и для раскола:
        осколки рождаются с той же ориентацией, что игрок видел в момент реза.
        """
        return (self._yaw0 + now * self._yaw_v) % (2.0 * math.pi)

    def cube_pitch(self, now):
        """Наклон кубика (pitch): качание ВВЕРХ-ВНИЗ вокруг базового CUBE_TILT.

        Амплитуда и направление качания случайные: одни кубы задираются вверх,
        другие опускаются вниз, каждый со своей скоростью. Ниже 0.10 рад не
        уходим -- куб всегда чуть развёрнут, а не «плашмя» экрану.
        """
        p = CUBE_TILT + self._pitch_amp * math.sin(now * self._pitch_v)
        return max(0.10, p)

    def star_state(self, now):
        """Элемент для fx.set_stars(): (x, y, r_px, a1, fade, shape, a2, rgb[,
        grid]).

        Звезда: shape = SHAPE_GENESIS_STAR, a1 = тип палитры Genesis,
        rgb = None (цвет берётся из палитры типа в шейдере).
        Куб:    shape = SHAPE_CUBE_WIRE, a1 = yaw (вращение), a2 = наклон,
        rgb = цвет фигуры, grid = размер решётки (у фигуры всегда 3).
        Вода:  shape = SHAPE_WATER, a1 = фаза волны (своя у каждого
        пузыря -- контур <плещет> по-разному), rgb = цвет воды.
        """
        if self.died_at is not None:
            # фигура исчезает МОМЕНТАЛЬНО (fade=0) -- сразу рисуется раскол
            fade = 0.0
        else:
            t = min(1.0, max(0.0, (now - self._born) / 0.5))
            fade = t * t * (3.0 - 2.0 * t)
        r = self.radius_at(now)
        if self.figure == 'water':
            # жидкость: форма живёт во времени (шейдер), здесь -- своя фаза
            return (self.x, self.y, r, self._phase, fade,
                    SHAPE_WATER, 0.0, self.rgb)
        if self.figure == 'cube':
            # вращение СВОЁ у каждого куба: yaw влево/вправо + pitch вверх/вниз
            return (self.x, self.y, r, self.cube_yaw(now), fade,
                    SHAPE_CUBE_WIRE, self.cube_pitch(now), self.rgb,
                    float(self.grid))
        if self._custom_rgb:
            # цвет прогрессии мелодии: a1 = STAR_CUSTOM -- шейдер читает
            # v_color.rgb вместо палитры типа (0..5)
            return (self.x, self.y, r, STAR_CUSTOM, fade,
                    SHAPE_GENESIS_STAR, 0.0, self.rgb)
        return (self.x, self.y, r, self.star_type, fade,
                SHAPE_GENESIS_STAR, 0.0, None)

    def should_remove(self, now):
        """Убираем ТОЛЬКО когда сфера вышла из окна реза (on_screen = False).

        Единственный источник границ -- on_screen(now) с CULL_MARGIN (+/-100):
        нигде больше координаты не обрубаются жёсткими нулями/краями, поэтому
        шар у самой кромки экрана и режется, и остаётся в игре.
        """
        if now <= self.hit_time:               # ещё летит к апексу
            return False
        if self._pin is not None:
            return False            # витрина: демо-фигура висит вечно
        return not self.on_screen(now)




class BevelLabel(Widget):
    """Шейдерный неон: белый текст как маска, неоновый ореол цвета u_neon_color.

    font_size фиксирован (по умолчанию 60) и НЕ анимируется.
    impact() бьёт через масштаб + прозрачность (scale / opacity); весь неон
    гаснет синхронно через widget.opacity -> u_opacity uniform шейдера.
    Текстура текста — белая маска (CoreLabel), цвет неона задаётся uniform
    u_neon_color (цвет последней сбитой сферы), ядро остаётся чисто белым.
    """

    scale = NumericProperty(1.0)
    font_size = NumericProperty(60.0)   # фиксированный большой кегль
    neon_color = ListProperty([1.0, 1.0, 1.0])

    def __init__(self, text='', rgb=(1.0, 1.0, 1.0), font=60,
                 bold=True, italic=True, glow=True, **kwargs):
        super().__init__(**kwargs)
        # Холст заменяем на RenderContext со своим фрагментным шейдером.
        self.canvas = RenderContext(
            vs=BEVEL_VS, fs=BEVEL_FS,
            use_parent_projection=True,
            use_parent_modelview=True,
        )
        self._text = str(text)
        self._bold = bool(bold)
        self._italic = bool(italic)
        self.font_size = float(font)    # рендер шрифта; задаётся через параметр font=
        self._mesh = None
        self._tex = None
        self._scale_instr = None
        self.neon_color = list(rgb[:3])
        self._build()
        self.bind(opacity=self._on_opacity)
        self.bind(scale=self._apply_scale)
        self.bind(neon_color=self._on_neon_color)
        self.bind(pos=self._trigger_layout, size=self._trigger_layout)

    # --- содержимое ---------------------------------------------------------
    def set_text(self, text):
        text = str(text)
        if text == self._text:
            return
        self._text = text
        self._build()

    def set_rgb(self, rgb):
        rgb = tuple(rgb[:3])
        if getattr(self, '_rgb', None) == rgb:
            return
        self._rgb = rgb
        self.neon_color = list(rgb)     # uniform u_neon_color -> шейдер

    # --- uniform-ы шейдера --------------------------------------------------
    def _on_opacity(self, _inst, value):
        self.canvas['u_opacity'] = float(value)
        self.canvas.ask_update()

    def _on_neon_color(self, _inst, value):
        self.canvas['u_neon_color'] = (
            float(value[0]), float(value[1]), float(value[2]))
        self.canvas.ask_update()

    def _apply_scale(self, *_a):
        if self._scale_instr is not None:
            self._scale_instr.x = self._scale_instr.y = float(self.scale)
            self.canvas.ask_update()

    def _trigger_layout(self, *args):
        """Пересобирает Mesh при изменении pos/size виджета."""
        self._build()

    def impact(self, _dt=0):
        """Удар: текст ВЗРЫВАЕТСЯ масштабом и затухает через opacity.

        font_size не меняется (фиксирован 60). Анимация идёт через scale
        (1.0 -> 1.45 -> 1.0) и opacity (1.0 -> 0.0), все слои гаснут
        синхронно через widget.opacity. К концу life=PERFECT_TEXT_T надпись
        полностью прозрачна и удаляется float_text()'ом.
        """
        self.scale = 1.0
        self.opacity = 1.0
        hit = Animation(scale=1.15, duration=0.08, t='out_quad')
        fade = Animation(scale=1.0, opacity=0.0,
                         duration=PERFECT_TEXT_T - 0.08, t='in_out_quad')
        (hit + fade).start(self)

    # --- построение текстуры-маски + один quad ------------------------------
    def _build(self):
        """Рендерим текст в белую текстуру-маску и собираем один quad.

        Единственный слой: белое ядро + неоновый ореол (шейдер). Цвет неона —
        u_neon_color (цвет последней сбитой сферы), ядро — чисто белый.
        """
        self.canvas.clear()
        self._scale_instr = None
        self._mesh = None
        lb = CoreLabel(text=self._text, font_size=self.font_size,
                       bold=self._bold, italic=self._italic)
        lb.refresh()
        tex = lb.texture
        self._tex = tex
        tw, th = (float(v) for v in tex.size)
        pad = max(4.0, self.font_size * 0.10)
        self.size = (tw + 2.0 * pad, th + 2.0 * pad)
        w, h = (float(v) for v in self.size)
        cx, cy = w * 0.5, h * 0.5
        verts = [
            0.0, 0.0,   0.0, 1.0,   1.0, 1.0, 1.0, 1.0,
            w,   0.0,   1.0, 1.0,   1.0, 1.0, 1.0, 1.0,
            w,   h,     1.0, 0.0,   1.0, 1.0, 1.0, 1.0,
            0.0, h,     0.0, 0.0,   1.0, 1.0, 1.0, 1.0,
        ]
        indices = [0, 1, 2, 0, 2, 3]
        fmt = [
            (b'a_pos', 2, 'float'),
            (b'a_uv', 2, 'float'),
            (b'a_color', 4, 'float'),
        ]
        with self.canvas:
            PushMatrix()
            Translate(self.x, self.y)   # привязка к реальной позиции виджета
            Translate(cx, cy)
            self._scale_instr = Scale(self.scale, self.scale, 1.0)
            Translate(-cx, -cy)
            self._mesh = Mesh(
                vertices=verts, indices=indices,
                fmt=fmt, mode='triangles',
            )
            self._mesh.texture = tex
            PopMatrix()
        self._apply_scale()
        self._on_opacity(None, self.opacity)
        self._on_neon_color(None, self.neon_color)
        self.canvas['u_texture_size'] = (tw, th)
        self.canvas['u_glow_radius'] = 4.0
        self.canvas['u_glow_strength'] = 1.3
        self.canvas.ask_update()
# ===================== шейдеры шейдерного неона BevelLabel ====================
# Вершинный шейдер: повторяет конвенцию проекта (effects.py) — $HEADER$,
# a_pos / a_uv / a_color, projection_mat * modelview_mat.
BEVEL_VS = '''$HEADER$
attribute vec2 a_pos;
attribute vec2 a_uv;
attribute vec4 a_color;

varying vec2 v_uv;
varying vec4 v_color;

void main(void) {
    v_uv = a_uv;
    v_color = a_color;
    gl_Position = projection_mat * modelview_mat * vec4(a_pos, 0.0, 1.0);
}
'''

# Фрагментный шейдер: текст — чисто белая маска (текстура CoreLabel), вокруг
# которой неоновый ореол цвета u_neon_color, плавно затухающий к краям.
# u_opacity (widget.opacity) гасит ВСЁ синхронно (ядро + ореол).
BEVEL_FS = '''$HEADER$
uniform vec3 u_neon_color;      // цвет неона (цвет последней сбитой сферы)
uniform float u_opacity;        // прозрачность всего виджета (синхронное затухание)
uniform vec2 u_texture_size;    // размер текстуры текста, px
uniform float u_glow_radius;    // радиус неонового ореола, px
uniform float u_glow_strength;  // яркость ореола

varying vec2 v_uv;
varying vec4 v_color;

void main(void) {
    vec2 px = 1.0 / u_texture_size;
    float r = u_glow_radius;

    // альфа в центре фрагмента
    vec4 tc = texture2D(texture0, v_uv);
    float a = tc.a * v_color.a;

    // выборка восьми соседей по радиусу r -> ореол за пределами глифа
    float near = (
        texture2D(texture0, v_uv + vec2(-r, -r) * px).a +
        texture2D(texture0, v_uv + vec2(0.0, -r) * px).a +
        texture2D(texture0, v_uv + vec2(r, -r) * px).a +
        texture2D(texture0, v_uv + vec2(-r, 0.0) * px).a +
        texture2D(texture0, v_uv + vec2(r, 0.0) * px).a +
        texture2D(texture0, v_uv + vec2(-r, r) * px).a +
        texture2D(texture0, v_uv + vec2(0.0, r) * px).a +
        texture2D(texture0, v_uv + vec2(r, r) * px).a
    ) / 8.0;

    if (a <= 0.0 && near <= 0.0) {
        discard;
    }

    // ядро: чисто белое внутри букв
    float core = smoothstep(0.30, 1.0, a);
    // ореол: неон вокруг текста (включая зону за пределами глифа)
    float halo = smoothstep(0.0, 0.5, near) * (1.0 - core);
    halo = max(halo, smoothstep(0.15, 0.9, a) * (1.0 - core));

    vec3 col = vec3(1.0) * core + u_neon_color * halo * u_glow_strength;
    float outA = max(a, halo * 0.5);
    gl_FragColor = vec4(col, outA) * vec4(1.0, 1.0, 1.0, u_opacity);
}
'''

class PerfectBar(Widget):
    """Полоска из PERFECT_BAR_N ячеек вверху экрана -- счётчик PERFECT-ов.

    value -- заполненность 0..PERFECT_BAR_N (дробная: ячейка заполняется
    настолько, насколько может). Каждый PERFECT добавляет целую ячейку; когда
    бар полон -- включается INCREDIBLE и бар УТЕКАЕТ, показывая остаток
    усиленного режима (цвет при этом жёлтый).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.value = 0.0
        self.fill = PERFECT_RGB
        self._track = []          # (Color, RoundedRectangle) -- пустая ячейка
        self._fill = []           # (Color, RoundedRectangle) -- заливка ячейки
        with self.canvas:
            for _i in range(PERFECT_BAR_N):
                self._track.append((Color(1, 1, 1, 0.13),
                                    RoundedRectangle(radius=[7.0, 7.0])))
                self._fill.append((Color(1, 1, 1, 0.9),
                                   RoundedRectangle(radius=[7.0, 7.0])))
        self.bind(pos=self._redraw, size=self._redraw)

    def set_value(self, value, color=None):
        self.value = min(max(float(value), 0.0), float(PERFECT_BAR_N))
        if color is not None:
            self.fill = color
        self._redraw()

    def _redraw(self, *_a):
        if self.width <= 0.0 or self.height <= 0.0:
            return
        n = PERFECT_BAR_N
        rad = self.height * 0.5      # стадион: круглые торцы у каждой ячейки
        gap = self.width * PERFECT_BAR_GAP
        cw = (self.width - gap * (n - 1)) / n
        for i in range(n):
            # value -- ЧИСЛО заполненных ячеек: каждый PERFECT = ровно одна
            frac = min(max(self.value - i, 0.0), 1.0)
            x = self.x + i * (cw + gap)
            tcol, trect = self._track[i]
            trect.radius = [rad, rad]
            trect.pos = (x, self.y)
            trect.size = (cw, self.height)
            fcol, frect = self._fill[i]
            frect.radius = [rad, rad]
            frect.pos = (x, self.y)
            frect.size = (cw * frac, self.height)
            fcol.rgba = (self.fill[0], self.fill[1], self.fill[2],
                         0.9 if frac > 0.0 else 0.0)


class PauseMenu(FloatLayout):
    """Пауза: ПОЛНОЭКРАННЫЙ слой затемнения rgba(0,0,0,0.65) + строго
    центральная неоновая панель (PAUSE_W×PAUSE_H, pos_hint 0.5/0.5).

    На canvas.before САМОЙ панели: глубокий полупрозрачный ЧЁРНЫЙ фон
    (0,0,0,0.55) + неоновая рамка PERFECT_RGB -- сферы на поле слегка
    просвечивают сквозь панель (эффект глубины), а чёрный не мешает
    чтению. Контент: show_main() и show_sound() (кнопка
    «НАСТРОЙКИ ЗВУКА» переключает слой поверх паузы).
    """

    def __init__(self, on_resume=None, on_settings=None, on_main_menu=None,
                 on_quit=None, on_volume_delta=None, on_volume_get=None,
                 on_volume_set=None, on_vibration_get=None,
                 on_vibration_set=None, **kwargs):
        super().__init__(**kwargs)
        self.on_resume = on_resume
        self.on_settings = on_settings
        self.on_main_menu = on_main_menu
        self.on_quit = on_quit
        self._on_volume_delta = on_volume_delta or (lambda _d: None)
        self._on_volume_get = on_volume_get or (lambda: 0.8)
        self._on_volume_set = on_volume_set or (lambda _v: None)
        self._on_vibration_get = on_vibration_get or (lambda: True)
        self._on_vibration_set = on_vibration_set or (lambda _a: None)
        self._vol_lbl = None
        self.size_hint = (1, 1)          # ВЕСЬ экран = слой затемнения

        # --- затемнение фона: глубокий чёрный rgba(0, 0, 0, 0.65) на весь
        # экран -- игровой процесс на фоне мягко приглушается ---
        with self.canvas.before:
            Color(0, 0, 0, 0.65)
            self._dim = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_dim, size=self._sync_dim)

        # --- центральная панель: фикс. размер + строго по центру экрана ---
        self._panel = BoxLayout(orientation='vertical', size_hint=(None, None),
                                size=(PAUSE_W, PAUSE_H),
                                pos_hint={'center_x': 0.5, 'center_y': 0.5},
                                padding=28, spacing=PAUSE_BTN_GAP)
        # фон панели + неоновая рамка ЖИВУТ НА ПАНЕЛИ -> всегда симметрично
        # её обрамляют. Фон -- глубокий полупрозрачный ЧЁРНЫЙ: сферы поля
        # слегка просвечивают (глубина), но текст читается идеально.
        with self._panel.canvas.before:
            Color(0, 0, 0, 0.55)
            self._bg_rect = RoundedRectangle(pos=self._panel.pos,
                                             size=self._panel.size,
                                             radius=[16.0, 16.0, 16.0, 16.0])
            # яркая неоновая линия вплотную к чёрной панели
            Color(*PERFECT_RGB, 1.0)
            self._border = Line(width=2, rounded_rectangle=(
                self._panel.x, self._panel.y,
                self._panel.width, self._panel.height, 16.0))
        self._panel.bind(pos=self._sync_border, size=self._sync_border)
        self.add_widget(self._panel)
        self.show_main()
        print('[SoundGrip] PauseMenu NEON v3 активна (%s): панель rgba(0,0,0,0.55), '
              'кнопки CAPS bold+italic 24 PERFECT_RGB, ПАУЗА 52 белая'
              % __file__)

    def _sync_dim(self, *_a):
        """Тёмный оверлей (0,0,0,0.65) всегда ровно по размеру экрана."""
        self._dim.pos = self.pos
        self._dim.size = self.size

    def _sync_border(self, *_a):
        """Подложка и неоновая рамка следуют за панелью ОДНОВРЕМЕННО."""
        self._bg_rect.pos = self._panel.pos
        self._bg_rect.size = self._panel.size
        self._border.rounded_rectangle = (
            self._panel.x, self._panel.y,
            self._panel.width, self._panel.height, 16.0)

    def _mk(self, text, cb, font=24):
        """Неоновая кнопка: ЗАГЛАВНЫЕ + bold + italic, цвет неон-голубой.

        Отклик нажатия: краткое гашение альфа-канала (1.0 -> 0.45),
        на отпускании кнопка вспыхивает обратно и срабатывает колбэк.
        """
        b = Button(text=text.upper(), size_hint=(1, None), height=PAUSE_BTN_H,
                   background_normal='', background_down='',
                   background_color=(0, 0, 0, 0),
                   color=COL_NEON_CYAN, font_size=font,
                   bold=True, italic=True)
        b.bind(on_press=lambda *_a: setattr(b, 'opacity', 0.45))
        b.bind(on_release=lambda *_a: setattr(b, 'opacity', 1.0))
        if cb is not None:
            b.bind(on_release=lambda *a: cb())
        return b

    def show_main(self):
        """Слой 1: огромный белый заголовок ПАУЗА + 4 неоновые кнопки."""
        self._panel.clear_widgets()
        self._panel.add_widget(Label(text='ПАУЗА', size_hint=(1, None),
                                     height=64, font_size=52,
                                     bold=True, italic=True,
                                     color=(1, 1, 1, 1)))
        self._panel.add_widget(self._mk('ПРОДОЛЖИТЬ', self.on_resume))
        self._panel.add_widget(self._mk('НАСТРОЙКИ ЗВУКА', self.on_settings))
        self._panel.add_widget(self._mk('ГЛАВНОЕ МЕНЮ', self.on_main_menu))
        self._panel.add_widget(self._mk('ВЫХОД ИЗ ИГРЫ', self.on_quit))

    def show_sound(self):
        """Слой 2: <Звук> поверх паузы -- уровень + Тише/Громче/Назад."""
        self._panel.clear_widgets()
        self._vol_lbl = Label(text='ГРОМКОСТЬ: %d%%'
                              % round(self._on_volume_get() * 100),
                              size_hint=(1, None), height=64, font_size=32,
                              bold=True, italic=True,
                              color=COL_NEON_CYAN)
        self._panel.add_widget(self._vol_lbl)
        self._panel.add_widget(
            self._mk('ТИШЕ  -', lambda: self._bump(-PAUSE_VOL_STEP)))
        self._panel.add_widget(
            self._mk('ГРОМЧЕ +', lambda: self._bump(+PAUSE_VOL_STEP)))
        self._panel.add_widget(self._mk('<- НАЗАД', self.show_main))

    def _bump(self, delta):
        """±громкость: игра применяет, метка обновляется сразу."""
        self._on_volume_delta(delta)
        if self._vol_lbl is not None:
            self._vol_lbl.text = 'ГРОМКОСТЬ: %d%%' % round(
                self._on_volume_get() * 100)

    def show_sound(self):
        """Слой 2: <Звук> поверх паузы — слайдер громкости, %, вибрация, Назад.

        Выглядит и работает как экран «Звук» из главного меню (интерактивный
        Slider, процент громкости, переключатель Вибрация: ON/OFF; без
        грубых кнопок +/-).
        """
        self._panel.clear_widgets()
        vol = max(0.0, min(1.0, float(self._on_volume_get())))

        # контент, центрированный на плашке паузы (как экран «Звук» в меню)
        content = FloatLayout(size_hint=(1, 1))

        self._vol_lbl = Label(
            text='ГРОМКОСТЬ: %d%%' % round(vol * 100),
            size_hint=(1, None), height=64,
            font_size=32, bold=True, italic=True,
            color=COL_NEON_CYAN,
            pos_hint={'center_x': 0.5, 'center_y': 0.72},
        )
        content.add_widget(self._vol_lbl)

        slider = Slider(
            min=0, max=1, value=vol,
            size_hint=(0.85, None), height=44,
            pos_hint={'center_x': 0.5, 'center_y': 0.63},
        )

        def _on_slider(_instance, value):
            v = max(0.0, min(1.0, float(value)))
            try:
                self._on_volume_set(v)
            except Exception:  # noqa: BLE001
                pass
            if self._vol_lbl is not None:
                self._vol_lbl.text = 'ГРОМКОСТЬ: %d%%' % round(v * 100)

        slider.bind(value=_on_slider)
        content.add_widget(slider)

        vib_enabled = bool(self._on_vibration_get())
        self._vib_lbl = Label(
            text='Вибрация: %s' % ('ON' if vib_enabled else 'OFF'),
            font_size=18, size_hint=(None, None), size=(220, 28),
            color=COL_NEON_CYAN,
            pos_hint={'center_x': 0.5, 'center_y': 0.50},
        )
        content.add_widget(self._vib_lbl)

        def _on_vib(_instance, active):
            try:
                self._on_vibration_set(bool(active))
            except Exception:  # noqa: BLE001
                pass
            if self._vib_lbl is not None:
                self._vib_lbl.text = 'Вибрация: %s' % ('ON' if active else 'OFF')

        vib_switch = Switch(
            active=vib_enabled,
            size_hint=(None, None), size=(66, 34),
            pos_hint={'center_x': 0.5, 'center_y': 0.42},
        )
        vib_switch.bind(active=_on_vib)
        content.add_widget(vib_switch)

        btn_back = self._mk('<- НАЗАД', self.show_main)
        btn_back.size_hint = (0.7, None)
        btn_back.pos_hint = {'center_x': 0.5, 'y': 0.10}
        content.add_widget(btn_back)

        self._panel.add_widget(content)

class GameWidget(FloatLayout):
    """Игровое поле: кик-ноты из JSON, тап, сферы Genesis, HUD."""

    def __init__(self, map_name=None, track=None, volume=0.8,
                 on_menu=None, **kwargs):
        super().__init__(**kwargs)

        # --- то, чем управляет приложение (меню) ---
        self.active = True          # False, пока открыто меню
        self.on_menu = on_menu      # Esc в паузе -> вернуться в меню
        self.is_paused = False      # открыто ли окно паузы (Esc)
        self._pause_menu = None     # виджет окна паузы (создаётся лениво)
        self.volume = max(0.0, min(1.0, float(volume)))

        # --- трек: карта нот + звук (аудио-часы = get_pos) ---
        self.track = None
        self.map_data = {}
        self.map_name = map_name or ''
        self.track_title = ''
        self._map_notes = []
        self._idx = 0                   # указатель спавна по JSON
        # --- жесткая тайм-сетка (tick-based) --------------------------------
        self.bpm = 162.35
        self.beat_duration = 60.0 / self.bpm          # ~0.36957 с (1 бит)
        self.tick_duration = self.beat_duration / 2.0 # ~0.18478 с (1 полубит)
        self.start_timestamp = 0.0                    # точка отсчета game_time
        self.note_pointer = 0                         # указатель по level_notes
        self.ahead_seconds = 0.0                      # время упреждения спавна (с)
        self.ahead_ticks = 0.0                        # то же в тиках
        self.level_notes = []                         # notas с полем "tick"
        self._sound = None
        self._song_path = None          # путь трека для ПОЛНОЙ перезагрузки
        self._seek_supported = False    # поддерживает ли текущий провайдер seek()
        self.real_duration = 0.0        # реальная длина аудиофайла с диска (с)
        self._resume_sync_t = 0.0       # окно жёсткой синхронизации аудио, с
        self._arm_text = ''             # подсказка <ПОЕХАЛИ...> перед стартом
        self._paused_pos = 0.0
        self.pause_time = 0.0           # позиция трека при паузе (для seek)
        self._perf_start = None
        self._audio_offset = _offset_from_argv()
        print('[SoundGrip] синхрон звука: %+d мс (менять клавишами [ / ])'
              % round(self._audio_offset * 1000))

        # --- состояние игры ---
        self.state = 'ready'            # ready/playing/paused/ended/effects
        self._score = 0                 # общий счёт: +SCORE_PER_CUT за фигуру
        self._combo = 0                 # серия попаданий подряд
        self._best_combo = 0
        self._last_cut_rgb = PERFECT_RGB  # цвет последней сбитой фигуры
        self.last_perfect_color = PERFECT_RGB  # RGBA-цвет надписи PERFECT!
        self._nmiss = 0                 # пропущенных фигур за трек
        self._end_lbls = []             # надписи финальной панели
        self._end_canvas = []           # её затемнение (инструкции canvas)
        self._end_score_lbl = None      # крупный итоговый счёт
        self._active = []               # живые FlySphere
        self._last_mouse = None         # позиция мыши прошлого кадра (свайп)
        self._cur_now = None            # игровое время прошлого кадра
        self._force_figure = None       # 'star'/'cube'/'water' -- форс (превью)
        self._fx_clock = 0.0            # виртуальные часы превью эффектов
        self._fx_kind = 'both'          # 'star'/'cube'/'water'/'both'
        # ВЫБОР ФИГУР (экран <Эффекты>): какие типы активны. Синхронизируется
        # с меню; влияет на спавн в игре (pick_figure) -- летят только выбранные.
        # ПЕРВЫЙ ЗАХОД В ИГРУ: всегда выбран ТОЛЬКО один эффект -- сфера-звезда;
        # остальные игрок включает сам через тумблеры <Эффекты>.
        self.active_shapes = ['star']

        # --- descending color series (10 -> 1, then reset) ---
        self.color_series_limit = 10
        self.color_series_counter = 0
        self.current_series_color = None
        # --- псевдо-рандом цветов (логика Dota 2): история <когда цвет
        # последний раз звучал>. Чем больше разрыв (color_pick_no - last_seen),
        # тем выше приоритет цвета при следующем выборе.
        self.color_last_seen = {}  # цвет (tuple) -> номер последнего выбора
        self.color_pick_no = 0     # сколько НОВЫХ цветов выбрано за трек
        self._demos = {}                # демо-фигуры витрины (Эффекты): kind -> FlySphere
        # --- бар PERFECT-ов и усиленный режим INCREDIBLE ---------------------
        self._bar = 0.0                # заполненность бара, 0..PERFECT_BAR_N
        self._buff_t = 0.0             # остаток INCREDIBLE, с
        self._mult_lbl = None          # плавающая надпись серии (не рисуется)
        self._bar_w = PerfectBar(size_hint=(None, None))   # не растягивать
        self.add_widget(self._bar_w)                       # по размеру экрана

        # --- ОЧКИ сверху: счёт растёт на SCORE_PER_CUT за каждую фигуру ------
        self._score_lbl = BevelLabel(text='0', rgb=SCORE_RGB,
                                     font=SCORE_FONT, size_hint=(None, None))
        self.add_widget(self._score_lbl)
        self.bind(size=self._layout_bar, pos=self._layout_bar)
        self._layout_bar()

        self.fx = ParticleFx(size_hint=(1, 1))
        self.add_widget(self.fx)

        # --- HUD: старт-подсказка (в момент игры ничего не рисуем --        #
        # игра начинается с пустого экрана, показываем только поле)           #

        # Подпись синхрона (<синхрон +50 мс [ / ] -- подстройка>) УБРАНА по
        # просьбе игрока: цифры в углу только отвлекали. Сами клавиши [ / ]
        # продолжают двигать латентность, просто молча.

        # Клавиши игры. Window может отсутствовать (headless/тесты) -- тогда
        # просто не на кого вешать обработчик, игра всё равно жива.
        if Window is not None:
            try:
                Window.bind(on_key_down=self._on_key)
            except Exception:                   # noqa: BLE001 -- не роняем
                pass


        # --- выбрать трек: из меню (track) либо по имени карты (CLI/тесты) ---
        self.load_track(track if track is not None
                        else _track_for_map(self.map_name or None))

    # --- аудио: единый бэкенд Kivy SoundLoader (ПК + Android) ---------------
    def _audio_load(self, path):
        """Загрузить трек через Kivy SoundLoader.

        Длительность берём из sound.length, чтобы не доверять битой карте JSON.
        Определяет, поддерживает ли текущий аудио-провайдер seek()
        (ffpyplayer — да, SDL2 для mp3 — нет).
        """
        self._song_path = path
        self._sound = SoundLoader.load(path) if path else None
        self._seek_supported = False
        if self._sound is not None:
            # fffpyplayer регистрируется в SoundLoader, поэтому если он
            # установлен, SoundLoader.load() вернёт SoundFFPy для поддерживаемых
            # форматов. SDL2 не поддерживает seek для mp3.
            self._seek_supported = type(self._sound).__name__ == 'SoundFFPy'
            try:
                self.real_duration = float(self._sound.length)
            except Exception:                       # noqa: BLE001
                self.real_duration = float(
                    self.map_data.get('duration') or 0.0)
            self.set_volume(self.volume)
            prov = type(self._sound).__name__
            print('[SoundGrip] звук: %s -- (%s)' % (prov, os.path.basename(path)))
            if not self._seek_supported and path.lower().endswith('.mp3'):
                print('[WARN] MP3 без fffpyplayer: seek() не перематывает '
                      '(SDL2 ограничение). При паузе/резюме трек сбросится в 0.0. '
                      'Решение: установить fffpyplayer или конвертировать трек в WAV.')
        else:
            self.real_duration = float(
                self.map_data.get('duration') or 0.0)
            print('[SoundGrip] трек не загрузился -- играем по таймеру (без звука)')

    def _audio_pause(self):
        """Пауза: сохраняем позицию и останавливаем звук (без выгрузки из памяти)."""
        if self._sound is not None:
            try:
                pos = self._sound.get_pos()
            except Exception:                   # noqa: BLE001
                pos = 0.0
            self._paused_pos = pos if (pos and pos > 0.0) else float(self.pause_time)
            try:
                self._sound.stop()
            except Exception:                   # noqa: BLE001
                pass
            print(f'[PAUSE] стоп на {self._paused_pos:.2f} сек')
        else:
            self._paused_pos = float(self.pause_time)

    def _audio_unpause(self):
        """Продолжить трек с той же секунды (без перезагрузки файла с диска)."""
        if self._song_path is None:
            return False
        try:
            if self._sound is None:
                self._sound = SoundLoader.load(self._song_path)
            if self._sound is not None:
                self.set_volume(self.volume)
                self._sound.play()
                # SDL2/Kivy игнорирует seek() в нулевой кадр сразу после play(),
                # поэтому перемотку откладываем на 120 мс.
                Clock.schedule_once(
                    lambda _dt: self._do_seek_after_play(), 0.12)
            self._resume_sync_t = 2.0
            return True
        except Exception:                       # noqa: BLE001
            return False

    def _do_seek_after_play(self):
        """Перемотка после инициализации аудио-буфера SDL2/ffpyplayer."""
        if self._sound is not None:
            try:
                self._sound.seek(self._paused_pos)
                self._sound.volume = self.volume
                fact = self._sound.get_pos()
                print(f'[RESUME] перемотка на {self._paused_pos:.2f} сек, факт={fact:.2f}')
                if not self._seek_supported and abs(fact - self._paused_pos) > 0.5:
                    print('[WARN] Seek не сработал точно (фактически %.2f вместо %.2f). '
                          'MP3 через SDL2 не поддерживает seek. '
                          'Установите fffpyplayer или используйте WAV.' % (fact, self._paused_pos))
            except Exception:                   # noqa: BLE001
                pass

    # --- трек -------------------------------------------------------------------
    def load_track(self, track):
        """Выбрать трек: карта нот + звук + длительность.

        track -- из меню <Выбор мелодии>. Без него берём карту по имени
        (python main.py --map=backinblack) с WAV из SONGS.
        """
        self._stop_sound()
        self._sound = None
        self.track = track
        if track is None:
            name = self.map_name or _map_from_argv()
            self.map_data = load_map(name)
            self.map_name = name
            self.track_title = name
            song_path = _sound_path_for(name)
        else:
            data = track.load_map()
            # карты нет (не собралась) -- играем по запасной, без анализа
            self.map_data = data or tracklib.demo_map(track.map_name)
            self.map_name = track.map_name
            self.track_title = track.title
            song_path = track.path
        self._map_notes = self.map_data.get('notes', [])
        # --- ДИНАМИЧЕСКИЙ BPM из метаданных карты ---------------------------
        self.bpm = float(self.map_data.get('bpm', 162.35))
        if not self.bpm or self.bpm <= 0:
            self.bpm = 162.35  # дефолт спасения при битом JSON
        self.beat_duration = 60.0 / self.bpm
        self.tick_duration = self.beat_duration / 2.0
        # Защита от деления на ноль перед конвертацией нот
        if not getattr(self, 'tick_duration', 0) or self.tick_duration == 0:
            self.bpm = 162.35
            self.beat_duration = 60.0 / self.bpm
            self.tick_duration = self.beat_duration / 2.0
        # Конвертируем ноты в tick-based сетку с защитой типов данных
        self.level_notes = []
        # --- ИНТЕЛЛЕКТУАЛЬНЫЙ ФИЛЬТР: МЕЛОДИЯ ПЕРВОЙ -----------------------
        # Track notes by kind: 'melody' for leading melody, 'kick'/ 'snare' for rhythm
        # During melody intervals, only keep melody notes
        # When melody goes silent for 1.5-2s, allow rhythm section
        # When melody returns, mute bass again
        self._melody_silent_until = 0.0          # when melody went silent
        self._melody_active = False              # is melody currently active
        self._melody_timeout = 1.8               # seconds until rhythm allowed
        
        for note in self._map_notes:
            try:
                t = float(note.get('t', 0.0))
                # Если карта старая (в секундах), переводим в тики.
                # Если уже новая — берем готовый 'tick'
                tick_val = note.get('tick', t / self.tick_duration)
                note_data = {
                    'tick': float(tick_val),
                    't': t,
                    'kind': note.get('kind', 'onset'),
                }
                # Apply melody-first filter
                note_kind = note_data['kind']
                # Check if this is a melody note
                is_melody = note_kind in ('melody', 'onset')
                
                if is_melody:
                    # Melody note - always include, reset silent timer
                    self.level_notes.append(note_data)
                    self._melody_active = True
                    self._melody_silent_until = 0.0
                else:
                    # Rhythm note (kick, snare, etc.)
                    if self._melody_active:
                        # Melody is active - include rhythm notes but they'll
                        # be filtered during spawning based on melody status
                        self.level_notes.append(note_data)
                    else:
                        # Melody is not active, include rhythm notes
                        self.level_notes.append(note_data)
                        
            except Exception as e:
                print('[ERROR] Ошибка парсинга ноты: %s' % e)
        # Apply audio offset compensation (0.05s for Kivy Android buffer)
        self._audio_offset = DEFAULT_AUDIO_OFFSET  # 0.05s underrun compensation
        # --- ДИНАМИЧЕСКИЙ BPM из метаданных карты ---------------------------
        self.bpm = float(self.map_data.get('bpm', 162.35))
        try:
            self._audio_load(song_path)
        except Exception as e:  # noqa: BLE001
            print('[SoundGrip] SoundLoader не смог: %s' % e)
        if self._sound is None:
            print('[SoundGrip] трек не загрузился -- играем по таймеру '
                  '(без звука)')
        print('[SoundGrip] трек: %s | нот: %d | %.0f с'
              % (self.track_title, len(self._map_notes),
                 float(self.map_data.get('duration') or 0.0)))

    def set_volume(self, v):
        """Громкость музыки (0..1) -- из пункта меню <Звук> / панели паузы."""
        self.volume = max(0.0, min(1.0, float(v)))
        if self._sound is not None:
            try:
                self._sound.volume = self.volume
            except Exception:                       # noqa: BLE001
                pass

    def _stop_sound(self):
        """Полная остановка музыки. Ошибки звука не роняют игру."""
        if self._sound is not None:
            try:
                self._sound.stop()
            except Exception:                       # noqa: BLE001
                pass

    def stop_playback(self):
        """Полный сброс звука, поля и СОСТОЯНИЯ: меню, превью, возврат.

        state обязан вернуться к 'ready': иначе после <Эффектов> / паузы /
        финиша он залипает (effects/ended/paused), start_track() выходит рано
        -- игра стартует без звука, а в режиме effects сферы режутся сами.
        """
        self._stop_sound()
        self._paused_pos = 0.0
        self.pause_time = 0.0
        self._perf_start = None
        self._cur_now = None            # путь сферы заново, без хвоста кадра
        self._last_mouse = None         # след лезвия не тянется через паузу
        self._fx_clock = 0.0            # часы превью эффектов с нуля
        for s in list(self._active):
            self._remove(s)
        self._active = []
        self.fx.set_stars([])
        self.hide_pause_menu()          # окно паузы -- прочь
        self.is_paused = False
        self.state = 'ready'

    # --- аудио-часы -----------------------------------------------------------
    def audio_now(self):
        """Время трека, с: гибридные часы.

        База -- стена (perf_counter), синхрон на старте/продолжении.
        Sound.get_pos() под SDL2 для WAV может ЗАСТРЯТЬ на нуле (проверено
        пробником), поэтому доверяем ему только если он идёт (gp > 0) и не
        разошёлся со стеной дальше 2 с. Иначе -- стена.
        """
        if self.state == 'effects':
            return self._fx_clock       # превью идёт без музыки: свои часы
        if self.state == 'playing' or self.state == 'ended':
            if self._perf_start is None:
                self._perf_start = time.perf_counter()
            wall = self._paused_pos + (time.perf_counter() - self._perf_start)
            if self._sound is not None:
                try:
                    gp = float(self._sound.get_pos())
                    if gp > 0.0 and abs(gp - wall) < 2.0:
                        return gp
                except Exception:                   # noqa: BLE001
                    pass
            # звук слышен с задержкой латентности -> часы отстают ровно на неё,
            # кольцо дожимается в момент, когда кик звучит в треке
            return max(0.0, wall - self._audio_offset)
        return self._paused_pos

    # --- старт / пауза --------------------------------------------------------
    def start_track(self):
        # Остановить зацикленную музыку меню, если вдруг играла,
        # чтобы она не накладывалась на игровой трек.
        try:
            app = App.get_running_app()
            if app._menu_sound is not None:
                app._menu_sound.stop()
        except Exception:                       # noqa: BLE001
            pass

        if self.state == 'paused':
            if self._audio_unpause():
                # Kivy-ветка: seek выполнен в _audio_unpause,
                # жёсткая синхронизация в update() подправит позицию
                pass
            # игровое время/таймер спавна НЕ сбрасываем (_idx/_map_notes
            # не трогаем, стена продолжает от _paused_pos)
            self._perf_start = time.perf_counter()
            self.start_timestamp = self._perf_start
            self.state = 'playing'
            return
        if self.state in ('effects', 'ended'):
            # страховка от залипшего состояния (превью эффектов / финиш):
            # без неё старт из меню молча выходит -- игра идёт без звука,
            # а в режиме effects сферы режутся сами (_effects_autoslice)
            self.reset()
        if self.state != 'ready':
            return
        # ЖЁСТКИЙ сброс новой партии: счёт/комбо/бар всегда с нуля.
        # stop_playback (выход в меню из паузы) поле чистит, но НЕ обнуляет
        # очки -- иначе новая игра стартовала бы со старым счётом/серией.
        # reset() идемпотентен, зовём и из effects/ended -- дубль без вреда.
        self.reset()
        self._perf_start = time.perf_counter()
        self.start_timestamp = self._perf_start
        self._paused_pos = 0.0
        if self._sound is not None:
            try:
                self._sound.play()
            except Exception:                       # noqa: BLE001
                pass
        self.state = 'playing'

    def toggle_pause(self):
        if self.state == 'playing':
            # 1) намертво фиксируем точное время ИГРОВОГО таймера --
            #    источник истины для спавна/хитбоксов
            self.pause_time = float(self.audio_now())
            self._paused_pos = self.pause_time
            # 2) Kivy Sound.stop() — позицию восстановим на резюме (reload + seek)
            #    движка (WAV/MP3/OGG, 100% гарантия); Android/Kivy:
            #    stop+unload -- позиция восстановится на резюме (reload+seek)
            self._audio_pause()
            self.state = 'paused'
            self.is_paused = True
            self.show_pause_menu()
        elif self.state == 'paused':
            self.is_paused = False
            self.hide_pause_menu()
            self._last_mouse = None    # свайп через паузу не тяет след
            self._cur_now = None       # путь сфер -- строго с pause_time
            self.start_track()          # резюм: unpause (ПК) или reload+seek

    def _seek_resume(self, sound):
        """Стартовая позиция свежезагруженного звука (зовётся со следующего
        кадра после play): двигаем на pause_time и печатаем диагностику."""
        if self.state != 'playing' or self.pause_time <= 0.0:
            return
        try:
            sound.seek(self.pause_time)
            gp = float(sound.get_pos())
            print('[PAUSE] резюм: pause_time=%0.1f c | seek -> get_pos=%0.1f c'
                  % (self.pause_time, gp))
        except Exception as e:                      # noqa: BLE001
            print('[PAUSE] резюм: seek недоступен у драйвера (%s)' % e)

    def show_pause_menu(self):
        """Показать окно паузы: полноэкранный слой (затемнение 0.65) с
        центральной неоновой панелью -- центровку держит pos_hint панели,
        руками size/pos не двигаем."""
        if self._pause_menu is None:
            self._pause_menu = PauseMenu(
                on_resume=self.toggle_pause,
                on_settings=self._pause_settings,
                on_main_menu=self._pause_to_main_menu,
                on_quit=self._pause_quit,
                on_volume_delta=self._pause_volume_delta,
                on_volume_get=lambda: self.volume,
                on_volume_set=self._pause_volume_set,
                on_vibration_get=self._pause_vibration_get,
                on_vibration_set=self._pause_vibration_set,
            )
        if self._pause_menu.parent is None:
            self.add_widget(self._pause_menu)

    def hide_pause_menu(self):
        """Убрать окно паузы (возобновление / уход в главное меню)."""
        if self._pause_menu is not None and self._pause_menu.parent is not None:
            self.remove_widget(self._pause_menu)

    def _pause_settings(self):
        """Кнопка «Настройки звука»: открыть слой <Звук> прямо поверх паузы."""
        if self._pause_menu is not None:
            self._pause_menu.show_sound()

    def _pause_volume_set(self, v):
        """Абсолютная громкость из ползунка звука паузы.

        Через App.set_volume(value) значение попадает в game.set_volume
        (текущий трек меняется мгновенно) и в self.volume, которое
        применяется к новому звуку при возобновлении (_audio_unpause).
        """
        v = max(0.0, min(1.0, float(v)))
        try:
            app = App.get_running_app()
        except Exception:  # noqa: BLE001
            app = None
        if app is not None:
            app.set_volume(v)
        else:
            self.set_volume(v)

    def _pause_vibration_get(self):
        try:
            return bool(getattr(App.get_running_app(), 'vibration_enabled', True))
        except Exception:  # noqa: BLE001
            return True

    def _pause_vibration_set(self, active):
        try:
            app = App.get_running_app()
        except Exception:  # noqa: BLE001
            return
        app.vibration_enabled = bool(active)

    def _pause_volume_delta(self, delta):
        """±громкость из панели паузы; синхрон App, чтобы меню <Звук>
        после выхода из игры показало тот же уровень."""
        v = max(0.0, min(1.0, self.volume + float(delta)))
        try:
            from kivy.app import App as _App
            app = _App.get_running_app()
        except Exception:                       # noqa: BLE001
            app = None
        if app is not None:
            app.set_volume(v)                   # App сам зовёт game.set_volume(v)
        else:
            self.set_volume(v)                  # headless/тесты

    def _pause_to_main_menu(self):
        self.hide_pause_menu()
        self.is_paused = False
        if self.on_menu is not None:
            self.on_menu()

    def _pause_quit(self):
        from kivy.app import App as _App
        app = _App.get_running_app()
        if app is not None:
            app.stop()

    def _on_key(self, _win, key, *_a):
        """Клавиши игры. Когда открыто меню, игра молчит (active=False).

        32 Пробел -- старт/продолжить, 27 Esc -- пауза, а из паузы/готовности
        Esc уводит в меню, 122 Z / 120 X -- тап, 91 [ / 93 ] -- синхрон.
        """
        if not self.active:
            return False
        if self.state in ('ready', 'paused') and key == 32:
            self.start_track()
            return True
        if key == 27:
            if self.state in ('playing', 'paused'):
                self.toggle_pause()     # Esc: пауза <-> продолжение (+ меню)
            elif self.on_menu is not None:
                self.on_menu()          # из готовности -- назад в меню
            return True
        if key == 91:
            self._audio_offset = max(0.0, self._audio_offset - 0.01)
            print('[SYNC] латентность звука: %+d мс'
                  % round(self._audio_offset * 1000))
        elif key == 93:
            self._audio_offset += 0.01
            print('[SYNC] латентность звука: %+d мс'
                  % round(self._audio_offset * 1000))
        elif self.state in ('playing', 'effects') and key in (122, 120):
            try:
                wx, wy = Window.mouse_pos
                self._try_tap(float(wx), float(wy))
            except Exception:                   # noqa: BLE001 -- окно без мыши
                pass
        return False

    # --- динамический разгон: фазы и плотность по прогрессу -------------------
    def _progress(self, t):
        """Прогресс трека 0..1 по времени t (длительность из карты)."""
        dur = float(self.map_data.get('duration') or 0.0)
        if dur <= 0.0:
            return 0.0
        return max(0.0, min(1.0, float(t) / dur))

    def _phase_weights(self, p):
        """Веса направлений подачи: снизу всегда, боковые и верх -- разгон."""
        span_side = max(1e-6, PHASE_SIDE_FULL - PHASE_SIDE_IN)
        span_top = max(1e-6, PHASE_TOP_FULL - PHASE_TOP_IN)
        return (1.0,
                _sstep((p - PHASE_SIDE_IN) / span_side),
                _sstep((p - PHASE_TOP_IN) / span_top))

    def _density(self, p):
        """Доля спавнимых нот: от DENSITY_START в начале до 1.0 -- разгон."""
        d = _sstep(p / max(1e-6, DENSITY_FULL_AT))
        return DENSITY_START + (1.0 - DENSITY_START) * d

    def _pick_mode(self, p, rnd=None):
        """Направление подачи по прогрессу p: bottom -> +side -> +top."""
        wb, ws, wt = self._phase_weights(p)
        r = (random.random() if rnd is None else rnd()) * (wb + ws + wt)
        if r < wb:
            return 'bottom'
        if r < wb + ws:
            return 'side'
        return 'top'

    # --- спавн нот из JSON ----------------------------------------------------
    def _spawn_due(self, now):
        """Вычисляет время упреждения спавна (хранит в self.ahead_*).

        Старый цикл спавна перенесен в update() — тик-базисный while.
        """
        h = self.fx._view_h()
        peak = h * TOSS_PEAK + TOSS_PEAK_JITTER        # самый высокий апекс
        drop_b = max(1.0, peak - TOSS_LAUNCH_Y)        # снизу до апекса
        drop_t = max(1.0, (h + TOP_LAUNCH_MARGIN)      # сверху до апекса
                     - (h * TOSS_PEAK - TOSS_PEAK_JITTER))
        # 'top' летит сверху вдвое медленнее (TOP_G_K) => рождается вдвое раньше
        ahead = max(math.sqrt(2.0 * drop_b / TOSS_G),
                    math.sqrt(2.0 * drop_t / (TOSS_G * TOP_G_K))) + 0.05
        self.ahead_seconds = ahead
        self.ahead_ticks = ahead / self.tick_duration

    def _remove(self, s):
        """Убрать сферу и из списка, и из дерева виджетов (осторожно)."""
        if s in self._active:
            self._active.remove(s)
        try:
            if s.parent is not None:
                s.parent.remove_widget(s)
        except Exception:                       # noqa: BLE001
            pass

    def _pick_series_color_dota(self, pool):
        """Взвешенный псевдо-выбор цвета из пула (логика Dota 2).

        Правила:
          * НЕПОВТОРЯЕМОСТЬ: текущий self.current_series_color исключается
            из кандидатов -- один и тот же цвет в двух сериях подряд
            невозможен ни в обычном режиме, ни в Режиме Безумия (лимит 1).
          * ПРИОРИТЕТ ДАВНО НЕ БЫВШИХ: вес цвета = разрыв
            (color_pick_no - last_seen), т.е. сколько выборов он не звучал.
            Среди кандидатов берутся только цвета с МАКСИМАЛЬНЫМ разрывом;
            среди равных -- чистый random, чтобы жизнь не была детерминированной.
        """
        now = self.color_pick_no
        cur = self.current_series_color
        # last_seen = номер выбора, когда цвет звучал; никогда не играл -> -1
        best_gap = -1
        best = []
        for c in pool:
            c = tuple(c)
            if c == cur:
                continue                       # НЕПОВТОРЯЕМОСТЬ подряд
            gap = now - self.color_last_seen.get(c, -1)
            if gap > best_gap:
                best_gap = gap
                best = [c]
            elif gap == best_gap:
                best.append(c)
        if not best:                           # деградация: пул из 1 цвета
            return cur
        pick = random.choice(best)
        self.color_last_seen[pick] = now       # запомнили, когда он звучал
        return pick

    def _next_series_color(self, figure=None):
        """Смена серий: 10 -> 9 -> 8 -> ... -> 1 (Режим Безумия).

        Как только лимит доходит до 1, он остаётся равным 1 ДО КОНЦА ТРЕКА,
        и каждая следующая фигура выбирает новый цвет. Сам цвет выбирает
        _pick_series_color_dota(): без повторов подряд + приоритет тем,
        кто давно не появлялся на экране.
        """
        pool = list(FIGURES.get(figure, FIGURES['star']))

        # самый первый запуск трека -- полный сброс истории
        if self.current_series_color is None:
            self.color_series_limit = 10
            self.color_series_counter = 0
            self.color_last_seen = {}
            self.color_pick_no = 1
            self.current_series_color = self._pick_series_color_dota(pool)
            self.color_series_counter = 1
            return self.current_series_color

        # текущая серия полностью отработала -> спуск лимита + новый цвет
        if self.color_series_counter >= self.color_series_limit:
            self.color_series_counter = 0

            # Уменьшаем лимит, но если дошли до 1 -- жестко держим 1 до конца трека!
            if self.color_series_limit > 1:
                self.color_series_limit -= 1

            self.color_pick_no += 1
            self.current_series_color = self._pick_series_color_dota(pool)

        self.color_series_counter += 1
        return self.current_series_color

    def _distinct_color(self, rgb, figure):
        """При НЕСКОЛЬКИХ активных эффектах: у объектов РАЗНЫХ типов,
        летящих на экране одновременно, не может быть одинакового цвета.

        Если цвет новой фигуры уже занят активной фигурой другого типа --
        заменяем его на другой оттенок из пула этой фигуры (в первую очередь
        не занятый НИКАКИМ активным объектом). Состояние серий
        (current_series_color/limit/counter) не трогаем -- это чисто
        визуальное разведение, сами серии продолжают идти по расписанию.
        Один активный эффект -- проверка не нужна (все объекты одного типа).
        """
        rgb = tuple(rgb)
        if len(self.active_shapes) < 2:
            return rgb                 # один эффект -- серии не трогаем
        taken_other = {tuple(s.rgb) for s in self._active
                       if getattr(s, 'rgb', None) is not None
                       and s.figure != figure}
        if rgb not in taken_other:
            return rgb                 # конфликта нет -- цвет серии остаётся
        pool = [tuple(c) for c in FIGURES.get(figure, FIGURES['star'])]
        taken_all = {tuple(s.rgb) for s in self._active
                     if getattr(s, 'rgb', None) is not None}
        free = ([c for c in pool if c not in taken_all]
                or [c for c in pool if c not in taken_other and c != rgb]
                or [c for c in pool if c != rgb])
        return random.choice(free)

    def _spawn_one(self, n):
        """Создать одну сферу по ноте карты (с вытеснением при переполнении)."""
        while len(self._active) >= MAX_NOTES:
            # вытесняем живую сферу, но не гаснущую -- её растворение короче
            victim = next((c for c in self._active if c.died_at is None),
                          self._active[0])
            self._remove(victim)
        w = self.fx._view_w()
        h = self.fx._view_h()
        # направление подачи по прогрессу трека (динамический разгон)
        mode = self._pick_mode(self._progress(n['t']))
        # апекс чуть выше центра, РОВНО в бит -- такт сохраняется всегда
        ay = h * TOSS_PEAK + random.uniform(-TOSS_PEAK_JITTER,
                                            TOSS_PEAK_JITTER)
        # гравитация полёта: у режима 'top' она мягче (лейт-гейм, вдвое
        # медленнее сверху вниз) -- задаётся ниже в ветке
        g_up, g_down = TOSS_G, TOSS_G_DOWN
        if mode == 'side':
            # дуга с БОКОВОГО нижнего угла через центр на другой край
            side = random.choice((-1.0, 1.0))
            ax = w * 0.5 + random.uniform(-60.0, 60.0)
            launch_y = TOSS_LAUNCH_Y
            rise = math.sqrt(max(0.05, 2.0 * (ay - launch_y) / TOSS_G))
            # горизонтальная скорость -15% (SIDE_SPEED_K): дуга идёт медленнее,
            # но апекс по-прежнему РОВНО в бит -- ритм не меняется
            vx = side * (w * TOSS_TRAVEL * SIDE_SPEED_K) / (2.0 * rise)
        elif mode == 'top':
            # падение СВЕРХУ: старт над экраном, низ дуги -- ровно в бит.
            # ЛЕЙТ-ГЕЙМ: гравитация xTOP_G_K (÷4) => скорость у бита вдвое
            # меньше, а путь над экраном проходится вдвое дольше: объект не
            # <сваливается> на игрока, а степенно падает сверху вниз.
            ax = random.uniform(w * 0.2, w * 0.8)
            launch_y = h + TOP_LAUNCH_MARGIN
            g_up = TOSS_G * TOP_G_K
            g_down = TOSS_G_DOWN * TOP_G_K
            rise = math.sqrt(max(0.05, 2.0 * (launch_y - ay) / g_up))
            vx = random.uniform(-40.0, 40.0)     # почти вертикальный спуск
        else:  # 'bottom' -- вертикальный подброс СНИЗУ, апекс в бит
            ax = random.uniform(TOSS_X_MIN, w - TOSS_X_MIN)
            launch_y = TOSS_LAUNCH_Y
            rise = math.sqrt(max(0.05, 2.0 * (ay - launch_y) / TOSS_G))
            vx = random.uniform(-40.0, 40.0)
        # каждая четвёртая звезда -- доп. палитра Genesis: белая/голубая/фиол.
        star_type = None
        if self._idx % 4 == 3:
            star_type = (2.0, 3.0, 4.0)[(self._idx // 4) % 3]
        # фигура: куб каждая CUBE_EVERY-я, вода каждая WATER_EVERY-я
        # (см. pick_figure); в превью можно форсировать
        figure = pick_figure(self._idx, self._force_figure,
                             self.active_shapes)
        # цвет ДИСКРЕТНО по времени: стадии палитры идут по расписанию
        # COLOR_SCHEDULE (10/7/7/5/5 c), переключение РЫВКОМ, без блендинга,
        # после 34 c -- случайный цвет из пула фигуры. В превью <Эффекты>
        # музыки нет -- считаем от его собственных часов, иначе -- от времени
        # ноты в треке.
        t_col = self._fx_clock if self.state == 'effects' else n['t']
        # цвет серии; при НЕСКОЛЬКИХ активных эффектах _distinct_color
        # разводит цвета разных типов, чтобы не совпадали на экране сразу
        rgb = self._distinct_color(self._next_series_color(figure), figure)
        s = FlySphere(ax, ay, vx, rise, n['t'], n.get('kind', 'onset'),
                      star_type=star_type, screen_w=w, screen_h=h,
                      mode=mode, launch_y=launch_y, figure=figure,
                      g_up=g_up, g_down=g_down,
                      rgb=rgb)
        self._active.append(s)
        self.add_widget(s)

    # --- разрезы: ТАП по сфере или СВАЙП мышкой через неё ---------------------
    def _try_tap(self, cx, cy):
        """Тап по сфере = разрез (для тех, кто не свайпает).

        Берём ближайшую сферу в радиусе её же хитбокса: попадание честное,
        ровно по видимому диску.
        """
        now = self.audio_now()
        best = None
        best_d = 1e9
        for s in list(self._active):
            if not s.sliceable(now):
                continue
            d = math.hypot(s.x - cx, s.y - cy)
            if d <= s.radius_at(now) and d < best_d:
                best = s
                best_d = d
        if best is None:
            return
        print('[HIT] тап: до центра %.1f px (диск %.1f,%.1f) @ %+.0f мс'
              % (best_d, best.x, best.y, (now - best.hit_time) * 1000.0))
        self._slice(best, now)

    def _hb_samples(self, s, t0, t1):
        """Цепочка хитбоксов вдоль пути сферы за интервал [t0, t1]."""
        t0 = max(float(t0), s._born)         # до рождения сэмплов нет
        if t1 < t0:
            t0 = t1                          # нестрелаемое время -> одна точка
        k = self._cut_k()                    # INCREDIBLE: радиус среза x1
        x0, y0 = s.pos_at(t0)
        x1, y1 = s.pos_at(t1)
        yield x0, y0, s.radius_at(t0) * k, t0
        if t1 <= t0:
            return
        # Идём по ПУТИ, а не по времени: скорость на дуге меняется
        # (парабола), равные шаги времени дали бы нерегулярный шаг в px.
        # Аппроксимируем кривую ломаной из 64 звеньев, копим дистанцию и
        # выдаём сэмпл ровно там, где накоплено HB_STEP px; время в точке
        # пересечения интерполируется линейно. Гарантия: расстояние между
        # соседними сэмплами <= HB_STEP.
        nsub = 64
        acc = 0.0
        lx, ly, lt = x0, y0, t0
        for i in range(1, nsub + 1):
            t = t0 + (t1 - t0) * i / nsub
            x, y = s.pos_at(t)
            seg = math.hypot(x - lx, y - ly)
            if seg <= 0.0:
                continue
            while acc + seg >= HB_STEP:
                need = HB_STEP - acc
                kk = need / seg
                tc = lt + (t - lt) * kk
                xc = lx + (x - lx) * kk
                yc = ly + (y - ly) * kk
                yield xc, yc, s.radius_at(tc) * k, tc
                lx, ly, lt = xc, yc, tc
                seg -= need
                acc = 0.0
            acc += seg
            lx, ly, lt = x, y, t
        if acc > 0.0 or lx != x1 or ly != y1:   # хвост: точная текущая поза
            yield x1, y1, s.radius_at(t1) * k, t1

    def _blade_sweep(self, mx, my, now, t0=None, prev=None):
        """Лезвие на отрезке (prev -> текущая точка) за интервал [t0, now].

        В главном цикле это ОДИН ПОДШАГ 200 Гц: prev -- позиция лезвия на
        момент t0, интерполированная между кадрами, поэтому лезвие идёт по
        настоящему пути мыши, а не одной прямой за весь кадр. Если prev=None
        (одиночный мах, тап-тесты) -- берём позицию прошлого кадра и рисуем
        след; в цикле подшагов след ставится один раз за кадр (см. update).

        Лезвие проверяется против цепочки хитбоксов вдоль пути сферы за этот
        интервал (каждые HB_STEP = 5 px). Хитбокс ЧЕСТНЫЙ: ровно радиус сферы
        r = radius_at, без коэффициентов запаса. Рез рождает взрыв и обломки
        СТРОГО в центре сферы (s.x, s.y) на момент суб-тика -- см. _slice.
        """
        if prev is None:
            last = self._last_mouse
            self._last_mouse = (mx, my)
            if last is None or (last[0] == mx and last[1] == my):
                return
            # след лезвия: сегмент движения мыши, живёт TRAIL_LIFE (0.2 с)
            self.fx.add_trail(last[0], last[1], mx, my, life=TRAIL_LIFE,
                              tip=self._trail_tip())
        else:
            last = prev
            if last[0] == mx and last[1] == my:
                return
        sweep_ang = math.atan2(my - last[1], mx - last[0])
        if t0 is None:
            t0 = now                         # нет прошлого времени -> точка
        for s in list(self._active):
            if not s.sliceable(now):
                continue
            for cx, cy, r, ts in self._hb_samples(s, t0, now):
                # ХИТБОКС = ровно визуальный радиус сферы r (radius_at(now)),
                # никаких запасов: лезвие не коснулось диска -- промах
                d = _seg_circle_dist(last[0], last[1], mx, my, cx, cy)
                if d > r + SWIPE_TOL:
                    continue
                print('[HIT] до центра %.1f px @ %+.0f мс (диск %.0f,%.0f '
                      '| лезвие %.0f,%.0f->%.0f,%.0f | r=%.1f)'
                      % (d, (now - s.hit_time) * 1000.0, cx, cy,
                         last[0], last[1], mx, my, r))
                # скорость лезвия в момент удара (px/с) -> инерция осколков кубика
                span = now - t0
                if span > 0.0:
                    vel_x = (mx - last[0]) / span
                    vel_y = (my - last[1]) / span
                else:
                    vel_x = vel_y = 0.0
                self._slice(s, now, angle=sweep_ang, vel_x=vel_x, vel_y=vel_y)
                break

    def _slice(self, sphere, now, angle=None, vel_x=0.0, vel_y=0.0):
        """Разрез: взрыв и обломки -- СТРОГО из центра сферы.

        Никаких <точек реза>: позиция берётся напрямую у сферы, приведённой к
        моменту суб-тика now -- это ровно то, что игрок видит на экране в этот
        миг.

        vel_x / vel_y — скорость мыши/лезвия в момент удара (px/с);
        передаётся в fx.emit_cube как инерция осколков кубика.
        """
        sphere.update_pos(now)      # центр сферы В МОМЕНТ РЕЗА (= как на экране)
        sphere.die(now)
        self._remove(sphere)        # виджет убираем, звезда гаснет в fx
        dt = abs(now - sphere.hit_time)
        power = (1.0 if dt <= HIT_WIN_300
                 else (0.8 if dt <= HIT_WIN_100 else 0.6))
        self._combo += 1
        self._best_combo = max(self._best_combo, self._combo)
        col = _sphere_rgb(sphere)   # цвет эффекта = цвет разрубленной звезды
        self._last_cut_rgb = tuple(col)      # цвет последней сбитой фигуры
        self._add_score(SCORE_PER_CUT)       # очки -- только счёт сверху
        px, py = float(sphere.x), float(sphere.y)   # СТРОГО центр сферы
        r = float(sphere.radius_at(now))            # её радиус в этот кадр
        self._update_mult(px, py, col)              # серия: только счётчик
        if sphere.figure == 'cube':
            try:
                self.fx.emit_cube(px, py, size=r, color=col, power=power,
                                  grid=sphere.grid, yaw=sphere.cube_yaw(now),
                                  pitch=sphere.cube_pitch(now),
                                  vel_x=vel_x, vel_y=vel_y)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[EMIT_CUBE CRASH] x={px} y={py} size={r} power={power} grid={sphere.grid}")
        elif sphere.figure == 'water':
            # пузырь лопается: случайный всплеск струй и капель,
            # крупные капли падают на пол, растекаются и тают
            self.fx.emit_water(px, py, color=col, power=power, radius=r)
        else:
            self.fx.emit_slice(px, py, color=col, power=power, radius=r)
            # ГОРИЗОНТАЛЬНЫЙ срез с уклоном (~200°): половинки разъезжаются
            # в стороны вдоль линии среза, затем падают вниз (гравитация)
            self.fx.slice_halves(px, py, radius=r,
                                 color=col, angle=CUT_ANGLE, power=power)
        if self._combo and self._combo % PERFECT_AT == 0:
            # серия из PERFECT_AT попаданий подряд: волна на весь экран;
            # цветом этой сбитой сферы окрасится и надпись PERFECT!
            self._perfect(px, py, col)

    def float_text(self, text, x, y, rgb=None, font=40, life=None,
                   anchor='center', bold=True):
        """Плавающая надпись в точке (x, y) игрового поля -- универсальный хелпер.

        КОГДА НАДО ПРИВЯЗАТЬ ТЕКСТ К МЕСТУ НА ЭКРАНЕ -- зови этот метод.
        x, y -- координаты РОДИТЕЛЯ (GameWidget): те же, что у сфер и курсора,
        левый-нижний угол = (0, 0). Берутся ПРЯМО от сбитой фигуры, без
        to_window() и прочих преобразований.

        ЖЁСТКИЕ ПРАВИЛА Kivy для FloatLayout (чтобы текст не лип к (0, 0)):
          1) size_hint=(None, None) -- иначе FloatLayout растянет виджет, и
             текст «уедет» в угол растянутого бокса;
          2) ЯВНЫЙ размер size=(w, h) -- фиксируем конечные w/h (BevelLabel сам
             считает их под текст; если размер ещё не просчитан -- разумный
             минимум, чтобы width/height не были нулевыми при центровке);
          3) позиция -- через center=(cx, cy), а НЕ через pos: центр надписи
             тогда всегда точно в (x, y), независимо от её размера.
        anchor задаёт смысл (x, y):
            'center'     -- центр надписи (как PERFECT в точке среза);
            'topleft'    -- (x, y) = левый ВЕРХНИЙ угол (для HUD/счёта);
            'topright'   -- правый верхний угол;
            'bottomleft' -- левый нижний угол;
            'bottomright'-- правый нижний угол.
        life -- через сколько секунд надпись сама удалится (None = висит).
        """
        # (1) size_hint ВЫКЛючен -- FloatLayout не будет ни растягивать, ни
        #     сжимать виджет.
        lbl = BevelLabel(text=text, rgb=(rgb or PERFECT_RGB), font=font,
                         size_hint=(None, None), bold=bold, italic=True)
        # Форсируем пересчёт размера СЕЙЧАС (BevelLabel -- кастомный Widget, у
        # него нет texture_update(); его внутренний _build() рендерит текстуру
        # и выставляет self.size -- только тогда width/height реальные).
        if hasattr(lbl, '_build'):
            lbl._build()
        w = lbl.width if lbl.width > 1.0 else max(1.0, len(text) * font * 0.62)
        h = lbl.height if lbl.height > 1.0 else font * 1.7
        lbl.size = (w, h)

        # ЖЁСТКАЯ привязка: держим центр в (x, y) при ЛЮБОМ изменении size/pos
        # виджета. Если внутренняя графика BevelLabel сбросит pos в (0, 0) при
        # перерисовке canvas -- Kivy перехватит изменение pos и мгновенно
        # вернёт надпись в точку среза. (center задаёт pos; same-value guard
        # Kivy гасит повторный срабатывание -- рекурсии нет, сходится за 1-2
        # шага.) Координаты -- от сбитой фигуры в системе родителя (GameWidget).
        def force_position(instance, _value):
            if anchor == 'center':
                instance.center = (float(x), float(y))
            elif anchor == 'topleft':
                instance.center = (float(x) + w * 0.5, float(y) - h * 0.5)
            elif anchor == 'topright':
                instance.center = (float(x) - w * 0.5, float(y) - h * 0.5)
            elif anchor == 'bottomright':
                instance.center = (float(x) - w * 0.5, float(y) + h * 0.5)
            else:  # bottomleft
                instance.center = (float(x) + w * 0.5, float(y) + h * 0.5)

        lbl.bind(size=force_position, pos=force_position)
        force_position(lbl, None)          # ставим сразу
        self.add_widget(lbl)

        if life:
            def _drop(_dt, _l=lbl):
                try:
                    if _l.parent is not None:
                        _l.parent.remove_widget(_l)
                except Exception:                   # noqa: BLE001
                    pass
            Clock.schedule_once(_drop, float(life))
        return lbl

    def _perfect(self, cx, cy, color=None):
        """PERFECT: волна на весь экран + надпись ЦВЕТОМ последней сбитой сферы.

        color -- rgb разрубленной сферы (из _slice). Наследуем его: синяя
        сфера -> синий неон надписи, фиолетовая -> фиолетовый и т.д.
        В RGBA: альфа = 1.0, а затухание (opacity 1.0 -> 0.0) идёт через
        widget.opacity в impact() -- цвет этому не мешает.
        Кольцо-волна остаётся фирменным зелёным PERFECT_RGB.
        """
        col = tuple(color)[:3] if color else tuple(self._last_cut_rgb)[:3]
        self.last_perfect_color = (col[0], col[1], col[2], 1.0)   # RGBA
        self.fx.emit_perfect_ring(cx, cy, color=PERFECT_RGB)
        print('[PERFECT] серия x%d | цвет надписи RGB=(%.2f, %.2f, %.2f)'
              % (self._combo, col[0], col[1], col[2]))
        # надпись -- РОВНО там, где сбили фигуру (cx, cy = центр сферы в момент
        # реза). Позицию виджета держим в точке среза через center= в float_text();
        # шейдерный слой отрисовывается относительно self.pos автоматически.
        lbl = self.float_text(PERFECT_TEXT, cx, cy,
                              rgb=self.last_perfect_color, font=35,
                              life=PERFECT_TEXT_T, anchor='center')
        # «удар в экран»: аккуратный всплеск масштаба 1.0 -> 1.15 -> 1.0 за 0.08 с,
        # затем плавное затухание opacity 1.0 -> 0.0 ровно к концу life=PERFECT_TEXT_T
        # (font_size фиксирован, без резких прыжков размера).
        if hasattr(lbl, 'impact'):
            lbl.impact()
        # каждая ячейка бара -- один PERFECT; полный бар включает INCREDIBLE
        self._bar += 1.0
        if self._bar >= PERFECT_BAR_N and self._buff_t <= 0.0:
            self._buff_t = INCREDIBLE_T
            self._bar = float(PERFECT_BAR_N)    # бар утекает в update()
            print('[INCREDIBLE] ускоренный режим на %.0f с' % INCREDIBLE_T)
        else:
            self._bar = min(self._bar, float(PERFECT_BAR_N))
        self._bar_w.set_value(self._bar, color=PERFECT_RGB)

    def _layout_bar(self, *_a):
        """Полоска-стадион по центру верхнего края; счёт -- в левом углу.

        Стадион занимает 60 % ширины ровно по центру, поэтому счёт уводим в
        левый верхний угол: он там не пересекается с полоской и не мешает
        свайпу в рабочей области.
        """
        bw = self.width * PERFECT_BAR_W
        self._bar_w.size = (bw, PERFECT_BAR_H)
        self._bar_w.pos = ((self.width - bw) * 0.5,
                           self.height - PERFECT_BAR_H - PERFECT_BAR_MARGIN)
        # счёт прижат к левому верхнему углу, на уровне полоски. Ширину НЕ
        # растягиваем (size_hint=None), а pos считаем от фактического размера
        # цифр: счёт растёт (<9> -> <10> -> <250>), и он не должен наехать на
        # полосу-стадион, начинающуюся с середины экрана.
        self._score_lbl.size_hint = (None, None)
        self._score_lbl.pos = (SCORE_MARGIN,
                               self.height - PERFECT_BAR_MARGIN
                               - self._score_lbl.height)

    def _add_score(self, n=SCORE_PER_CUT):
        """Начислить очки за разрезанную фигуру и обновить счёт сверху."""
        self._score += n
        self._score_lbl.set_text('%d' % self._score)

    def _update_mult(self, px, py, rgb):
        """Серия попаданий: только счётчик, БЕЗ надписей на экране.

        Счётчик продолжает считаться (он нужен для PERFECT/INCREDIBLE), но на
        экране не рисуется ничего.
        """
        return

    def _break_streak(self, rgb=None):
        """Серия оборвалась: просто обнуляем счётчик серии."""
        self._combo = 0

    def _cut_k(self):
        """Множитель радиуса срабатывания среза: всегда БАЗОВЫЙ (1.0).

        В INCREDIBLE радиус НЕ растёт (вернули к обычному): усиление режима --
        <сварка> на конце следа, её искры режут сферы при касании
        (см. _spark_cuts). Так хитбокс лезвия остаётся честным в любом режиме.
        """
        return INCREDIBLE_K

    def _trail_tip(self):
        """Цвет <сварки> на конце следа (жёлтый) во время INCREDIBLE."""
        return INCREDIBLE_RGB if self._buff_t > 0.0 else None

    def _spark_cuts(self, now):
        """ИСКРЫ СВАРКИ РЕЖУТ: живая искра коснулась сферы -- сфера разрезана.

        Проверка честная: расстояние между центрами <= радиус сферы +
        полуразмер искры (искра короткая -- режет вплотную к следу лезвия).
        Работает ровно пока есть искры, то есть в INCREDIBLE; рез идёт тем же
        путём, что свайп (_slice): взрыв, половинки, PERFECT-серия.
        """
        pts = self.fx.weld_points()
        if not pts:
            return
        for s in list(self._active):
            if not s.sliceable(now):
                continue
            r = s.radius_at(now)
            for sx, sy, sr in pts:
                d = math.hypot(s.x - sx, s.y - sy)
                if d <= r + sr:
                    print('[HIT] искра сварки: до центра %.1f px '
                          '(сфера r=%.1f, искра %.1f)' % (d, r, sr))
                    self._slice(s, now)

    # --- тач-обработчик: ТАП тоже режет --------------------------------------
    def on_touch_down(self, touch):
        if not self.active:             # открыто меню -- игру не трогаем
            return False
        if self.is_paused:
            # пауза: тачи ловит ТОЛЬКО окно паузы; клик мимо кнопок -- игнор
            # (сферы в паузе не режем, случайно не резюмим)
            return super().on_touch_down(touch)
        if super().on_touch_down(touch):
            return True
        if getattr(touch, 'button', None) not in (None, 'left', 'touch'):
            return False
        if self.state in ('ready', 'paused'):
            self.start_track()
            return True
        if self.state == 'ended':
            self.reset()
            return True
        if self.state == 'effects':
            # витрина: клики по полю ловит меню (чекбоксы) -- демо не трогаем
            return True
        self._try_tap(float(touch.x), float(touch.y))
        return True

    def reset(self):
        """Заново: счёт/комбо/указатель/ноты с нуля."""
        for n in list(self._active):
            self._remove(n)
        self._active = []
        self._idx = 0
        self.note_pointer = 0
        self._score = 0
        self._combo = 0
        self._best_combo = 0
        self._nmiss = 0
        self._last_cut_rgb = PERFECT_RGB   # цвет последней сбитой фигуры
        self._mult_lbl = None
        self._score_lbl.set_text('0')
        self._clear_float()            # плавающие надписи -- прочь
        self._clear_end()               # на финише панель -- прочь
        self._bar = 0.0                   # бар PERFECT-ов и режим INCREDIBLE
        self._buff_t = 0.0
        self._bar_w.set_value(0.0, color=PERFECT_RGB)
        self._paused_pos = 0.0
        self._perf_start = None
        self.real_duration = 0.0
        self._arm_text = ''
        self._force_figure = None    # новая партия: без форса витрины
        # сброс descending-серии цветов для новой партии
        self.color_series_limit = 10
        self.color_series_counter = 0
        self.current_series_color = None
        self.color_last_seen = {}
        self.color_pick_no = 0
        self._stop_sound()
        self.state = 'ready'

    # --- главный цикл --------------------------------------------------------
    def update(self, dt):
        # ШЛАГБАУМ ПАУЗЫ: пока висит меню паузы -- НИКАКОЙ геймплейной логики.
        # fx.update не зовётся -> сферы, осколки и следы застывают в пространстве;
        # бар INCREDIBLE не убывает; спавн/подшаги реза не считаются. Мир замирает.
        if self.state == 'paused':
            return
        self.fx.update(dt)

        # --- INCREDIBLE: бар утекает, пока идёт усиленный режим --------------
        if self._buff_t > 0.0:
            self._buff_t = max(0.0, self._buff_t - dt)
            if self._buff_t <= 0.0:
                self._bar = 0.0
                self._bar_w.set_value(0.0, color=PERFECT_RGB)
                print('[INCREDIBLE] режим закончился, бар пуст')
            else:
                self._bar = PERFECT_BAR_N * (self._buff_t / INCREDIBLE_T)
                self._bar_w.set_value(self._bar, color=INCREDIBLE_RGB)

        if self.state == 'effects':
            self._fx_clock += dt        # превью: часы идут без музыки
            # ВИТРИНА: три демо-фигуры строго вертикальным рядом напротив
            # своих чекбоксов. Активная (тип совпадает с активным чекбоксом
            # меню) — яркая (fade = 1.0), крутится и разрезается раз в 3 с.
            # Остальные гасятся (fade = 0.0) — мусорных пикселей в углу
            # (0, 0) не создают, т.к. каждая прибита к своей строке.
            stars = []
            active_kind = self._fx_kind if self._fx_kind in ('star', 'cube', 'water') else 'star'
            # Динамически обновляем "пин" позиций: при первом старте окно ещё
            # могло не иметь реального размера, и фигуры могли остаться в (0,0).
            # Как только Kivy задаст self.width/self.height — фигуры мгновенно
            # встанут на свои строки напротив чекбоксов.
            for k, demo_shape in self._demos.items():
                idx = 0 if k == 'star' else (1 if k == 'cube' else 2)
                current_px = self.width * 0.75
                current_py = self.height / 2 + 96 - idx * 56
                demo_shape.pin = (current_px, current_py)
            for kind, s in self._demos.items():
                s.update_pos(self._fx_clock)          # pin: позиция фиксирована
                st = list(s.star_state(self._fx_clock))
                st[4] = 1.0 if kind == active_kind else 0.0   # fade
                stars.append(tuple(st))
            self.fx.set_stars(stars)
            # демонстрационный разрез активной фигуры раз в 3 секунды
            now = self._fx_clock
            last = getattr(self, '_fx_last_slice', 0.0)
            if active_kind in self._demos and now - last >= 3.0:
                s = self._demos[active_kind]
                r = s.radius_at(now)
                self.fx.emit_slice(s.x, s.y, color=s.rgb, radius=r)
                self._fx_last_slice = now
            self._update_hud()
            return
        elif self.state != 'playing':
            self._last_mouse = None     # свайп через паузу не режет ноты
            self._cur_now = None        # путь сферы через паузу не тянем
            self._update_hud()
            return

        # --- ПРЕДОХРАНИТЕЛЬ: не обновляем игровой цикл без валидной сетки ---
        if self.state != 'playing' or not getattr(self, 'tick_duration', 0):
            return True

        self.game_time = self._paused_pos + (time.perf_counter() - self.start_timestamp)
        now = self.game_time
        current_game_tick = self.game_time / self.tick_duration
        prev_now = self._cur_now        # время прошлого кадра
        self._cur_now = now

        # --- ЖЁСТКАЯ СИНХРОНИЗАЦИЯ АУДИО с pause_time после резюма ----------
        # Игровой таймер (now) -- источник истины. Если драйвер мотает трек
        # (get_pos живой, mp3/ogg) и аудио разошлось с таймером больше чем
        # на 2 с (seek не приклеился / отстал) -- доворачиваем на текущую
        # игровую секунду. Драйверы с get_pos==0 молча пропускаем.
        if self._resume_sync_t > 0.0:
            self._resume_sync_t = max(0.0, self._resume_sync_t - dt)
            if self._sound is not None:
                try:
                    gp = float(self._sound.get_pos())
                except Exception:                   # noqa: BLE001
                    gp = 0.0
                if gp > 0.0 and abs(gp - now) > 2.0:
                    try:
                        self._sound.seek(now)
                        print('[PAUSE] update-синхр: get_pos=%0.1f -> '
                              'seek(%0.1f)' % (gp, now))
                    except Exception:               # noqa: BLE001
                        pass

        # позиция мыши этого кадра (свайп = путь лезвия между кадрами)
        try:
            mouse_b = (float(Window.mouse_pos[0]), float(Window.mouse_pos[1]))
        except Exception:                       # noqa: BLE001 -- окно без мыши
            mouse_b = None
        mouse_a = self._last_mouse           # позиция мыши прошлого кадра
        self._last_mouse = mouse_b
        if (mouse_a is not None and mouse_b is not None
                and mouse_a != mouse_b):
            # след рисуем ОДИН раз за кадр -- по всему пути мыши
            self.fx.add_trail(mouse_a[0], mouse_a[1],
                              mouse_b[0], mouse_b[1], life=TRAIL_LIFE,
                              tip=self._trail_tip())

        # --- ПОДШАГИ 200 Гц: рез проверяется не раз за кадр, а каждые ~5 мс ---
        # Kivy зовёт update() раз за кадр (60 Гц), поэтому дробим интервал
        # кадра: позиции сфер берутся из точной формулы pos_at(t), лезвие
        # интерполируется по тому же времени. Разрешение момента реза
        # 1/200 с вместо 1/60, и удар приходится ровно на свой суб-тик.
        span = (now - prev_now) if (prev_now is not None
                                    and prev_now <= now) else 0.0
        steps = max(1, min(SUB_MAX, int(span * SUB_HZ) + 1))
        for i in range(1, steps + 1):
            k0 = (i - 1) / float(steps)
            k1 = i / float(steps)
            t0 = prev_now + span * k0 if span else now
            t1 = prev_now + span * k1 if span else now
            self._spawn_due(t1)              # вычисляет self.ahead_ticks
            # ТИК-БАЗИСНЫЙ СПАВН: нота рождается когда hit_tick - ahead_ticks <= current_tick
            while (self.note_pointer < len(self.level_notes)
                   and (self.level_notes[self.note_pointer]["tick"] - self.ahead_ticks) <= current_game_tick):
                note_data = self.level_notes[self.note_pointer]
                self.note_pointer += 1
                self._idx = self.note_pointer  # синхронизируем со старым указателем для _spawn_one
                if random.random() > self._density(self._progress(note_data['t'])):
                    continue                       # разгон: в начале шаров меньше
                exact_hit_time = note_data["tick"] * self.tick_duration
                note = {'t': exact_hit_time, 'kind': note_data.get('kind', 'onset')}
                self._spawn_one(note)
            for s in self._active:
                s.update_pos(t1)             # точная позиция на суб-тике
            if mouse_b is None:
                continue
            if mouse_a is None or not span:
                pa = pb = mouse_b
            else:
                pa = (mouse_a[0] + (mouse_b[0] - mouse_a[0]) * k0,
                      mouse_a[1] + (mouse_b[1] - mouse_a[1]) * k0)
                pb = (mouse_a[0] + (mouse_b[0] - mouse_a[0]) * k1,
                      mouse_a[1] + (mouse_b[1] - mouse_a[1]) * k1)
            self._blade_sweep(pb[0], pb[1], t1, t0, prev=pa)

        # <сварка> INCREDIBLE: искры на конце следа режут всё, чего коснутся
        self._spark_cuts(now)

        # полёт Genesis-звезд; разрезанные растворяются (мост к половинкам),
        # несрезанные тихо падают за экран
        stars = []
        for s in list(self._active):
            if s.dead:
                if s.die_done(now):            # погасла -- убираем
                    self._active.remove(s)
                    self._remove(s)
                else:
                    stars.append(s.star_state(now))   # растворение по кадрам
                continue
            if s.should_remove(now):
                self._active.remove(s)
                self._remove(s)
                self._nmiss += 1
                # серия попаданий оборвалась -> просто сброс счётчика
                self._break_streak()
                print('[MISS] сфера ушла несрезанной: dt=%+.0f мс'
                      % ((now - s.hit_time) * 1000.0))
            else:
                stars.append(s.star_state(now))
        self.fx.set_stars(stars)

        # конец трека: музыка доиграла -> ФИНАЛЬНЫЙ СЧЁТ
        duration = float(self.map_data.get('duration') or 0.0)
        duration = TRACK_DURATION_OVERRIDE.get(self.map_name, duration)
        # Финиш по времени: игровой таймер превысил длину трека.
        audio_done = False
        if duration > 0 and now > duration - 0.5:
            audio_done = True
        if self.state == 'playing' and duration > 0 and audio_done:
            self.state = 'ended'
            self._finish()

        if self.state == 'effects':
            self._effects_autoslice(now)     # превью: фигуры тают сами

        self._update_hud()

    # --- экран <Эффекты>: ВИТРИНА фигур (меню с чекбоксами остаётся) --------
    def enter_effects(self, kind=None):
        """Открыть экран <Эффекты>: чистое поле под меню с чекбоксами.

        Поле активно (state='effects'), но фигуры-НОТЫ не спавнятся -- здесь
        живут ТРИ демо-фигуры (star/cube/water), каждая строго напротив своей
        строки чекбокса. Музыки нет, часы идут от кадров.

        kind -- 'star' | 'cube' | 'water' | 'both' | None.
        Если None -- используется 'star' (первая строка меню).
        """
        self.stop_playback()                 # музыка стоп + сброс поля
        self._sound = None
        self._fx_clock = 0.0
        self._cur_now = None
        self._last_mouse = None
        self._idx = 0
        self._map_notes = []                 # в витрине нот нет -- демо рулит
        self.map_data = {'duration': 3.0}
        self.map_name = 'ЭФФЕКТЫ'
        self.state = 'effects'
        self._clear_demo()
        # превью для ВСЕХ трёх типов — каждое напротив своего чекбокса
        for k in ('star', 'cube', 'water'):
            self.spawn_demo(k)
        # активный тип: переданный kind, иначе 'star' (первая строка меню)
        self._fx_kind = kind if kind in ('star', 'cube', 'water') else 'star'
        self._force_figure = self._fx_kind

    def exit_effects(self):
        """Покинуть экран <Эффекты>: убрать демо и вернуть поле в ready."""
        # форс витрины НЕ должен течь в игру: иначе pick_figure вернёт
        # только демо-тип фигуры и галочки тумблеров будут игнорироваться
        self._force_figure = None
        self._clear_demo()
        self.stop_playback()

    def _clear_demo(self):
        """Удалить все демо-фигуры витрины (при переключении / выходе)."""
        for s in list(self._demos.values()):
            self._remove(s)
        self._demos.clear()

    def spawn_demo(self, kind):
        """Показать/переместить демо-фигуру типа kind напротив её строки меню.

        kind: 'star' | 'cube' | 'water'. Фигура прибита к фиксированной
        позиции (Y — центр строки её чекбокса, X — общая колонка правее
        меток/чехлов) и больше не блуждает по экрану.
        """
        if kind not in ('star', 'cube', 'water'):
            kind = 'star'
        # если такая фигура уже есть — заменить на новую (на том же месте)
        if kind in self._demos:
            self._remove(self._demos[kind])
        # Фоллбек: при первом старте Kivy ещё не определил реальный размер
        # окна (self.width/self.height == 0) — иначе px/py считаются в 0 и
        # фигура падает в левый нижний угол (скриншот 3).
        w = self.width if self.width > 100 else 800
        h = self.height if self.height > 100 else 600
        # Y строго по центру строки чекбокса (зеркало menu.py MenuWidget.rebuild):
        #   start_y = h/2 + 4*24 (4 строки: star, cube, water, Назад), шаг 56 px
        idx = ('star', 'cube', 'water').index(kind)
        py = h * 0.5 + 4 * 24.0 - idx * 56.0
        # X — вертикальная колонка правее чекбоксов/текстовых меток
        px = w * 0.75
        s = FlySphere(px, py, 0.0, 1.0, 0.0, kind='kick',
                      screen_w=w, screen_h=h, figure=kind,
                      rgb=figure_rgb(kind, self._fx_clock), pin=(px, py))
        self._demos[kind] = s
        self._active.append(s)
        self.add_widget(s)
        # форс типа для обратной совместимости (превью конкретной фигуры)
        self._fx_kind = kind
        self._force_figure = kind
        print('[FX] витрина: демо %s в (%.0f, %.0f)' % (kind, px, py))

    def toggle_shape(self, kind):
        """Галочка: добавить/убрать фигуру из active_shapes (из меню)."""
        if kind not in ('star', 'cube', 'water'):
            return
        if kind in self.active_shapes:
            self.active_shapes.remove(kind)
        else:
            self.active_shapes.append(kind)
        print('[FX] активные фигуры: %s' % ', '.join(self.active_shapes)
              or '(нет)')

    def set_shape_active(self, kind, active):
        """Явное вкл/выкл фигуры в пуле -- принимает бинд тумблера active.

        Идемпотентно: повторное то же значение ничего не меняет, поэтому
        безопасно вызывается из бинда при каждой инициализации/синхронизации
        (не разворачивает состояние обратно и не петляет).
        """
        if kind not in ('star', 'cube', 'water'):
            return
        active = bool(active)
        has = kind in self.active_shapes
        if active == has:
            return
        if active:
            self.active_shapes.append(kind)
        else:
            self.active_shapes.remove(kind)
        print('[FX] активные фигуры: %s' % ', '.join(self.active_shapes)
              or '(нет)')

    def select_all_shapes(self):
        """Кнопка <Выбрать все>: включить все три фигуры."""
        self.active_shapes = ['star', 'cube', 'water']
        print('[FX] выбраны все фигуры')

    def _effects_autoslice(self, now):
        """В витрине фигуры НЕ рассыпаются сами -- демо крутится вечно."""
        return

    def show_effects(self, kind='both'):
        """Обратная совместимость: старое имя -> новая витрина."""
        self.enter_effects()
        if kind in ('star', 'cube', 'water'):
            self.spawn_demo(kind)

    def _hint_text(self):
        if self.state == 'ready':
            if self._arm_text:
                return self._arm_text
            return ('%s\nклик / Пробел -- начать   Esc -- меню'
                    % (self.track_title or self.map_name))
        if self.state == 'effects':
            # витрина: меню с чекбоксами поверх -- на поле ничего не пишем
            return ''
        if self.state == 'paused':
            return 'ПАУЗА\nклик / Пробел -- продолжить   Esc -- в меню'
        if self.state == 'ended':
            # итог показывает финальная панель (см. _finish) -- тут только пусто
            return ''
        return ''

    def _finish(self):
        """Финиш: общий счёт на весь экран + фанфары."""
        if getattr(self, '_end_lbls', None):
            return                      # панель уже собрана -- не дублируем
        self._stop_sound()              # глушим музыку — не орать на фоне статистики
        self._clear_float()             # плавающие надписи на финише не висят
        self._bar = 0.0                  # полоса стадиона на финише пустая
        self._bar_w.set_value(0.0, color=PERFECT_RGB)
        cx, cy = self.width * 0.5, self.height * 0.5

        def _mk(text, dy, font, rgb):
            lbl = BevelLabel(text=text, rgb=rgb, font=font,
                             size_hint=(None, None))
            lbl.pos = (cx - lbl.width * 0.5, cy + dy)
            self.add_widget(lbl)
            self._end_lbls.append(lbl)
            return lbl

        _mk('ФИНИШ', 110.0, 44, SCORE_RGB)
        _mk('%d ОЧКОВ' % self._score, 40.0, 64, PERFECT_RGB)
        _mk('лучшая серия x%d   фигур: %d   miss: %d'
            % (self._best_combo, int(self._score // SCORE_PER_CUT),
               self._nmiss),
            -30.0, 24, (0.78, 0.88, 1.00))
        _mk('клик -- сыграть ещё раз      Esc -- в меню', -90.0, 22,
            (0.58, 0.70, 0.88))
        self._fanfare(cx, cy)
        print('[END] общий счёт: %d очков | лучшая серия x%d | фигур: %d | '
              'miss: %d' % (self._score, self._best_combo,
                            int(self._score // SCORE_PER_CUT), self._nmiss))

    def _fanfare(self, cx, cy):
        """Фанфары: залпы волн и искр, расходящихся от центра к краям."""
        palette = [INCREDIBLE_RGB, PERFECT_RGB, (1.00, 0.45, 0.15),
                   (0.30, 0.85, 1.00), (0.65, 0.30, 0.95)]
        for i in range(FANFARE_SHOTS):
            rgb = palette[i % len(palette)]

            def _shot(_dt, _rgb=rgb, _i=i):
                """Один залп фанфар: волна во весь экран + веер искр."""
                self.fx.emit_perfect_ring(cx, cy, dur=2.4, color=_rgb)
                ang = 2.0 * math.pi * (_i / float(FANFARE_SHOTS))
                for k in range(6):
                    a = ang + k * (2.0 * math.pi / 6.0)
                    rr = self.width * 0.22
                    self.fx._spawn(14, cx + math.cos(a) * rr,
                                   cy + math.sin(a) * rr,
                                   speed=(260.0, 700.0), ang0=a, spread=1.1,
                                   life=(0.5, 1.1), size=(2.0, 4.0),
                                   elong=(1.6, 2.6), color=_rgb, strength=1.3,
                                   damp=(0.95, 0.99), gravity=260.0,
                                   shape=0.0, follow_vel=1)
            Clock.schedule_once(_shot, i * FANFARE_STEP)

    def _clear_float(self):
        """Убрать все плавающие объёмные надписи, кроме постоянного счёта."""
        for lbl in list(self.children):
            if isinstance(lbl, BevelLabel) and lbl is not self._score_lbl:
                self.remove_widget(lbl)
        self._mult_lbl = None

    def _clear_end(self):
        """Убрать финальную панель (при возврате в игру или в меню)."""
        for lbl in getattr(self, '_end_lbls', None) or []:
            if lbl.parent is not None:
                self.remove_widget(lbl)
        self._end_lbls = []
        for ins in getattr(self, '_end_canvas', None) or []:
            if ins in self.canvas.children:
                self.canvas.remove(ins)
        self._end_canvas = []
        self._end_score_lbl = None

    def _update_hud(self):
        """HUD обновляется сам из виджетов: подписи синхрона больше нет."""
        return


class RhythmSlasherApp(App):
    """SoundGrip: стартовое меню -> игра -> меню.

    Трек и громкость живут в приложении: меню только выбирает и зовёт
    колбэки (on_play / on_volume / on_exit / on_effects), игра -- GameWidget.
    """

    def build(self):
        if Window is not None:
            Window.clearcolor = (0, 0, 0, 1)
        self.volume = 0.8
        self._menu_music_playing = False
        self._menu_sound = None
        self.tracks = tracklib.list_tracks()
        want = _map_from_argv()
        self.track = next((t for t in self.tracks if t.map_name == want), None)
        if self.track is None and self.tracks:
            self.track = self.tracks[0]
        print('[SoundGrip] треков в меню: %d%s'
              % (len(self.tracks),
                 '' if tracklib.has_ffmpeg()
                 else ' (ffmpeg не найден -- новые карты не собрать)'))

        self.root_w = FloatLayout()
        self.game = GameWidget(track=self.track, volume=self.volume,
                               on_menu=self.show_menu)
        self.menu = MenuWidget(on_play=self.start_game,
                               on_exit=self.stop,
                               on_volume=self.set_volume,
                               on_effects=self.start_effects,
                               on_exit_effects=self.exit_effects)
        self.menu.set_game(self.game)          # чекбоксы/демо <Эффекты>
        Clock.schedule_interval(self.game.update, 0)   # игра: каждый кадр
        Clock.schedule_interval(self.menu.update, 0)   # меню: наведение
        self.show_menu()
        return self.root_w

    def on_play(self):
        """Toggle playback state and update Play button text."""
        if self._sound is None:
            print('[SoundGrip] No sound loaded')
            return
        if self._sound.state == 'play':
            self._sound.pause()
            self.ids.play_button.text = 'Play'
        else:
            self._sound.play()
            self.ids.play_button.text = 'Pause'

    def on_volume(self, value=None):
        """Apply the slider value to the audio source volume."""
        if self._sound is None:
            print('[SoundGrip] No sound loaded')
            return
        # Get volume from slider if provided, otherwise use the class volume attribute
        vol = value if value is not None else self.volume
        vol = max(0.0, min(1.0, float(vol)))
        self._sound.volume = vol
        if self._vol_lbl is not None:
            self._vol_lbl.text = 'ГРОМКОСТЬ: %d%%' % round(vol * 100)

    def on_speed(self, value=None):
        """Adjust playback speed based on the speed slider."""
        if self._sound is None:
            print('[SoundGrip] No sound loaded')
            return
        # Get speed from slider if provided, otherwise use default
        speed = value if value is not None else 1.0
        # Kivy SoundLoader uses playback_rate for speed control
        self._sound.playback_rate = max(0.5, min(2.0, float(speed)))

    # --- переключение экранов -------------------------------------------------
    def show_menu(self):
        """Вернуться в меню (Esc из паузы, конец трека, выход из игры)."""
        self.game.exit_effects()            # убрать демо-витрину, если была
        self.game.stop_playback()
        self.game._clear_end()              # убрать надписи финиша (счёт, ФИНИШ, …)
        self.game.active = False
        self._play_menu_music()             # музыка меню по кругу
        # Скрыть игровой HUD (очки + бар INCREDIBLE) в меню
        try:
            self.game._bar_w.opacity = 0
            self.game._score_lbl.opacity = 0
        except Exception:
            pass
        if self.game.parent is self.root_w:
            self.root_w.remove_widget(self.game)
        if self.menu.parent is None:
            self.root_w.add_widget(self.menu)
        self.menu.show(self.track)
        self.menu.set_volume(self.volume)

    def start_game(self, track):
        """Новая игра / выбранный трек: карта есть -> играем, нет -> соберём."""
        if track is None:
            return
        self._stop_menu_music()             # стоп музыку меню перед игрой
        self.track = track
        # НЕ сбрасываем active_shapes здесь — выбор эффектов из меню
        # должен переноситься в геймплей. Дефолт ['star'] уже задан в __init__.
        if not track.has_map:
            self.menu.set_status('создаю карту: слушаю трек...')
            # генерация блокирует поток -- сначала даём кадр на отрисовку
            Clock.schedule_once(lambda _dt: self._prepare_and_play(track), 0.05)
            return
        self._enter_game(track)

    def _prepare_and_play(self, track):
        """Достроить карту трека (офлайн-анализ) и войти в игру."""
        try:
            _data, state = tracklib.ensure_map(track,
                                               status=self.menu.set_status)
        except Exception as e:                  # noqa: BLE001 -- не роняем
            state = 'ошибка: %s' % e
        self.menu.set_status('карта: %s' % state)
        self._enter_game(track)

    def _enter_game(self, track):
        """Показать игровое поле и запустить трек."""
        self.menu.hide()
        if self.menu.parent is self.root_w:
            self.root_w.remove_widget(self.menu)
        if self.game.parent is None:
            self.root_w.add_widget(self.game)
        self.game.active = True
        # Показать игровой HUD (очки + бар INCREDIBLE) при старте уровня
        try:
            self.game._bar_w.opacity = 1
            self.game._score_lbl.opacity = 1
        except Exception:
            pass
        self.game.load_track(track)
        self.game.start_track()

    def start_effects(self, kind='both'):
        """Пункт меню <Эффекты>: витрина фигур -- поле демо ПОД меню.

        Меню с чекбокками ОСТАЁТСЯ на экране (и перехватывает ввод), а поле
        GameWidget показывает вращающуюся демо-фигуру справа. Оба виджета
        лежат в root_w, menu -- последний (поверх), поэтому он сверху.
        """
        # убрать надписи финиша, если они остались на экране
        self.game._clear_end()
        # показать поле (витрину) под меню
        if self.game.parent is None:
            self.root_w.add_widget(self.game)
        self.game.active = False          # ввод у меню; поле только рисует демо
        self.game.enter_effects(kind)     # state='effects' + демо-фигура нужного типа
        # меню -- обязательно поверх поля (последним в root_w)
        if self.menu.parent is None:
            self.root_w.add_widget(self.menu)
        else:
            self.root_w.remove_widget(self.menu)
            self.root_w.add_widget(self.menu)
        self.menu.active = True
        self.menu.show_effects(kind)
        self.menu.show_effects(kind)
        # --- СИНХРОНИЗАЦИЯ: активные фигуры игры <-> чекбоксы меню ---
        # без этого бинды эффектов не работают: в игре всегда летят только сферы
        for k in ('star', 'cube', 'water'):
            self.game.set_shape_active(k, self.menu._fx_active.get(k, False))

    def exit_effects(self):
        """Очистить демо-поле эффектов при возврате из меню.

        Вызывается из menu.back() через on_exit_effects.
        Убирает GameWidget из root_w и сбрасывает демо-состояние границ/фигур,
        чтобы «зелёный куб-призрак» исчез. Меню остаётся на экране.
        """
        self.game.exit_effects()
        if self.game.parent is self.root_w:
            self.root_w.remove_widget(self.game)

    def set_volume(self, v):
        """Громкость из меню <Звук> — мгновенно применяется к игровому треку
        и к зацикленной музыке меню (если сейчас играет)."""
        self.volume = max(0.0, min(1.0, float(v)))
        self.game.set_volume(self.volume)
        if self._menu_sound is not None:
            try:
                self._menu_sound.volume = self.volume
            except Exception:  # noqa: BLE001
                pass

    # --- музыка меню (главное меню + пауза), зацикленная -----------------------
    def _play_menu_music(self):
        """Запустить трек меню по кругу через Kivy Sound.

        Kivy Sound не поддерживает loops=-1, поэтому зацикливание делаем
        через привязку к событию on_stop: при остановке трека перезапускаем
        его заново, если флаг _menu_music_playing всё ещё True.
        """
        if not os.path.exists(MENU_MUSIC_PATH):
            print('[MENU] трек меню не найден: %s' % MENU_MUSIC_PATH)
            return
        try:
            self._menu_sound = SoundLoader.load(MENU_MUSIC_PATH)
            if self._menu_sound is None:
                print('[MENU] не удалось загрузить музыку меню: %s' % MENU_TRACK)
                return
            self._menu_sound.volume = self.volume
            self._menu_sound.bind(on_stop=self._on_menu_music_stop)
            self._menu_sound.play()
            self._menu_music_playing = True
            print('[MENU] музыка меню (цикл, Kivy Sound): %s' % MENU_TRACK)
        except Exception as e:  # noqa: BLE001
            print('[MENU] не удалось запустить музыку меню: %s' % e)

    def _on_menu_music_stop(self, instance):
        """Перезапуск музыки меню, если она должна играть дальше (зацикливание)."""
        if self._menu_music_playing and self._menu_sound is not None:
            try:
                self._menu_sound.play()
            except Exception:  # noqa: BLE001
                pass

    def _stop_menu_music(self):
        """Остановить музыку меню."""
        self._menu_music_playing = False
        try:
            if self._menu_sound is not None:
                self._menu_sound.stop()
                self._menu_sound.unbind(on_stop=self._on_menu_music_stop)
                self._menu_sound = None
        except Exception:  # noqa: BLE001
            pass

if __name__ == '__main__':
    try:
        RhythmSlasherApp().run()
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
